"""Extensive edge case and scenario tests for Consent, Liability Arbiter, Representment, Cross-Rail, and Reputation."""

from datetime import datetime, timedelta, timezone

import pytest

from kya.canonical import now_utc
from kya.clearing.evidence import envelope as evidence_envelope
from kya.clearing.evidence import from_agent, from_rail
from kya.clearing.service import ClearingService
from kya.disputes.arbiter import LiabilityArbiter
from kya.disputes.consent import ConsentLedger, create_consent_record
from kya.disputes.representment import (
    RepresentmentGenerator,
    create_settlement_certificate,
)
from kya.enums import (
    DisputeClaimReason,
    DisputeParty,
    Finality,
    LiabilityOutcome,
    Tier,
)
from kya.evidence import EvidenceClass
from kya.obligation.receipt import (
    CLAIM_AMOUNT_CHARGED,
    CLAIM_DELIVERED_AT,
    CLAIM_DELIVERED_SKUS,
)
from kya.rails.cross_rail import CrossRailAdapter
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
from kya.reputation.network import ReputationNetwork
from kya.schemas import (
    Cart,
    ClearingPassport,
    DisputeClaim,
    IntentConstraints,
    IntentMandate,
    MandateBundle,
)
from kya.simulation import (
    AgentIdentity,
    Principal,
    build_signed_request,
    make_cart,
    make_mandates,
    standard_sandbox,
)


# --- Consent Ledger Edge Cases -----------------------------------------------


def test_consent_exact_boundary_amount() -> None:
    sandbox, agent, principal = standard_sandbox()
    cart_exact = make_cart(items=[("SKU-1", "Item", 1, 5_000_00)])
    mandates = make_mandates(agent, principal, cart_exact, max_amount=5_000_00)

    ledger = ConsentLedger()
    record = ledger.record(mandates)

    # 1. Exact amount -> Valid
    valid, reasons = ledger.verify_consent(record, charged_cart=cart_exact)
    assert valid is True
    assert L001.code in reasons

    # 2. Exact amount + 1 paisa -> Invalid
    cart_over = make_cart(items=[("SKU-1", "Item", 1, 5_000_01)])
    valid_over, violations = ledger.verify_consent(record, charged_cart=cart_over)
    assert valid_over is False
    assert any("exceeds max_amount" in v for v in violations)


def test_consent_category_and_merchant_filtering() -> None:
    sandbox, agent, principal = standard_sandbox()
    cart = make_cart(items=[("SKU-1", "Item", 1, 2_000_00)])
    cart.category = "electronics"

    mandates = make_mandates(
        agent,
        principal,
        cart,
        max_amount=10_000_00,
        allowed_merchants=["merchant_whitelisted_1", "merchant_whitelisted_2"],
    )
    mandates.intent.constraints.allowed_categories = ["electronics", "gadgets"]

    ledger = ConsentLedger()
    record = ledger.record(mandates)

    # Allowed merchant & category
    cart.merchant_id = "merchant_whitelisted_1"
    valid, _ = ledger.verify_consent(record, charged_cart=cart)
    assert valid is True

    # Unallowed merchant
    cart_bad_m = cart.model_copy(deep=True)
    cart_bad_m.merchant_id = "merchant_unauthorized"
    valid_m, violations_m = ledger.verify_consent(record, charged_cart=cart_bad_m)
    assert valid_m is False
    assert any("not in allowed_merchants" in v for v in violations_m)

    # Unallowed category
    cart_bad_c = cart.model_copy(deep=True)
    cart_bad_c.category = "apparel"
    valid_c, violations_c = ledger.verify_consent(record, charged_cart=cart_bad_c)
    assert valid_c is False
    assert any("not in allowed_categories" in v for v in violations_c)


