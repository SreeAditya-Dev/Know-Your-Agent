"""G3 — cart binding.

The gate that catches mandate substitution and price tampering: is the cart
being *charged* the cart that was *signed*?

Everything upstream can pass cleanly and this can still fail. A correctly
identified agent, holding a validly signed mandate from a real principal, can
present that mandate alongside a different cart. Identity-only defence — the
shipped state of the art — stops none of it.

On mismatch the gate reports which fields moved rather than only that the
digests disagreed, because "the total moved by ₹100" is what a dispute reviewer
can act on and "hash mismatch" is not.
"""

from __future__ import annotations

from typing import Any

from kya.enums import Gate
from kya.gates.base import BaseGate
from kya.gates.context import GateContext
from kya.schemas import Cart, GateResult


class G3Cart(BaseGate):
    gate = Gate.G3

    def evaluate(self, ctx: GateContext) -> GateResult:
        charged = ctx.request.cart
        bundle = ctx.request.mandates

        if bundle is None:
            # G2 already cited M001; nothing to bind against.
            return self._degraded(reason="no mandate bundle to bind against")
        if charged is None:
            return self._fail("C001", detail="no cart presented for charge")

        signed = bundle.cart.cart
        if charged.content_hash() == bundle.cart.cart_hash:
            return self._check_constraints(ctx, charged)

        # Digests disagree. Work out what actually moved.
        drift = _diff_carts(signed, charged)
        code = _classify_drift(drift)
        return self._fail(
            code,
            signed_hash=bundle.cart.cart_hash[:16],
            charged_hash=charged.content_hash()[:16],
            drift=drift,
        )

    def _check_constraints(self, ctx: GateContext, charged: Cart) -> GateResult:
        """The cart is authentic. Is it within what the buyer permitted?"""
        assert ctx.request.mandates is not None
        constraints = ctx.request.mandates.intent.constraints

        if charged.total > constraints.max_amount:
            return self._fail(
                "C004",
                violated="max_amount",
                limit=constraints.max_amount,
                actual=charged.total,
            )

        if (
            constraints.allowed_merchants
            and charged.merchant_id not in constraints.allowed_merchants
        ):
            return self._fail(
                "C004",
                violated="allowed_merchants",
                merchant_id=charged.merchant_id,
                allowed=constraints.allowed_merchants,
            )

        if (
            constraints.allowed_categories is not None
            and charged.category is not None
            and charged.category not in constraints.allowed_categories
        ):
            return self._fail(
                "C004",
                violated="allowed_categories",
                category=charged.category,
                allowed=constraints.allowed_categories,
            )

        if charged.total > ctx.policy.hard_max_amount:
            return self._fail(
                "C004",
                violated="hard_max_amount",
                limit=ctx.policy.hard_max_amount,
                actual=charged.total,
            )

        return self._pass(total=charged.total, merchant_id=charged.merchant_id)


def _diff_carts(signed: Cart, charged: Cart) -> dict[str, Any]:
    """Field-level difference between the signed and charged carts."""
    drift: dict[str, Any] = {}

    for field in ("merchant_id", "currency", "subtotal", "shipping", "tax", "total"):
        before, after = getattr(signed, field), getattr(charged, field)
        if before != after:
            entry: dict[str, Any] = {"signed": before, "charged": after}
            if isinstance(before, int) and isinstance(after, int):
                entry["delta"] = after - before
            drift[field] = entry

    signed_items = {li.sku: li for li in signed.line_items}
    charged_items = {li.sku: li for li in charged.line_items}

    added = sorted(charged_items.keys() - signed_items.keys())
    removed = sorted(signed_items.keys() - charged_items.keys())
    if added:
        drift["skus_added"] = added
    if removed:
        drift["skus_removed"] = removed

    changed: dict[str, Any] = {}
    for sku in sorted(signed_items.keys() & charged_items.keys()):
        before_item, after_item = signed_items[sku], charged_items[sku]
        item_drift: dict[str, Any] = {}
        if before_item.qty != after_item.qty:
            item_drift["qty"] = {"signed": before_item.qty, "charged": after_item.qty}
        if before_item.unit_price != after_item.unit_price:
            item_drift["unit_price"] = {
                "signed": before_item.unit_price,
                "charged": after_item.unit_price,
                "delta": after_item.unit_price - before_item.unit_price,
            }
        if item_drift:
            changed[sku] = item_drift
    if changed:
        drift["line_items"] = changed

    return drift


def _classify_drift(drift: dict[str, Any]) -> str:
    """Pick the reason code that best names what happened.

    Item substitution outranks price movement: swapping the goods is the more
    fundamental breach, and if both occurred the audit trail should say so.
    """
    if drift.get("skus_added") or drift.get("skus_removed"):
        return "C003"

    line_drift: dict[str, Any] = drift.get("line_items", {})
    if any("qty" in item for item in line_drift.values()):
        return "C003"  # a quantity change alters what is being bought
    if any("unit_price" in item for item in line_drift.values()):
        return "C002"

    if any(f in drift for f in ("total", "subtotal", "shipping", "tax")):
        return "C002"

    return "C001"
