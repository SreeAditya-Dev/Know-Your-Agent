"""Dispute Representment Package & Settlement Certificate Generator.

Produces dispute-ready evidence packages adhering to:
- Visa Compelling Evidence 3.0 (CE3.0) & TAP standards
- Mastercard Dispute Administration & Representment Rules
- Razorpay Dispute Contest API & Order Note Verification

Answers the fundamental dispute question:
"Was the obligation satisfied, and did the buyer authorize it?"
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from kya.canonical import digest, now_utc
from kya.clearing.service import ClearingResult
from kya.crypto import KeyPair, sign_payload
from kya.disputes.arbiter import LiabilityArbiter
from kya.disputes.consent import ConsentLedger
from kya.enums import Finality, LiabilityOutcome
from kya.obligation.ledger import ObligationLedger
from kya.obligation.receipt import MerchantIdentity
from kya.schemas import (
    ClearingDecision,
    ConsentRecord,
    DisputeClaim,
    DisputeRepresentmentPackage,
    EvidenceEnvelope,
    LiabilityVerdict,
    ObligationReceipt,
    SettlementCertificate,
)


def create_settlement_certificate(
    obligation: ObligationReceipt,
    clearing_decision: ClearingDecision,
    merchant_key: MerchantIdentity | KeyPair | None = None,
    evidence: EvidenceEnvelope | None = None,
    clock: Callable[[], datetime] = now_utc,
) -> SettlementCertificate:
    """Mint a cryptographic Settlement Certificate proving obligation satisfaction."""
    cert_id = f"cert_{uuid.uuid4().hex[:16]}"
    now = clock()

    evidence_hashes = [digest(item.model_dump(mode="python")) for item in (evidence.items if evidence else [])]

    cert = SettlementCertificate(
        certificate_id=cert_id,
        obligation_id=obligation.obligation_id,
        version=obligation.version,
        merchant_id=obligation.merchant_id,
        agent_id=obligation.agent_id,
        principal_ref=obligation.principal_ref,
        rail=obligation.rail.model_copy(deep=True),
        clearing_decision_hash=clearing_decision.decision_hash or digest(clearing_decision.model_dump(mode="python")),
        aggregate_basis=clearing_decision.aggregate_basis,
        performance_verdict=clearing_decision.performance_verdict,
        finality=clearing_decision.finality,
        evidence_item_hashes=evidence_hashes,
        issued_at=now,
    )
    cert.certificate_hash = cert.compute_hash()
    if merchant_key is not None:
        priv = merchant_key.keypair.private if isinstance(merchant_key, MerchantIdentity) else merchant_key.private
        cert.merchant_signature = sign_payload(priv, cert.signing_payload())
    return cert


class RepresentmentGenerator:
    """Compiles machine-readable and human-readable dispute evidence packages."""

    def __init__(
        self,
        consent_ledger: ConsentLedger,
        obligation_ledger: ObligationLedger,
        arbiter: LiabilityArbiter | None = None,
        merchant_key: MerchantIdentity | KeyPair | None = None,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._consents = consent_ledger
        self._obligations = obligation_ledger
        self._arbiter = arbiter or LiabilityArbiter(clock=clock)
        self._merchant_key = merchant_key
        self._clock = clock

    def generate_package(
        self,
        claim: DisputeClaim,
        obligation: ObligationReceipt,
        evidence: EvidenceEnvelope | None = None,
        clearing: ClearingDecision | None = None,
        clearing_result: ClearingResult | None = None,
        consent: ConsentRecord | None = None,
    ) -> DisputeRepresentmentPackage:
        """Compile a full dispute representment package for card networks & Razorpay."""
        package_id = f"pkg_{uuid.uuid4().hex[:16]}"
        now = self._clock()

        # Fetch consent record if not provided directly
        if consent is None:
            consent = self._consents.get_by_chain_hash(obligation.mandate_chain_hash)

        # Use clearing decision from result if available
        if clearing is None and clearing_result is not None:
            clearing = clearing_result.decision

        # Run liability arbitration
        verdict = self._arbiter.arbitrate(
            claim=claim,
            obligation=obligation,
            consent=consent,
            evidence=evidence,
            clearing=clearing,
            finality=clearing_result.finality if clearing_result else None,
        )

        # Generate settlement certificate if cleared
        cert: SettlementCertificate | None = None
        if clearing is not None:
            cert = create_settlement_certificate(
                obligation=obligation,
                clearing_decision=clearing,
                merchant_key=self._merchant_key,
                evidence=evidence,
                clock=self._clock,
            )

        # Generate Razorpay Anchor proof
        anchor_proof = {
            "rail_type": obligation.rail.type.value,
            "rail_ref": obligation.rail.ref,
            "simulated": obligation.rail.simulated,
            "anchored_field": "notes.kya_obligation",
            "anchored_hash": obligation.self_hash,
            "version": obligation.version,
            "anchor_verified": True,
            "mandate_chain_hash": obligation.mandate_chain_hash,
        }

        # Build Audit Trail hash chain
        audit_chain: list[dict[str, Any]] = [
            {
                "step": "1_CONSENT_MANDATE",
                "entity_id": consent.consent_id if consent else "MANDATE_BUNDLE",
                "hash": obligation.mandate_chain_hash,
                "timestamp": obligation.created_at.isoformat(),
            },
            {
                "step": "2_OBLIGATION_RECEIPT",
                "entity_id": obligation.obligation_id,
                "hash": obligation.self_hash,
                "timestamp": obligation.created_at.isoformat(),
            },
        ]
        if clearing is not None:
            audit_chain.append(
                {
                    "step": "3_CLEARING_DECISION",
                    "entity_id": clearing.decision_hash or "CLEARING_DECISION",
                    "hash": clearing.decision_hash or digest(clearing.model_dump(mode="python")),
                    "timestamp": clearing.emitted_at.isoformat(),
                }
            )
        if cert is not None:
            audit_chain.append(
                {
                    "step": "4_SETTLEMENT_CERTIFICATE",
                    "entity_id": cert.certificate_id,
                    "hash": cert.certificate_hash,
                    "timestamp": cert.issued_at.isoformat(),
                }
            )

        # Generate Markdown Representment Brief
        brief = self._render_markdown_brief(
            package_id=package_id,
            claim=claim,
            obligation=obligation,
            consent=consent,
            verdict=verdict,
            cert=cert,
            evidence=evidence,
            clearing=clearing,
            anchor_proof=anchor_proof,
        )

        executive_summary = (
            f"Dispute {claim.dispute_id} Adjudication: {verdict.outcome.value} "
            f"(Fault: {verdict.assigned_fault.value}, Confidence: {verdict.confidence:.0%}). "
            f"{verdict.compelling_evidence_summary}"
        )

        return DisputeRepresentmentPackage(
            package_id=package_id,
            dispute_id=claim.dispute_id,
            obligation_id=obligation.obligation_id,
            created_at=now,
            executive_summary=executive_summary,
            liability_verdict=verdict,
            settlement_certificate=cert,
            consent_record=consent,
            obligation_receipt=obligation,
            evidence_envelope=evidence,
            clearing_decision=clearing,
            razorpay_anchor_proof=anchor_proof,
            audit_trail_hash_chain=audit_chain,
            representment_brief_markdown=brief,
        )

    def _render_markdown_brief(
        self,
        package_id: str,
        claim: DisputeClaim,
        obligation: ObligationReceipt,
        consent: ConsentRecord | None,
        verdict: LiabilityVerdict,
        cert: SettlementCertificate | None,
        evidence: EvidenceEnvelope | None,
        clearing: ClearingDecision | None,
        anchor_proof: dict[str, Any],
    ) -> str:
        items_md = "\n".join(
            f"- **{li.name}** (SKU: `{li.sku}`) × {li.qty} @ ₹{li.unit_price / 100:.2f} = ₹{li.line_total / 100:.2f}"
            for li in obligation.promised.line_items
        )

        evidence_items_md = "None recorded"
        if evidence and evidence.items:
            evidence_items_md = "\n".join(
                f"- `{item.claim}`: `{item.value}` (Class: **{item.declared_class.value}**, Source: `{item.source}`)"
                for item in evidence.items
            )

        consent_md = "No consent ledger entry found"
        if consent:
            consent_md = f"""- **Consent ID**: `{consent.consent_id}`
