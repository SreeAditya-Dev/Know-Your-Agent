"""Tests for Cross-Merchant Agent Reputation Network & Credit Score."""

import pytest

from kya.enums import Tier
from kya.reputation.network import ReputationNetwork
from kya.schemas import ClearingPassport


def test_reputation_scoring_high_trust() -> None:
    network = ReputationNetwork()

    # Add passports from 3 different merchants
    network.record_merchant_passport(
        "merchant_1",
        ClearingPassport(
            agent_id="agent_pro_shopper",
            tier=Tier.T3,
            cleared_count=30,
            disputed_count=0,
            basis_drift_events=0,
            total_cleared_value=200_000_00,
        ),
    )
    network.record_merchant_passport(
        "merchant_2",
        ClearingPassport(
            agent_id="agent_pro_shopper",
            tier=Tier.T2,
            cleared_count=25,
            disputed_count=0,
            basis_drift_events=0,
            total_cleared_value=150_000_00,
        ),
    )
    network.record_merchant_passport(
        "merchant_3",
        ClearingPassport(
            agent_id="agent_pro_shopper",
            tier=Tier.T2,
            cleared_count=15,
            disputed_count=0,
            basis_drift_events=0,
            total_cleared_value=80_000_00,
        ),
    )

    score = network.calculate_reputation("agent_pro_shopper")
    assert score.credit_score >= 800
    assert score.risk_band == "LOW_RISK"
    assert score.cross_merchant_cleared_count == 70
    assert score.distinct_merchants_count == 3
    assert score.cross_merchant_dispute_rate == 0.0


def test_reputation_scoring_high_dispute_penalized() -> None:
    network = ReputationNetwork()
    network.record_merchant_passport(
        "merchant_1",
        ClearingPassport(
            agent_id="agent_bad_actor",
            tier=Tier.T0,
            cleared_count=5,
            disputed_count=15,
            basis_drift_events=2,
            total_cleared_value=30_000_00,
        ),
    )

    score = network.calculate_reputation("agent_bad_actor")
    assert score.credit_score < 400
    assert score.risk_band in ("ELEVATED_RISK", "HIGH_RISK")
    assert score.cross_merchant_dispute_rate > 0.5
