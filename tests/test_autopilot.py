"""AgentPay Autopilot recovery plans are safe under payment uncertainty."""

from __future__ import annotations

from datetime import timedelta

from kya.autopilot import (
    AutopilotRecoveryAgent,
    FailureClass,
    RecoveryAction,
    RecoveryPlanner,
    RecoveryPolicy,
)
from kya.reconcile import Reconciler
from kya.simulation import build_signed_request, make_cart, make_mandates


MUTATING_CALLS = {"create_order", "refund"}


def _agent(sandbox) -> AutopilotRecoveryAgent:
    return AutopilotRecoveryAgent(
        Reconciler(sandbox.ledger, sandbox.rail, clock=sandbox.clock)
    )


class TestAutopilotRecovery:
    def test_verified_capture_completes_without_a_retry(
        self, sandbox, gateway, rail, agent, principal
    ):
        rail.drop_responses = True
        cart = make_cart()
        created = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )
        rail.drop_responses = False
        rail.pay(next(iter(rail.orders)))
        rail.calls.clear()

        plan = _agent(sandbox).assess(created.obligation.obligation_id)

        assert plan.failure is FailureClass.PAYMENT_ALREADY_CAPTURED
        assert plan.action is RecoveryAction.COMPLETE
        assert plan.recovered
        assert sandbox.ledger.current(created.obligation.obligation_id).amount_due == 0
        assert not any(operation in MUTATING_CALLS for operation, _ in rail.calls)

    def test_existing_unpaid_order_is_observed_not_recreated(
        self, sandbox, gateway, rail, agent, principal
    ):
        rail.drop_responses = True
        cart = make_cart()
        created = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )
        rail.drop_responses = False
        rail.calls.clear()

        plan = _agent(sandbox).assess(created.obligation.obligation_id)

        assert plan.failure is FailureClass.PAYMENT_PENDING
        assert plan.action is RecoveryAction.OBSERVE_AGAIN
        assert plan.next_check_after_seconds == 60
        assert not any(operation in MUTATING_CALLS for operation, _ in rail.calls)

    def test_missing_order_requires_human_approval_not_an_unsafe_retry(
        self, sandbox, gateway, rail, agent, principal
    ):
        rail.unreachable = True
        cart = make_cart()
        created = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )
        rail.unreachable = False
        sandbox.advance(timedelta(seconds=61))
        rail.calls.clear()

        plan = _agent(sandbox).assess(created.obligation.obligation_id)

        assert plan.failure is FailureClass.ORDER_NOT_FOUND
        assert plan.action is RecoveryAction.HUMAN_REVIEW
        assert plan.requires_human_approval
        assert not any(operation in MUTATING_CALLS for operation, _ in rail.calls)

    def test_repeated_unavailability_escalates_after_policy_bound(
        self, sandbox, gateway, rail, agent, principal
    ):
        rail.drop_responses = True
        cart = make_cart()
        created = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )
        rail.drop_responses = False
        rail.unreachable = True
        planner = RecoveryPlanner(RecoveryPolicy(max_observation_attempts=1))
        recovery = AutopilotRecoveryAgent(
            Reconciler(sandbox.ledger, rail, clock=sandbox.clock), planner
        )

        plan = recovery.assess(
            created.obligation.obligation_id, observation_attempt=1
        )

        assert plan.failure is FailureClass.RAIL_UNAVAILABLE
        assert plan.action is RecoveryAction.HUMAN_REVIEW
        assert plan.requires_human_approval
