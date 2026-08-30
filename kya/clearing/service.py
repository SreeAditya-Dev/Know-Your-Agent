"""The clearing service — the whole control plane in one call path.

    evidence → mesh → aggregate → finality → settle or reverse

This runs **off the money path**. The inline pipeline decided whether the
transaction was permitted; this decides, later and with more information,
whether the obligation was satisfied. Nothing here is on a latency budget, and
nothing here can authorise a payment — the only monetary action it can take is
giving money back.

The ordering that matters: rail evidence is collected *first*, before any
verifier runs, so that every verifier reasons over the same evidence set and
every citation names an item the mesh can find. A verifier that fetched its own
evidence would be citing things nobody else could check, which is precisely the
condition basis-drift detection exists to rule out.

A decision that comes back PROVISIONAL is not a failure. It means the
substantive checks passed and the appeal window has not closed — the state a
counterparty is given in order to contest the outcome before it becomes
irreversible. Re-running the same obligation after the window elapses is the
intended usage, and is idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from kya.canonical import now_utc
from kya.clearing.evidence import EvidenceIndex, collect_rail_evidence
from kya.clearing.finality import FinalityCheck, evaluate_finality
from kya.clearing.mesh import MeshOutcome, VerificationMesh, aggregate
from kya.clearing.reversal import SettlementExecutor, SettlementResult
from kya.clearing.verifiers.base import VerificationContext, Verifier
from kya.clearing.verifiers.constraint import ConstraintVerifier
from kya.clearing.verifiers.policy import PolicyVerifier
from kya.clearing.verifiers.receipt import ReceiptVerifier
from kya.clearing.verifiers.semantic import SemanticVerifier
from kya.enums import Finality
from kya.obligation.ledger import ObligationLedger
from kya.passport import PassportStore
from kya.policy import Policy, default_policy
from kya.rails.razorpay_client import RazorpayRail
from kya.reserve_pay import BlockLedger
from kya.schemas import ClearingDecision, EvidenceEnvelope, ObligationReceipt


@dataclass(slots=True)
class ClearingResult:
    """Everything one clearing pass produced, in the order it happened."""

    obligation: ObligationReceipt
    decision: ClearingDecision
    finality: FinalityCheck
    mesh: MeshOutcome
    settlement: SettlementResult | None = None

    @property
    def cleared(self) -> bool:
        return self.decision.finality is Finality.FINAL

    @property
    def disputed(self) -> bool:
        return self.decision.finality is Finality.DISPUTED

    def explain(self) -> str:
        """Reviewer-facing prose, generated from the decision.

        Presentation, never inference — the same rule the inline explainer
        follows. Everything below is already decided by the time this runs.
        """
        lines = [self.finality.summary()]

        if self.mesh.excluded:
            lines.append(
                "Given weight zero for falling below the "
                f"{self.obligation.admissibility_floor.value} floor: "
                + ", ".join(
                    f"{o.role.value} at {o.declared_basis.value}"
                    for o in self.mesh.excluded
                )
                + "."
            )
        if self.mesh.drifted:
            lines.append(
                "Basis drift detected from: "
                + ", ".join(r.value for r in self.mesh.drifted)
                + " — declared better evidence than cited."
            )
        if self.settlement is not None:
            lines.append(self.settlement.detail.capitalize() + ".")
        return " ".join(lines)


class ClearingService:
    def __init__(
        self,
        ledger: ObligationLedger,
        rail: RazorpayRail | None = None,
        blocks: BlockLedger | None = None,
        passports: PassportStore | None = None,
        policy: Policy | None = None,
        verifiers: list[Verifier] | None = None,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.ledger = ledger
        self.rail = rail
        self.policy = policy or default_policy()
        self._clock = clock
        self.mesh = VerificationMesh(verifiers or default_verifiers())
        self.executor = SettlementExecutor(
            ledger=ledger, rail=rail, blocks=blocks, passports=passports, clock=clock
        )

    def submit(
        self,
        obligation_id: str,
        envelope: EvidenceEnvelope,
        now: datetime | None = None,
        execute: bool = True,
    ) -> ClearingResult:
        """Clear an obligation against submitted evidence."""
        at = now or self._clock()
        obligation = self.ledger.current(obligation_id)
        if obligation is None:
            raise ValueError(f"unknown obligation {obligation_id!r}")

        rail_id = self.ledger.rail_id_for(obligation_id)

        # Rail evidence first, so every verifier sees one evidence set.
        augmented = envelope.model_copy(deep=True)
        augmented.items.extend(
            collect_rail_evidence(self.rail, obligation, rail_id, now=at)
        )

        index = EvidenceIndex(augmented)
        ctx = VerificationContext(
            obligation=obligation, index=index, now=at, rail_id=rail_id
        )

        outcome = self.mesh.run(ctx)
        decision = aggregate(ctx, outcome)
        check = evaluate_finality(decision, obligation, self.policy, at)
        decision.finality = check.finality
        if check.finality is not Finality.PROVISIONAL:
            decision.finalized_at = at

        # Drift is about the evidence's integrity, not this transaction's
        # outcome, so it is recorded whichever way the decision went.
        if outcome.drifted:
            self.executor.record_basis_drift(obligation.agent_id, outcome.drifted)

        settlement = None
        if execute and check.finality is not Finality.PROVISIONAL:
            settlement = self.executor.execute(obligation_id, decision)

        return ClearingResult(
            obligation=obligation,
            decision=decision,
            finality=check,
            mesh=outcome,
            settlement=settlement,
        )

    def reconsider(
        self, obligation_id: str, decision: ClearingDecision, now: datetime | None = None
    ) -> ClearingResult:
        """Re-test a PROVISIONAL decision once its appeal window has closed.

        Nothing is re-verified. The evidence has not changed; only the clock
        has, and re-running the mesh could produce a *different* verdict from
        the same facts if a rail state moved in the meantime. Re-testing the
        predicate against the stored decision is what makes the appeal window a
        waiting period rather than a second chance at a different answer.
        """
        at = now or self._clock()
        obligation = self.ledger.current(obligation_id)
        if obligation is None:
            raise ValueError(f"unknown obligation {obligation_id!r}")

        check = evaluate_finality(decision, obligation, self.policy, at)
        decision.finality = check.finality
        if check.finality is not Finality.PROVISIONAL:
            decision.finalized_at = at

        settlement = None
        if check.finality is not Finality.PROVISIONAL:
            settlement = self.executor.execute(obligation_id, decision)

        return ClearingResult(
            obligation=obligation,
            decision=decision,
            finality=check,
            mesh=MeshOutcome(outputs=decision.verifier_outputs, admitted=[], excluded=[]),
            settlement=settlement,
        )


def default_verifiers(judge=None) -> list[Verifier]:
    """The four-verifier mesh from docs/04.

    The semantic verifier defaults to the offline judge. A test suite whose
    numbers move because a remote model was retrained is a test suite that
    cannot be reproduced, and the eval's credibility rests on reproducibility.
    """
    return [
        ConstraintVerifier(),
        ReceiptVerifier(),
        SemanticVerifier(judge=judge),
        PolicyVerifier(),
    ]
