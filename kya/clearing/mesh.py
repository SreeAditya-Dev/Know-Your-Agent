"""The verification mesh and its aggregator.

Verifiers run independently and disagree; this module decides what their
disagreement amounts to. Three rules, in order.

**1. The admissibility floor.** Any verdict whose declared basis fails to reach
the obligation's `φO` receives weight zero — not reduced weight, none. This is
the single predicate behind the project's central claim, and it is applied
before anything else so that no later step can be influenced by evidence that
was never admissible.

**2. Maximal bases decide.** Among admitted verifiers, a verdict is only
overridden by one resting on *strictly stronger* evidence. So a `REC`-class
receipt overrules a `SIGN`-class dissent, and the disagreement is resolved. But
`WIT` and `REC` are incomparable — a courier's witness statement and a payment
receipt neither dominates the other — so when those two disagree, neither wins.
That is the poset earning its place: an ordering would have silently picked a
winner where no principled winner exists.

**3. Unresolved disagreement fails closed.** When the maximal set splits, the
verdict is the conservative one and the conflict is recorded, which is enough
on its own to keep the decision out of FINAL.

The aggregator also checks the one thing verifiers are trusted about. RAILS
treats `declared_basis` as trusted and names the abuse LAUNDER-BASIS. Trust is
the right default for a component of the system, but a declaration nobody
checks is not a guarantee — so the mesh recomputes the meet of each verifier's
cited items and flags any that claimed better than it cited. That flag reaches
the agent's passport, where enough of it floors the tier outright.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kya.canonical import digest
from kya.clearing.evidence import EvidenceIndex, basis_drift
from kya.clearing.verifiers.base import VerificationContext, Verifier
from kya.enums import Finality, VerifierRole
from kya.evidence import EvidenceClass, join_all, leq, meets_floor
from kya.schemas import ClearingDecision, VerifierOutput

#: Roles whose verdicts speak to *performance* — was the promise kept. The
#: policy verifier is deliberately excluded: conduct around the transaction is
#: a separate question with a separate remedy, and folding it in would let a
#: late delivery read as a non-delivery.
PERFORMANCE_ROLES = (
    VerifierRole.CONSTRAINT,
    VerifierRole.RECEIPT,
    VerifierRole.SEMANTIC,
)


@dataclass(slots=True)
class MeshOutcome:
    """The aggregator's working, kept for the audit trail."""

    outputs: list[VerifierOutput]
    admitted: list[VerifierOutput]
    excluded: list[VerifierOutput]
    drifted: list[VerifierRole] = field(default_factory=list)
    conflict: bool = False
    conflicting_roles: list[VerifierRole] = field(default_factory=list)

    def by_role(self, role: VerifierRole) -> VerifierOutput | None:
        return next((o for o in self.outputs if o.role is role), None)


class VerificationMesh:
    """Runs verifiers and aggregates them under the obligation's floor."""

    def __init__(self, verifiers: list[Verifier]) -> None:
        self.verifiers = verifiers

    def run(self, ctx: VerificationContext) -> MeshOutcome:
        outputs = [v.run(ctx) for v in self.verifiers]
        floor = ctx.obligation.admissibility_floor

        admitted: list[VerifierOutput] = []
        excluded: list[VerifierOutput] = []
        drifted: list[VerifierRole] = []

        for output in outputs:
            if basis_drift(ctx.index, output.declared_basis, output.cited_items):
                # A verifier claiming better than it cited is excluded on the
                # recomputed basis, not the declared one — the honest answer is
                # what it actually read, and the overclaim is recorded.
                drifted.append(output.role)
                output.declared_basis = ctx.index.basis_of(output.cited_items)

            if meets_floor(output.declared_basis, floor):
                admitted.append(output)
            else:
                excluded.append(output)

        conflicting = _conflicting_roles(admitted)
        return MeshOutcome(
            outputs=outputs,
            admitted=admitted,
            excluded=excluded,
            drifted=drifted,
            conflict=bool(conflicting),
            conflicting_roles=conflicting,
        )


