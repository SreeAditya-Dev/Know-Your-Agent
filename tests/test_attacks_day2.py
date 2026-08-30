"""The Day-2 attack classes: the ones only visible across requests.

A7 refund flood · A10 Reserve Pay block drain · plus the velocity and spend
bounds that make the tier ladder mean something.

What separates these from Day 1 is that **every individual request here would
pass every other gate**. The signature verifies, the mandate chain is intact,
the cart binds to what was signed. Each request is, on its own terms, correct.
That is exactly why identity-only defence — the shipped state of the art —
does not see any of it: there is nothing wrong with any single request, only
with the sequence.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from kya.enums import Decision, RailType, Tier
from kya.simulation import (
    build_block_debit_request,
    build_refund_request,
    build_signed_request,
    make_cart,
    make_mandates,
    make_obligation,
)


def codes(envelope) -> set[str]:
    return set(envelope.reason_codes)


def purchase(sandbox, agent, principal, amount: int, tier: Tier = Tier.T3, **kwargs):
    """One clean purchase of ``amount`` paise through the whole pipeline.

    Everything is issued and signed at the *sandbox* clock, so a test that
    advances time does not fail on skew for an unrelated reason.
    """
    cart = make_cart(items=[("SKU-A", "Item", 1, amount)])
    mandates = make_mandates(
        agent,
        principal,
        cart,
        max_amount=10_000_000_00,
        issued_at=sandbox.clock(),
        **kwargs,
    )
    request = build_signed_request(agent, mandates, cart, created=sandbox.clock())
    return sandbox.evaluate(request, tier=tier)


class TestA7RefundFlood:
    """Unit 42's bot-farm shape: thousands of returns in an hour, liquidating
    a retailer's cash before a human notices."""

    def test_refunds_without_any_orders_trip_the_breaker_immediately(
        self, sandbox, agent, principal
    ):
        """The blind spot a bare ratio leaves open. With zero orders the
        refund-to-order ratio is undefined, so a threshold alone says nothing —
        and that is precisely where a bot farm with no purchase history sits."""
        cart = make_cart()
        mandates = make_mandates(agent, principal, cart)

        env = sandbox.evaluate(
            build_refund_request(agent, mandates, cart, amount=1_000_00)
        )

        assert env.decision is Decision.QUARANTINE
        assert "E003" in codes(env)
        trace = {g.gate.value: g for g in env.gate_trace}
        assert trace["G4"].detail["refund_breaker"]["rule"] == "refunds_exceed_orders"

    def test_a_normal_refund_after_real_orders_is_allowed(
        self, sandbox, agent, principal
    ):
        """The false-positive guard. One refund against ten orders is ordinary
        retail, and a breaker that stops it is a breaker nobody will deploy."""
        for _ in range(10):
            assert purchase(sandbox, agent, principal, 1_000_00).decision is Decision.ALLOW

        cart = make_cart(items=[("SKU-A", "Item", 1, 1_000_00)])
        mandates = make_mandates(agent, principal, cart)
        env = sandbox.evaluate(build_refund_request(agent, mandates, cart, 1_000_00))

        assert env.decision is Decision.ALLOW

    def test_the_breaker_trips_partway_through_a_flood(
        self, sandbox, agent, principal
    ):
        """Twenty legitimate orders, then a flood. The breaker must open while
        the flood is running, not after it drains the merchant."""
        for _ in range(20):
            purchase(sandbox, agent, principal, 500_00)

        decisions = []
        for _ in range(20):
            cart = make_cart(items=[("SKU-A", "Item", 1, 500_00)])
            mandates = make_mandates(agent, principal, cart)
            decisions.append(
                sandbox.evaluate(
                    build_refund_request(agent, mandates, cart, 500_00)
                ).decision
            )

        # 35% of 20 orders is 7 refunds; the 8th must be held.
        assert decisions[:7] == [Decision.ALLOW] * 7
        assert all(d is Decision.QUARANTINE for d in decisions[7:])

    def test_a_high_value_refund_trips_on_value_even_within_the_count_ratio(
        self, sandbox, agent, principal
    ):
        """Refunding one order in ten is fine. Refunding one order that is
        worth more than half the window's takings is not, and a count-only
        breaker would wave it through."""
        for _ in range(9):
            purchase(sandbox, agent, principal, 100_00)
        purchase(sandbox, agent, principal, 5_000_00)

        cart = make_cart(items=[("SKU-A", "Item", 1, 5_000_00)])
        mandates = make_mandates(agent, principal, cart)
        env = sandbox.evaluate(build_refund_request(agent, mandates, cart, 5_000_00))

        assert env.decision is Decision.QUARANTINE
        trace = {g.gate.value: g for g in env.gate_trace}
        assert trace["G4"].detail["refund_breaker"]["rule"] == "value_ratio"


