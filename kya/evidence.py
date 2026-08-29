"""Evidence admissibility lattice.

Implements the partial order from RAILS (arXiv 2606.08790):

        SELF  ⪯  SIGN  ⪯  {WIT, REC}  ⪯  ATT  ⪯  PROOF

Drawn as a Hasse diagram:

            PROOF
              |
             ATT
            /   \\
          WIT   REC
            \\   /
            SIGN
              |
            SELF

WIT and REC are deliberately **incomparable**: a witness attests to having
*observed* an event, a receipt records that an external system *processed* a
transaction. Neither dominates the other, so this is a genuine partial order
and not a ranking.

This module is the load-bearing element behind the project's central claim:
the semantic (LLM) verifier declares SELF/SIGN-class basis, so an obligation
whose floor is REC can never be cleared by a model's opinion alone. That is a
property of ``meets_floor`` and ``join``, not a policy we promise to follow.
"""

from __future__ import annotations

from enum import Enum
from functools import reduce
from typing import Iterable


class EvidenceClass(str, Enum):
    """Admissibility class of a piece of evidence."""

    SELF = "SELF"  # unverified self-report by the acting party
    SIGN = "SIGN"  # signed by the acting party — non-repudiation, not truth
    WIT = "WIT"  # third-party witness signature
    REC = "REC"  # receipt from a non-interested external system
    ATT = "ATT"  # trusted-execution-environment attestation
    PROOF = "PROOF"  # cryptographic proof


# Upward closure: the set of classes each class is ⪯ to (reflexive).
# This table *is* the partial order; everything else is derived from it.
_UP: dict[EvidenceClass, frozenset[EvidenceClass]] = {
    EvidenceClass.SELF: frozenset(
        {
            EvidenceClass.SELF,
            EvidenceClass.SIGN,
            EvidenceClass.WIT,
            EvidenceClass.REC,
            EvidenceClass.ATT,
            EvidenceClass.PROOF,
        }
    ),
    EvidenceClass.SIGN: frozenset(
        {
            EvidenceClass.SIGN,
            EvidenceClass.WIT,
            EvidenceClass.REC,
            EvidenceClass.ATT,
            EvidenceClass.PROOF,
        }
    ),
    EvidenceClass.WIT: frozenset(
        {EvidenceClass.WIT, EvidenceClass.ATT, EvidenceClass.PROOF}
    ),
    EvidenceClass.REC: frozenset(
        {EvidenceClass.REC, EvidenceClass.ATT, EvidenceClass.PROOF}
    ),
    EvidenceClass.ATT: frozenset({EvidenceClass.ATT, EvidenceClass.PROOF}),
    EvidenceClass.PROOF: frozenset({EvidenceClass.PROOF}),
}

# Downward closure, derived from _UP so the two cannot drift apart.
_DOWN: dict[EvidenceClass, frozenset[EvidenceClass]] = {
    c: frozenset(d for d in EvidenceClass if c in _UP[d]) for c in EvidenceClass
}


def leq(a: EvidenceClass, b: EvidenceClass) -> bool:
    """True if ``a ⪯ b`` — b is at least as admissible as a."""
    return b in _UP[a]


def comparable(a: EvidenceClass, b: EvidenceClass) -> bool:
    """False for the WIT/REC pair, which is the point of using a poset."""
    return leq(a, b) or leq(b, a)


def _greatest(candidates: Iterable[EvidenceClass]) -> EvidenceClass:
    """The unique element of ``candidates`` that all others are ⪯ to."""
    items = list(candidates)
    for c in items:
        if all(leq(other, c) for other in items):
            return c
    raise ValueError(f"no greatest element in {items!r}")  # pragma: no cover


def _least(candidates: Iterable[EvidenceClass]) -> EvidenceClass:
    """The unique element of ``candidates`` that is ⪯ to all others."""
    items = list(candidates)
    for c in items:
        if all(leq(c, other) for other in items):
            return c
    raise ValueError(f"no least element in {items!r}")  # pragma: no cover


def meet(a: EvidenceClass, b: EvidenceClass) -> EvidenceClass:
    """Greatest lower bound — the *weakest link*.

    Used for multi-hop provenance and for a single verifier's basis across the
    items it relied on: a chain is only as admissible as its weakest step.

    ``meet(WIT, REC) == SIGN``.
    """
    return _greatest(_DOWN[a] & _DOWN[b])


def join(a: EvidenceClass, b: EvidenceClass) -> EvidenceClass:
    """Least upper bound — the *strongest survivor*.

    Used to aggregate basis across independent verifiers that survived the
    admissibility floor.

    ``join(WIT, REC) == ATT``.
    """
    return _least(_UP[a] & _UP[b])


def meet_all(classes: Iterable[EvidenceClass]) -> EvidenceClass:
    """Fold ``meet`` over a provenance chain. Empty chain yields SELF."""
    items = list(classes)
    if not items:
        return EvidenceClass.SELF
    return reduce(meet, items)


def join_all(classes: Iterable[EvidenceClass]) -> EvidenceClass:
    """Fold ``join`` over surviving verifier bases. Empty set yields SELF."""
    items = list(classes)
    if not items:
        return EvidenceClass.SELF
    return reduce(join, items)


def meets_floor(basis: EvidenceClass, floor: EvidenceClass) -> bool:
    """Admissibility test: does ``basis`` reach the obligation's floor ``φO``?

    Verdicts failing this receive weight zero in the aggregator. This single
    predicate is what makes it structurally impossible for a model's opinion
    (SELF/SIGN) to clear an obligation whose floor is REC.
    """
    return leq(floor, basis)


# Human-readable descriptions, surfaced in the dashboard and explanations.
DESCRIPTIONS: dict[EvidenceClass, str] = {
    EvidenceClass.SELF: "unverified self-report by the acting party",
    EvidenceClass.SIGN: "cryptographically signed by the acting party",
    EvidenceClass.WIT: "third-party witness signature",
    EvidenceClass.REC: "signed receipt from a non-interested external system",
    EvidenceClass.ATT: "attestation from a trusted execution environment",
    EvidenceClass.PROOF: "cryptographic proof",
}
