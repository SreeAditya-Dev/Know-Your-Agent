"""Payment rails the gateway acts against.

Two implementations of one protocol. ``LiveRazorpayClient`` talks to real
test-mode Razorpay; ``FakeRazorpayClient`` models the same object lifecycle in
memory. The fake is not a stub — it enforces the same invariants the real API
does (an order cannot be over-captured, a refund cannot exceed what was
captured), because a fake that is more permissive than production turns the
test suite into a source of false confidence.

The fake is also what makes the lost-response failure demonstrable at all. You
cannot ask a live API to succeed and then drop the response on the floor, and
that is precisely the failure the reconciler exists for.
"""

from kya.rails.razorpay_client import (
    FakeRazorpayClient,
    LiveRazorpayClient,
    RailError,
    RazorpayRail,
    order_reference,
)
from kya.rails.webhooks import (
    WebhookEvent,
    WebhookReceiver,
    WebhookRejected,
    verify_webhook_signature,
)

__all__ = [
    "FakeRazorpayClient",
    "LiveRazorpayClient",
    "RailError",
    "RazorpayRail",
    "order_reference",
    "WebhookEvent",
    "WebhookReceiver",
    "WebhookRejected",
    "verify_webhook_signature",
]
