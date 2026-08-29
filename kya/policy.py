"""Merchant policy and the trust ladder.

Policy is data, not code. Every decision records the policy version that
produced it, so a limit change is visible in the audit trail rather than
silently rewriting history.

The tier ladder is the answer to the cold-start problem: a brand-new agent
transacts immediately, bounded, rather than being refused for lacking a track
record it cannot acquire without transacting. That is also why a false positive
here is usually a *bounded* sale rather than a lost one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kya.enums import Tier
from kya.evidence import EvidenceClass

RUPEE = 100  # paise


@dataclass(frozen=True, slots=True)
class TierPolicy:
    tier: Tier
    spend_cap: int  # paise, rolling window
    velocity_per_hour: int
    evidence_floor: EvidenceClass
    step_up_above: int  # paise


#: The ladder from docs/04. Promotion is earned through cleared obligations;
#: disputes and basis drift demote.
TIERS: dict[Tier, TierPolicy] = {
    Tier.T0: TierPolicy(Tier.T0, 2_000 * RUPEE, 3, EvidenceClass.REC, 1_000 * RUPEE),
    Tier.T1: TierPolicy(Tier.T1, 10_000 * RUPEE, 20, EvidenceClass.REC, 5_000 * RUPEE),
    Tier.T2: TierPolicy(Tier.T2, 50_000 * RUPEE, 100, EvidenceClass.SIGN, 25_000 * RUPEE),
    Tier.T3: TierPolicy(Tier.T3, 200_000 * RUPEE, 500, EvidenceClass.SIGN, 100_000 * RUPEE),
}


@dataclass(frozen=True, slots=True)
class RefundBreakerPolicy:
    """Circuit breaker against the bot-farm refund flood Unit 42 documented."""

    window_seconds: int = 3600
    min_orders: int = 5  # below this the ratio is not meaningful
    max_refund_ratio: float = 0.35
    max_refund_value_ratio: float = 0.50


@dataclass(frozen=True, slots=True)
class Policy:
    version: str = "v1"
    merchant_id: str = "merch_sandbox_01"

    clock_skew_seconds: int = 300
    nonce_ttl_seconds: int = 600
    appeal_window_seconds: int = 900

    #: Absolute ceiling regardless of tier.
    hard_max_amount: int = 500_000 * RUPEE

    #: Callback and webhook hosts permitted per agent. Anything else is a
    #: counterfeit-callback attempt as far as G5 is concerned.
    registered_callback_domains: dict[str, list[str]] = field(default_factory=dict)

    refund_breaker: RefundBreakerPolicy = field(default_factory=RefundBreakerPolicy)

    def tier_policy(self, tier: Tier) -> TierPolicy:
        return TIERS[tier]


def default_policy() -> Policy:
    return Policy()
