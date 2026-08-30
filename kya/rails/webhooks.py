"""Razorpay webhook intake.

Webhooks are the only path by which the outside world tells the gateway that
money moved. That makes them the one inbound channel an attacker would most
like to forge: a counterfeit ``payment.captured`` would have the gateway clear
an obligation nobody paid for.

So three rules, all enforced here rather than by the caller:

1. **Verify before parsing.** The signature covers the raw bytes. Parsing first
   and verifying a re-serialized body is the classic way to make a signature
   check meaningless, because the bytes you verify are no longer the bytes you
   received.
2. **Compare in constant time.** ``hmac.compare_digest``, not ``==``.
3. **Deduplicate.** Razorpay retries delivery, so the same event arrives more
   than once by design. A handler that credits an obligation twice on a
   duplicate delivery has re-created the double-charge bug on the inbound side.

The signature scheme is HMAC-SHA256 over the raw request body, keyed with the
webhook secret, hex-encoded in ``X-Razorpay-Signature``. This module implements
it directly rather than calling the SDK's helper so that verification works
without SDK credentials and can be tested against known vectors.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from kya.canonical import now_utc

SIGNATURE_HEADER = "x-razorpay-signature"

#: Events the gateway acts on. Anything else is recorded and ignored — an
#: unrecognised event is not an error, and treating it as one would make every
#: Razorpay feature launch an outage.
HANDLED_EVENTS = (
    "order.paid",
    "payment.authorized",
    "payment.captured",
    "payment.failed",
    "refund.processed",
    "refund.failed",
)


class WebhookRejected(ValueError):
    """The delivery failed verification and was not processed."""


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    """HMAC-SHA256 over the raw body, compared in constant time.

    Returns False rather than raising, matching ``kya.crypto``: the caller has
    to distinguish "forged" from "could not check", and an exception collapses
    the two.
    """
    if not signature or not secret:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


@dataclass(slots=True)
class WebhookEvent:
    """A verified delivery, flattened to what handlers actually need."""

    event_id: str
    event: str
    payload: dict[str, Any]
    received_at: datetime

    @property
    def order_id(self) -> str | None:
        return self._entity("order", "id") or self._entity("payment", "order_id")

    @property
    def order_receipt(self) -> str | None:
        """Our own reference, echoed back by the rail."""
        return self._entity("order", "receipt")

    @property
    def payment_id(self) -> str | None:
        return self._entity("payment", "id")

    @property
    def refund_id(self) -> str | None:
        return self._entity("refund", "id")

    @property
    def amount(self) -> int | None:
        for kind in ("payment", "refund", "order"):
            value = self._entity(kind, "amount")
            if isinstance(value, int):
                return value
        return None

    def _entity(self, kind: str, key: str) -> Any:
        entity = self.payload.get(kind)
        if isinstance(entity, dict):
            inner = entity.get("entity")
            if isinstance(inner, dict):
                return inner.get(key)
            return entity.get(key)
        return None


@dataclass
class WebhookReceiver:
    """Verifies, deduplicates and dispatches deliveries.

    Handlers are registered per event name and may be registered more than once
    for the same event; all of them run. A handler that raises does not stop the
    others and does not fail the delivery, because Razorpay retries anything it
    does not get a success for, and one broken consumer must not turn every
    delivery into an infinite retry loop for the healthy ones.
    """

    secret: str
    clock: Callable[[], datetime] = now_utc
    handlers: dict[str, list[Callable[[WebhookEvent], None]]] = field(
        default_factory=dict
    )
    seen: set[str] = field(default_factory=set)
    #: Deliveries that verified but whose handlers raised. Surfaced rather than
    #: swallowed — a silently failing webhook consumer is how obligations go
    #: stale without anyone noticing.
    handler_errors: list[tuple[str, str]] = field(default_factory=list)

    def on(self, event: str, handler: Callable[[WebhookEvent], None]) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def receive(
        self, body: bytes, headers: dict[str, str]
    ) -> WebhookEvent | None:
        """Process one delivery. Returns None if it was a duplicate.

        Raises ``WebhookRejected`` on a bad signature or unparseable body —
        those are the cases where something is wrong with the *sender*, and
        answering them with a success would keep a forgery quiet.
        """
        signature = _header(headers, SIGNATURE_HEADER)
        if not verify_webhook_signature(body, signature, self.secret):
            raise WebhookRejected("webhook signature did not verify")

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise WebhookRejected(f"webhook body is not JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise WebhookRejected("webhook body is not a JSON object")

        event_id = _header(headers, "x-razorpay-event-id") or _fallback_id(body)
        if event_id in self.seen:
            return None
        self.seen.add(event_id)

        event = WebhookEvent(
            event_id=event_id,
            event=str(parsed.get("event", "")),
            payload=parsed.get("payload") or {},
            received_at=self.clock(),
        )

        for handler in self.handlers.get(event.event, []):
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001 - see class docstring
                self.handler_errors.append((event.event_id, f"{type(exc).__name__}: {exc}"))

        return event


def _header(headers: dict[str, str], name: str) -> str:
    """Case-insensitive lookup. HTTP header casing is not ours to rely on."""
    lowered = {k.lower(): v for k, v in headers.items()}
    return lowered.get(name, "")


def _fallback_id(body: bytes) -> str:
    """Deduplication key when the delivery carries no event id.

    A digest of the body, so an identical retransmission still deduplicates.
    Weaker than a real event id — two genuinely distinct but byte-identical
    events would collapse — which is why it is only a fallback.
    """
    return "sha256:" + hashlib.sha256(body).hexdigest()