class TestA10ReservePayBlockDrain:
    """SIMULATED SBMD. One consent blocks funds; the rail then permits repeated
    debits without fresh authentication, bounded only by amount, time and
    merchant. Nothing in the rail asks whether anything was owed."""

    @pytest.fixture
    def block(self, sandbox):
        return sandbox.blocks.create_block(
            principal_ref="user_alice",
            merchant_id="merch_sandbox_01",
            reserved=50_000_00,
        )

    def test_an_unbacked_debit_is_denied(self, sandbox, agent, principal, block):
        """A perfectly signed debit, inside every bound the rail enforces,
        denied for the one reason the rail cannot check."""
        cart = make_cart(items=[("SKU-A", "Item", 1, 5_000_00)])
        mandates = make_mandates(agent, principal, cart)

        env = sandbox.evaluate(
            build_block_debit_request(agent, mandates, cart, block.block_id, 5_000_00)
        )

        assert env.decision is Decision.DENY
        assert "E004" in codes(env)
        assert sandbox.blocks.get(block.block_id).debited == 0

    def test_a_backed_debit_is_allowed_and_books_against_the_block(
        self, sandbox, agent, principal, block
    ):
        cart = make_cart(items=[("SKU-A", "Item", 1, 5_000_00)])
        mandates = make_mandates(agent, principal, cart)
        obligation = sandbox.obligations.add(
            make_obligation(
                agent,
                principal,
                cart,
                rail_type=RailType.RESERVE_PAY_BLOCK,
                rail_ref=block.block_id,
            )
        )

        env = sandbox.evaluate(
            build_block_debit_request(agent, mandates, cart, block.block_id, 5_000_00)
        )

        assert env.decision is Decision.ALLOW
        assert sandbox.blocks.get(block.block_id).debited == 5_000_00
        booked = sandbox.blocks.debits_for(block.block_id)
        assert [d.obligation_id for d in booked] == [obligation.obligation_id]

    def test_the_drain_is_stopped_at_the_first_debit_not_the_last(
        self, sandbox, agent, principal, block
    ):
        """Ten debits of ₹4,000 against a ₹50,000 block. Every one is inside
        the block's bounds; the rail would settle all ten. Not one books."""
        for _ in range(10):
            cart = make_cart(items=[("SKU-A", "Item", 1, 4_000_00)])
            mandates = make_mandates(agent, principal, cart)
            env = sandbox.evaluate(
                build_block_debit_request(
                    agent, mandates, cart, block.block_id, 4_000_00
                )
            )
            assert env.decision is Decision.DENY
            assert "E004" in codes(env)

        assert sandbox.blocks.get(block.block_id).debited == 0

    def test_a_denied_debit_does_not_move_the_block(
        self, sandbox, agent, principal, block
    ):
        """Booking happens in commit, on ALLOW only. A guard that moved the
        ledger while denying would be worse than no guard."""
        cart = make_cart(items=[("SKU-A", "Item", 1, 90_000_00)])
        mandates = make_mandates(agent, principal, cart, max_amount=10_000_000_00)
        sandbox.obligations.add(
            make_obligation(
                agent,
                principal,
                cart,
                rail_type=RailType.RESERVE_PAY_BLOCK,
                rail_ref=block.block_id,
            )
        )

        env = sandbox.evaluate(
            build_block_debit_request(agent, mandates, cart, block.block_id, 90_000_00)
        )

        assert env.decision is Decision.DENY
        assert "E006" in codes(env)
        assert sandbox.blocks.get(block.block_id).debited == 0


class TestVelocity:
    def test_a_burst_beyond_the_tier_rate_is_quarantined(
        self, sandbox, agent, principal
    ):
        """T0 permits three an hour. The fourth is held for review, not
        denied — a new agent moving fast is suspicious, not proven hostile."""
        for _ in range(3):
            assert purchase(sandbox, agent, principal, 100_00, tier=Tier.T0).decision \
                is Decision.ALLOW

        env = purchase(sandbox, agent, principal, 100_00, tier=Tier.T0)
        assert env.decision is Decision.QUARANTINE
        assert "E001" in codes(env)

    def test_the_denial_tells_the_agent_when_to_retry(
        self, sandbox, agent, principal
    ):
        """A machine caller that is told only 'no' retries immediately and
        forever. Retry-after is what turns a denial into something an agent
        can act on."""
        for _ in range(3):
            purchase(sandbox, agent, principal, 100_00, tier=Tier.T0)

        env = purchase(sandbox, agent, principal, 100_00, tier=Tier.T0)
        trace = {g.gate.value: g for g in env.gate_trace}
        assert trace["G4"].detail["velocity"][0]["retry_after_seconds"] > 0

    def test_capacity_returns_as_the_window_rolls(self, sandbox, agent, principal):
        for _ in range(3):
            purchase(sandbox, agent, principal, 100_00, tier=Tier.T0)
        assert purchase(sandbox, agent, principal, 100_00, tier=Tier.T0).decision \
            is Decision.QUARANTINE

        sandbox.advance(timedelta(minutes=21))
        assert purchase(sandbox, agent, principal, 100_00, tier=Tier.T0).decision \
            is Decision.ALLOW


