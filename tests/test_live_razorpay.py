"""Live test-mode Razorpay. Skipped unless real ``rzp_test_`` keys are present.

These are the tests that prove claims the rest of the suite can only
demonstrate. Everywhere else, a receipt hash is checked against an order object
we constructed ourselves. Here the order is fetched back from Razorpay, so the
hash is matched against a record held by someone else — which is the entire
point of anchoring — and the reconciler's recovery path runs against the real
API rather than a fake that agrees with our assumptions.

That last distinction earned its keep. The list endpoint turned out to be
eventually consistent, which no test against the fake would ever have shown.

Run with credentials in ``.env``::

    pytest tests/test_live_razorpay.py -m live -v -s

Each run creates one ₹100 test-mode order. No real money is involved, and
``LiveRazorpayClient`` refuses to construct against a non-test key.
"""

from __future__ import annotations

import time

import pytest

from kya.config import Settings
from kya.gateway import Gateway
from kya.obligation import verify_anchor
from kya.obligation.ledger import ObligationLedger
from kya.obligation.receipt import ReceiptMinter
from kya.rails.razorpay_client import LiveRazorpayClient
from kya.reconcile import ORDER_RECOVERED, Reconciler
from kya.simulation import (
    AgentIdentity,
    Principal,
    Sandbox,
    build_signed_request,
    make_cart,
    make_mandates,
)

pytestmark = pytest.mark.live

#: Long enough to cover the observed 10-20s list-endpoint lag with headroom,
#: short enough that a genuine failure does not hang the suite.
PROPAGATION_TIMEOUT_SECONDS = 90
POLL_INTERVAL_SECONDS = 5


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


@pytest.fixture(scope="module")
def live_order(live_gateway):
    """One real order, created once and shared.

    Module-scoped because the list endpoint has to catch up before it can be
    looked up by reference, and paying that wait per-test would be the bulk of
    the suite's runtime for no extra coverage.
    """
    gateway, ledger, rail, agent, principal = live_gateway

    cart = make_cart(items=[("SKU-KYA-DEMO", "KYA demo item", 1, 100_00)])
    result = gateway.create_order(
        build_signed_request(agent, make_mandates(agent, principal, cart), cart)
    )
    assert result.allowed
    assert result.order is not None, result.rail_error
    return result


def wait_until_listable(rail, receipt_ref: str) -> dict:
    """Poll until the order appears in the list endpoint.

    Razorpay's order list is eventually consistent — a new order is not
    findable by ``receipt`` for roughly 10-20 seconds, though fetching it by id
    works immediately. The reconciler handles this by refusing to conclude
    anything from an early miss; this helper simply waits it out so the *found*
    path can be tested.
    """
    deadline = time.monotonic() + PROPAGATION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        found = rail.order_by_receipt(receipt_ref)
        if found is not None:
            return found
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(
        f"order {receipt_ref} never became listable within "
        f"{PROPAGATION_TIMEOUT_SECONDS}s — the reconciler's recovery path "
        "depends on this lookup working"
    )


class RecordingRail:
    """Delegates to the live client and records which operations were called.

    A wrapper rather than instrumentation inside the client: recording exists
    for one assertion, and production code should not carry scaffolding only a
    test reads.
    """

    MUTATING = {"create_order", "refund"}

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def recorded(*args, **kwargs):
            self.calls.append(name)
            return attr(*args, **kwargs)

        return recorded

    @property
    def mutating_calls(self) -> list[str]:
        return [c for c in self.calls if c in self.MUTATING]


def test_a_real_test_mode_order_carries_a_verifiable_obligation_hash(
    live_gateway, live_order
):
    """The anchoring claim, against a record we do not control."""
    _, ledger, rail, _, _ = live_gateway

    # Re-fetch rather than trust the create response. What is being tested is
    # that the note is durably on *their* record.
    fetched = rail.fetch_order(live_order.order["id"])

    assert fetched["notes"]["kya_obligation"] == live_order.obligation.self_hash
    assert fetched["notes"]["kya_version"] == "1"
    assert fetched["receipt"] == live_order.obligation.rail.ref
    assert fetched["amount"] == live_order.obligation.promised.total

    # The reviewer's path: recompute the hash from the receipt alone.
    check = verify_anchor(
        ledger.original(live_order.obligation.obligation_id), fetched
    )
    assert check.ok, check.reason

    print()
    print(f"  Razorpay order : {fetched['id']}")
    print(f"  anchored hash  : {fetched['notes']['kya_obligation']}")
    print(f"  verify         : {check.summary()}")


def test_an_order_is_findable_by_our_own_reference(live_gateway, live_order):
    """The reconciler's whole recovery path rests on this API behaving as
    assumed, and it is the one assumption a fake cannot check.

    The dangerous failure is not an error. It is Razorpay ignoring the filter
    and returning the most recent order regardless, which would have the
    reconciler bind a capture to somebody else's obligation — so the negative
    case is asserted too.
    """
    _, _, rail, _, _ = live_gateway

    found = wait_until_listable(rail, live_order.obligation.rail.ref)
    assert found["id"] == live_order.order["id"]

    assert rail.order_by_receipt("kya_obl_definitely_not_a_real_ref") is None


def test_a_lost_response_is_recovered_against_live_razorpay(
    live_gateway, live_order
):
    """Graceful failure #1, against the real rail.

    The order is live at Razorpay and the local binding is removed, which is
    exactly the state a lost response leaves: obligation open, order real, its
    id unknown to us. Recovery has to start from the reference we chose before
    the call went out.

    The capture branch is not exercised here — a test-mode payment needs a
    human through checkout — so this proves recovery of the *order*. Binding an
    existing capture and preventing the duplicate charge is covered against the
    fake rail in ``test_reconcile.py``, where payments can be driven directly.
    """
    _, ledger, live_rail, _, _ = live_gateway

    obligation_id = live_order.obligation.obligation_id
    real_order_id = live_order.order["id"]
    wait_until_listable(live_rail, live_order.obligation.rail.ref)

    # Forget the order id, keep the obligation. The lost-response state.
    ledger._conn.execute(
        "DELETE FROM rail_bindings WHERE obligation_id = ?", (obligation_id,)
    )
    ledger._conn.commit()
    assert ledger.rail_id_for(obligation_id) is None

    recording = RecordingRail(live_rail)
    outcome = Reconciler(ledger, recording).reconcile(obligation_id)

    assert outcome.action == ORDER_RECOVERED
    assert outcome.order_id == real_order_id
    assert ledger.rail_id_for(obligation_id) == real_order_id

    # Nothing was created, nothing was retried.
    assert recording.mutating_calls == []
    assert ledger.verify().ok

    print()
    print(f"  recovered      : {real_order_id}")
    print(f"  found via      : {live_order.obligation.rail.ref}")
    print(f"  rail calls     : {recording.calls}")


def test_the_live_client_refuses_a_non_test_key(settings):
    from kya.rails.razorpay_client import RailError

    with pytest.raises(RailError, match="test mode"):
        LiveRazorpayClient("rzp_live_something", "secret")
