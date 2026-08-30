"""Razorpay Orders, Payments and Refunds.

Only the operations the gateway actually performs are exposed. A thin protocol
rather than a wrapper around the whole SDK, because the surface the gateway
depends on is the surface the reconciler has to be able to fake, and every
extra method is another place the fake can drift from reality.

Money is integer paise on both sides of this boundary, matching Razorpay's own
convention, so no conversion happens anywhere in the system.
"""

from __future__ import annotations

import itertools
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

#: Razorpay caps the merchant-side ``receipt`` field at 40 characters.
RECEIPT_MAX_LEN = 40


class RailError(RuntimeError):
    """The rail refused an operation, or could not be reached."""


def order_reference(obligation_id: str) -> str:
    """Our own order reference, derived from the obligation id.

    Deterministic and known *before* the order exists, which is what lets the
    obligation commit to it at mint time. The rail's own order id cannot serve
    this purpose: it is assigned by the response to the very call whose request
    body has to carry the anchor.
    """
    ref = f"kya_{obligation_id}"
    if len(ref) > RECEIPT_MAX_LEN:  # pragma: no cover - ids are short by construction
        ref = ref[:RECEIPT_MAX_LEN]
    return ref


class RazorpayRail(Protocol):
    """What the gateway and reconciler need from a payment rail."""

    def create_order(
        self,
        amount: int,
        receipt: str,
        notes: Mapping[str, str],
        currency: str = "INR",
    ) -> dict[str, Any]: ...

    def fetch_order(self, order_id: str) -> dict[str, Any]: ...

    def order_by_receipt(self, receipt: str) -> dict[str, Any] | None:
        """Find an order by our own reference rather than the rail's id.

        The reconciler's entry point. After a lost response the rail's order id
        is precisely the thing we never learned, so recovery has to start from
        an identifier we chose ourselves.
        """
        ...

    def order_payments(self, order_id: str) -> list[dict[str, Any]]: ...

    def fetch_payment(self, payment_id: str) -> dict[str, Any]: ...

    def refund(
        self, payment_id: str, amount: int, notes: Mapping[str, str] | None = None
    ) -> dict[str, Any]: ...


