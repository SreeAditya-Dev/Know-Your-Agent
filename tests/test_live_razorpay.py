"""Live test-mode Razorpay. Skipped unless real ``rzp_test_`` keys are present.

This is the one test that proves the anchoring claim rather than demonstrating
it. Everything else in the suite verifies a receipt hash against an order object
we constructed. Here the order is fetched back from Razorpay after the fact, so
the hash is matched against a record held by someone other than us — which is
the entire point of anchoring.

Run it with credentials in ``.env``::

    pytest tests/test_live_razorpay.py -v -m live

It creates a test-mode order for a small amount. No real money is involved, and
``LiveRazorpayClient`` refuses to construct against a non-test key.
"""

from __future__ import annotations

import pytest

from kya.config import Settings
from kya.gateway import Gateway
from kya.obligation import verify_anchor
from kya.obligation.ledger import ObligationLedger
from kya.obligation.receipt import ReceiptMinter
from kya.rails.razorpay_client import LiveRazorpayClient
from kya.simulation import (
    Sandbox,
    AgentIdentity,
    Principal,
    build_signed_request,
    make_cart,
    make_mandates,
)

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def settings() -> Settings:
    loaded = Settings()
    if not loaded.has_razorpay_credentials:
        pytest.skip("no RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET configured")
    loaded.require_test_mode()
    return loaded


@pytest.fixture(scope="module")
def live_gateway(settings):
    sandbox = Sandbox()
    agent = sandbox.register_agent(AgentIdentity.create("agent_shopper"))
    principal = sandbox.register_principal(Principal.create("user_alice"))

    rail = LiveRazorpayClient(settings.razorpay_key_id, settings.razorpay_key_secret)
    ledger = ObligationLedger(sandbox.merchant)
    gateway = Gateway(
        pipeline=sandbox.pipeline,
        ledger=ledger,
        rail=rail,
        minter=ReceiptMinter(sandbox.merchant),
        context_factory=sandbox.context,
    )
    return gateway, ledger, rail, agent, principal


def test_a_real_test_mode_order_carries_a_verifiable_obligation_hash(live_gateway):
    gateway, ledger, rail, agent, principal = live_gateway

    cart = make_cart(items=[("SKU-KYA-DEMO", "KYA demo item", 1, 100_00)])
    result = gateway.create_order(
        build_signed_request(agent, make_mandates(agent, principal, cart), cart)
    )

    assert result.allowed
    assert result.order is not None, result.rail_error

    # Re-fetch from Razorpay rather than trusting the create response. What is
    # being tested is that the note is durably on *their* record.
    fetched = rail.fetch_order(result.order["id"])

    assert fetched["notes"]["kya_obligation"] == result.obligation.self_hash
    assert fetched["receipt"] == result.obligation.rail.ref
    assert fetched["amount"] == cart.total

    # The reviewer's path: recompute the hash from the receipt alone.
    check = verify_anchor(ledger.original(result.obligation.obligation_id), fetched)
    assert check.ok, check.reason

    print(f"\n  Razorpay order : {fetched['id']}")
    print(f"  anchored hash  : {fetched['notes']['kya_obligation']}")
    print(f"  verify         : {check.summary()}")


def test_the_live_client_refuses_a_non_test_key(settings):
    from kya.rails.razorpay_client import RailError

    with pytest.raises(RailError, match="test mode"):
        LiveRazorpayClient("rzp_live_something", "secret")
