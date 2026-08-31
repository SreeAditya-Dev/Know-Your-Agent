"""Tests for Liability Arbiter and multi-party fault adjudication."""

from datetime import datetime, timedelta

import pytest

from kya.canonical import now_utc
from kya.clearing.evidence import envelope as evidence_envelope
from kya.clearing.evidence import from_rail
from kya.clearing.service import ClearingService
from kya.disputes.arbiter import LiabilityArbiter
from kya.disputes.consent import create_consent_record
from kya.enums import (
    DisputeClaimReason,
    DisputeParty,
    Finality,
    LiabilityOutcome,
    Tier,
)
from kya.evidence import EvidenceClass
from kya.obligation.receipt import CLAIM_AMOUNT_CHARGED, CLAIM_DELIVERED_SKUS
from kya.policy import Policy
from kya.reasons import L001, L003, L004, L005, L002, L006
from kya.schemas import (
    ClearingDecision,
    DisputeClaim,
    IntentConstraints,
    IntentMandate,
    MandateBundle,
)
from kya.simulation import (
    AgentIdentity,
    Principal,
    Sandbox,
    build_signed_request,
    make_cart,
    make_mandates,
    standard_sandbox,
)


def test_friendly_fraud_merchant_protected() -> None:
    sandbox, agent, principal = standard_sandbox()
    cart = make_cart(items=[("SKU-1", "Noise-cancelling headphones", 1, 5_000_00)])
    mandates = make_mandates(agent, principal, cart, max_amount=10_000_00)
    consent = create_consent_record(mandates)

    result = sandbox.gateway().create_order(
        build_signed_request(agent, mandates, cart)
    )
    assert result.obligation is not None
    obligation = result.obligation

    # Courier evidence delivered exact SKU + payment captured
    evidence = evidence_envelope(
        obligation.self_hash,
        [
            from_rail("rec_amt_1", CLAIM_AMOUNT_CHARGED, obligation.promised.total, source="razorpay"),
            from_rail("rec_del_1", CLAIM_DELIVERED_SKUS, "SKU-1", source="fedex"),
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
    clearing = clearing_service.submit(obligation.obligation_id, evidence, execute=False)

    claim = DisputeClaim(
        dispute_id="dsp_friendly_1",
        obligation_id=obligation.obligation_id,
        claimant=DisputeParty.BUYER_PRINCIPAL,
        claim_reason=DisputeClaimReason.UNAUTHORIZED_AGENT_SPEND,
        disputed_amount=obligation.promised.total,
        details="User claims they never authorized their agent to buy headphones.",
    )

    arbiter = LiabilityArbiter()
    verdict = arbiter.arbitrate(
        claim=claim,
        obligation=obligation,
        consent=consent,
        evidence=evidence,
        clearing=clearing.decision,
        finality=clearing.finality,
    )

    assert verdict.assigned_fault is DisputeParty.BUYER_PRINCIPAL
    assert verdict.outcome is LiabilityOutcome.MERCHANT_PROTECTED
    assert verdict.confidence >= 0.95
    assert L005.code in verdict.reason_codes
    assert L001.code in verdict.reason_codes
    assert L003.code in verdict.reason_codes


def test_merchant_delivery_failure() -> None:
    sandbox, agent, principal = standard_sandbox()
    cart = make_cart(items=[("SKU-1", "Noise-cancelling headphones", 1, 5_000_00)])
    mandates = make_mandates(agent, principal, cart, max_amount=10_000_00)
    consent = create_consent_record(mandates)

    result = sandbox.gateway().create_order(
        build_signed_request(agent, mandates, cart)
    )
    assert result.obligation is not None
    obligation = result.obligation

    # Merchant delivered wrong SKU
    evidence = evidence_envelope(
        obligation.self_hash,
        [from_rail("rec_del_wrong", CLAIM_DELIVERED_SKUS, "SKU-WRONG-ITEM", source="courier")],
    )

    clearing_service = ClearingService(
        ledger=sandbox.ledger,
        rail=sandbox.rail,
        blocks=sandbox.blocks,
        passports=sandbox.passport_store,
        policy=sandbox.policy,
        clock=sandbox.clock,
    )
    clearing = clearing_service.submit(obligation.obligation_id, evidence, execute=False)

    claim = DisputeClaim(
        dispute_id="dsp_merch_fail_1",
        obligation_id=obligation.obligation_id,
        claimant=DisputeParty.BUYER_PRINCIPAL,
        claim_reason=DisputeClaimReason.NOT_AS_DESCRIBED,
        disputed_amount=obligation.promised.total,
        details="Customer received wrong SKU.",
    )

    arbiter = LiabilityArbiter()
    verdict = arbiter.arbitrate(
        claim=claim,
        obligation=obligation,
        consent=consent,
        evidence=evidence,
        clearing=clearing.decision,
        finality=clearing.finality,
    )

    assert verdict.assigned_fault is DisputeParty.MERCHANT
    assert verdict.outcome is LiabilityOutcome.REFUND_ISSUED
    assert L004.code in verdict.reason_codes


def test_rogue_agent_constraint_breach() -> None:
    sandbox, agent, principal = standard_sandbox()
    cart_order = make_cart(items=[("SKU-LUX", "Luxury Watch", 1, 50_000_00)])
    mandates_order = make_mandates(agent, principal, cart_order, max_amount=50_000_00)
    result = sandbox.gateway().create_order(
        build_signed_request(agent, mandates_order, cart_order)
    )
    assert result.obligation is not None
    obligation = result.obligation

    # User's actual consent constraint was only 10,000 paise
    mandates_actual_consent = make_mandates(
        agent, principal, make_cart(items=[("SKU-STRAP", "Watch strap", 1, 5_000_00)]), max_amount=10_000_00
    )
    consent = create_consent_record(mandates_actual_consent)

    claim = DisputeClaim(
        dispute_id="dsp_rogue_1",
        obligation_id=obligation.obligation_id,
        claimant=DisputeParty.BUYER_PRINCIPAL,
        claim_reason=DisputeClaimReason.UNAUTHORIZED_AGENT_SPEND,
        disputed_amount=50_000_00,
        details="Agent exceeded max spend limit by 5x.",
    )

    arbiter = LiabilityArbiter()
    verdict = arbiter.arbitrate(
        claim=claim,
        obligation=obligation,
        consent=consent,
    )

    assert verdict.assigned_fault is DisputeParty.AGENT_OPERATOR
    assert verdict.outcome is LiabilityOutcome.AGENT_FAULT_ESCROW_CLAIM
    assert L002.code in verdict.reason_codes
    assert L006.code in verdict.reason_codes
