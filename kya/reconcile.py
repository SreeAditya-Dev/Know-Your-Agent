"""The reconciler — graceful failure #1, the agentic double charge.

The failure it exists for is mundane and extremely common: the gateway creates
an order, the buyer pays, and the response never comes back. A human in that
position refreshes the page and looks. An agent retries. Retrying a payment you
already made is how one purchase becomes two, and it is the single most likely
way an autonomous buyer costs a merchant real money without anyone acting in
bad faith.

**The reconciler never writes to the rail.** It fetches, it compares, and it
binds what it finds to the obligation that was minted before the call went out.
It does not create orders, does not initiate payments, and does not retry the
failed request — which is the whole point, and is asserted directly in the
tests by checking that no mutating rail call was made.

The recovery works because of an ordering decision made in the gateway: the
obligation is minted, sealed and stored *before* the rail is touched, and it
commits to the order reference the rail was asked to use. So even when the
response is lost and we never learn the rail's order id, there is a local
record naming an identifier the rail can be searched by. Without that ordering
the situation is unrecoverable: money in flight, and nothing to look it up with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from kya.canonical import now_utc
from kya.enums import ObligationState, RailType
from kya.obligation.ledger import ObligationLedger
from kya.rails.razorpay_client import RailError, RazorpayRail

#: Outcomes. Stable strings — the metrics report counts them by name.
BOUND_EXISTING_CAPTURE = "bound_existing_capture"
ALREADY_BOUND = "already_bound"
ORDER_RECOVERED = "order_recovered_no_payment"
NO_PAYMENT_YET = "no_payment_yet"
ORDER_MISSING = "order_never_created"
RAIL_UNREACHABLE = "rail_unreachable"
NOT_APPLICABLE = "not_applicable"


@dataclass(slots=True)
class ReconcileOutcome:
    obligation_id: str
    action: str
    order_id: str | None = None
    payment_id: str | None = None
    amount: int = 0
    #: Incremented only when a capture was found for an obligation that still
    #: believed money was outstanding — the case where a naive retry would
    #: have charged a second time.
    duplicate_charges_prevented: int = 0
    detail: str = ""

    @property
    def recovered(self) -> bool:
        return self.action in (BOUND_EXISTING_CAPTURE, ORDER_RECOVERED)


@dataclass
class ReconcileReport:
    outcomes: list[ReconcileOutcome] = field(default_factory=list)

    @property
    def duplicate_charges_prevented(self) -> int:
        return sum(o.duplicate_charges_prevented for o in self.outcomes)

    def by_action(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.action] = counts.get(outcome.action, 0) + 1
        return counts

    def summary(self) -> str:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(self.by_action().items()))
        return (
            f"{len(self.outcomes)} obligation(s) reconciled [{counts}]; "
            f"duplicate charges prevented: {self.duplicate_charges_prevented}"
        )


class Reconciler:
    """Read-only against the rail. Writes only to the obligation ledger."""

    def __init__(
        self,
        ledger: ObligationLedger,
        rail: RazorpayRail,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.ledger = ledger
        self.rail = rail
        self._clock = clock

    def reconcile(self, obligation_id: str) -> ReconcileOutcome:
        receipt = self.ledger.current(obligation_id)
        if receipt is None:
            return ReconcileOutcome(
                obligation_id, NOT_APPLICABLE, detail="unknown obligation"
            )
        if receipt.rail.type is not RailType.RAZORPAY_ORDER:
            return ReconcileOutcome(
                obligation_id,
                NOT_APPLICABLE,
                detail=f"rail {receipt.rail.type.value} is not reconciled here",
            )
        if receipt.state is not ObligationState.OPEN:
            return ReconcileOutcome(
                obligation_id,
                ALREADY_BOUND,
                detail=f"obligation is {receipt.state.value}",
            )

        try:
            order = self._resolve_order(obligation_id, receipt.rail.ref)
        except RailError as exc:
            # An outage is not evidence of anything. Report it and leave the
            # obligation open; the next pass will settle it either way.
            return ReconcileOutcome(obligation_id, RAIL_UNREACHABLE, detail=str(exc))

        if order is None:
            # The rail never got the request. Nothing was charged, so there is
            # nothing to reconcile — and creating the order now is the agent's
            # call to make, not ours.
            return ReconcileOutcome(
                obligation_id,
                ORDER_MISSING,
                detail=f"no order carries reference {receipt.rail.ref}",
            )

        order_id = order["id"]
        newly_bound = self.ledger.rail_id_for(obligation_id) is None
        if newly_bound:
            self.ledger.bind_rail(obligation_id, order_id, now=self._clock())

        try:
            payments = self.rail.order_payments(order_id)
        except RailError as exc:
            return ReconcileOutcome(
                obligation_id, RAIL_UNREACHABLE, order_id=order_id, detail=str(exc)
            )

        captured = _captured(payments)
        if captured is None:
            return ReconcileOutcome(
                obligation_id,
                ORDER_RECOVERED if newly_bound else NO_PAYMENT_YET,
                order_id=order_id,
                detail="order exists; no captured payment against it",
            )

        if receipt.amount_due == 0:
            return ReconcileOutcome(
                obligation_id,
                ALREADY_BOUND,
                order_id=order_id,
                payment_id=captured["id"],
                detail="capture was already recorded",
            )

        # The capture is real and we had not recorded it. Bind it — and do not
        # retry the payment. Nothing more is owed, so nothing more is collected.
        #
        # State stays OPEN on purpose. The buyer has paid; the merchant has not
        # yet delivered. Payment settles value transfer, clearing settles
        # obligation state, and conflating them here would mark an undelivered
        # order as satisfied.
        self.ledger.amend(obligation_id, amount_due=0, now=self._clock())

        return ReconcileOutcome(
            obligation_id,
            BOUND_EXISTING_CAPTURE,
            order_id=order_id,
            payment_id=captured["id"],
            amount=int(captured.get("amount", 0)),
            duplicate_charges_prevented=1,
            detail="existing capture bound to the obligation; no retry issued",
        )

    def reconcile_open(self) -> ReconcileReport:
        """Sweep every open obligation. The scheduled form of the above."""
        report = ReconcileReport()
        for receipt in self.ledger.open_obligations():
            if receipt.rail.type is not RailType.RAZORPAY_ORDER:
                continue
            report.outcomes.append(self.reconcile(receipt.obligation_id))
        return report

    # --- event-driven binding ------------------------------------------------

    def bind_capture(
        self, order_id: str, payment_id: str, amount: int = 0
    ) -> ReconcileOutcome:
        """Bind a capture we were *told* about rather than went looking for.

        Same effect as ``reconcile``, reached from a webhook instead of a poll.
        Deliberately one implementation of the binding rule: a webhook path
        that recorded captures slightly differently from the polling path is
        two sources of truth about whether a merchant has been paid.
        """
        receipt = self._obligation_for_order(order_id)
        if receipt is None:
            return ReconcileOutcome(
                "", NOT_APPLICABLE, order_id=order_id,
                detail="no obligation is bound to this order",
            )

        if self.ledger.rail_id_for(receipt.obligation_id) is None:
            self.ledger.bind_rail(receipt.obligation_id, order_id, now=self._clock())

        if receipt.amount_due == 0:
            return ReconcileOutcome(
                receipt.obligation_id, ALREADY_BOUND, order_id=order_id,
                payment_id=payment_id, detail="capture was already recorded",
            )

        # OPEN still, for the same reason the polling path leaves it open:
        # the buyer has paid, the merchant has not yet delivered.
        self.ledger.amend(receipt.obligation_id, amount_due=0, now=self._clock())
        return ReconcileOutcome(
            receipt.obligation_id,
            BOUND_EXISTING_CAPTURE,
            order_id=order_id,
            payment_id=payment_id,
            amount=amount,
            duplicate_charges_prevented=1,
            detail="capture bound from webhook",
        )

    def _obligation_for_order(self, order_id: str):
        """Find the obligation an order belongs to.

        Two routes, because the first fails in exactly the case that matters.
        A webhook can arrive for an order whose id we never learned — the lost
        response again — so when the local binding is missing we ask the rail
        for the order and match on the reference we chose ourselves.
        """
        receipt = self.ledger.by_rail_id(order_id)
        if receipt is not None:
            return receipt

        try:
            order = self.rail.fetch_order(order_id)
        except RailError:
            return None
        rail_ref = order.get("receipt")
        if not rail_ref:
            return None
        return self.ledger.by_rail_ref(RailType.RAZORPAY_ORDER, rail_ref)

    def _resolve_order(
        self, obligation_id: str, rail_ref: str
    ) -> dict[str, Any] | None:
        """Find the order, preferring the binding we already hold.

        Falling back to a lookup by our own reference is the recovery path:
        after a lost response the rail's id is exactly what we do not have.
        """
        rail_id = self.ledger.rail_id_for(obligation_id)
        if rail_id is not None:
            return self.rail.fetch_order(rail_id)
        return self.rail.order_by_receipt(rail_ref)


def _captured(payments: list[dict[str, Any]]) -> dict[str, Any] | None:
    for payment in payments:
        if payment.get("status") == "captured" or payment.get("captured") is True:
            return payment
    return None


def install_webhook_handlers(receiver, reconciler: Reconciler) -> None:
    """Wire capture and refund events into the obligation ledger.

    Registered against ``payment.captured`` rather than ``order.paid`` because
    capture is the event that means money actually moved; an order can be
    marked paid on an authorisation that is later voided.
    """

    def on_capture(event) -> None:
        if event.order_id and event.payment_id:
            reconciler.bind_capture(
                event.order_id, event.payment_id, event.amount or 0
            )

    receiver.on("payment.captured", on_capture)
