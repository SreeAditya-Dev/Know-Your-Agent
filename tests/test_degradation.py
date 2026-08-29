"""Degradation policy — the GF2 graceful-failure demonstration.

Governing rule from docs/02: **fail closed on evidence of wrongdoing, fail soft
on absence of evidence.**

The tests below pin the distinction that makes the rule meaningful. A directory
that answers and does not list a key is evidence of impersonation, and denies.
A directory that cannot be reached is an availability failure, and steps up.
Collapsing the two either hands impersonators a free pass during an outage or
zeroes out agent revenue whenever a third party's DNS wobbles.
"""

from __future__ import annotations

import base64
from datetime import timedelta

from kya.canonical import now_utc
from kya.enums import Decision, GateVerdict
from kya.nonce import InMemoryNonceStore
from kya.simulation import build_signed_request, make_cart, make_mandates


def _fresh_mandates(sandbox, agent, principal, cart):
    """Mandates issued at the sandbox's current time."""
    return make_mandates(agent, principal, cart, issued_at=sandbox.clock())


def _fresh_request(sandbox, agent, mandates, cart):
    """A request signed at the sandbox's current time.

    Needed whenever the clock is advanced: a request signed at the old time
    would fail the skew check for an unrelated reason and mask what is
    actually under test.
    """
    return build_signed_request(agent, mandates, cart, created=sandbox.clock())


class TestDirectoryOutage:
    def test_cold_outage_steps_up_rather_than_denying(
        self, sandbox, agent, good_request
    ):
        """No cached key and the directory is down: identity is unproven, but
        unproven is not disproven."""
        sandbox.fetcher.set_unreachable(agent.origin)

        env = sandbox.evaluate(good_request)

        assert env.decision is Decision.STEP_UP
        assert "I004" in env.reason_codes
        assert env.decision is not Decision.DENY

    def test_warm_outage_still_verifies_from_stale_cache(
        self, sandbox, agent, principal
    ):
        """Revenue is preserved through an outage when we already hold the key.

        The clock is pushed past the directory TTL so the gateway genuinely
        tries to refresh and falls back, rather than silently serving a cache
        entry that was still fresh.
        """
        sandbox.set_time(now_utc())
        cart = make_cart()
        mandates = _fresh_mandates(sandbox, agent, principal, cart)
        sandbox.evaluate(_fresh_request(sandbox, agent, mandates, cart))  # warm cache

        sandbox.fetcher.set_unreachable(agent.origin)
        sandbox.advance(timedelta(seconds=400))  # past the 300s TTL

        env = sandbox.evaluate(_fresh_request(sandbox, agent, mandates, cart))

        assert env.decision is Decision.ALLOW
        g1 = next(g for g in env.gate_trace if g.gate.value == "G1")
        assert g1.detail["served_from"] == "stale_cache"

    def test_bad_signature_still_denies_during_an_outage(
        self, sandbox, agent, principal
    ):
        """Degradation relaxes the treatment of *missing* evidence, never of
        *contradicted* evidence."""
        sandbox.set_time(now_utc())
        cart = make_cart()
        mandates = _fresh_mandates(sandbox, agent, principal, cart)
        sandbox.evaluate(_fresh_request(sandbox, agent, mandates, cart))  # warm cache

        sandbox.fetcher.set_unreachable(agent.origin)
        sandbox.advance(timedelta(seconds=400))

        forged = _fresh_request(sandbox, agent, mandates, cart)
        label, _, wire = forged.signature.partition("=")
        raw = bytearray(base64.b64decode(wire.strip(":")))
        raw[0] ^= 0xFF
        forged.signature = f"{label}=:{base64.b64encode(bytes(raw)).decode()}:"

        env = sandbox.evaluate(forged)

        assert env.decision is Decision.DENY
        assert "I002" in env.reason_codes

    def test_unknown_key_denies_even_though_it_is_a_lookup_failure(
        self, sandbox, agent, good_request
    ):
        """The directory answered. That answer is evidence."""
        sandbox.fetcher.withdraw(agent.origin, agent.keypair.key_id)
        sandbox.directory.invalidate(agent.origin)

        env = sandbox.evaluate(good_request)

        assert env.decision is Decision.DENY
        assert "I003" in env.reason_codes


class TestNonceStoreOutage:
    def test_unavailable_nonce_store_steps_up(self, sandbox, good_request):
        """Replay cannot be ruled out, so the request is not waved through —
        but neither is a legitimate buyer refused."""
        sandbox.nonce_store.available = False

        env = sandbox.evaluate(good_request)

        assert env.decision is Decision.STEP_UP
        assert "R004" in env.reason_codes
        g0 = next(g for g in env.gate_trace if g.gate.value == "G0")
        assert g0.verdict is GateVerdict.DEGRADED

    def test_downstream_gates_still_run_when_g0_degrades(
        self, sandbox, good_request
    ):
        """A degraded gate is not a denial, so evaluation continues and the
        audit trail stays complete."""
        sandbox.nonce_store.available = False

        env = sandbox.evaluate(good_request)
        trace = {g.gate.value: g for g in env.gate_trace}

        assert trace["G1"].verdict is GateVerdict.PASS
        assert trace["G3"].verdict is GateVerdict.PASS


class TestNonceExpiry:
    def test_nonces_expire_so_the_store_does_not_grow_without_bound(self):
        from datetime import datetime, timedelta, timezone

        clock = {"now": datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)}
        store = InMemoryNonceStore(ttl_seconds=600, clock=lambda: clock["now"])

        assert store.check_and_record("a") is True
        assert store.check_and_record("a") is False  # replay caught

        clock["now"] += timedelta(seconds=1200)
        assert store.check_and_record("b") is True
        assert len(store) == 1  # "a" expired out
