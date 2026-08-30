"""Razorpay webhook intake.

Webhooks are the only channel through which the outside world tells the gateway
that money moved, which makes them the inbound path an attacker would most like
to forge. A counterfeit ``payment.captured`` would have the gateway record a
payment nobody made.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from kya.enums import ObligationState
from kya.rails.webhooks import (
    SIGNATURE_HEADER,
    WebhookReceiver,
    WebhookRejected,
    verify_webhook_signature,
)
from kya.reconcile import Reconciler, install_webhook_handlers
from kya.simulation import build_signed_request, make_cart, make_mandates

SECRET = "whsec_sandbox_secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def delivery(event: str, payload: dict, event_id: str = "evt_1"):
    body = json.dumps({"event": event, "payload": payload}).encode()
    return body, {SIGNATURE_HEADER: sign(body), "X-Razorpay-Event-Id": event_id}


def capture_payload(order_id: str, payment_id: str = "pay_1", amount: int = 5_499_00):
    return {
        "payment": {
            "entity": {
                "id": payment_id,
                "order_id": order_id,
                "amount": amount,
                "status": "captured",
            }
        }
    }


@pytest.fixture
def receiver():
    return WebhookReceiver(secret=SECRET)


class TestSignatureVerification:
    def test_a_correctly_signed_body_verifies(self):
        body = b'{"event":"payment.captured"}'
        assert verify_webhook_signature(body, sign(body), SECRET)

    def test_a_body_altered_after_signing_does_not_verify(self):
        body = b'{"event":"payment.captured","amount":100}'
        signature = sign(body)
        tampered = body.replace(b"100", b"999")

        assert not verify_webhook_signature(tampered, signature, SECRET)

    def test_the_wrong_secret_does_not_verify(self):
        body = b'{"event":"payment.captured"}'
        assert not verify_webhook_signature(body, sign(body, "other"), SECRET)

    @pytest.mark.parametrize("signature", ["", "   ", "not-hex", "0" * 64])
    def test_missing_or_junk_signatures_are_refused(self, signature):
        assert not verify_webhook_signature(b"{}", signature, SECRET)

    def test_verification_returns_rather_than_raises(self):
        """Matching ``kya.crypto``: the caller has to tell 'forged' apart from
        'could not check', and an exception collapses the two."""
        assert verify_webhook_signature(b"{}", "bad", SECRET) is False


class TestReceiver:
    def test_a_forged_delivery_is_rejected(self, receiver):
        body = json.dumps({"event": "payment.captured"}).encode()

        with pytest.raises(WebhookRejected, match="signature"):
            receiver.receive(body, {SIGNATURE_HEADER: "deadbeef"})

    def test_a_forged_delivery_never_reaches_a_handler(self, receiver):
        """Verification comes before dispatch, not alongside it."""
        seen = []
        receiver.on("payment.captured", seen.append)
        body = json.dumps({"event": "payment.captured"}).encode()

        with pytest.raises(WebhookRejected):
            receiver.receive(body, {SIGNATURE_HEADER: "deadbeef"})
        assert seen == []

    def test_a_valid_delivery_is_dispatched(self, receiver):
        seen = []
        receiver.on("payment.captured", seen.append)
        body, headers = delivery("payment.captured", capture_payload("order_9"))

        event = receiver.receive(body, headers)

        assert event.event == "payment.captured"
        assert event.order_id == "order_9"
        assert event.payment_id == "pay_1"
        assert event.amount == 5_499_00
        assert len(seen) == 1

    def test_a_redelivered_event_is_processed_once(self, receiver):
        """Razorpay retries by design. A handler that credits an obligation
        twice on a duplicate delivery has re-created the double-charge bug on
        the inbound side."""
        seen = []
        receiver.on("payment.captured", seen.append)
        body, headers = delivery("payment.captured", capture_payload("order_9"))

        first = receiver.receive(body, headers)
        repeats = [receiver.receive(body, headers) for _ in range(4)]

        assert first is not None
        assert all(r is None for r in repeats)
        assert len(seen) == 1

    def test_deduplication_falls_back_to_the_body_digest(self, receiver):
        seen = []
        receiver.on("payment.captured", seen.append)
        body, headers = delivery("payment.captured", capture_payload("order_9"))
        headers.pop("X-Razorpay-Event-Id")

        receiver.receive(body, headers)
        receiver.receive(body, headers)

        assert len(seen) == 1

    def test_header_casing_is_not_relied_on(self, receiver):
        body, _ = delivery("payment.captured", capture_payload("order_9"))
        event = receiver.receive(
            body, {"X-RaZoRpAy-SiGnAtUrE": sign(body), "x-razorpay-event-id": "e1"}
        )
        assert event is not None

    def test_an_unparseable_body_is_rejected(self, receiver):
        body = b"not json at all"
        with pytest.raises(WebhookRejected, match="JSON"):
            receiver.receive(body, {SIGNATURE_HEADER: sign(body)})

    def test_an_unhandled_event_is_accepted_and_ignored(self, receiver):
        """An unrecognised event is not an error. Treating it as one would make
        every Razorpay feature launch look like an outage."""
        body, headers = delivery("payment.dispute.created", {})
        event = receiver.receive(body, headers)
        assert event is not None

    def test_one_failing_handler_does_not_break_the_others(self, receiver):
        """Razorpay retries anything it does not get a success for. A single
        broken consumer must not turn every delivery into a retry storm for the
        healthy ones."""
        healthy = []
        receiver.on("payment.captured", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
        receiver.on("payment.captured", healthy.append)
        body, headers = delivery("payment.captured", capture_payload("order_9"))

        event = receiver.receive(body, headers)

        assert event is not None
        assert len(healthy) == 1
        assert receiver.handler_errors and "boom" in receiver.handler_errors[0][1]


class TestCaptureBinding:
    """The webhook wired to the ledger — the push half of the reconciler."""

    @pytest.fixture
    def wired(self, sandbox, receiver):
        reconciler = Reconciler(sandbox.ledger, sandbox.rail, clock=sandbox.clock)
        install_webhook_handlers(receiver, reconciler)
        return reconciler

    def test_a_capture_event_binds_the_payment_to_the_obligation(
        self, sandbox, gateway, ledger, rail, receiver, wired, agent, principal
    ):
        cart = make_cart()
        result = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )
        payment = rail.pay(result.order["id"])

        body, headers = delivery(
            "payment.captured",
            capture_payload(result.order["id"], payment["id"], cart.total),
        )
        receiver.receive(body, headers)

        current = ledger.current(result.obligation.obligation_id)
        assert current.amount_due == 0
        # Paid is not delivered. The obligation stays open until clearing.
        assert current.state is ObligationState.OPEN

    def test_a_capture_for_an_order_we_never_learned_still_binds(
        self, gateway, ledger, rail, receiver, wired, agent, principal
    ):
        """The lost response, recovered from the push side instead of a poll.
        We hold no binding for this order id, so the handler asks the rail for
        the order and matches on the reference we chose ourselves."""
        rail.drop_responses = True
        cart = make_cart()
        result = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )
        rail.drop_responses = False

        order_id = next(iter(rail.orders))
        payment = rail.pay(order_id)
        assert ledger.rail_id_for(result.obligation.obligation_id) is None

        body, headers = delivery(
            "payment.captured", capture_payload(order_id, payment["id"], cart.total)
        )
        receiver.receive(body, headers)

        assert ledger.rail_id_for(result.obligation.obligation_id) == order_id
        assert ledger.current(result.obligation.obligation_id).amount_due == 0

    def test_a_duplicate_capture_delivery_amends_once(
        self, gateway, ledger, rail, receiver, wired, agent, principal
    ):
        cart = make_cart()
        result = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )
        payment = rail.pay(result.order["id"])

        for event_id in ("evt_a", "evt_b"):
            body, headers = delivery(
                "payment.captured",
                capture_payload(result.order["id"], payment["id"], cart.total),
                event_id=event_id,
            )
            receiver.receive(body, headers)

        # Two distinct event ids, so deduplication does not apply — the
        # *binding rule* has to be idempotent on its own.
        assert len(ledger.history(result.obligation.obligation_id)) == 2
        assert ledger.verify().ok

    def test_a_capture_for_an_unknown_order_is_ignored_quietly(
        self, receiver, wired, ledger
    ):
        body, headers = delivery("payment.captured", capture_payload("order_unknown"))
        assert receiver.receive(body, headers) is not None
        assert len(ledger) == 0
