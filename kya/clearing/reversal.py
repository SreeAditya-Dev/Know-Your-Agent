"""Settlement and reversal — where a clearing decision becomes money.

The step that makes the rest of the clearing layer more than bookkeeping. A
DISPUTED decision that does not move funds is an opinion; one that issues a
refund is a remedy.

Two properties are load-bearing.

**Reversal is a system action, not an agent action.** It does not pass through
the inline gates and is not subject to G4's refund-rate circuit breaker. That
is deliberate and the opposite of a loophole: the breaker exists to stop an
*agent* flooding refunds to drain a merchant, and routing the merchant's own
remedy through it would let exactly that attack also block the defence against
it. An agent that floods refunds would trip the breaker and thereby prevent the
merchant reversing the fraudulent orders.

**It is idempotent.** A reversal runs off an async decision that may be
re-evaluated, redelivered by a webhook, or retried by a scheduler. Reversing
twice refunds twice, which converts a remedy into a second loss — so the
obligation's own state is the guard, and an obligation already REVERSED is left
alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from kya.canonical import now_utc
from kya.enums import Finality, ObligationState, RailType
from kya.obligation.ledger import ObligationLedger
from kya.passport import PassportStore
from kya.rails.razorpay_client import RailError, RazorpayRail
from kya.reserve_pay import BlockLedger
from kya.schemas import ClearingDecision, ObligationReceipt, RailRef, SettlementInstruction


@dataclass(slots=True)
class SettlementResult:
    instruction: SettlementInstruction
    obligation: ObligationReceipt
    executed: bool
    detail: str
    #: True when the obligation was already in its terminal state and this call
    #: did nothing. Distinguished from a failure, because "nothing to do" and
    #: "could not do it" demand opposite responses from a retry loop.
    idempotent_noop: bool = False


class SettlementExecutor:
    """Turns FINAL and DISPUTED into ledger state, money, and reputation."""

    def __init__(
        self,
        ledger: ObligationLedger,
        rail: RazorpayRail | None = None,
        blocks: BlockLedger | None = None,
        passports: PassportStore | None = None,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.ledger = ledger
        self.rail = rail
        self.blocks = blocks
        self.passports = passports
        self._clock = clock

    def execute(
        self, obligation_id: str, decision: ClearingDecision
    ) -> SettlementResult:
        if decision.finality is Finality.FINAL:
            return self.settle(obligation_id, decision)
        if decision.finality is Finality.DISPUTED:
            return self.reverse(obligation_id, decision)
        raise ValueError(
            "a PROVISIONAL decision moves nothing; wait for the appeal window"
        )

    # --- the happy path ------------------------------------------------------

    def settle(
        self, obligation_id: str, decision: ClearingDecision
    ) -> SettlementResult:
        """Mark the obligation satisfied and credit the agent's record."""
        current = self._require(obligation_id)
        instruction = SettlementInstruction(
            clearing_hash=decision.decision_hash,
            principal_amount=current.promised.total,
            reputation_delta=1,
            rail=current.rail,
        )

        if current.state is ObligationState.SETTLED:
            return SettlementResult(
                instruction, current, False, "already settled", idempotent_noop=True
            )
        if current.state is not ObligationState.OPEN:
            return SettlementResult(
                instruction,
                current,
                False,
                f"cannot settle an obligation that is {current.state.value}",
            )

        settled = self.ledger.amend(
            obligation_id, state=ObligationState.SETTLED, now=self._clock()
        )
        if self.passports is not None:
            self.passports.record_cleared(
                current.agent_id, value=current.promised.total
            )

        instruction.executed = True
        return SettlementResult(
            instruction, settled, True, "obligation cleared FINAL and settled"
        )

    # --- the remedy ----------------------------------------------------------

    def reverse(
        self, obligation_id: str, decision: ClearingDecision
    ) -> SettlementResult:
        """Return the money and record the dispute against the agent."""
        current = self._require(obligation_id)
        refund_amount = decision.loss_estimate or current.promised.total
        instruction = SettlementInstruction(
            clearing_hash=decision.decision_hash,
            refund_amount=refund_amount,
            reputation_delta=-1,
            rail=current.rail,
        )

        if current.state is ObligationState.REVERSED:
            return SettlementResult(
                instruction, current, False, "already reversed", idempotent_noop=True
            )
        if current.state is ObligationState.SETTLED:
            # Settled obligations are reopened by a dispute, not silently
            # reversed underneath a decision that already cleared them.
            return SettlementResult(
                instruction,
                current,
                False,
                "obligation is SETTLED; raise a dispute rather than reversing",
            )

        executed, ref, detail = self._return_funds(current, refund_amount)
        instruction.executed = executed
        instruction.executed_ref = ref

        # The ledger records the reversal whether or not the rail cooperated.
        # An obligation that failed verification is disputed as a matter of
        # fact; whether the refund has landed yet is a separate question, and
        # conflating them would leave a failed refund looking like a clean one.
        reversed_receipt = self.ledger.amend(
            obligation_id, state=ObligationState.REVERSED, now=self._clock()
        )
        if self.passports is not None:
            self.passports.record_disputed(current.agent_id)

        return SettlementResult(instruction, reversed_receipt, executed, detail)

    def record_basis_drift(self, agent_id: str, roles) -> None:
        """Feed LAUNDER-BASIS detections into the agent's passport.

        Drift is not about this transaction — it is a claim about the integrity
        of the evidence itself, so it demotes the agent independently of
        whether this particular obligation cleared.
        """
        if self.passports is None:
            return
        for _ in roles:
            self.passports.record_basis_drift(agent_id)

    # --- rails ---------------------------------------------------------------

    def _return_funds(
        self, obligation: ObligationReceipt, amount: int
    ) -> tuple[bool, str | None, str]:
        if obligation.rail.type is RailType.RESERVE_PAY_BLOCK:
            return self._release_block(obligation, amount)
        return self._refund_order(obligation, amount)

    def _refund_order(
        self, obligation: ObligationReceipt, amount: int
    ) -> tuple[bool, str | None, str]:
        rail_id = self.ledger.rail_id_for(obligation.obligation_id)
        if self.rail is None or rail_id is None:
            return False, None, "no rail binding; reversal recorded but not executed"

        try:
            payments = self.rail.order_payments(rail_id)
        except RailError as exc:
            return False, None, f"could not read payments for reversal: {exc}"

        captured = next(
            (
                p
                for p in payments
                if p.get("status") == "captured" or p.get("captured") is True
            ),
            None,
        )
        if captured is None:
            # Nothing was ever collected, so there is nothing to give back.
            # The obligation is still disputed; the remedy is simply empty.
            return True, None, "no captured payment to refund; nothing was collected"

        refundable = int(captured.get("amount", 0)) - int(
            captured.get("amount_refunded", 0)
        )
        payable = min(amount, refundable)
        if payable <= 0:
            return True, None, "payment already fully refunded"

        try:
            refund = self.rail.refund(
                captured["id"],
                payable,
                notes={
                    "kya_obligation": obligation.self_hash,
                    "kya_reason": "clearing_disputed",
                },
            )
        except RailError as exc:
            return False, None, f"refund failed: {exc}"

        return True, refund["id"], f"refunded {payable} paise via {refund['id']}"

    def _release_block(
        self, obligation: ObligationReceipt, amount: int
    ) -> tuple[bool, str | None, str]:
        """SIMULATED rail. Revoking the block is the closest analogue to a
        refund: it stops any further debit against a reservation whose
        obligation did not hold up."""
        if self.blocks is None:
            return False, None, "no block ledger; reversal recorded but not executed"

        block = self.blocks.get(obligation.rail.ref)
        if block is None:
            return False, None, f"unknown block {obligation.rail.ref}"

        self.blocks.revoke(block.block_id)
        return (
            True,
            block.block_id,
            f"SIMULATED: block {block.block_id} revoked, "
            f"{block.available} paise released",
        )

    def _require(self, obligation_id: str) -> ObligationReceipt:
        current = self.ledger.current(obligation_id)
        if current is None:
            raise ValueError(f"unknown obligation {obligation_id!r}")
        return current
