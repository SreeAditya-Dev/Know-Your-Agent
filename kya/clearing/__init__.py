"""The clearing layer — the part that does not exist anywhere else in production.

Everything in the inline data plane is a well-defended version of a known idea:
signature verification, mandate chains, rate limits. This package is the new
thing, and it answers the question the payment rails structurally cannot.

    Payment settles value transfer. Clearing settles obligation state.
                                                    — RAILS, arXiv 2606.08790

An agent transaction can pass every identity check, carry a perfectly valid
mandate chain, and charge exactly the signed amount — and still deliver the
wrong thing. Razorpay records that money moved. Nothing records whether the
promise was kept, which is why "was the obligation satisfied?" has no answer
today and disputes are adjudicated on argument rather than evidence.

The pieces, in the order they run:

* ``evidence`` — grading submitted evidence on the RAILS partial order, where
  the composition rules live and where "who fetched it" decides the class.
* ``verifiers/`` — four independent opinions: constraint, receipt, semantic
  (the LLM, capped), policy.
* ``mesh`` — aggregation under the obligation's admissibility floor.
* ``finality`` — RAILS' four conjuncts, each reported separately.
* ``reversal`` — where a DISPUTED decision becomes a refund.
* ``service`` — the whole path in one call.
"""

from kya.clearing.evidence import (
    EvidenceIndex,
    basis_drift,
    collect_rail_evidence,
    effective_class,
    envelope,
    from_agent,
    from_rail,
    from_witness,
    item,
)
from kya.clearing.finality import Conjunct, FinalityCheck, evaluate_finality
from kya.clearing.mesh import (
    PERFORMANCE_ROLES,
    MeshOutcome,
    VerificationMesh,
    aggregate,
)
from kya.clearing.reversal import SettlementExecutor, SettlementResult
from kya.clearing.service import ClearingResult, ClearingService, default_verifiers
from kya.clearing.verifiers import (
    CLAIM_DELIVERY_DESCRIPTION,
    ConstraintVerifier,
    Judge,
    Judgement,
    KeywordJudge,
    NvidiaJudge,
    PolicyVerifier,
    ReceiptVerifier,
    SemanticVerifier,
    VerificationContext,
    Verifier,
    judge_from_env,
)

__all__ = [
    "EvidenceIndex",
    "basis_drift",
    "collect_rail_evidence",
    "effective_class",
    "envelope",
    "from_agent",
    "from_rail",
    "from_witness",
    "item",
    "Conjunct",
    "FinalityCheck",
    "evaluate_finality",
    "PERFORMANCE_ROLES",
    "MeshOutcome",
    "VerificationMesh",
    "aggregate",
    "SettlementExecutor",
    "SettlementResult",
    "ClearingResult",
    "ClearingService",
    "default_verifiers",
    "CLAIM_DELIVERY_DESCRIPTION",
    "ConstraintVerifier",
    "Judge",
    "Judgement",
    "KeywordJudge",
    "NvidiaJudge",
    "PolicyVerifier",
    "ReceiptVerifier",
    "SemanticVerifier",
    "VerificationContext",
    "Verifier",
    "judge_from_env",
]
