"""Anchoring the obligation hash into Razorpay's own order record.

The cheapest high-value idea in the build, and the one a payments engineer
notices.

A tamper-evident log inside our own database proves very little to a dispute
reviewer, because we control the database. The chain in ``ledger.py`` makes
local tampering *visible to us*; it does nothing for someone who suspects us.

So at order creation the version-1 hash is written into the Razorpay order's
``notes``. Razorpay's order record is immutable, timestamped, and outside our
control. A reviewer holding nothing but dashboard access can now:

1. read ``notes.kya_obligation`` from the order;
2. take the obligation receipt we hand them;
3. recompute its hash themselves, with this module or without it;
4. confirm the two match — and that the order's own timestamp proves the
   receipt existed *before* the payment was captured.

That last point is what the anchor really buys. Anyone can produce a receipt
after a dispute starts. Only someone who wrote it before capture can have its
hash sitting inside a payment record created at capture time.

**Verify against version 1, always.** The anchor pins what was promised, and
what was promised does not change. Later versions record where the obligation
stands — settled, reversed, partially refunded — and their hashes necessarily
differ. Matching a current version against the anchor would fail precisely when
the obligation has had an interesting life, which is when anyone checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from kya.schemas import ObligationReceipt

#: The keys written into ``order.notes``. Razorpay notes are string→string, so
#: the version travels as a string too.
ANCHOR_KEY = "kya_obligation"
ANCHOR_VERSION_KEY = "kya_version"

#: Razorpay's own field for a merchant-side order reference. We put our
#: obligation reference here, which makes the binding bidirectional: the order
#: names the obligation, and the obligation's ``rail.ref`` names the order.
RECEIPT_FIELD = "receipt"


@dataclass(slots=True)
class AnchorCheck:
    """The result a dispute reviewer actually wants."""

    ok: bool
    reason: str
    anchored_hash: str | None = None
    recomputed_hash: str | None = None
    version: int | None = None

    def summary(self) -> str:
        if self.ok:
            return (
                f"anchor verified: order note matches receipt v{self.version} "
                f"({self.recomputed_hash[:16] if self.recomputed_hash else '?'})"
            )
        return f"anchor NOT verified: {self.reason}"


def anchor_notes(receipt: ObligationReceipt) -> dict[str, str]:
    """The ``notes`` payload written at order creation.

    Merged into, not substituted for, any merchant notes — an anchor that
    silently discarded a merchant's own order metadata would be an anchor the
    merchant turns off.
    """
    return {
        ANCHOR_KEY: receipt.self_hash,
        ANCHOR_VERSION_KEY: str(receipt.version),
    }


def verify_anchor(
    receipt: ObligationReceipt, order: Mapping[str, Any]
) -> AnchorCheck:
    """Independently re-derive the receipt's hash and match it to the order.

    Deliberately recomputes rather than reading ``receipt.self_hash``. Trusting
    the stored hash would verify that we copied a string correctly; recomputing
    verifies that the receipt's *contents* are the ones that were anchored,
    which is the only version of this check worth running.
    """
    notes = order.get("notes") or {}
    if not isinstance(notes, Mapping):
        return AnchorCheck(False, "order notes are not a mapping")

    anchored = notes.get(ANCHOR_KEY)
    if not anchored:
        return AnchorCheck(False, f"order carries no {ANCHOR_KEY} note")

    if receipt.version != 1:
        return AnchorCheck(
            False,
            f"anchor pins version 1; receipt supplied is version {receipt.version}",
            anchored_hash=anchored,
            version=receipt.version,
        )

    recomputed = receipt.compute_hash()
    if recomputed != anchored:
        return AnchorCheck(
            False,
            "recomputed receipt hash does not match the anchored note",
            anchored_hash=anchored,
            recomputed_hash=recomputed,
            version=receipt.version,
        )

    # Cross-check the other half of the binding where the rail preserved it.
    order_receipt_ref = order.get(RECEIPT_FIELD)
    if order_receipt_ref and order_receipt_ref != receipt.rail.ref:
        return AnchorCheck(
            False,
            f"order receipt field {order_receipt_ref!r} does not name "
            f"this obligation's rail ref {receipt.rail.ref!r}",
            anchored_hash=anchored,
            recomputed_hash=recomputed,
            version=receipt.version,
        )

    return AnchorCheck(
        True,
        "receipt hash matches the anchored order note",
        anchored_hash=anchored,
        recomputed_hash=recomputed,
        version=receipt.version,
    )
