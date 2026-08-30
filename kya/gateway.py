"""The gateway — what sits between an AI buyer and the merchant's money.

This is the body of `POST /v1/agent/orders` with the HTTP peeled off. Day 6's
FastAPI layer calls it; the eval harness calls it; the tests call it. Keeping
the orchestration out of the web layer means the sequencing below is testable
without a server, and the sequencing is the part that matters.

**The ordering is the design.** For a purchase the gateway:

1. runs the inline pipeline;
2. on ALLOW, mints and seals the Obligation Receipt — *before* touching the
   rail;
3. creates the Razorpay order with the receipt hash anchored in ``notes``;
4. binds the rail's order id back to the obligation.

Step 2 must precede step 3, and not only for the audit story. If the order were
created first and the obligation minted after, a response lost between 3 and 4
would leave a live order that no local record points at — money in flight with
nothing to reconcile from. Minting first inverts the failure: a lost response
leaves an obligation that is open, unbound, and *findable*, because it already
commits to the order reference the rail was asked to use. That is what makes
the reconciler possible at all, and it is why the receipt carries our own
reference rather than the rail's id.

**Two independent defences against the double charge**, because agents retry
without judgement and the two failure shapes are different:

* A retry with the *same* idempotency key never re-runs the gates — the
  pipeline returns the cached decision — and never re-enters this orchestration,
  because the gateway returns the cached result for that decision.
* A retry with a *fresh* idempotency key does re-run the gates, and is caught
  by the mandate chain hash: the agent cannot forge a second cart mandate
  without the principal's key, so the same promise resolves to the obligation
  that already exists rather than minting a second one.

**Payment does not satisfy an obligation.** A capture sets ``amount_due`` to
zero — nothing more to collect — and leaves the obligation OPEN. What was
promised is still outstanding until the clearing layer says otherwise. Treating
capture as settlement would collapse the exact distinction the project exists
to make.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from kya.canonical import now_utc
from kya.enums import Decision, ObligationState, RailType
from kya.gates.context import GateContext
from kya.gates.pipeline import Pipeline
from kya.obligation.anchor import AnchorCheck, anchor_notes, verify_anchor
from kya.obligation.ledger import ObligationLedger
from kya.obligation.receipt import ReceiptMinter
from kya.rails.razorpay_client import RailError, RazorpayRail, order_reference
from kya.schemas import (
    AgentRequest,
    DecisionEnvelope,
    DeliveryWindow,
    ObligationReceipt,
    RailRef,
)


@dataclass(slots=True)
class GatewayResult:
    """Everything one guarded money action produced."""

    envelope: DecisionEnvelope
    obligation: ObligationReceipt | None = None
    order: dict[str, Any] | None = None
    refund: dict[str, Any] | None = None
    anchor: AnchorCheck | None = None
    #: Set when the decision was allowed and the obligation minted, but the
    #: rail call did not return. The obligation is open and reconcilable.
    rail_error: str | None = None
    #: True when this is a cached answer to a repeated request rather than a
    #: fresh evaluation.
    replayed: bool = False

    @property
    def allowed(self) -> bool:
        return self.envelope.allowed

    @property
    def needs_reconciliation(self) -> bool:
        return self.rail_error is not None and self.obligation is not None


class Gateway:
    """Orchestrates decision → obligation → rail, in that order."""

    def __init__(
        self,
        pipeline: Pipeline,
        ledger: ObligationLedger,
        rail: RazorpayRail,
        minter: ReceiptMinter,
        context_factory: Callable[[AgentRequest], GateContext],
        clock: Callable[[], datetime] = now_utc,
        delivery_days: int = 5,
        return_window_days: int = 7,
        cancellation_terms: str = "Cancellable until dispatch.",
    ) -> None:
        self.pipeline = pipeline
        self.ledger = ledger
        self.rail = rail
        self.minter = minter
        self.context_factory = context_factory
        self._clock = clock
        self.delivery_days = delivery_days
        self.return_window_days = return_window_days
        self.cancellation_terms = cancellation_terms
        self._results: dict[str, GatewayResult] = {}

    # --- purchases -----------------------------------------------------------

    def create_order(
        self,
        request: AgentRequest,
        extra_notes: Mapping[str, str] | None = None,
    ) -> GatewayResult:
        ctx = self.context_factory(request)
        envelope = self.pipeline.evaluate(ctx)

        cached = self._results.get(envelope.decision_id)
        if cached is not None:
            replay = GatewayResult(
                envelope=envelope,
                obligation=cached.obligation,
                order=cached.order,
                anchor=cached.anchor,
                rail_error=cached.rail_error,
                replayed=True,
            )
            return replay

        if envelope.decision is not Decision.ALLOW or request.cart is None:
            return self._remember(GatewayResult(envelope=envelope))

        # A retry that changed its idempotency key still presents the same
        # signed cart mandate. Same promise, same obligation.
        existing = self.ledger.open_for_mandate_chain(
            request.mandates.chain_hash() if request.mandates else ""
        )
        if existing is not None:
            envelope.obligation_id = existing.obligation_id
            rail_id = self.ledger.rail_id_for(existing.obligation_id)
            order = self._safe_fetch_order(rail_id)
            return self._remember(
                GatewayResult(
                    envelope=envelope,
                    obligation=self.ledger.original(existing.obligation_id),
                    order=order,
                    replayed=True,
                )
            )

        sealed = self._mint(ctx, request, now=ctx.now)
        envelope.obligation_id = sealed.obligation_id

        notes = dict(extra_notes or {})
        notes.update(anchor_notes(sealed))

        try:
            order = self.rail.create_order(
                amount=sealed.promised.total,
                receipt=sealed.rail.ref,
                notes=notes,
                currency=sealed.promised.currency,
            )
        except RailError as exc:
            # The obligation stands. It is open, carries the reference the rail
            # was asked to record, and the reconciler can find it from that.
            return self._remember(
                GatewayResult(
                    envelope=envelope, obligation=sealed, rail_error=str(exc)
                )
            )

        self.ledger.bind_rail(sealed.obligation_id, order["id"], now=ctx.now)

        # Verify our own anchor immediately rather than assuming it round
        # tripped. Discovering at dispute time that the note never landed is
        # discovering it far too late.
        anchor = verify_anchor(sealed, order)

        return self._remember(
            GatewayResult(
                envelope=envelope, obligation=sealed, order=order, anchor=anchor
            )
        )

    # --- refunds -------------------------------------------------------------

    def submit_refund(
        self, request: AgentRequest, payment_id: str, amount: int
    ) -> GatewayResult:
        """A guarded, agent-initiated refund.

        G4's circuit breaker has already had its say by the time the rail is
        touched. Reversals the *control plane* issues against a DISPUTED
        obligation do not come through here — they are a system action, and
        routing them past an agent-facing breaker would let a flood of agent
        refunds block the merchant's own remedy.
        """
        ctx = self.context_factory(request)
        envelope = self.pipeline.evaluate(ctx)

        cached = self._results.get(envelope.decision_id)
        if cached is not None:
            return GatewayResult(
                envelope=envelope, refund=cached.refund, replayed=True
            )

        if envelope.decision is not Decision.ALLOW:
            return self._remember(GatewayResult(envelope=envelope))

        obligation = None
        if request.mandates is not None:
            obligation = self.ledger.open_for_mandate_chain(
                request.mandates.chain_hash()
            )

        try:
            refund = self.rail.refund(
                payment_id,
                amount,
                notes={"kya_obligation": obligation.self_hash} if obligation else {},
            )
        except RailError as exc:
            return self._remember(
                GatewayResult(envelope=envelope, rail_error=str(exc))
            )

        if obligation is not None:
            envelope.obligation_id = obligation.obligation_id
            if amount >= obligation.promised.total:
                self.ledger.amend(
                    obligation.obligation_id,
                    state=ObligationState.REVERSED,
                    now=ctx.now,
                )

        return self._remember(
            GatewayResult(envelope=envelope, obligation=obligation, refund=refund)
        )

    # --- internals -----------------------------------------------------------

    def _mint(
        self, ctx: GateContext, request: AgentRequest, now: datetime
    ) -> ObligationReceipt:
        assert request.cart is not None
        obligation_id = f"obl_{uuid.uuid4().hex[:12]}"
        principal_ref = (
            request.mandates.intent.principal_ref if request.mandates else "unknown"
        )
        # Which key signed, recorded so the receipt survives key rotation: an
        # agent that rotates keys next week must still be attributable to the
        # promise it made today.
        key_id = (
            ctx.parsed_signature.params.key_id
            if ctx.parsed_signature is not None
            else ""
        )

        receipt = self.minter.mint(
            obligation_id=obligation_id,
            agent_id=request.agent_id,
            agent_key_id=key_id,
            principal_ref=principal_ref,
            cart=request.cart,
            mandate_chain_hash=(
                request.mandates.chain_hash() if request.mandates else ""
            ),
            rail=RailRef(
                type=RailType.RAZORPAY_ORDER, ref=order_reference(obligation_id)
            ),
            # The tier ladder reaches the clearing layer here: an established
            # agent's deliveries clear on a weaker basis than a stranger's.
            admissibility_floor=ctx.tier_policy.evidence_floor,
            delivery_window=DeliveryWindow(
                **{
                    "from": now + timedelta(days=1),
                    "to": now + timedelta(days=self.delivery_days),
                }
            ),
            return_window_days=self.return_window_days,
            cancellation_terms=self.cancellation_terms,
            now=now,
        )
        return self.ledger.append(receipt)

    def _safe_fetch_order(self, rail_id: str | None) -> dict[str, Any] | None:
        if rail_id is None:
            return None
        try:
            return self.rail.fetch_order(rail_id)
        except RailError:
            # The obligation is what matters here; a rail read that fails does
            # not change the answer we owe the caller.
            return None

    def _remember(self, result: GatewayResult) -> GatewayResult:
        self._results[result.envelope.decision_id] = result
        return result
