"""The Day-1 attack classes, end to end through the pipeline.

Each attack is a *perturbation of a request that would otherwise pass*, which
is what makes the result meaningful: exactly one thing is wrong, so the gate
that fires is the gate being tested.

Covers A1–A6 from docs/03. A7–A11 arrive with G4 and the clearing layer.
"""

from __future__ import annotations

import base64
import uuid
from datetime import timedelta

import pytest

from kya.canonical import now_utc
from kya.enums import Decision, GateVerdict
from kya.simulation import (
    AgentIdentity,
    Principal,
    build_signed_request,
    make_cart,
    make_mandates,
    resign_request,
)


def codes(envelope) -> set[str]:
    return set(envelope.reason_codes)


class TestBaseline:
    def test_legitimate_request_is_allowed(self, sandbox, good_request):
        env = sandbox.evaluate(good_request)
        assert env.decision is Decision.ALLOW
        assert env.reason_codes == []
        assert all(g.verdict is GateVerdict.PASS for g in env.gate_trace)

    def test_latency_is_well_inside_budget(self, sandbox, good_request):
        env = sandbox.evaluate(good_request)
        assert env.latency_ms < 50.0


class TestA1AgentImpersonation:
    def test_unsigned_request_is_denied(self, sandbox, good_request):
        good_request.signature = None
        good_request.signature_input_raw = None

        env = sandbox.evaluate(good_request)
        assert env.decision is Decision.DENY
        assert "I001" in codes(env)

    def test_tampered_signature_is_denied(self, sandbox, good_request):
        """Flip the signature bytes. Positive evidence of forgery."""
        label, _, wire = good_request.signature.partition("=")
        raw = bytearray(base64.b64decode(wire.strip(":")))
        raw[0] ^= 0xFF
        good_request.signature = f"{label}=:{base64.b64encode(bytes(raw)).decode()}:"

        env = sandbox.evaluate(good_request)
        assert env.decision is Decision.DENY
        assert "I002" in codes(env)

    def test_agent_signing_with_an_unpublished_key_is_denied(
        self, sandbox, principal
    ):
        """An impostor mints its own keypair and claims a known agent's origin.

        The claimed identity is real and registered; the key is not one of
        theirs. This is the DataDome-measured impersonation shape.
        """
        impostor = AgentIdentity.create("agent_shopper", key_seed_tag="impostor")

        cart = make_cart()
        mandates = make_mandates(impostor, principal, cart)
        request = build_signed_request(impostor, mandates, cart)

        env = sandbox.evaluate(request)
        assert env.decision is Decision.DENY
        assert "I003" in codes(env)

    def test_missing_signature_agent_is_denied(self, sandbox, good_request):
        good_request.signature_agent = None
        env = sandbox.evaluate(good_request)
        assert env.decision is Decision.DENY
        assert "I005" in codes(env)


class TestA2KeySubstitution:
    def test_rotated_out_key_is_denied(self, sandbox, agent, good_request):
        """The directory answers and no longer lists this key."""
        sandbox.fetcher.withdraw(agent.origin, agent.keypair.key_id)
        sandbox.directory.invalidate(agent.origin)

        env = sandbox.evaluate(good_request)
        assert env.decision is Decision.DENY
        assert "I003" in codes(env)

    def test_key_from_a_different_agents_directory_is_denied(
        self, sandbox, principal
    ):
        """Signing key is real, but published by someone else's origin."""
        other = sandbox.register_agent(AgentIdentity.create("agent_other"))
        impersonator = AgentIdentity.create(
            "agent_shopper", origin=other.origin, key_seed_tag="impostor"
        )

        cart = make_cart()
        mandates = make_mandates(impersonator, principal, cart)
        request = build_signed_request(impersonator, mandates, cart)

        env = sandbox.evaluate(request)
        assert env.decision is Decision.DENY
        assert "I003" in codes(env)