class LiveRazorpayClient:
    """Test-mode Razorpay over the official SDK.

    Refuses to construct against a live key. The whole project is a defensive
    demonstration run against a sandbox merchant, and an accidental ``rzp_live_``
    in an environment file is the one configuration mistake that could move real
    money — so it is a hard failure at construction, not a warning in a log.
    """

    def __init__(self, key_id: str, key_secret: str) -> None:
        if not key_id.startswith("rzp_test_"):
            raise RailError(
                f"refusing to start against a non-test key ({key_id[:12]}...); "
                "this gateway is only ever run against test mode"
            )
        import razorpay  # imported here so the package is optional for unit tests

        self._client = razorpay.Client(auth=(key_id, key_secret))
        self.key_id = key_id

    def create_order(
        self,
        amount: int,
        receipt: str,
        notes: Mapping[str, str],
        currency: str = "INR",
    ) -> dict[str, Any]:
        try:
            return self._client.order.create(
                {
                    "amount": amount,
                    "currency": currency,
                    "receipt": receipt,
                    "notes": dict(notes),
                    # Capture is explicit: the gateway mints the obligation
                    # before the money moves, and auto-capture would invert
                    # that ordering for anyone reading the timestamps.
                    "payment_capture": 0,
                }
            )
        except Exception as exc:  # pragma: no cover - network path
            raise RailError(f"order create failed: {exc}") from exc

    def fetch_order(self, order_id: str) -> dict[str, Any]:
        try:
            return self._client.order.fetch(order_id)
        except Exception as exc:  # pragma: no cover - network path
            raise RailError(f"order fetch failed: {exc}") from exc

    def order_by_receipt(self, receipt: str) -> dict[str, Any] | None:
        try:
            response = self._client.order.all({"receipt": receipt, "count": 1})
        except Exception as exc:  # pragma: no cover - network path
            raise RailError(f"order lookup by receipt failed: {exc}") from exc
        items = response.get("items", [])
        return items[0] if items else None

    def order_payments(self, order_id: str) -> list[dict[str, Any]]:
        try:
            response = self._client.order.payments(order_id)
        except Exception as exc:  # pragma: no cover - network path
            raise RailError(f"order payments fetch failed: {exc}") from exc
        return list(response.get("items", []))

    def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        try:
            return self._client.payment.fetch(payment_id)
        except Exception as exc:  # pragma: no cover - network path
            raise RailError(f"payment fetch failed: {exc}") from exc

    def refund(
        self, payment_id: str, amount: int, notes: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        try:
            return self._client.payment.refund(
                payment_id, {"amount": amount, "notes": dict(notes or {})}
            )
        except Exception as exc:  # pragma: no cover - network path
            raise RailError(f"refund failed: {exc}") from exc


@dataclass
class FakeRazorpayClient:
    """In-memory rail with the same object lifecycle and the same refusals.

    Beyond standing in for the network, this can do one thing the real API
    cannot be asked to do: succeed while the caller never learns that it did.
    ``drop_responses`` makes ``create_order`` raise *after* the order exists,
    which is exactly the shape of the lost-response failure the reconciler
    handles, and the only honest way to test it.
    """

    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    payments: dict[str, dict[str, Any]] = field(default_factory=dict)
    refunds: dict[str, dict[str, Any]] = field(default_factory=dict)

    #: When True, mutating calls take effect and then raise, simulating a
    #: response lost between the rail and us.
    drop_responses: bool = False
    #: When True, every call raises before doing anything — a plain outage.
    unreachable: bool = False

    _seq: itertools.count = field(default_factory=lambda: itertools.count(1))
    calls: list[tuple[str, str]] = field(default_factory=list)

    def _next(self, prefix: str) -> str:
        return f"{prefix}_{next(self._seq):04d}{uuid.uuid4().hex[:6]}"

    def _guard(self, op: str, ref: str) -> None:
        self.calls.append((op, ref))
        if self.unreachable:
            raise RailError(f"rail unreachable during {op}")

    # --- orders --------------------------------------------------------------

    def create_order(
        self,
        amount: int,
        receipt: str,
        notes: Mapping[str, str],
        currency: str = "INR",
    ) -> dict[str, Any]:
        self._guard("create_order", receipt)
        order_id = self._next("order")
        self.orders[order_id] = {
            "id": order_id,
            "entity": "order",
            "amount": amount,
            "amount_paid": 0,
            "amount_due": amount,
            "currency": currency,
            "receipt": receipt,
            "notes": dict(notes),
            "status": "created",
            "created_at": int(time.time()),
        }
        if self.drop_responses:
            raise RailError("response lost after order was created")
        return dict(self.orders[order_id])

    def fetch_order(self, order_id: str) -> dict[str, Any]:
        self._guard("fetch_order", order_id)
        order = self.orders.get(order_id)
        if order is None:
            raise RailError(f"no such order {order_id}")
        return dict(order)

    def order_by_receipt(self, receipt: str) -> dict[str, Any] | None:
        self._guard("order_by_receipt", receipt)
        for order in self.orders.values():
            if order["receipt"] == receipt:
                return dict(order)
        return None

    def order_payments(self, order_id: str) -> list[dict[str, Any]]:
        self._guard("order_payments", order_id)
        return [
            dict(p) for p in self.payments.values() if p.get("order_id") == order_id
        ]

    # --- payments ------------------------------------------------------------

    def pay(self, order_id: str, amount: int | None = None) -> dict[str, Any]:
        """Simulate a buyer paying. Not part of the rail protocol — the gateway
        never initiates a payment, it only observes one."""
        order = self.orders[order_id]
        amount = order["amount"] if amount is None else amount
        payment_id = self._next("pay")
        self.payments[payment_id] = {
            "id": payment_id,
            "entity": "payment",
            "order_id": order_id,
            "amount": amount,
            "amount_refunded": 0,
            "currency": order["currency"],
            "status": "captured",
            "captured": True,
            "created_at": int(time.time()),
        }
        order["amount_paid"] = amount
        order["amount_due"] = order["amount"] - amount
        order["status"] = "paid" if order["amount_due"] == 0 else "attempted"
        return dict(self.payments[payment_id])

    def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        self._guard("fetch_payment", payment_id)
        payment = self.payments.get(payment_id)
        if payment is None:
            raise RailError(f"no such payment {payment_id}")
        return dict(payment)

    # --- refunds -------------------------------------------------------------

    def refund(
        self, payment_id: str, amount: int, notes: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        self._guard("refund", payment_id)
        payment = self.payments.get(payment_id)
        if payment is None:
            raise RailError(f"no such payment {payment_id}")

        refundable = payment["amount"] - payment["amount_refunded"]
        if amount > refundable:
            # The real API refuses this too. A fake that allowed it would let
            # a reversal bug reach production looking tested.
            raise RailError(
                f"refund of {amount} exceeds refundable {refundable} on {payment_id}"
            )

        refund_id = self._next("rfnd")
        self.refunds[refund_id] = {
            "id": refund_id,
            "entity": "refund",
            "payment_id": payment_id,
            "amount": amount,
            "currency": payment["currency"],
            "status": "processed",
            "notes": dict(notes or {}),
            "created_at": int(time.time()),
        }
        payment["amount_refunded"] += amount
        return dict(self.refunds[refund_id])