class TestSpendBounds:
    def test_one_action_above_the_tier_ceiling_steps_up(
        self, sandbox, agent, principal
    ):
        """The cold-start answer, stated as a decision: a T0 agent asking for
        more than ₹2,000 is not refused, it is asked to re-authenticate. The
        false positive costs friction, not the sale."""
        env = purchase(sandbox, agent, principal, 5_000_00, tier=Tier.T0)

        assert env.decision is Decision.STEP_UP
        assert "E005" in codes(env)

    def test_cumulative_spend_over_the_window_is_quarantined(
        self, sandbox, agent, principal
    ):
        """Distinct from E005 on purpose. Being above your station steps up;
        draining a rolling budget in small pieces is the flood shape, and gets
        held for a human."""
        for _ in range(2):
            assert purchase(sandbox, agent, principal, 900_00, tier=Tier.T0).decision \
                is Decision.ALLOW

        env = purchase(sandbox, agent, principal, 900_00, tier=Tier.T0)
        assert env.decision is Decision.QUARANTINE
        assert "E002" in codes(env)

    def test_a_denied_purchase_does_not_consume_the_buyers_budget(
        self, sandbox, agent, principal
    ):
        """Spend books in commit, on ALLOW only. Otherwise an attacker could
        exhaust a legitimate buyer's cap with requests that never pay.

        The ₹5,000 attempt is stepped up and books nothing. Both ₹900
        purchases then fit inside T0's ₹2,000 window — which they could not
        have done if the stepped-up attempt had been counted.
        """
        blocked = purchase(sandbox, agent, principal, 5_000_00, tier=Tier.T0)
        assert blocked.decision is Decision.STEP_UP

        for _ in range(2):
            assert purchase(sandbox, agent, principal, 900_00, tier=Tier.T0).decision                 is Decision.ALLOW

    def test_the_ladder_changes_the_answer_for_the_same_request(
        self, sandbox, agent, principal
    ):
        """The ladder has to be observable, not merely recorded. The identical
        ₹5,000 purchase is stepped up at T0 and allowed at T2."""
        cart = make_cart(items=[("SKU-A", "Item", 1, 5_000_00)])
        mandates = make_mandates(agent, principal, cart, max_amount=10_000_000_00)

        at_t0 = sandbox.evaluate(
            build_signed_request(agent, mandates, cart), tier=Tier.T0
        )
        at_t2 = sandbox.evaluate(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart),
            tier=Tier.T2,
        )

        assert at_t0.decision is Decision.STEP_UP and "E005" in codes(at_t0)
        assert at_t2.decision is Decision.ALLOW


class TestDelegatedTransactionCap:
    """``max_transactions`` sits in the intent mandate and constrains a
    *sequence*, so no gate that sees one request at a time can enforce it.
    Left to G3 it would be a bound the buyer believes they set and nothing
    applies."""

    def test_the_buyers_transaction_cap_is_enforced(self, sandbox, agent, principal):
        cart = make_cart(items=[("SKU-A", "Item", 1, 100_00)])
        mandates = make_mandates(
            agent, principal, cart, max_amount=10_000_00, max_transactions=2
        )

        decisions = [
            sandbox.evaluate(build_signed_request(agent, mandates, cart)).decision
            for _ in range(3)
        ]

        assert decisions[:2] == [Decision.ALLOW, Decision.ALLOW]
        assert decisions[2] is Decision.QUARANTINE

    def test_a_fresh_delegation_starts_a_fresh_count(self, sandbox, agent, principal):
        """The cap belongs to the delegation, not to the agent. A new intent
        from the buyer is a new authorisation and must not inherit the last
        one's exhaustion."""
        cart = make_cart(items=[("SKU-A", "Item", 1, 100_00)])
        first = make_mandates(
            agent, principal, cart, max_amount=10_000_00, max_transactions=1
        )
        sandbox.evaluate(build_signed_request(agent, first, cart))

        second = make_mandates(
            agent, principal, cart, max_amount=10_000_00, max_transactions=1
        )
        env = sandbox.evaluate(build_signed_request(agent, second, cart))

        assert env.decision is Decision.ALLOW


class TestIdempotencyUnderCounters:
    def test_a_replayed_decision_does_not_double_count(
        self, sandbox, agent, principal
    ):
        """Agents retry aggressively. If a cached decision re-consumed spend,
        a well-behaved retry would look like a flood — and the gateway would
        punish exactly the callers that implement retries correctly."""
        cart = make_cart(items=[("SKU-A", "Item", 1, 900_00)])
        mandates = make_mandates(agent, principal, cart, max_amount=10_000_00)
        request = build_signed_request(agent, mandates, cart)

        first = sandbox.evaluate(request, tier=Tier.T0)
        assert first.decision is Decision.ALLOW

        for _ in range(10):
            replay = sandbox.evaluate(request, tier=Tier.T0)
            assert replay.decision is Decision.ALLOW
            assert replay.idempotent_replay is True

        # One purchase booked, not eleven: ₹900 of a ₹2,000 T0 cap consumed.
        env = purchase(sandbox, agent, principal, 1_000_00, tier=Tier.T0)
        assert env.decision is Decision.ALLOW
