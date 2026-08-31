"""Tests for Dispute Representment Packages and Settlement Certificates."""

import pytest

from kya.canonical import now_utc
from kya.clearing.evidence import envelope as evidence_envelope
from kya.clearing.evidence import from_rail
from kya.clearing.service import ClearingService
from kya.disputes.consent import ConsentLedger
from kya.disputes.representment import (
    RepresentmentGenerator,
    create_settlement_certificate,
)
from kya.enums import DisputeClaimReason, DisputeParty, Finality, LiabilityOutcome
from kya.obligation.receipt import CLAIM_AMOUNT_CHARGED, CLAIM_DELIVERED_SKUS
from kya.schemas import DisputeClaim
from kya.simulation import (
    build_signed_request,
    make_cart,
    make_mandates,
    standard_sandbox,
)


def test_settlement_certificate_and_representment_package() -> None:
    sandbox, agent, principal = standard_sandbox()
    cart = make_cart(items=[("SKU-PRO-KEYBOARD", "Mechanical Keyboard", 1, 8_500_00)])
    mandates = make_mandates(agent, principal, cart, max_amount=15_000_00)

    consent_ledger = ConsentLedger()
    consent = consent_ledger.record(mandates, anchored_rail_ref="order_rzp_mock_1")

    gateway_res = sandbox.gateway().create_order(
        build_signed_request(agent, mandates, cart)
    )
    assert gateway_res.obligation is not None
    obligation = gateway_res.obligation

    # Submit evidence & clear obligation
    evidence = evidence_envelope(
        obligation.self_hash,
        [
            from_rail("rec_amt_kb", CLAIM_AMOUNT_CHARGED, obligation.promised.total, source="razorpay"),
            from_rail("rec_del_kb", CLAIM_DELIVERED_SKUS, "SKU-PRO-KEYBOARD", source="dhl_express"),
        ],
    )
    clearing_service = ClearingService(
        ledger=sandbox.ledger,
        rail=sandbox.rail,
        blocks=sandbox.blocks,
        passports=sandbox.passport_store,
        policy=sandbox.policy,
        clock=sandbox.clock,
    )
    clearing_res = clearing_service.submit(obligation.obligation_id, evidence, execute=False)

    # Mint certificate
    cert = create_settlement_certificate(
        obligation=obligation,
        clearing_decision=clearing_res.decision,
        merchant_key=sandbox.merchant,
        evidence=evidence,
    )
    assert cert.certificate_id.startswith("cert_")
    assert cert.performance_verdict == "SATISFIED"
    assert cert.certificate_hash != ""
    assert cert.merchant_signature != ""

    # Generate representment package
    generator = RepresentmentGenerator(
        consent_ledger=consent_ledger,
        obligation_ledger=sandbox.ledger,
        merchant_key=sandbox.merchant,
    )

    claim = DisputeClaim(
        dispute_id="dsp_test_rep_1",
        obligation_id=obligation.obligation_id,
        claimant=DisputeParty.BUYER_PRINCIPAL,
        claim_reason=DisputeClaimReason.UNAUTHORIZED_AGENT_SPEND,
        disputed_amount=8_500_00,
        details="Friendly fraud dispute claiming agent bought keyboard without permission.",
    )

    package = generator.generate_package(
        claim=claim,
        obligation=obligation,
        evidence=evidence,
        clearing_result=clearing_res,
        consent=consent,
    )

    assert package.package_id.startswith("pkg_")
    assert package.liability_verdict.outcome is LiabilityOutcome.MERCHANT_PROTECTED
    assert package.settlement_certificate is not None
    assert package.consent_record is not None
    assert "AGENTIC COMMERCE DISPUTE EVIDENCE BRIEF" in package.representment_brief_markdown
    assert "Visa Compelling Evidence 3.0" in package.representment_brief_markdown
    assert package.razorpay_anchor_proof["anchor_verified"] is True