class TestA3Replay:
    def test_replayed_signature_is_denied(self, sandbox, good_request):
        """Same signature, fresh idempotency key — a genuine replay, not a retry."""
        first = sandbox.evaluate(good_request)
        assert first.decision is Decision.ALLOW

        good_request.idempotency_key = uuid.uuid4().hex
        second = sandbox.evaluate(good_request)

        assert second.decision is Decision.DENY
        assert "R001" in codes(second)

    def test_stale_timestamp_is_denied(self, sandbox, agent, principal):
        cart = make_cart()
        mandates = make_mandates(agent, principal, cart)
        long_ago = now_utc() - timedelta(hours=2)
        request = build_signed_request(
            agent, mandates, cart, created=long_ago, expires_in=timedelta(hours=3)
        )

        env = sandbox.evaluate(request, now=now_utc())
        assert env.decision is Decision.DENY
        assert "R002" in codes(env)

    def test_expired_signature_is_denied(self, sandbox, agent, principal):
        """Inside the skew window, but the signature's own expiry has passed."""
        cart = make_cart()
        mandates = make_mandates(agent, principal, cart)
        created = now_utc() - timedelta(seconds=200)
        request = build_signed_request(
            agent, mandates, cart, created=created, expires_in=timedelta(seconds=30)
        )

        env = sandbox.evaluate(request, now=now_utc())
        assert env.decision is Decision.DENY
        assert "R003" in codes(env)


class TestA4MandateSubstitution:
    def test_valid_mandate_paired_with_a_different_cart_is_denied(
        self, sandbox, agent, principal
    ):
        """The heart of the thesis.

        Every signature here is genuine. The agent is correctly identified, the
        principal really did delegate, and the mandate really was signed. Only
        the binding between mandate and charged cart is broken — and identity-
        only defence sees nothing wrong.
        """
        signed_cart = make_cart(items=[("SKU-CASE", "Phone case", 1, 499_00)])
        mandates = make_mandates(agent, principal, signed_cart, max_amount=100_000_00)

        substituted = make_cart(items=[("SKU-TV-55", "55in TV", 1, 64_999_00)])
        request = build_signed_request(agent, mandates, substituted)

        env = sandbox.evaluate(request)

        assert env.decision is Decision.DENY
        assert codes(env) & {"C001", "C002", "C003"}

        g1 = next(g for g in env.gate_trace if g.gate.value == "G1")
        g2 = next(g for g in env.gate_trace if g.gate.value == "G2")
        assert g1.verdict is GateVerdict.PASS, "identity is genuine"
        assert g2.verdict is GateVerdict.PASS, "mandate chain is intact"

    def test_cart_mandate_from_a_different_intent_is_denied(
        self, sandbox, agent, principal
    ):
        """Two real mandates that do not reference each other."""
        cart = make_cart()
        bundle_a = make_mandates(agent, principal, cart)
        bundle_b = make_mandates(agent, principal, cart)

        bundle_a.cart = bundle_b.cart  # both genuinely signed, chain broken
        request = build_signed_request(agent, bundle_a, cart)

        env = sandbox.evaluate(request)
        assert env.decision is Decision.DENY
        assert "M002" in codes(env)

    def test_mandate_delegated_to_another_agent_is_denied(
        self, sandbox, agent, principal
    ):
        other = sandbox.register_agent(AgentIdentity.create("agent_other"))
        cart = make_cart()
        mandates = make_mandates(other, principal, cart)  # delegates to `other`

        # `agent` presents it as its own, re-signing the cart mandate honestly.
        from kya.crypto import sign_payload

        mandates.cart.signer_key_id = agent.keypair.key_id
        mandates.cart.signature = sign_payload(
            agent.keypair.private, mandates.cart.signing_payload()
        )
        request = build_signed_request(agent, mandates, cart)

        env = sandbox.evaluate(request)
        assert env.decision is Decision.DENY
        assert "M006" in codes(env)

    def test_mandate_signed_by_an_unregistered_principal_is_denied(
        self, sandbox, agent
    ):
        stranger = Principal.create("user_mallory")  # never registered
        cart = make_cart()
        mandates = make_mandates(agent, stranger, cart)
        request = build_signed_request(agent, mandates, cart)

        env = sandbox.evaluate(request)
        assert env.decision is Decision.DENY
        assert "M004" in codes(env)

    def test_expired_mandate_is_denied(self, sandbox, agent, principal):
        cart = make_cart()
        issued = now_utc() - timedelta(hours=3)
        mandates = make_mandates(
            agent, principal, cart, issued_at=issued, intent_ttl=timedelta(minutes=30)
        )
        request = build_signed_request(agent, mandates, cart)

        env = sandbox.evaluate(request)
        assert env.decision is Decision.DENY
        assert "M003" in codes(env)


