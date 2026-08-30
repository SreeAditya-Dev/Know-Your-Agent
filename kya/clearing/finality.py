"""The finality predicate — RAILS' four conjuncts, kept apart.

    φ(CD, t, ε) =  cls(B) ⪰ φO                  evidence meets the floor
                 ∧ c ≥ c_min                     confidence threshold
                 ∧ NoUnresolvedConflict(V, ε)    verifiers do not contradict
                 ∧ t ≥ t_emit + τ_appeal         appeal window elapsed

Every conjunct is evaluated and reported *individually*, even once one has
already failed. That costs nothing and buys the thing a dispute reviewer
actually needs: "this did not clear" is not an answer, and "the evidence was
admissible and the verifiers agreed, but confidence was 0.5 against a 0.7
threshold" is.

The four are genuinely independent, which is why none can stand in for another:

* **Admissibility** is about the *kind* of evidence. It can pass while the
  evidence points the wrong way.
* **Confidence** is about certainty. A `REC`-class receipt can be admissible
  and still leave the question open.
* **Conflict** is about agreement. Two impeccable, equally strong verifiers can
  contradict each other, and averaging them would be inventing a consensus.
* **The appeal window** is about time. It exists so that finality is something
  a counterparty can contest *before* it becomes irreversible, rather than a
  state they discover afterwards.

Failing any one produces DISPUTED, never a delayed FINAL — with the single
exception of the appeal window, which is the only conjunct that becomes true on
its own. A decision waiting out its appeal window stays PROVISIONAL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from kya.enums import Finality
from kya.evidence import meets_floor
from kya.policy import Policy
from kya.schemas import ClearingDecision, ObligationReceipt


@dataclass(slots=True)
class Conjunct:
    name: str
    passed: bool
    detail: str


@dataclass(slots=True)
class FinalityCheck:
    finality: Finality
    conjuncts: list[Conjunct] = field(default_factory=list)
    appeal_expires_at: datetime | None = None

    @property
    def failed(self) -> list[Conjunct]:
        return [c for c in self.conjuncts if not c.passed]

    @property
    def waiting(self) -> bool:
        return self.finality is Finality.PROVISIONAL

    def summary(self) -> str:
        if self.finality is Finality.FINAL:
            return "FINAL: all four finality conjuncts hold."
        if self.finality is Finality.PROVISIONAL:
            window = (
                self.appeal_expires_at.isoformat() if self.appeal_expires_at else "?"
            )
            return f"PROVISIONAL: substantive checks hold; appeal window open until {window}."
        return "DISPUTED: " + "; ".join(c.detail for c in self.failed)


def evaluate_finality(
    decision: ClearingDecision,
    obligation: ObligationReceipt,
    policy: Policy,
    now: datetime,
) -> FinalityCheck:
    """Apply the predicate. Reports every conjunct, not just the first failure."""
    floor = obligation.admissibility_floor
    admissible = meets_floor(decision.aggregate_basis, floor)

    conjuncts = [
        Conjunct(
            "admissibility",
            admissible,
            (
                f"aggregate basis {decision.aggregate_basis.value} "
                f"{'meets' if admissible else 'is below'} the obligation floor "
                f"{floor.value}"
            ),
        ),
        Conjunct(
            "performance",
            decision.performance_verdict == "SATISFIED",
            f"performance verdict is {decision.performance_verdict}",
        ),
        Conjunct(
            "policy",
            decision.policy_verdict != "VIOLATED",
            f"policy verdict is {decision.policy_verdict}",
        ),
        Conjunct(
            "confidence",
            decision.confidence >= policy.min_confidence,
            (
                f"confidence {decision.confidence:.2f} against a "
                f"{policy.min_confidence:.2f} threshold"
            ),
        ),
        Conjunct(
            "no_conflict",
            not _has_conflict(decision),
            _conflict_detail(decision),
        ),
    ]

    appeal_expires = decision.emitted_at + timedelta(
        seconds=policy.appeal_window_seconds
    )
    appeal_elapsed = now >= appeal_expires
    conjuncts.append(
        Conjunct(
            "appeal_window",
            appeal_elapsed,
            (
                f"appeal window {'elapsed' if appeal_elapsed else 'still open'} "
                f"(closes {appeal_expires.isoformat()})"
            ),
        )
    )

    substantive = [c for c in conjuncts if c.name != "appeal_window"]
    if not all(c.passed for c in substantive):
        # A substantive failure is not cured by waiting, so there is no reason
        # to hold the counterparty's money while the clock runs out.
        finality = Finality.DISPUTED
    elif not appeal_elapsed:
        finality = Finality.PROVISIONAL
    else:
        finality = Finality.FINAL

    return FinalityCheck(
        finality=finality, conjuncts=conjuncts, appeal_expires_at=appeal_expires
    )


def _has_conflict(decision: ClearingDecision) -> bool:
    """Unresolved contradiction among admitted performance verifiers.

    Recomputed from the decision rather than carried on it, so a stored
    decision can be re-evaluated later — the replay path a dispute reviewer
    needs — without the mesh's working having to be persisted alongside it.
    """
    from kya.clearing.mesh import PERFORMANCE_ROLES, _maximal

    admitted = [
        o
        for o in decision.verifier_outputs
        if o.role in PERFORMANCE_ROLES
        and o.role not in decision.excluded_verifiers
        and o.verdict != "INDETERMINATE"
    ]
    return len({o.verdict for o in _maximal(admitted)}) > 1


def _conflict_detail(decision: ClearingDecision) -> str:
    if not _has_conflict(decision):
        return "verifiers do not contradict each other"
    return (
        "verifiers on incomparable or equal bases disagree; neither dominates, "
        "so the disagreement cannot be resolved by evidence class"
    )
