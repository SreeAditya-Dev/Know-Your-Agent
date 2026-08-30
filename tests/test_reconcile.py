"""Graceful failure #1 — payment succeeded, response lost.

Track 01 asks for one failure handled gracefully. This is it, and it is worth
being precise about what "gracefully" has to mean, because the obvious
implementation is wrong in an expensive way.

The naive recovery is to retry the failed call. That is exactly how one
purchase becomes two: the order was created and paid for, and the only thing
that failed was our knowledge of it. So the reconciler's defining property is
negative — it never writes to the rail. It looks, it binds what it finds, and
it stops. Several tests below assert that by inspecting which rail calls were
made, because a property this important should not rest on reading the code.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from kya.enums import Decision, ObligationState
from kya.reconcile import (
    ALREADY_BOUND,
    BOUND_EXISTING_CAPTURE,
    LOOKUP_TOO_SOON,
    NO_PAYMENT_YET,
    ORDER_MISSING,
    ORDER_RECOVERED,
    PROPAGATION_GRACE_SECONDS,
    RAIL_UNREACHABLE,
    Reconciler,
)
from kya.simulation import build_signed_request, make_cart, make_mandates

MUTATING_CALLS = {"create_order", "refund"}


@pytest.fixture
def reconciler(sandbox):
    return Reconciler(sandbox.ledger, sandbox.rail, clock=sandbox.clock)


def place_order(gateway, agent, principal, amount: int = 5_499_00):
    cart = make_cart(items=[("SKU-PHONE-256", "Phone 256GB", 1, amount)])
    return gateway.create_order(
        build_signed_request(agent, make_mandates(agent, principal, cart), cart)
    )


class TestTheLostResponse:
    """The full scenario, in the order it happens in the world."""

    def test_end_to_end_the_capture_is_bound_and_nothing_is_charged_twice(
        self, sandbox, gateway, ledger, rail, reconciler, agent, principal
    ):
        # 1. The agent orders. The rail creates the order and then the response
        #    is lost on the way back to us.
        rail.drop_responses = True
        result = place_order(gateway, agent, principal)

        assert result.envelope.decision is Decision.ALLOW
        assert result.order is None and result.needs_reconciliation
        obligation_id = result.obligation.obligation_id

        # 2. The buyer pays anyway. The order exists on the rail; we do not
        #    know its id and have recorded no capture.
        order_id = next(iter(rail.orders))
        rail.pay(order_id)
        rail.drop_responses = False

        assert ledger.current(obligation_id).amount_due == result.obligation.promised.total

        # 3. The reconciler runs.
        rail.calls.clear()
        outcome = reconciler.reconcile(obligation_id)

        assert outcome.action == BOUND_EXISTING_CAPTURE
        assert outcome.duplicate_charges_prevented == 1
        assert outcome.order_id == order_id

        # 4. No second order, no second payment, nothing retried.
        assert not any(op in MUTATING_CALLS for op, _ in rail.calls)
        assert len(rail.orders) == 1
        assert len(rail.payments) == 1

        # 5. The obligation now knows the money arrived...
        current = ledger.current(obligation_id)
        assert current.amount_due == 0
        assert ledger.rail_id_for(obligation_id) == order_id

        # 6. ...and is still OPEN, because nothing has been delivered. Payment
        #    settles value transfer; clearing settles obligation state.
        assert current.state is ObligationState.OPEN

        assert ledger.verify().ok

    def test_the_order_is_found_by_our_own_reference_not_the_rails_id(
        self, gateway, ledger, rail, reconciler, agent, principal
    ):
        """The recovery hinges on this. After a lost response the rail's order
        id is precisely the thing we never learned, so the lookup has to start
        from an identifier we chose before the call went out."""
        rail.drop_responses = True
        result = place_order(gateway, agent, principal)
        rail.drop_responses = False

        assert ledger.rail_id_for(result.obligation.obligation_id) is None

        rail.calls.clear()
        reconciler.reconcile(result.obligation.obligation_id)

        assert ("order_by_receipt", result.obligation.rail.ref) in rail.calls

    def test_reconciling_twice_binds_once(
        self, gateway, ledger, rail, reconciler, agent, principal
    ):
        """The reconciler runs on a schedule. It must be safe to run it against
        the same obligation for the rest of the obligation's life."""
        rail.drop_responses = True
        result = place_order(gateway, agent, principal)
        rail.drop_responses = False
        rail.pay(next(iter(rail.orders)))

        first = reconciler.reconcile(result.obligation.obligation_id)
        second = reconciler.reconcile(result.obligation.obligation_id)
        third = reconciler.reconcile(result.obligation.obligation_id)

        assert first.action == BOUND_EXISTING_CAPTURE
        assert second.action == third.action == ALREADY_BOUND
        assert second.duplicate_charges_prevented == 0
        assert len(ledger.history(result.obligation.obligation_id)) == 2