class TestA5PriceTampering:
    def test_inflated_total_is_denied_with_field_level_drift(
        self, sandbox, agent, principal
    ):
        signed_cart = make_cart(items=[("SKU-PHONE-256", "Phone 256GB", 1, 5_499_00)])
        mandates = make_mandates(agent, principal, signed_cart, max_amount=100_000_00)

        inflated = make_cart(items=[("SKU-PHONE-256", "Phone 256GB", 1, 5_599_00)])
        request = build_signed_request(agent, mandates, inflated)

        env = sandbox.evaluate(request)

        assert env.decision is Decision.DENY
        assert "C002" in codes(env)

        g3 = next(g for g in env.gate_trace if g.gate.value == "G3")
        drift = g3.detail["drift"]
        assert drift["total"]["signed"] == 5_499_00
        assert drift["total"]["charged"] == 5_599_00
        assert drift["total"]["delta"] == 100_00
        assert "₹100.00" in env.explanation

    def test_shipping_inflation_is_denied(self, sandbox, agent, principal):
        signed_cart = make_cart(shipping=0)
        mandates = make_mandates(agent, principal, signed_cart, max_amount=100_000_00)
        tampered = make_cart(shipping=999_00)

        env = sandbox.evaluate(build_signed_request(agent, mandates, tampered))
        assert env.decision is Decision.DENY
        assert "C002" in codes(env)

    def test_quantity_change_is_flagged_as_substitution(
        self, sandbox, agent, principal
    ):
        signed_cart = make_cart(items=[("SKU-PHONE-256", "Phone", 1, 5_499_00)])
        mandates = make_mandates(agent, principal, signed_cart, max_amount=100_000_00)
        tampered = make_cart(items=[("SKU-PHONE-256", "Phone", 3, 5_499_00)])

        env = sandbox.evaluate(build_signed_request(agent, mandates, tampered))
        assert env.decision is Decision.DENY
        assert "C003" in codes(env)

    def test_sku_swap_is_denied(self, sandbox, agent, principal):
        signed_cart = make_cart(items=[("SKU-CASE", "Case", 1, 499_00)])
        mandates = make_mandates(agent, principal, signed_cart, max_amount=100_000_00)
        tampered = make_cart(items=[("SKU-TV-55", "TV", 1, 499_00)])

        env = sandbox.evaluate(build_signed_request(agent, mandates, tampered))
        assert env.decision is Decision.DENY
        assert "C003" in codes(env)

        g3 = next(g for g in env.gate_trace if g.gate.value == "G3")
        assert g3.detail["drift"]["skus_added"] == ["SKU-TV-55"]
        assert g3.detail["drift"]["skus_removed"] == ["SKU-CASE"]

    def test_body_tampering_breaks_the_signature_first(
        self, sandbox, agent, principal
    ):
        """Defence in depth: the body is covered by the signature, so a cart
        swapped without re-signing fails at G1 before G3 is consulted."""
        cart = make_cart()
        mandates = make_mandates(agent, principal, cart, max_amount=100_000_00)
        request = build_signed_request(agent, mandates, cart)

        request.body["cart"]["total"] = 1
        request.headers["content-digest"] = __import__(
            "kya.simulation", fromlist=["content_digest"]
        ).content_digest(request.body)

        env = sandbox.evaluate(request)
        assert env.decision is Decision.DENY
        assert "I002" in codes(env)


class TestA6ScopeEscalation:
    def test_charge_above_mandate_ceiling_is_denied(self, sandbox, agent, principal):
        cart = make_cart(items=[("SKU-TV-55", "TV", 1, 64_999_00)])
        mandates = make_mandates(agent, principal, cart, max_amount=10_000_00)
        request = build_signed_request(agent, mandates, cart)

        env = sandbox.evaluate(request)
        assert env.decision is Decision.DENY
        assert "C004" in codes(env)

        g3 = next(g for g in env.gate_trace if g.gate.value == "G3")
        assert g3.detail["violated"] == "max_amount"
        assert "₹10,000.00" in env.explanation

    def test_merchant_outside_mandate_is_denied(self, sandbox, agent, principal):
        cart = make_cart(merchant_id="merch_elsewhere")
        mandates = make_mandates(
            agent, principal, cart, allowed_merchants=["merch_sandbox_01"]
        )
        env = sandbox.evaluate(build_signed_request(agent, mandates, cart))

        assert env.decision is Decision.DENY
        assert "C004" in codes(env)

    def test_category_outside_mandate_is_denied(self, sandbox, agent, principal):
        cart = make_cart(category="alcohol")
        mandates = make_mandates(
            agent, principal, cart, allowed_categories=["electronics", "books"]
        )
        env = sandbox.evaluate(build_signed_request(agent, mandates, cart))

        assert env.decision is Decision.DENY
        assert "C004" in codes(env)
