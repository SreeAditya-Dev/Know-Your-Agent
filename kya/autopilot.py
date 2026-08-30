"""AgentPay-style recovery planning for uncertain payment outcomes.

KYA's reconciler already performs the one recovery that can be proved safe: it
binds an existing captured payment to the obligation that was minted before
the rail call.  This module turns those low-level outcomes into a bounded,
auditable recovery plan suitable for an AI buyer or an operator dashboard.

It deliberately does not create another order or retry a payment. A missing
result from Razorpay's eventually-consistent order list is not proof that an
earlier request did not succeed, so a blind retry would defeat the exact
double-charge protection this component exists to provide.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kya.reconcile import (
    ALREADY_BOUND,
    BOUND_EXISTING_CAPTURE,
    LOOKUP_TOO_SOON,
    NO_PAYMENT_YET,
    ORDER_MISSING,
    ORDER_RECOVERED,
    RAIL_UNREACHABLE,
    ReconcileOutcome,
    Reconciler,
)


class FailureClass(str, Enum):
    """Stable failure taxonomy for recovery metrics and audit records."""

    PAYMENT_ALREADY_CAPTURED = "payment_already_captured"
    ALREADY_RESOLVED = "already_resolved"
    PAYMENT_PENDING = "payment_pending"
    ORDER_LOOKUP_PENDING = "order_lookup_pending"
    ORDER_NOT_FOUND = "order_not_found"
    RAIL_UNAVAILABLE = "rail_unavailable"
    NOT_RECOVERABLE = "not_recoverable"


class RecoveryAction(str, Enum):
    """Actions a caller may present or schedule after a diagnosis."""

    COMPLETE = "complete"
    OBSERVE_AGAIN = "observe_again"
    HUMAN_REVIEW = "human_review"


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    """Merchant-controlled bounds for autonomous recovery observation."""

    max_observation_attempts: int = 2
    pending_recheck_seconds: int = 60
    rail_outage_recheck_seconds: int = 30


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    """A deterministic diagnosis, proposed next action, and audit explanation."""

    obligation_id: str
    failure: FailureClass
    action: RecoveryAction
    reconcile_action: str
    amount: int
    observation_attempt: int
    next_check_after_seconds: int | None
    requires_human_approval: bool
    explanation: str

    @property
    def recovered(self) -> bool:
        return self.action is RecoveryAction.COMPLETE


class RecoveryPlanner:
    """Map reconciler facts to safe, merchant-bounded recovery actions."""

    def __init__(self, policy: RecoveryPolicy | None = None) -> None:
        self.policy = policy or RecoveryPolicy()

    def plan(
        self,
        outcome: ReconcileOutcome,
        *,
        amount: int,
        observation_attempt: int = 0,
    ) -> RecoveryPlan:
        """Produce a plan without issuing a rail mutation.

        ``observation_attempt`` is supplied by the scheduler or caller so the
        plan remains serializable and does not hide mutable retry state inside
        a process-local agent instance.
        """
        if outcome.action == BOUND_EXISTING_CAPTURE:
            return self._plan(
                outcome,
                amount,
                observation_attempt,
                FailureClass.PAYMENT_ALREADY_CAPTURED,
                RecoveryAction.COMPLETE,
                None,
                False,
                "A captured payment was verified and bound to the existing "
                "obligation. The original payment is complete; no retry was issued.",
            )

        if outcome.action == ALREADY_BOUND:
            return self._plan(
                outcome,
                amount,
                observation_attempt,
                FailureClass.ALREADY_RESOLVED,
                RecoveryAction.COMPLETE,
                None,
                False,
                "The obligation is already bound to its known payment state. "
                "No further payment action is needed.",
            )

        if outcome.action in (ORDER_RECOVERED, NO_PAYMENT_YET):
            return self._observe_or_escalate(
                outcome,
                amount,
                observation_attempt,
                FailureClass.PAYMENT_PENDING,
                self.policy.pending_recheck_seconds,
                "The order exists but no capture is confirmed. Keep the existing "
                "order and check it again; creating another order is unsafe.",
            )

        if outcome.action == LOOKUP_TOO_SOON:
            return self._observe_or_escalate(
                outcome,
                amount,
                observation_attempt,
                FailureClass.ORDER_LOOKUP_PENDING,
                self.policy.pending_recheck_seconds,
                "The rail lookup is inside its propagation window. Wait and "
                "reconcile again instead of retrying the original action.",
            )

        if outcome.action == RAIL_UNREACHABLE:
            return self._observe_or_escalate(
                outcome,
                amount,
                observation_attempt,
                FailureClass.RAIL_UNAVAILABLE,
                self.policy.rail_outage_recheck_seconds,
                "Razorpay could not be queried. This is unavailable evidence, not "
                "proof of a failed payment, so the plan is to re-check later.",
            )

        if outcome.action == ORDER_MISSING:
            return self._plan(
                outcome,
                amount,
                observation_attempt,
                FailureClass.ORDER_NOT_FOUND,
                RecoveryAction.HUMAN_REVIEW,
                None,
                True,
                "No order was found after the propagation window. The rail cannot "
                "prove absence strongly enough for an autonomous re-create, so an "
                "operator must approve any new payment attempt.",
            )

        return self._plan(
            outcome,
            amount,
            observation_attempt,
            FailureClass.NOT_RECOVERABLE,
            RecoveryAction.HUMAN_REVIEW,
            None,
            True,
            "The outcome cannot be recovered automatically. Preserve the audit "
            "record and send it to an operator.",
        )

    def _observe_or_escalate(
        self,
        outcome: ReconcileOutcome,
        amount: int,
        observation_attempt: int,
        failure: FailureClass,
        retry_after: int,
        explanation: str,
    ) -> RecoveryPlan:
        if observation_attempt >= self.policy.max_observation_attempts:
            return self._plan(
                outcome,
                amount,
                observation_attempt,
                failure,
                RecoveryAction.HUMAN_REVIEW,
                None,
                True,
                "Automatic observation is exhausted. " + explanation,
            )
        return self._plan(
            outcome,
            amount,
            observation_attempt,
            failure,
            RecoveryAction.OBSERVE_AGAIN,
            retry_after,
            False,
            explanation,
        )

    @staticmethod
    def _plan(
        outcome: ReconcileOutcome,
        amount: int,
        observation_attempt: int,
        failure: FailureClass,
        action: RecoveryAction,
        next_check_after_seconds: int | None,
        requires_human_approval: bool,
        explanation: str,
    ) -> RecoveryPlan:
        return RecoveryPlan(
            obligation_id=outcome.obligation_id,
            failure=failure,
            action=action,
            reconcile_action=outcome.action,
            amount=amount,
            observation_attempt=observation_attempt,
            next_check_after_seconds=next_check_after_seconds,
            requires_human_approval=requires_human_approval,
            explanation=explanation,
        )


class AutopilotRecoveryAgent:
    """Diagnose real rail state through ``Reconciler`` and produce a plan."""

    def __init__(
        self,
        reconciler: Reconciler,
        planner: RecoveryPlanner | None = None,
    ) -> None:
        self.reconciler = reconciler
        self.planner = planner or RecoveryPlanner()

    def assess(
        self, obligation_id: str, *, observation_attempt: int = 0
    ) -> RecoveryPlan:
        outcome = self.reconciler.reconcile(obligation_id)
        receipt = self.reconciler.ledger.current(obligation_id)
        amount = receipt.promised.total if receipt is not None else 0
        return self.planner.plan(
            outcome,
            amount=amount,
            observation_attempt=observation_attempt,
        )