class TestOtherOutcomes:
    def test_a_fresh_lookup_miss_is_not_called_a_missing_order(
        self, sandbox, gateway, rail, reconciler, agent, principal
    ):
        """Razorpay's order *list* endpoint is eventually consistent — measured
        at roughly 10-20 seconds against live test mode. Inside that window a
        miss says nothing, and "never created" is the one conclusion nobody
        should reach during a lost-response recovery."""
        rail.unreachable = True
        result = place_order(gateway, agent, principal)
        rail.unreachable = False

        outcome = reconciler.reconcile(result.obligation.obligation_id)

        assert outcome.action == LOOKUP_TOO_SOON
        assert "Retry after" in outcome.detail

    def test_an_order_that_was_never_created_is_reported_not_recreated(
        self, sandbox, gateway, rail, reconciler, agent, principal
    ):
        """Past the propagation window the miss is real. The rail never got the
        request, so nothing was charged — and placing the order now would be
        deciding on the buyer's behalf. The reconciler reports and stops."""
        rail.unreachable = True
        result = place_order(gateway, agent, principal)
        rail.unreachable = False

        sandbox.advance(timedelta(seconds=PROPAGATION_GRACE_SECONDS + 5))
        rail.calls.clear()
        outcome = reconciler.reconcile(result.obligation.obligation_id)

        assert outcome.action == ORDER_MISSING
        assert not any(op in MUTATING_CALLS for op, _ in rail.calls)
        assert rail.orders == {}

    def test_an_unpaid_order_is_recovered_without_being_marked_paid(
        self, gateway, ledger, rail, reconciler, agent, principal
    ):
        rail.drop_responses = True
        result = place_order(gateway, agent, principal)
        rail.drop_responses = False

        outcome = reconciler.reconcile(result.obligation.obligation_id)

        assert outcome.action == ORDER_RECOVERED
        assert outcome.duplicate_charges_prevented == 0
        assert ledger.current(result.obligation.obligation_id).amount_due > 0

    def test_a_healthy_unpaid_order_is_left_alone(
        self, gateway, ledger, reconciler, agent, principal
    ):
        result = place_order(gateway, agent, principal)

        outcome = reconciler.reconcile(result.obligation.obligation_id)

        assert outcome.action == NO_PAYMENT_YET
        assert len(ledger.history(result.obligation.obligation_id)) == 1

    def test_a_rail_outage_leaves_the_obligation_open(
        self, gateway, ledger, rail, reconciler, agent, principal
    ):
        """An outage is not evidence of anything. Fail soft, report, try later
        — the alternative is a reconciler that resolves obligations on the
        strength of not being able to check them."""
        result = place_order(gateway, agent, principal)
        rail.unreachable = True

        outcome = reconciler.reconcile(result.obligation.obligation_id)

        assert outcome.action == RAIL_UNREACHABLE
        assert ledger.current(result.obligation.obligation_id).state is ObligationState.OPEN

    def test_an_unknown_obligation_is_reported_not_raised(self, reconciler):
        outcome = reconciler.reconcile("obl_does_not_exist")
        assert "unknown" in outcome.detail


class TestSweep:
    def test_the_scheduled_sweep_covers_every_open_obligation(
        self, gateway, rail, reconciler, agent, principal
    ):
        """What actually runs in production: nobody knows which obligation lost
        its response, so the sweep checks all of them."""
        rail.drop_responses = True
        lost = [place_order(gateway, agent, principal, 1_000_00) for _ in range(3)]
        rail.drop_responses = False
        healthy = [place_order(gateway, agent, principal, 1_000_00) for _ in range(2)]

        for order_id in list(rail.orders)[:3]:
            rail.pay(order_id)

        rail.calls.clear()
        report = reconciler.reconcile_open()

        assert len(report.outcomes) == len(lost) + len(healthy)
        assert report.duplicate_charges_prevented == 3
        assert not any(op in MUTATING_CALLS for op, _ in rail.calls)
        assert "duplicate charges prevented: 3" in report.summary()

    def test_the_sweep_ignores_obligations_on_other_rails(
        self, sandbox, ledger, reconciler, agent, principal
    ):
        """A block-backed obligation is not reconciled against Razorpay orders.
        Reaching for the wrong rail would report an order missing that was
        never supposed to exist."""
        from kya.enums import RailType
        from kya.simulation import make_obligation

        ledger.append(
            make_obligation(
                agent,
                principal,
                make_cart(),
                rail_type=RailType.RESERVE_PAY_BLOCK,
                rail_ref="blk_1",
            )
        )

        assert reconciler.reconcile_open().outcomes == []