def aggregate(ctx: VerificationContext, outcome: MeshOutcome) -> ClearingDecision:
    """Fold the mesh into one PROVISIONAL clearing decision."""
    performance = [o for o in outcome.admitted if o.role in PERFORMANCE_ROLES]
    policy = [o for o in outcome.admitted if o.role is VerifierRole.POLICY]

    performance_verdict, performance_confidence = _decide(performance)
    policy_verdict, policy_confidence = _decide(policy)

    # The strongest basis among survivors — RAILS' join across verifiers. It
    # describes what the decision *rests on*, and is what the finality
    # predicate re-tests against the floor.
    aggregate_basis = join_all([o.declared_basis for o in outcome.admitted])

    loss = max(
        (o.loss_estimate for o in outcome.admitted if o.verdict == "VIOLATED"),
        default=0,
    )

    decision = ClearingDecision(
        obligation_hash=ctx.obligation.self_hash,
        performance_verdict=performance_verdict,
        policy_verdict=policy_verdict,
        fault=_assign_fault(performance_verdict, policy_verdict),
        aggregate_basis=aggregate_basis,
        confidence=min(performance_confidence, policy_confidence),
        loss_estimate=loss,
        verifier_outputs=outcome.outputs,
        excluded_verifiers=[o.role for o in outcome.excluded],
        finality=Finality.PROVISIONAL,
        emitted_at=ctx.now,
    )
    decision.decision_hash = digest(
        decision.model_dump(mode="python", exclude={"decision_hash"})
    )
    return decision


def _decide(outputs: list[VerifierOutput]) -> tuple[str, float]:
    """Verdict and confidence from one group of admitted verifiers.

    Only the maximal-basis verifiers get a vote. A verdict resting on strictly
    weaker evidence than another is not a dissent — it is answered.
    """
    speaking = [o for o in outputs if o.verdict != "INDETERMINATE"]
    if not speaking:
        # Nobody with admissible evidence had anything to say. Confidence 1.0
        # would be a lie and 0.0 is the truth: this is exactly the state that
        # must not reach finality.
        return "INDETERMINATE", 0.0

    maximal = _maximal(speaking)
    verdicts = {o.verdict for o in maximal}

    if verdicts == {"SATISFIED"}:
        return "SATISFIED", min(o.confidence for o in maximal)
    if "VIOLATED" in verdicts:
        # Covers both unanimous violation and a split. A split resolves
        # conservatively; the split itself is recorded separately and blocks
        # finality on its own.
        violating = [o for o in maximal if o.verdict == "VIOLATED"]
        return "VIOLATED", min(o.confidence for o in violating)
    return "INDETERMINATE", 0.0


def _maximal(outputs: list[VerifierOutput]) -> list[VerifierOutput]:
    """Those not strictly dominated in basis by another in the set."""
    return [
        candidate
        for candidate in outputs
        if not any(
            other is not candidate
            and leq(candidate.declared_basis, other.declared_basis)
            and not leq(other.declared_basis, candidate.declared_basis)
            for other in outputs
        )
    ]


def _conflicting_roles(admitted: list[VerifierOutput]) -> list[VerifierRole]:
    """Roles in genuine, unresolvable disagreement about performance.

    Genuine means both sides survived the floor and neither's basis strictly
    dominates the other's. Two `REC`-class verifiers disagreeing is a real
    conflict; a `REC` overruling a `SIGN` is not.
    """
    speaking = [
        o
        for o in admitted
        if o.role in PERFORMANCE_ROLES and o.verdict != "INDETERMINATE"
    ]
    maximal = _maximal(speaking)
    if len({o.verdict for o in maximal}) <= 1:
        return []
    return sorted({o.role for o in maximal}, key=lambda r: r.value)


def _assign_fault(performance: str, policy: str) -> str | None:
    """Who the decision holds responsible.

    Both failures land on the merchant because this gateway sits on the
    merchant's side of the transaction: non-delivery and over-collection are
    both things the merchant did. Agent-side fault is caught inline by the
    gates and never reaches clearing — an agent that tampered with a cart was
    denied before an obligation existed to clear.
    """
    if performance == "VIOLATED":
        return "merchant:non_performance"
    if policy == "VIOLATED":
        return "merchant:policy_breach"
    return None
