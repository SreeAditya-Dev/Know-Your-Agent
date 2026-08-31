"""Tests for Cross-Rail Gateway Adapters."""

import pytest

from kya.rails.cross_rail import CrossRailAdapter


def test_cross_rail_stripe_spt() -> None:
    adapter = CrossRailAdapter()
    token = adapter.parse_stripe_spt(
        token_id="spt_test_9988",
        agent_id="agent_stripe_buyer",
        principal_ref="user_stripe_1",
        amount=4_500_00,
        currency="INR",
    )
    assert token.token_type == "stripe_spt"
    assert token.amount == 4_500_00
    assert token.issuer == "stripe_acp_gateway"
    assert adapter.verify_token(token) is True


def test_cross_rail_mc_agentic_token() -> None:
    adapter = CrossRailAdapter()
    token = adapter.parse_mc_agentic_token(
        token_id="mc_tok_112233",
        agent_id="agent_mc_buyer",
        principal_ref="user_mc_1",
        amount=12_000_00,
        currency="INR",
    )
    assert token.token_type == "mc_agentic_token"
    assert token.amount == 12_000_00
    assert token.issuer == "mastercard_agent_pay"
    assert adapter.verify_token(token) is True


def test_cross_rail_x402() -> None:
    adapter = CrossRailAdapter()
    token = adapter.parse_x402_header(
        auth_header="x402 payload_crypto_signature_base",
        agent_id="agent_crypto_buyer",
        principal_ref="user_base_eth_0x123",
        amount=25_00,
        currency="USDC",
    )
    assert token.token_type == "x402_usdc"
    assert token.currency == "USDC"
    assert token.issuer == "coinbase_x402_base"
    assert adapter.verify_token(token) is True
