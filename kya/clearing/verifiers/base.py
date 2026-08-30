"""What every mesh verifier is, and the constraints it works under.

A verifier answers one question about one obligation and reports four things:
a verdict, a confidence, **the class of evidence it relied on**, and the items
it read. It does not decide anything. The mesh aggregates; finality is a
separate predicate again.

That separation is what keeps the central claim structural rather than
aspirational. A verifier cannot clear a settlement even if it wants to, because
clearing is not a thing a verifier does — the strongest statement it can make
is a verdict at a declared basis, and the aggregator discards any verdict whose
basis is below the obligation's floor.

Verifiers must not raise. An exception in one verifier is a missing opinion,
not a failed clearing, and it resolves to INDETERMINATE — which never on its
own moves an obligation toward FINAL.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

from kya.clearing.evidence import EvidenceIndex
from kya.enums import VerifierRole
from kya.evidence import EvidenceClass
from kya.schemas import ObligationReceipt, VerifierOutput

Verdict = Literal["SATISFIED", "VIOLATED", "INDETERMINATE"]


class VerificationContext:
    """Everything a verifier is allowed to see."""

    def __init__(
        self,
        obligation: ObligationReceipt,
        index: EvidenceIndex,
        now: datetime,
        rail_id: str | None = None,
        extras: dict[str, Any] | None = None,
    ) -> None:
        self.obligation = obligation
        self.index = index
        self.now = now
        #: The rail's identifier for this obligation, where one is bound. The
        #: receipt verifier needs it to go and fetch the payment itself.
        self.rail_id = rail_id
        self.extras = extras or {}


class Verifier(ABC):
    role: VerifierRole

    #: Hard ceiling on what this verifier may ever declare, regardless of what
    #: it read. Only the semantic verifier sets one, and that cap is the
    #: project's central claim expressed as a class attribute.
    max_basis: EvidenceClass | None = None

    @abstractmethod
    def verify(self, ctx: VerificationContext) -> VerifierOutput:
        """Inspect and report. Must not raise."""

    def run(self, ctx: VerificationContext) -> VerifierOutput:
        try:
            output = self.verify(ctx)
        except Exception as exc:  # pragma: no cover - defensive
            return self._out(
                "INDETERMINATE",
                0.0,
                EvidenceClass.SELF,
                detail=f"verifier error: {type(exc).__name__}: {exc}"[:200],
            )
        return self._apply_cap(output)

    def _apply_cap(self, output: VerifierOutput) -> VerifierOutput:
        """Enforce ``max_basis`` after the fact.

        Applied here rather than trusted to the subclass so that the cap holds
        for any verifier of this class, including one written later by someone
        who has not read this file.
        """
        from kya.evidence import meet

        if self.max_basis is not None:
            output.declared_basis = meet(output.declared_basis, self.max_basis)
        return output

    def _out(
        self,
        verdict: Verdict,
        confidence: float,
        basis: EvidenceClass,
        cited: list[str] | None = None,
        loss: int = 0,
        detail: str = "",
    ) -> VerifierOutput:
        return VerifierOutput(
            role=self.role,
            verdict=verdict,
            confidence=max(0.0, min(1.0, confidence)),
            declared_basis=basis,
            cited_items=cited or [],
            loss_estimate=max(0, loss),
            detail=detail[:400],
        )
