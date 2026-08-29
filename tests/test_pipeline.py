"""Pipeline semantics: idempotency, short-circuiting, adjudication, tiers."""

from __future__ import annotations

import uuid

import pytest

from kya.enums import Decision, GateVerdict, Tier
from kya.schemas import GateResult
from kya.simulation import build_signed_request, make_cart, make_mandates


class TestIdempotency:
    def test_identical_request_returns_the_cached_decision(
        self, sandbox, good_request
    ):
        first = sandbox.evaluate(good_request)
        second = sandbox.evaluate(good_request)

        assert first.decision is second.decision
        assert first.idempotent_replay is False
        assert second.idempotent_replay is True
        assert second.decision_id == first.decision_id

    def test_replay_does_not_re_run_gates(self, sandbox, good_request):
        """A retry must not consume a second nonce, or the gateway would
        convert its own idempotency into a replay denial."""
        sandbox.evaluate(good_request)
        nonces_after_first = len(sandbox.nonce_store)

        sandbox.evaluate(good_request)
        assert len(sandbox.nonce_store) == nonces_after_first

    def test_denial_is_also_cached(self, sandbox, agent, principal):
        cart = make_cart(items=[("SKU-TV-55", "TV", 1, 64_999_00)])
        mandates = make_mandates(agent, principal, cart, max_amount=10_000_00)
        request = build_signed_request(agent, mandates, cart)

        first = sandbox.evaluate(request)
        second = sandbox.evaluate(request)

        assert first.decision is Decision.DENY
        assert second.decision is Decision.DENY
        assert second.idempotent_replay is True

    def test_different_idempotency_key_is_a_new_evaluation(
        self, sandbox, agent, principal
    ):
        cart = make_cart()
        mandates = make_mandates(agent, principal, cart)

        first = sandbox.evaluate(build_signed_request(agent, mandates, cart))
        second = sandbox.evaluate(build_signed_request(agent, mandates, cart))

        assert first.decision is Decision.ALLOW
        assert second.decision is Decision.ALLOW
        assert second.idempotent_replay is False
        assert second.decision_id != first.decision_id


class TestShortCircuit:
    def test_downstream_gates_are_skipped_after_a_denial(
        self, sandbox, good_request
    ):
        good_request.signature = None
        good_request.signature_input_raw = None

        env = sandbox.evaluate(good_request)
        trace = {g.gate.value: g for g in env.gate_trace}

        assert trace["G1"].verdict is GateVerdict.FAIL
        assert trace["G2"].verdict is GateVerdict.SKIPPED
        assert trace["G3"].verdict is GateVerdict.SKIPPED
        assert trace["G2"].detail["reason"] == "short_circuit"

    def test_every_gate_appears_in_the_trace(self, sandbox, good_request):
        """The audit trail must account for all gates, including skipped ones."""
        env = sandbox.evaluate(good_request)
        assert [g.gate.value for g in env.gate_trace] == ["G0", "G1", "G2", "G3"]


class TestTierModulation:
    def test_new_agent_above_threshold_is_stepped_up_not_denied(
        self, sandbox, agent, principal
    ):
        """The cold-start answer: a bounded sale, not a lost one."""
        cart = make_cart(items=[("SKU-PHONE-256", "Phone", 1, 1_500_00)])
        mandates = make_mandates(agent, principal, cart, max_amount=100_000_00)
        request = build_signed_request(agent, mandates, cart)

        env = sandbox.evaluate(request, tier=Tier.T0)

        assert env.decision is Decision.STEP_UP
        assert "A002" in env.reason_codes
        assert env.tier is Tier.T0

    def test_same_amount_passes_for_a_trusted_agent(
        self, sandbox, agent, principal
    ):
        cart = make_cart(items=[("SKU-PHONE-256", "Phone", 1, 1_500_00)])
        mandates = make_mandates(agent, principal, cart, max_amount=100_000_00)
        request = build_signed_request(agent, mandates, cart)

        env = sandbox.evaluate(request, tier=Tier.T3)
        assert env.decision is Decision.ALLOW

    def test_tier_never_excuses_an_integrity_failure(
        self, sandbox, agent, principal
    ):
        """Earned trust buys larger limits, never permission to break binding."""
        signed = make_cart(items=[("SKU-CASE", "Case", 1, 499_00)])
        mandates = make_mandates(agent, principal, signed, max_amount=100_000_00)
        substituted = make_cart(items=[("SKU-TV-55", "TV", 1, 64_999_00)])

        env = sandbox.evaluate(
            build_signed_request(agent, mandates, substituted), tier=Tier.T3
        )
        assert env.decision is Decision.DENY


class TestAdjudication:
    def test_most_restrictive_reason_governs(self, sandbox, agent, principal):
        """Cart above both the tier threshold (step-up) and the mandate
        ceiling (deny) must deny."""
        cart = make_cart(items=[("SKU-TV-55", "TV", 1, 64_999_00)])
        mandates = make_mandates(agent, principal, cart, max_amount=10_000_00)

        env = sandbox.evaluate(build_signed_request(agent, mandates, cart), tier=Tier.T0)
        assert env.decision is Decision.DENY

    def test_explanation_is_generated_for_every_denial(
        self, sandbox, agent, principal
    ):
        cart = make_cart(merchant_id="merch_elsewhere")
        mandates = make_mandates(
            agent, principal, cart, allowed_merchants=["merch_sandbox_01"]
        )
        env = sandbox.evaluate(build_signed_request(agent, mandates, cart))

        assert env.explanation
        assert env.decision.value in env.explanation


class TestReasonCodeIntegrity:
    def test_unknown_reason_code_is_rejected(self):
        """An unrecognised code in an audit trail is a bug, not a display
        problem, so the schema refuses to carry one."""
        from kya.enums import Gate

        with pytest.raises(ValueError, match="unknown reason code"):
            GateResult(gate=Gate.G1, verdict=GateVerdict.FAIL, codes=["NOPE"])

    def test_every_registered_code_is_constructible(self):
        from kya.enums import Gate
        from kya.reasons import REGISTRY

        for code in REGISTRY:
            GateResult(gate=Gate.G1, verdict=GateVerdict.FAIL, codes=[code])
