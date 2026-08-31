"""Tests for Consent Ledger and delegation boundary verification."""

from datetime import timedelta

import pytest

from kya.canonical import now_utc
from kya.disputes.consent import ConsentLedger, create_consent_record
from kya.reasons import L001
from kya.simulation import (
    AgentIdentity,
    Principal,
    make_cart,
    make_mandates,
    standard_sandbox,
)


def test_consent_ledger_record_and_get() -> None:
    sandbox, agent, principal = standard_sandbox()
    cart = make_cart(items=[("SKU-BOOK", "Python Cookbook", 1, 1_200_00)])
    mandates = make_mandates(agent, principal, cart, max_amount=2_000_00)

    ledger = ConsentLedger()
    record = ledger.record(mandates, anchored_rail_ref="order_rzp_123")

    assert record.consent_id.startswith("cst_")
    assert record.principal_ref == principal.principal_ref
    assert record.agent_id == agent.agent_id
    assert record.constraints.max_amount == 2_000_00
    assert record.anchored_rail_ref == "order_rzp_123"

    by_id = ledger.get_by_id(record.consent_id)
    assert by_id is not None
    assert by_id.consent_id == record.consent_id

    by_chain = ledger.get_by_chain_hash(record.mandate_chain_hash)
    assert by_chain is not None
    assert by_chain.consent_id == record.consent_id


def test_verify_consent_in_bounds() -> None:
    sandbox, agent, principal = standard_sandbox()
    cart = make_cart(items=[("SKU-BOOK", "Python Cookbook", 1, 1_200_00)])
    mandates = make_mandates(agent, principal, cart, max_amount=2_000_00)

    ledger = ConsentLedger()
    record = ledger.record(mandates)

    is_valid, reasons = ledger.verify_consent(record, charged_cart=cart)
    assert is_valid is True
    assert L001.code in reasons


def test_verify_consent_exceeded_max_amount() -> None:
    sandbox, agent, principal = standard_sandbox()
    cart_small = make_cart(items=[("SKU-BOOK", "Python Cookbook", 1, 1_200_00)])
    mandates = make_mandates(agent, principal, cart_small, max_amount=2_000_00)

    ledger = ConsentLedger()
    record = ledger.record(mandates)

    cart_expensive = make_cart(items=[("SKU-EXPENSIVE", "Server Hardware", 1, 50_000_00)])
    is_valid, violations = ledger.verify_consent(record, charged_cart=cart_expensive)
    assert is_valid is False
    assert any("exceeds max_amount" in v for v in violations)


def test_verify_consent_expired() -> None:
    sandbox, agent, principal = standard_sandbox()
    cart = make_cart(items=[("SKU-BOOK", "Python Cookbook", 1, 1_200_00)])
    mandates = make_mandates(agent, principal, cart, max_amount=2_000_00)

    ledger = ConsentLedger()
    record = ledger.record(mandates)

    past = record.expires_at + timedelta(seconds=10)
    is_valid, violations = ledger.verify_consent(record, charged_cart=cart, at=past)
    assert is_valid is False
    assert any("expired" in v for v in violations)