def test_consent_tamper_detection() -> None:
    sandbox, agent, principal = standard_sandbox()
    cart = make_cart(items=[("SKU-1", "Item", 1, 2_000_00)])
    mandates = make_mandates(agent, principal, cart, max_amount=10_000_00)

    ledger = ConsentLedger()
    record = ledger.record(mandates)

    # Tamper with constraints without re-computing hash
    tampered = record.model_copy(deep=True)
    tampered.constraints.max_amount = 999_999_00

    valid, violations = ledger.verify_consent(tampered, charged_cart=cart)
    assert valid is False
    assert any("hash mismatch" in v for v in violations)


# --- Liability Arbiter Edge Cases --------------------------------------------


def test_arbiter_sub_admissibility_evidence_merchant_liable() -> None:
    """When merchant evidence is below the required admissibility floor, merchant is liable."""
    sandbox, agent, principal = standard_sandbox()
    cart = make_cart(items=[("SKU-1", "Headphones", 1, 5_000_00)])
    mandates = make_mandates(agent, principal, cart, max_amount=10_000_00)
    consent = create_consent_record(mandates)

    result = sandbox.gateway().create_order(
        build_signed_request(agent, mandates, cart)
    )
    assert result.obligation is not None
    obligation = result.obligation

    # Merchant submits SELF-class evidence (e.g. unverified merchant self note)
    evidence = evidence_envelope(
        obligation.self_hash,
        [
            from_agent(
                "self_assertion_1",
                CLAIM_DELIVERED_SKUS,
                "SKU-1",
                source="merchant_internal_note",
            ),
            from_rail(
                "rec_amt_1",
                CLAIM_AMOUNT_CHARGED,
                obligation.promised.total,
                source="razorpay",
            ),
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

    claim = DisputeClaim(
        dispute_id="dsp_sub_floor_1",
        obligation_id=obligation.obligation_id,
        claimant=DisputeParty.BUYER_PRINCIPAL,
        claim_reason=DisputeClaimReason.MERCHANDISE_NOT_RECEIVED,
        disputed_amount=5_000_00,
    )

    arbiter = LiabilityArbiter()
    verdict = arbiter.arbitrate(
        claim=claim,
        obligation=obligation,
        consent=consent,
        evidence=evidence,
        clearing=clearing_res.decision,
        finality=clearing_res.finality,
    )

    assert verdict.assigned_fault is DisputeParty.MERCHANT
    assert verdict.outcome is LiabilityOutcome.REFUND_ISSUED
    assert L004.code in verdict.reason_codes


def test_arbiter_contributory_split_liability() -> None:
    """When both agent breached intent bounds AND merchant failed delivery, liability is split."""
    sandbox, agent, principal = standard_sandbox()
    cart_order = make_cart(items=[("SKU-1", "Laptop", 1, 80_000_00)])
    mandates_order = make_mandates(agent, principal, cart_order, max_amount=80_000_00)
    result = sandbox.gateway().create_order(
        build_signed_request(agent, mandates_order, cart_order)
    )
    assert result.obligation is not None
    obligation = result.obligation

    # 1. Agent breached human's actual intent constraint (human set 20,000 max)
    mandates_user = make_mandates(agent, principal, make_cart(items=[("SKU-0", "Bag", 1, 10_000_00)]), max_amount=20_000_00)
    consent = create_consent_record(mandates_user)

    # 2. Merchant also failed delivery (delivered wrong SKU)
    evidence = evidence_envelope(
        obligation.self_hash,
        [
            from_rail("rec_amt_1", CLAIM_AMOUNT_CHARGED, obligation.promised.total, source="razorpay"),
            from_rail("rec_del_wrong", CLAIM_DELIVERED_SKUS, "SKU-WRONG-ITEM", source="courier"),
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

    claim = DisputeClaim(
        dispute_id="dsp_split_1",
        obligation_id=obligation.obligation_id,
        claimant=DisputeParty.BUYER_PRINCIPAL,
        claim_reason=DisputeClaimReason.UNAUTHORIZED_AGENT_SPEND,
        disputed_amount=80_000_00,
    )

    arbiter = LiabilityArbiter()
    verdict = arbiter.arbitrate(
        claim=claim,
        obligation=obligation,
        consent=consent,
        evidence=evidence,
        clearing=clearing_res.decision,
        finality=clearing_res.finality,
    )

    assert verdict.assigned_fault is DisputeParty.SPLIT
    assert verdict.outcome is LiabilityOutcome.SPLIT_LIABILITY
    assert verdict.fault_allocation.get(DisputeParty.AGENT_OPERATOR.value) == 0.5
    assert verdict.fault_allocation.get(DisputeParty.MERCHANT.value) == 0.5
    assert L008.code in verdict.reason_codes


def test_arbiter_no_prior_clearing_escalates_or_refunds() -> None:
    """When no clearing evidence exists at all, merchant cannot prove fulfilment."""
    sandbox, agent, principal = standard_sandbox()
    cart = make_cart(items=[("SKU-1", "Earbuds", 1, 3_000_00)])
    mandates = make_mandates(agent, principal, cart, max_amount=5_000_00)
    consent = create_consent_record(mandates)

    result = sandbox.gateway().create_order(
        build_signed_request(agent, mandates, cart)
    )
    assert result.obligation is not None
    obligation = result.obligation

    claim = DisputeClaim(
        dispute_id="dsp_no_evidence_1",
        obligation_id=obligation.obligation_id,
        claimant=DisputeParty.BUYER_PRINCIPAL,
        claim_reason=DisputeClaimReason.MERCHANDISE_NOT_RECEIVED,
        disputed_amount=3_000_00,
    )

    arbiter = LiabilityArbiter()
    # No clearing decision provided
    verdict = arbiter.arbitrate(
        claim=claim,
        obligation=obligation,
        consent=consent,
        clearing=None,
    )

    assert verdict.assigned_fault is DisputeParty.MERCHANT
    assert verdict.outcome is LiabilityOutcome.REFUND_ISSUED


# --- Cross-Rail Token Edge Cases ----------------------------------------------


def test_cross_rail_expired_and_zero_amount() -> None:
    adapter = CrossRailAdapter()

    # Expired token
    token = adapter.parse_stripe_spt(
        token_id="spt_exp",
        agent_id="agent_1",
        principal_ref="user_1",
        amount=1000,
        expires_in_seconds=-10,
    )
    assert adapter.verify_token(token) is False

    # Zero amount
    token_zero = adapter.parse_mc_agentic_token(
        token_id="mc_zero",
        agent_id="agent_1",
        principal_ref="user_1",
        amount=0,
    )
    assert adapter.verify_token(token_zero) is False


# --- Reputation Network Edge Cases --------------------------------------------


def test_reputation_cold_start_agent() -> None:
    network = ReputationNetwork()
    score = network.calculate_reputation("agent_never_seen_before")

    assert score.credit_score == 500
    assert score.risk_band == "MODERATE_RISK"
    assert score.reputation_tier is Tier.T0
    assert score.cross_merchant_cleared_count == 0
    assert score.distinct_merchants_count == 0


def test_reputation_basis_drift_penalty() -> None:
    network = ReputationNetwork()

    # Agent with 30 cleared orders but 2 basis drift violations
    network.record_merchant_passport(
        "merchant_1",
        ClearingPassport(
            agent_id="agent_drifter",
            tier=Tier.T0,
            cleared_count=30,
            disputed_count=0,
            basis_drift_events=2,  # -300 pts penalty
            total_cleared_value=200_000_00,
        ),
    )

    score = network.calculate_reputation("agent_drifter")
    # Base 500 + 150 (tx bonus) + 100 (vol) + 20 (merchant) - 300 (drift) = 470
    assert score.credit_score == 470
    assert score.risk_band == "MODERATE_RISK"
