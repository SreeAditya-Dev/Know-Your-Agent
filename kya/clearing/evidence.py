"""Evidence envelopes and the admissibility arithmetic over them.

`kya/evidence.py` holds the lattice — the partial order itself. This module
holds what the lattice is *used for*: turning a bag of submitted evidence into
the classes a verifier is entitled to claim.

Two rules from RAILS, and they pull in opposite directions, which is why they
have to be applied to the right thing:

* **A chain takes the meet.** An item's admissibility is its weakest link. A
  courier receipt (`REC`) forwarded to us by the agent (`SELF`) is `SELF`
  evidence — passing through an interested party destroys the class, and this
  is the composition rule most systems get wrong.
* **Independent support takes the join.** Two separate items backing the same
  claim leave that claim as strong as the better of them.

The distinction that does the most work here is **who fetched it**. A Razorpay
payment object we pulled from Razorpay ourselves is `REC`: a receipt from a
system with no stake in the outcome. The byte-identical object handed to us by
the agent is `SELF`, because the agent could have written it. Same content,
different provenance, different admissibility — and only the second one is
something an attacker can manufacture.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from kya.canonical import now_utc
from kya.evidence import EvidenceClass, leq, meet_all
from kya.schemas import EvidenceEnvelope, EvidenceItem

#: Deterministic tiebreak when two items backing a claim are *incomparable* —
#: the WIT/REC pair. Neither dominates, so there is no principled winner; what
#: matters is that the choice is stable, so the same evidence always produces
#: the same clearing decision. It is a tiebreak, not a ranking.
_TIEBREAK = {
    EvidenceClass.PROOF: 5,
    EvidenceClass.ATT: 4,
    EvidenceClass.REC: 3,
    EvidenceClass.WIT: 2,
    EvidenceClass.SIGN: 1,
    EvidenceClass.SELF: 0,
}


def effective_class(item: EvidenceItem) -> EvidenceClass:
    """What this item is actually worth, after its provenance chain.

    The declared class is folded into the meet rather than trusted on its own,
    so an item cannot claim `REC` while admitting a `SELF` hop in its own
    provenance.
    """
    return meet_all([item.declared_class, *item.provenance])


@dataclass(slots=True)
class ClaimSupport:
    """Everything submitted in support of one claim."""

    claim: str
    items: list[EvidenceItem]

    @property
    def best(self) -> EvidenceItem | None:
        """The single strongest item. What a verifier should cite.

        A verifier relying on one sufficient item cites that item; it must not
        cite several and take their meet, which would make *more* evidence
        produce a *weaker* basis.
        """
        if not self.items:
            return None
        return max(
            self.items,
            key=lambda i: (
                _TIEBREAK[effective_class(i)],
                # Stable across runs regardless of submission order.
                i.item_id,
            ),
        )

    @property
    def basis(self) -> EvidenceClass:
        best = self.best
        return effective_class(best) if best is not None else EvidenceClass.SELF

    def __bool__(self) -> bool:
        return bool(self.items)


class EvidenceIndex:
    """Submitted evidence, grouped by claim, with classes resolved once."""

    def __init__(self, envelope: EvidenceEnvelope) -> None:
        self.envelope = envelope
        self._by_claim: dict[str, list[EvidenceItem]] = {}
        for item in envelope.items:
            self._by_claim.setdefault(item.claim, []).append(item)

    def support(self, claim: str) -> ClaimSupport:
        return ClaimSupport(claim=claim, items=list(self._by_claim.get(claim, [])))

    def claims(self) -> list[str]:
        return sorted(self._by_claim)

    def item(self, item_id: str) -> EvidenceItem | None:
        for items in self._by_claim.values():
            for candidate in items:
                if candidate.item_id == item_id:
                    return candidate
        return None

    def basis_of(self, item_ids: Iterable[str]) -> EvidenceClass:
        """The meet over cited items — a verifier's own basis.

        Citing nothing yields `SELF`: a verifier that read no evidence is
        offering an opinion, and an opinion is self-reported by definition.
        """
        classes = [
            effective_class(item)
            for item in (self.item(i) for i in item_ids)
            if item is not None
        ]
        return meet_all(classes) if classes else EvidenceClass.SELF

    def __len__(self) -> int:
        return len(self.envelope.items)


def basis_drift(index: EvidenceIndex, declared: EvidenceClass, cited: list[str]) -> bool:
    """Did a verifier claim better evidence than it actually cited?

    RAILS names this failure LAUNDER-BASIS and treats the declaration as
    trusted. Trusting it is the right default — a verifier is part of the
    system, not a caller — but a declaration nobody can check is not a
    guarantee, and the cost of checking is one meet. Drift feeds the agent's
    passport, where enough of it floors the tier outright.
    """
    return not leq(declared, index.basis_of(cited))


# --- envelope construction ---------------------------------------------------


def item(
    item_id: str,
    claim: str,
    value: Any,
    declared_class: EvidenceClass,
    source: str,
    provenance: Iterable[EvidenceClass] = (),
    observed_at: datetime | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        item_id=item_id,
        claim=claim,
        value=value,
        declared_class=declared_class,
        provenance=list(provenance),
        source=source,
        observed_at=observed_at or now_utc(),
    )


def envelope(
    obligation_hash: str,
    items: Iterable[EvidenceItem],
    submitted_at: datetime | None = None,
) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        obligation_hash=obligation_hash,
        submitted_at=submitted_at or now_utc(),
        items=list(items),
    )


def from_agent(
    item_id: str,
    claim: str,
    value: Any,
    source: str = "agent",
    observed_at: datetime | None = None,
) -> EvidenceItem:
    """Evidence the agent handed us.

    Fixed at `SELF`, whatever it contains. An interested party's report of a
    fact is not a receipt for it, and this constructor exists so that calling
    sites cannot accidentally grade agent-supplied data as anything better.
    """
    return item(
        item_id,
        claim,
        value,
        EvidenceClass.SELF,
        source,
        observed_at=observed_at,
    )


def from_rail(
    item_id: str,
    claim: str,
    value: Any,
    source: str = "razorpay",
    observed_at: datetime | None = None,
) -> EvidenceItem:
    """Evidence we pulled from the payment rail ourselves — `REC`.

    The class comes from *us having fetched it*, not from what it says. The
    same object relayed by the agent goes through ``from_agent``.
    """
    return item(
        item_id,
        claim,
        value,
        EvidenceClass.REC,
        source,
        observed_at=observed_at,
    )


def from_witness(
    item_id: str,
    claim: str,
    value: Any,
    source: str,
    relayed_by_agent: bool = False,
    observed_at: datetime | None = None,
) -> EvidenceItem:
    """A third-party attestation — a courier confirming handover.

    ``relayed_by_agent`` adds the agent as a provenance hop, which collapses
    the item to `SELF` under the meet rule. That is the correct answer and the
    reason the parameter exists: a witness statement is only worth its class if
    it reached us without passing through someone with a stake in it.
    """
    provenance = [EvidenceClass.SELF] if relayed_by_agent else []
    return item(
        item_id,
        claim,
        value,
        EvidenceClass.WIT,
        source,
        provenance=provenance,
        observed_at=observed_at,
    )


# --- rail-sourced evidence ---------------------------------------------------


def collect_rail_evidence(
    rail,
    obligation,
    rail_id: str | None,
    now: datetime | None = None,
) -> list[EvidenceItem]:
    """Fetch what the payment rail says, as `REC`-class items.

    Collected here, once, before any verifier runs — rather than inside the
    receipt verifier — for two reasons. Every verifier then reasons over the
    same evidence set, and a verifier's ``cited_items`` always name items the
    mesh can find, which is what makes the basis-drift check meaningful. A
    verifier that fetched its own evidence would be citing things nobody else
    could see, and an unverifiable citation is the problem drift detection
    exists to catch.

    Returns an empty list on any rail failure. Not being able to ask is not
    evidence of anything, and a rail outage must not manufacture a violation.
    """
    from kya.obligation.receipt import CLAIM_AMOUNT_CHARGED
    from kya.rails.razorpay_client import RailError

    if rail is None or rail_id is None:
        return []

    try:
        payments = rail.order_payments(rail_id)
    except RailError:
        return []

    captured = [
        p
        for p in payments
        if p.get("status") == "captured" or p.get("captured") is True
    ]
    if not captured:
        return []

    collected = sum(int(p.get("amount", 0)) for p in captured)
    refunded = sum(int(p.get("amount_refunded", 0)) for p in captured)

    return [
        from_rail(
            f"rec_{rail_id}_amount",
            CLAIM_AMOUNT_CHARGED,
            collected - refunded,
            source=f"razorpay:{rail_id}",
            observed_at=now,
        )
    ]
