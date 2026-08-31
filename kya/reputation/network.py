"""Cross-Merchant Agent Reputation Network & Credit Score.

Extends single-merchant ClearingPassports into a decentralized, cross-merchant
reputation graph — providing the "Agent Credit Score" for agentic commerce.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from kya.canonical import now_utc
from kya.enums import Tier
from kya.schemas import AgentReputationScore, ClearingPassport


class ReputationNetwork:
    """Decentralized cross-merchant agent reputation and scoring engine."""

    def __init__(self, clock: Callable[[], datetime] = now_utc) -> None:
        self._clock = clock
        self._merchant_passports: dict[str, dict[str, ClearingPassport]] = {}  # merchant_id -> agent_id -> passport

    def record_merchant_passport(
        self,
        merchant_id: str,
        passport: ClearingPassport,
    ) -> None:
        """Ingest a merchant-specific passport into the network graph."""
        if merchant_id not in self._merchant_passports:
            self._merchant_passports[merchant_id] = {}
        self._merchant_passports[merchant_id][passport.agent_id] = passport.model_copy(deep=True)

    def calculate_reputation(self, agent_id: str) -> AgentReputationScore:
        """Compute the global Agent Credit Score (0-1000) and risk band."""
        merchant_records: list[ClearingPassport] = []
        for m_id, agents in self._merchant_passports.items():
            if agent_id in agents:
                merchant_records.append(agents[agent_id])

        if not merchant_records:
            return AgentReputationScore(
                agent_id=agent_id,
                credit_score=500,
                risk_band="MODERATE_RISK",
                network_cleared_volume=0,
                cross_merchant_cleared_count=0,
                cross_merchant_dispute_rate=0.0,
                distinct_merchants_count=0,
                reputation_tier=Tier.T0,
                attestations_count=0,
                calculated_at=self._clock(),
            )

        total_cleared = sum(p.cleared_count for p in merchant_records)
        total_disputed = sum(p.disputed_count for p in merchant_records)
        total_volume = sum(p.total_cleared_value for p in merchant_records)
        total_basis_drift = sum(p.basis_drift_events for p in merchant_records)
        distinct_merchants = len(merchant_records)

        dispute_rate = (
            total_disputed / (total_cleared + total_disputed)
            if (total_cleared + total_disputed) > 0
            else 0.0
        )

        # Credit Scoring Algorithm (0 - 1000)
        # Base: 500
        score = 500

        # Cleared volume & transaction count bonus (up to +300)
        tx_bonus = min(200, total_cleared * 5)
        vol_bonus = min(100, int(total_volume / 10000))
        score += tx_bonus + vol_bonus

        # Merchant diversity bonus (up to +100)
        merchant_bonus = min(100, distinct_merchants * 20)
        score += merchant_bonus

        # Dispute rate penalty (up to -400)
        dispute_penalty = int(dispute_rate * 400)
        score -= dispute_penalty

        # Basis drift hard penalty (-150 per event)
        drift_penalty = total_basis_drift * 150
        score -= drift_penalty

        # Clamp between 0 and 1000
        score = max(0, min(1000, score))

        # Risk band & tier assignment
        if score >= 800 and dispute_rate <= 0.02 and total_basis_drift == 0:
            risk_band = "LOW_RISK"
            tier = Tier.T3 if total_cleared >= 50 else Tier.T2
        elif score >= 650 and dispute_rate <= 0.05:
            risk_band = "LOW_RISK"
            tier = Tier.T2 if total_cleared >= 15 else Tier.T1
        elif score >= 450:
            risk_band = "MODERATE_RISK"
            tier = Tier.T1 if total_cleared >= 1 else Tier.T0
        elif score >= 300:
            risk_band = "ELEVATED_RISK"
            tier = Tier.T0
        else:
            risk_band = "HIGH_RISK"
            tier = Tier.T0

        return AgentReputationScore(
            agent_id=agent_id,
            credit_score=score,
            risk_band=risk_band,
            network_cleared_volume=total_volume,
            cross_merchant_cleared_count=total_cleared,
            cross_merchant_dispute_rate=dispute_rate,
            distinct_merchants_count=distinct_merchants,
            reputation_tier=tier,
            attestations_count=distinct_merchants,
            calculated_at=self._clock(),
        )