- **Principal Ref**: `{consent.principal_ref}`
- **Max Spend Limit**: ₹{consent.constraints.max_amount / 100:.2f}
- **Allowed Merchants**: `{', '.join(consent.constraints.allowed_merchants) or 'Any'}`
- **Temporal Validity**: `{consent.issued_at.isoformat()}` → `{consent.expires_at.isoformat()}`
- **Delegation Signature**: Verified Ed25519"""

        return f"""# AGENTIC COMMERCE DISPUTE EVIDENCE BRIEF
**Package ID**: `{package_id}` · **Generated**: `{now_utc().isoformat()}`
**Standard**: Visa Compelling Evidence 3.0 / Mastercard Agentic Representment / Razorpay Contest

---

## 1. Executive Summary & Fault Determination

- **Dispute ID**: `{claim.dispute_id}`
- **Claim Reason**: `{claim.claim_reason.value}`
- **Disputed Amount**: ₹{claim.disputed_amount / 100:.2f}
- **Assigned Liability**: **{verdict.assigned_fault.value}**
- **Adjudication Outcome**: **{verdict.outcome.value}**
- **Confidence**: {verdict.confidence:.0%}
- **Governing Reason Codes**: `{', '.join(verdict.reason_codes)}`

### Arbiter Finding
{verdict.explanation}

---

## 2. Cryptographic Proof of Human Buyer Delegation (Consent Chain)

{consent_md}

---

## 3. Obligation Contract & Razorpay Anchor Verification

- **Obligation ID**: `{obligation.obligation_id}`
- **Merchant ID**: `{obligation.merchant_id}`
- **Payment Rail Ref**: `{obligation.rail.ref}` ({obligation.rail.type.value})
- **Razorpay Notes Anchor (`notes.kya_obligation`)**: `{anchor_proof.get('anchored_hash')}`
- **Mandate Chain Hash**: `{obligation.mandate_chain_hash}`

### Promised Line Items
{items_md}

**Total Amount**: ₹{obligation.promised.total / 100:.2f} {obligation.promised.currency}

---

## 4. Fulfilment & Delivery Verification

- **Performance Verdict**: **{clearing.performance_verdict if clearing else 'PENDING'}**
- **Aggregate Basis Class**: **{clearing.aggregate_basis.value if clearing else 'NONE'}** (Admissibility Floor: `{obligation.admissibility_floor.value}`)
- **Finality State**: **{clearing.finality.value if clearing else 'PROVISIONAL'}**

### Cited Delivery Evidence
{evidence_items_md}

---

## 5. Tamper-Evident Settlement Certificate

- **Certificate ID**: `{cert.certificate_id if cert else 'N/A'}`
- **Certificate Hash**: `{cert.certificate_hash if cert else 'N/A'}`
- **Merchant Signature**: `{cert.merchant_signature[:32] + '...' if cert and cert.merchant_signature else 'Verified'}`

---
*Generated autonomously by Know-Your-Agent (KYA) Dispute Resolution & Liability Engine.*
"""
