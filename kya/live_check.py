"""Runnable proof that the anchor works against real Razorpay test mode.

    python -m kya.live_check

Places one small test-mode order through the full gateway, fetches it back
from Razorpay, and re-derives the obligation hash from the receipt alone. It
prints the order id so the same record can be opened in the Razorpay dashboard
and checked by hand — which is the version of this that convinces a reviewer.

Falls back to the in-memory rail when no credentials are configured, and says
so loudly, so the output can never be mistaken for a live run.
"""

from __future__ import annotations

import sys

from kya.config import Settings
from kya.gateway import Gateway
from kya.obligation import verify_anchor
from kya.obligation.ledger import ObligationLedger
from kya.obligation.receipt import ReceiptMinter
from kya.rails.razorpay_client import FakeRazorpayClient, LiveRazorpayClient
from kya.simulation import (
    AgentIdentity,
    Principal,
    Sandbox,
    build_signed_request,
    make_cart,
    make_mandates,
)

DEMO_AMOUNT = 100_00  # ₹100, test mode


def build(settings: Settings):
    sandbox = Sandbox()
    agent = sandbox.register_agent(AgentIdentity.create("agent_shopper"))
    principal = sandbox.register_principal(Principal.create("user_alice"))

    if settings.has_razorpay_credentials:
        settings.require_test_mode()
        rail = LiveRazorpayClient(
            settings.razorpay_key_id, settings.razorpay_key_secret
        )
        mode = f"LIVE test-mode Razorpay ({settings.razorpay_key_id})"
    else:
        rail = FakeRazorpayClient()
        mode = "SIMULATED rail — no Razorpay credentials configured"

    ledger = ObligationLedger(sandbox.merchant)
    gateway = Gateway(
        pipeline=sandbox.pipeline,
        ledger=ledger,
        rail=rail,
        minter=ReceiptMinter(sandbox.merchant),
        context_factory=sandbox.context,
    )
    return gateway, ledger, rail, agent, principal, mode


def main() -> int:
    settings = Settings()
    gateway, ledger, rail, agent, principal, mode = build(settings)

    print(f"rail          : {mode}")
    if settings.merchant_key_is_ephemeral:
        print(
            "merchant key  : DERIVED FALLBACK — deterministic and not secret. "
            "Set KYA_MERCHANT_KEY_SEED for anything beyond a demo."
        )

    cart = make_cart(items=[("SKU-KYA-DEMO", "KYA demo item", 1, DEMO_AMOUNT)])
    result = gateway.create_order(
        build_signed_request(agent, make_mandates(agent, principal, cart), cart)
    )

    print(f"decision      : {result.envelope.decision.value}")
    if not result.allowed:
        print(f"reason codes  : {result.envelope.reason_codes}")
        return 1
    if result.order is None:
        print(f"rail error    : {result.rail_error}")
        print("obligation is open and reconcilable; run the reconciler.")
        return 1

    obligation = result.obligation
    print(f"obligation    : {obligation.obligation_id} v{obligation.version}")
    print(f"self_hash     : {obligation.self_hash}")
    print(f"order id      : {result.order['id']}")
    print(f"order receipt : {result.order['receipt']}")

    fetched = rail.fetch_order(result.order["id"])
    print(f"fetched notes : {fetched.get('notes')}")

    check = verify_anchor(ledger.original(obligation.obligation_id), fetched)
    print(f"anchor        : {check.summary()}")
    print(f"ledger chain  : {ledger.verify().summary()}")

    if settings.has_razorpay_credentials:
        print(
            "\nOpen this order in the Razorpay dashboard and read "
            "notes.kya_obligation — it should equal self_hash above."
        )

    return 0 if check.ok else 1


if __name__ == "__main__":
    sys.exit(main())
