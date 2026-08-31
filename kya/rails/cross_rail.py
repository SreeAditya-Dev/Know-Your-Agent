"""Cross-Rail Gateway Adapters.

Enables KYA to sit in front of multi-rail agentic commerce:
1. Stripe Shared Payment Tokens (SPT)
2. Mastercard Agentic Tokens (Agent Pay for Machines)
3. Coinbase x402 HTTP micropayments (USDC settlement)
4. Razorpay Test/Live Rail

Normalizes disparate rail tokens into KYA's unified Obligation & Mandate model.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from kya.canonical import digest, now_utc
from kya.schemas import CrossRailPaymentToken, RailRef, RailType


class CrossRailAdapter:
    """Unified multi-rail adapter translating external tokens into KYA obligations."""

    def __init__(self, clock: Callable[[], datetime] = now_utc) -> None:
        self._clock = clock

    def parse_stripe_spt(
        self,
        token_id: str,
        agent_id: str,
        principal_ref: str,
        amount: int,
        currency: str = "INR",
        expires_in_seconds: int = 3600,
        metadata: dict[str, Any] | None = None,
    ) -> CrossRailPaymentToken:
        """Parse and normalize a Stripe Shared Payment Token (SPT)."""
        now = self._clock()
        return CrossRailPaymentToken(
            token_type="stripe_spt",
            token_id=token_id,
            agent_id=agent_id,
            principal_ref=principal_ref,
            amount=amount,
            currency=currency,
            issuer="stripe_acp_gateway",
            expires_at=now + timedelta(seconds=expires_in_seconds),
            mandate_bound=True,
            metadata=metadata or {},
        )

    def parse_mc_agentic_token(
        self,
        token_id: str,
        agent_id: str,
        principal_ref: str,
        amount: int,
        currency: str = "INR",
        expires_in_seconds: int = 7200,
        metadata: dict[str, Any] | None = None,
    ) -> CrossRailPaymentToken:
        """Parse and normalize a Mastercard Agentic Token (Agent Pay)."""
        now = self._clock()
        return CrossRailPaymentToken(
            token_type="mc_agentic_token",
            token_id=token_id,
            agent_id=agent_id,
            principal_ref=principal_ref,
            amount=amount,
            currency=currency,
            issuer="mastercard_agent_pay",
            expires_at=now + timedelta(seconds=expires_in_seconds),
            mandate_bound=True,
            metadata=metadata or {},
        )

    def parse_x402_header(
        self,
        auth_header: str,
        agent_id: str,
        principal_ref: str,
        amount: int,
        currency: str = "USDC",
        metadata: dict[str, Any] | None = None,
    ) -> CrossRailPaymentToken:
        """Parse Coinbase x402 HTTP payment authorization."""
        now = self._clock()
        return CrossRailPaymentToken(
            token_type="x402_usdc",
            token_id=auth_header.replace("x402 ", "").strip() or f"x402_{uuid.uuid4().hex[:12]}",
            agent_id=agent_id,
            principal_ref=principal_ref,
            amount=amount,
            currency=currency,
            issuer="coinbase_x402_base",
            expires_at=now + timedelta(seconds=1800),
            mandate_bound=True,
            metadata=metadata or {"network": "base", "token": "USDC"},
        )

    def verify_token(self, token: CrossRailPaymentToken) -> bool:
        """Verify token expiration and structural integrity."""
        now = self._clock()
        if now > token.expires_at:
            return False
        if token.amount <= 0:
            return False
        return True
