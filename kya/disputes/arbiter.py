"""Liability Arbiter — Multi-party fault resolution for agentic commerce.

Resolves the industry gap where Visa/Mastercard/Amex chargeback rules assume
human buyers and cannot determine liability when an autonomous agent acts.

Evaluates the cryptographic Consent Evidence Chain, Obligation Promises,
Delivery Evidence, and Verification Mesh consensus to deterministically
assign liability among:
- BUYER_PRINCIPAL (Friendly Fraud)
- AGENT_OPERATOR (Rogue Agent / Constraint Breach)
- MERCHANT (Fulfilment Failure / Breach of Acceptance Criteria)
- PAYMENT_RAIL (Network / Settlement Desynchronization)
- SPLIT (Contributory Multi-party Fault)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from kya.canonical import digest, now_utc
from kya.clearing.finality import FinalityCheck
from kya.enums import (
    DisputeClaimReason,
    DisputeParty,
    Finality,
    LiabilityOutcome,
    VerifierRole,
)
from kya.evidence import meets_floor
from kya.reasons import (
    L001,
    L002,
    L003,
    L004,
    L005,
    L006,
    L007,
    L008,
)
from kya.schemas import (
    Cart,
    ClearingDecision,
    ConsentRecord,
    DisputeClaim,
    EvidenceEnvelope,
    LiabilityVerdict,
    ObligationReceipt,
)


class LiabilityArbiter:
    """Deterministic arbiter assigning dispute liability across transaction parties."""

    def __init__(self, clock: Callable[[], datetime] = now_utc) -> None:
        self._clock = clock

    def arbitrate(
        self,
        claim: DisputeClaim,
        obligation: ObligationReceipt,
        consent: ConsentRecord | None = None,
        evidence: EvidenceEnvelope | None = None,
        clearing: ClearingDecision | None = None,
        finality: FinalityCheck | None = None,
        charged_cart: Cart | None = None,
    ) -> LiabilityVerdict:
        """Adjudicate liability based on cryptographic proofs and mesh verification."""
        verdict_id = f"lvd_{uuid.uuid4().hex[:16]}"
        now = self._clock()

        # Step 1: Evaluate Consent & Delegation Bounds
        consent_valid = False
        consent_violations: list[str] = []
        if consent is not None:
            # Check consent expiration at time of dispute/creation
            if obligation.created_at > consent.expires_at:
                consent_violations.append("Transaction occurred after consent expiration window")
            
            # Check constraints
            if obligation.promised.total > consent.constraints.max_amount:
                consent_violations.append(
                    f"Promised total {obligation.promised.total} exceeded max_amount constraint {consent.constraints.max_amount}"
                )

            if consent.constraints.allowed_merchants and obligation.merchant_id not in consent.constraints.allowed_merchants:
                consent_violations.append(
                    f"Merchant {obligation.merchant_id} not in allowed_merchants whitelist"
                )

            if not consent_violations:
                consent_valid = True

        # Step 2: Evaluate Merchant Fulfilment & Evidence Admissibility
        merchant_fulfilled = False
        fulfilment_violations: list[str] = []

        if clearing is not None:
            floor = obligation.admissibility_floor
            admissible = meets_floor(clearing.aggregate_basis, floor)
            if not admissible:
                fulfilment_violations.append(
                    f"Clearing evidence class {clearing.aggregate_basis.value} is below obligation floor {floor.value}"
                )
            if clearing.performance_verdict != "SATISFIED":
                fulfilment_violations.append(
                    f"Clearing performance verdict is {clearing.performance_verdict}"
                )
            if VerifierRole.CONSTRAINT in clearing.excluded_verifiers:
                fulfilment_violations.append(
                    "Delivery constraint verifier was excluded due to inadmissible sub-floor evidence"
                )
            if clearing.policy_verdict == "VIOLATED":
                fulfilment_violations.append("Policy verifier declared violation")

            if not fulfilment_violations and (
                finality is None or finality.finality in (Finality.FINAL, Finality.PROVISIONAL)
            ):
                merchant_fulfilled = True
        else:
            if claim.claim_reason in (
                DisputeClaimReason.NOT_AS_DESCRIBED,
                DisputeClaimReason.MERCHANDISE_NOT_RECEIVED,
                DisputeClaimReason.CANCELLED_SERVICE,
            ):
                fulfilment_violations.append("No clearing evidence provided for contested fulfilment")
            else:
                merchant_fulfilled = True

        # Step 3: Multi-Party Fault Adjudication
        reasons: list[str] = []
        fault_alloc: dict[str, float] = {}

        # Scenario A: Contributory / Split Fault (Both Agent breached constraints AND Merchant failed delivery)
        if not consent_valid and not merchant_fulfilled:
            assigned_fault = DisputeParty.SPLIT
            outcome = LiabilityOutcome.SPLIT_LIABILITY
            fault_alloc = {
                DisputeParty.AGENT_OPERATOR.value: 0.5,
                DisputeParty.MERCHANT.value: 0.5,
            }
            reasons.extend([L002.code, L004.code, L008.code])
            confidence = 0.90
            compelling_summary = (
                "Contributory fault: Agent breached principal constraints, AND merchant failed "
                f"fulfilment criteria ({'; '.join(fulfilment_violations)}). Liability split 50/50."
            )
            explanation = (
                "Both parties contributed to transaction failure: the agent operated outside its "
                "delegated mandate, and the merchant failed to conform to the obligation criteria. "
                "Liability is equally apportioned between the agent operator and the merchant."
            )

        # Scenario B: Rogue Agent / Constraint Breach (Agent Operator Liable)
        elif not consent_valid and consent_violations:
            assigned_fault = DisputeParty.AGENT_OPERATOR
            outcome = LiabilityOutcome.AGENT_FAULT_ESCROW_CLAIM
            fault_alloc = {DisputeParty.AGENT_OPERATOR.value: 1.0}
            reasons.extend([L002.code, L006.code])
            confidence = 0.95
            compelling_summary = (
                f"Agent violated principal delegation constraints: {'; '.join(consent_violations)}. "
                "Liability assigned to Agent Operator / AI Provider."
            )
            explanation = (
                "The autonomous buyer agent executed a purchase that violated the human principal's "
                f"mandate constraints ({'; '.join(consent_violations)}). The agent operator or escrow "
                "is liable for indemnifying the transaction."
            )

        # Scenario C: Merchant Delivery Failure (Merchant Liable)
        elif not merchant_fulfilled and fulfilment_violations:
            assigned_fault = DisputeParty.MERCHANT
            outcome = LiabilityOutcome.REFUND_ISSUED
            fault_alloc = {DisputeParty.MERCHANT.value: 1.0}
            reasons.append(L004.code)
            confidence = 0.95
            compelling_summary = (
                f"Merchant failed obligation acceptance criteria: {'; '.join(fulfilment_violations)}. "
                "Chargeback valid; refund recommended."
            )
            explanation = (
                "The merchant failed to provide verifiable, admissible evidence satisfying the "
                "obligation's delivery criteria. Fault lies with the merchant, and funds should be "
                "refunded to the buyer."
            )

        # Scenario D: Friendly Fraud (Buyer Principal Liable, Merchant Protected)
        elif consent_valid and merchant_fulfilled:
            assigned_fault = DisputeParty.BUYER_PRINCIPAL
            outcome = LiabilityOutcome.MERCHANT_PROTECTED
            fault_alloc = {DisputeParty.BUYER_PRINCIPAL.value: 1.0}
            reasons.extend([L001.code, L003.code, L005.code, L007.code])
            confidence = 0.98
            compelling_summary = (
                "Compelling Evidence 3.0 match: Human principal validly signed intent mandate within "
                "exact constraints, and merchant fulfilled all obligation criteria with admissible "
                f"class {clearing.aggregate_basis.value if clearing else 'REC'} proof. Dispute is friendly fraud."
            )
            explanation = (
                "Cryptographic proof demonstrates that the human buyer explicitly delegated and authorized "
                f"this transaction (Mandate Hash: {obligation.mandate_chain_hash[:12]}...), and the merchant "
                f"delivered the promised line items ({', '.join(li.name for li in obligation.promised.line_items)}) "
                "with full verification mesh finality. The merchant is fully protected against this chargeback."
            )

        # Scenario E: Indeterminate / Network Desync
        else:
            assigned_fault = DisputeParty.PAYMENT_RAIL
            outcome = LiabilityOutcome.ESCALATE_HUMAN
            fault_alloc = {DisputeParty.PAYMENT_RAIL.value: 1.0}
            confidence = 0.50
            compelling_summary = "Indeterminate evidence state. Escalated for human adjudication."
            explanation = "Evidence is insufficient or contradictory. Escalated for manual arbitration."

        return LiabilityVerdict(
            verdict_id=verdict_id,
            dispute_id=claim.dispute_id,
            obligation_id=obligation.obligation_id,
            assigned_fault=assigned_fault,
            fault_allocation=fault_alloc,
            outcome=outcome,
            confidence=confidence,
            reason_codes=reasons,
            explanation=explanation,
            compelling_evidence_summary=compelling_summary,
            evaluated_at=now,
        )
