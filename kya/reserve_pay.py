"""Reserve Pay block ledger — **SIMULATED**.

This models NPCI's Single Block Multi Debit semantics against a local ledger.
It is not a live Reserve Pay integration, has no connection to UPI, and every
object it produces carries ``simulated=True``. Nothing in this module should
ever be presented as evidence that the real rail was exercised.

**Why simulate it at all.** SBMD is the India-native version of the hole this
whole project is about. One consent blocks funds, and the merchant may then
debit repeatedly without fresh authentication for each debit. The boundary the
rail enforces is amount, time and merchant — three facts about *authority*.
Nothing in the rail binds an individual debit to an obligation actually
incurred. An agent (or anything that has captured one) holding a valid block
can therefore drain it in a sequence of individually well-formed debits, none
of which the rail has grounds to refuse.

The guard here supplies the missing conjunct::

    ∃ obligation o :  o.rail.ref == block_id
                    ∧ o.state == OPEN
                    ∧ debit.amount ≤ o.amount_due
                    ∧ Σ(debits on block) ≤ block.reserved

An unmatched debit is denied. That converts a spending envelope into a
per-debit obligation check, which is the control the rail does not have today.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Protocol

from kya.canonical import now_utc
from kya.enums import ObligationState, RailType
from kya.schemas import BlockDebit, ObligationReceipt, ReservePayBlock


class ObligationSource(Protocol):
    """Whatever can answer 'what is still owed against this block?'.

    Day 3's hash-chained obligation ledger implements this. Keeping the gate
    behind a protocol means the block guard is testable now, and does not have
    to be rewritten when the real ledger lands behind it.
    """

    def open_for_block(self, block_ref: str) -> list[ObligationReceipt]:
        """Open obligations bound to this block, in mint order."""
        ...


class InMemoryObligations:
    """Minimal obligation index. Superseded by the Day-3 ledger."""

    def __init__(self) -> None:
        self._by_id: dict[str, ObligationReceipt] = {}

    def add(self, receipt: ObligationReceipt) -> ObligationReceipt:
        self._by_id[receipt.obligation_id] = receipt
        return receipt

    def get(self, obligation_id: str) -> ObligationReceipt | None:
        return self._by_id.get(obligation_id)

    def open_for_block(self, block_ref: str) -> list[ObligationReceipt]:
        return [
            o
            for o in self._by_id.values()
            if o.rail.type is RailType.RESERVE_PAY_BLOCK
            and o.rail.ref == block_ref
            and o.state is ObligationState.OPEN
        ]

    def __len__(self) -> int:
        return len(self._by_id)


@dataclass(slots=True)
class DebitCheck:
    """Verdict on one proposed debit, in the gate's vocabulary."""

    ok: bool
    code: str | None = None
    detail: dict[str, object] = field(default_factory=dict)
    matched_obligation_id: str | None = None


class BlockLedger:
    """SIMULATED SBMD block ledger with the unbacked-debit guard."""

    #: Read by the API layer and the dashboard so a simulated rail can never be
    #: mistaken for a live one, whatever a screenshot happens to show.
    SIMULATED = True

    def __init__(
        self,
        obligations: ObligationSource | None = None,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        # ``is None``, never ``or``: an empty index defines ``__len__`` and is
        # therefore falsy, so ``or`` would silently swap an injected store for
        # a fresh one and every guard would read against the wrong ledger.
        self.obligations: ObligationSource = (
            InMemoryObligations() if obligations is None else obligations
        )
        self._clock = clock
        self._blocks: dict[str, ReservePayBlock] = {}
        self._debits: list[BlockDebit] = []

    # --- block lifecycle -----------------------------------------------------

    def create_block(
        self,
        principal_ref: str,
        merchant_id: str,
        reserved: int,
        ttl: timedelta = timedelta(days=7),
        block_id: str | None = None,
    ) -> ReservePayBlock:
        now = self._clock()
        block = ReservePayBlock(
            block_id=block_id or f"blk_{uuid.uuid4().hex[:12]}",
            principal_ref=principal_ref,
            merchant_id=merchant_id,
            reserved=reserved,
            created_at=now,
            expires_at=now + ttl,
        )
        self._blocks[block.block_id] = block
        return block

    def get(self, block_id: str) -> ReservePayBlock | None:
        return self._blocks.get(block_id)

    def revoke(self, block_id: str) -> None:
        block = self._blocks.get(block_id)
        if block is not None:
            block.revoked = True

    def debits_for(self, block_id: str) -> list[BlockDebit]:
        return [d for d in self._debits if d.block_ref == block_id]

    # --- the guard -----------------------------------------------------------

    def check_debit(
        self,
        block_id: str,
        amount: int,
        now: datetime | None = None,
    ) -> DebitCheck:
        """Is this debit backed by something actually owed?

        Order of checks is deliberate. The obligation match is tested *before*
        the reserve arithmetic, because "nothing was owed" is a different and
        more serious finding than "the block ran out", and whichever fires
        first is the one a dispute reviewer reads at the top of the trail.
        """
        at = now or self._clock()
        block = self._blocks.get(block_id)

        if block is None:
            return DebitCheck(False, "E004", {"reason": "unknown_block", "block_id": block_id})
        if block.revoked:
            return DebitCheck(False, "E004", {"reason": "block_revoked", "block_id": block_id})
        if at > block.expires_at:
            return DebitCheck(
                False,
                "E004",
                {
                    "reason": "block_expired",
                    "block_id": block_id,
                    "expired_at": block.expires_at.isoformat(),
                },
            )

        candidates = self.obligations.open_for_block(block_id)
        backing = next((o for o in candidates if o.amount_due >= amount), None)
        if backing is None:
            return DebitCheck(
                False,
                "E004",
                {
                    "reason": "no_open_obligation" if not candidates else "amount_exceeds_due",
                    "block_id": block_id,
                    "amount": amount,
                    "open_obligations": len(candidates),
                    "max_amount_due": max((o.amount_due for o in candidates), default=0),
                },
            )

        if amount > block.available:
            return DebitCheck(
                False,
                "E006",
                {
                    "block_id": block_id,
                    "amount": amount,
                    "reserved": block.reserved,
                    "already_debited": block.debited,
                    "available": block.available,
                },
                matched_obligation_id=backing.obligation_id,
            )

        return DebitCheck(
            True,
            detail={
                "block_id": block_id,
                "amount": amount,
                "available_after": block.available - amount,
            },
            matched_obligation_id=backing.obligation_id,
        )

    def apply_debit(
        self,
        block_id: str,
        amount: int,
        obligation_id: str | None = None,
        now: datetime | None = None,
    ) -> BlockDebit:
        """Commit a debit that ``check_debit`` has already cleared.

        Checking and applying are separate calls because a debit must only be
        booked once the whole pipeline has returned ALLOW. Folding them together
        would let a request denied by a later gate still move the ledger.
        """
        block = self._blocks[block_id]
        block.debited += amount
        debit = BlockDebit(
            debit_id=f"dbt_{uuid.uuid4().hex[:12]}",
            block_ref=block_id,
            obligation_id=obligation_id,
            amount=amount,
            requested_at=now or self._clock(),
        )
        self._debits.append(debit)
        return debit
