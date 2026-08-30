"""G4 — bounded action envelope.

Everything upstream asks whether this *one* request is well formed: correctly
signed, validly delegated, honestly bound to its cart. Every one of those
questions can be answered yes ten thousand times in an hour by a bot farm, and
each answer is individually correct.

G4 is the only gate that looks at an agent across requests. It is therefore the
only place the flood shapes are visible at all — the refund flood Unit 42
documented, and the block drain that SBMD's amount-and-time boundary cannot
see.

Four controls, in the order a reviewer reads them:

1. **Velocity** — token bucket per agent and per (agent × merchant), at the
   tier's hourly rate.
2. **Delegated transaction count** — the buyer's own ``max_transactions``. It
   sits in the intent mandate and is a claim about a sequence, so it cannot be
   enforced by a gate that sees one request at a time.
3. **Spend** — a single action above the tier ceiling steps up (``E005``); a
   *cumulative* breach of the rolling window quarantines (``E002``). The
   distinction is the point: being above your station is not the same as
   draining a budget, and flattening them into one code would make the
   false-positive story unreadable.
4. **Refund-rate breaker** and the **Reserve Pay block guard**.

**Velocity is consumed here; spend is recorded in ``commit``.** A request costs
capacity whether or not it is ultimately allowed — that is what makes rate
limiting a flood defence rather than a formality. Spend is different: a denied
purchase moved no money and must not eat the buyer's budget.

Note what this implies for requests denied *upstream*. The pipeline
short-circuits before G4 on a positive integrity failure, so a flood of
forged signatures never touches this gate's counters. That is correct, not an
oversight: a request that failed to prove whose it was must not be charged to
the agent whose name it borrowed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from kya.enums import Action, Gate
from kya.gates.base import BaseGate
from kya.gates.context import GateContext
from kya.limits import (
    DEFAULT_RETENTION_SECONDS,
    LimitStore,
    agent_key,
    agent_merchant_key,
    intent_key,
    principal_key,
)
from kya.policy import Policy
from kya.reserve_pay import BlockLedger
from kya.schemas import AgentRequest, DecisionEnvelope, GateResult

#: Actions that debit the buyer, and so count against the spend cap.
_SPEND_ACTIONS = (Action.PURCHASE, Action.BLOCK_DEBIT)

_ORDERS_PATH = re.compile(r"^/v\d+/agent/orders/?$")
_REFUNDS_PATH = re.compile(r"^/v\d+/agent/refunds/?$")
_BLOCK_DEBIT_PATH = re.compile(r"^/v\d+/agent/blocks/(?P<block_id>[^/]+)/debit/?$")

#: Where the resolved action is parked so ``commit`` books exactly what
#: ``evaluate`` judged, rather than re-deriving it and risking a divergence.
_SPEC_NOTE = "g4_action_spec"


@dataclass(slots=True)
class ActionSpec:
    """What this request is asking to do, and for how much."""

    action: Action
    amount: int
    merchant_id: str
    block_id: str | None = None
    obligation_id: str | None = None

    def as_detail(self) -> dict[str, Any]:
        detail = {
            "action": self.action.value,
            "amount": self.amount,
            "merchant_id": self.merchant_id,
        }
        if self.block_id:
            detail["block_id"] = self.block_id
        return detail


def resolve_action(request: AgentRequest, policy: Policy) -> ActionSpec:
    """Classify a request from its path, and read the amount at risk.

    The action comes from the routed path, never from a caller-supplied label,
    so an agent cannot present a refund as a purchase to escape the breaker.
    """
    merchant_id = (
        request.cart.merchant_id if request.cart is not None else policy.merchant_id
    )

    if _REFUNDS_PATH.match(request.path):
        return ActionSpec(
            action=Action.REFUND,
            amount=_body_amount(request, "refund"),
            merchant_id=merchant_id,
        )

    block_match = _BLOCK_DEBIT_PATH.match(request.path)
    if block_match:
        return ActionSpec(
            action=Action.BLOCK_DEBIT,
            amount=_body_amount(request, "debit"),
            merchant_id=merchant_id,
            block_id=block_match.group("block_id"),
        )

    if _ORDERS_PATH.match(request.path):
        return ActionSpec(
            action=Action.PURCHASE,
            amount=request.cart.total if request.cart is not None else 0,
            merchant_id=merchant_id,
        )

    # An unrecognised path carrying a cart is still a purchase for accounting
    # purposes. One carrying nothing has no amount to bound, and G4 has nothing
    # to say about it — the gates that do have already spoken.
    if request.cart is not None:
        return ActionSpec(
            action=Action.PURCHASE, amount=request.cart.total, merchant_id=merchant_id
        )
    return ActionSpec(action=Action.UNKNOWN, amount=0, merchant_id=merchant_id)


def _body_amount(request: AgentRequest, section: str) -> int:
    """Integer paise from ``body[section]["amount"]``, or 0 if absent.

    A missing or non-integer amount reads as zero rather than raising: the
    request is malformed, the endpoint behind us will reject it, and a gate
    that crashes on bad input is a gate that can be taken offline by bad input.
    """
    block = request.body.get(section)
    if not isinstance(block, dict):
        return 0
    amount = block.get("amount")
    return amount if isinstance(amount, int) and not isinstance(amount, bool) else 0


class G4Envelope(BaseGate):
    gate = Gate.G4

    def __init__(
        self,
        limits: LimitStore | None = None,
        blocks: BlockLedger | None = None,
    ) -> None:
        # Injected rather than read off the context, because these are the only
        # gate dependencies that must persist *between* requests. A counter
        # rebuilt per request is not a counter.
        self.limits = LimitStore() if limits is None else limits
        self.blocks = BlockLedger() if blocks is None else blocks

    # --- inline evaluation ---------------------------------------------------

    def evaluate(self, ctx: GateContext) -> GateResult:
        spec = resolve_action(ctx.request, ctx.policy)
        ctx.notes[_SPEC_NOTE] = spec

        codes: list[str] = []
        detail: dict[str, Any] = spec.as_detail()
        detail["tier"] = ctx.tier.value

        self._check_velocity(ctx, spec, codes, detail)
        self._check_delegated_count(ctx, spec, codes, detail)
        self._check_spend(ctx, spec, codes, detail)
        self._check_refund_breaker(ctx, spec, codes, detail)
        self._check_block_guard(ctx, spec, codes, detail)

        if codes:
            # De-duplicated: two scopes can trip the same control, and the
            # audit trail should cite the reason once with both scopes in the
            # detail rather than twice with none.
            return self._fail(*dict.fromkeys(codes), **detail)
        return self._pass(**detail)

    def _check_velocity(
        self,
        ctx: GateContext,
        spec: ActionSpec,
        codes: list[str],
        detail: dict[str, Any],
    ) -> None:
        if spec.action is Action.UNKNOWN:
            return

        per_hour = ctx.tier_policy.velocity_per_hour
        tripped: list[dict[str, Any]] = []

        for key in (
            agent_key(ctx.request.agent_id),
            agent_merchant_key(ctx.request.agent_id, spec.merchant_id),
        ):
            result = self.limits.try_consume(key, per_hour, now=ctx.now)
            if not result.allowed:
                tripped.append(
                    {
                        "scope": key[0],
                        "limit_per_hour": per_hour,
                        "retry_after_seconds": result.retry_after_seconds,
                    }
                )

        if tripped:
            codes.append("E001")
            detail["velocity"] = tripped

    def _check_delegated_count(
        self,
        ctx: GateContext,
        spec: ActionSpec,
        codes: list[str],
        detail: dict[str, Any],
    ) -> None:
        """Enforce the buyer's own ``max_transactions``.

        This bound lives in the intent mandate but cannot be checked by G3:
        it constrains a *sequence*, and G3 sees one cart. Left unenforced it is
        a limit the buyer believes they set and nothing applies.
        """
        bundle = ctx.request.mandates
        if bundle is None or spec.action not in _SPEND_ACTIONS:
            return

        cap = bundle.intent.constraints.max_transactions
        if cap is None:
            return

        used = self.limits.stats(
            intent_key(bundle.intent.intent_id),
            window_seconds=DEFAULT_RETENTION_SECONDS,
            now=ctx.now,
            actions=_SPEND_ACTIONS,
        ).count

        if used + 1 > cap:
            codes.append("E001")
            detail["delegated_transactions"] = {
                "intent_id": bundle.intent.intent_id,
                "limit": cap,
                "already_used": used,
            }

    def _check_spend(
        self,
        ctx: GateContext,
        spec: ActionSpec,
        codes: list[str],
        detail: dict[str, Any],
    ) -> None:
        if spec.action not in _SPEND_ACTIONS or spec.amount <= 0:
            return

        cap = ctx.tier_policy.spend_cap
        window_seconds = ctx.policy.spend_window_seconds

        if spec.amount > cap:
            # One action larger than the tier's entire budget. Not abuse —
            # an agent above its station, which re-authentication resolves.
            codes.append("E005")
            detail["tier_ceiling"] = {"tier_spend_cap": cap, "amount": spec.amount}
            return

        window = self.limits.stats(
            agent_key(ctx.request.agent_id),
            window_seconds=window_seconds,
            now=ctx.now,
            actions=_SPEND_ACTIONS,
        )
        if window.value + spec.amount > cap:
            codes.append("E002")
            detail["spend"] = {
                "tier_spend_cap": cap,
                "window_seconds": window_seconds,
                "spent_in_window": window.value,
                "requested": spec.amount,
            }

    def _check_refund_breaker(
        self,
        ctx: GateContext,
        spec: ActionSpec,
        codes: list[str],
        detail: dict[str, Any],
    ) -> None:
        """Trip when refunds stop looking like corrections and start looking
        like extraction.

        Two rules, because one alone has a hole. The ratio thresholds need a
        meaningful sample, so below ``min_orders`` they say nothing — and a bot
        farm with zero orders and ten thousand refunds sits precisely in that
        blind spot. The count invariant closes it: refunds may never outnumber
        orders in the window, at any sample size.

        System-issued reversals do not pass through here. A DISPUTED obligation
        is reversed by the control plane, not by an agent request, so it is
        never subject to the breaker and needs no exemption from it.
        """
        if spec.action is not Action.REFUND:
            return

        breaker = ctx.policy.refund_breaker
        key = agent_key(ctx.request.agent_id)
        orders = self.limits.stats(
            key, breaker.window_seconds, now=ctx.now, actions=_SPEND_ACTIONS
        )
        refunds = self.limits.stats(
            key, breaker.window_seconds, now=ctx.now, actions=(Action.REFUND,)
        )

        proposed_count = refunds.count + 1
        proposed_value = refunds.value + spec.amount
        observed: dict[str, Any] = {
            "window_seconds": breaker.window_seconds,
            "orders": orders.count,
            "refunds_including_this": proposed_count,
        }

        if proposed_count > orders.count:
            codes.append("E003")
            detail["refund_breaker"] = {
                **observed,
                "rule": "refunds_exceed_orders",
            }
            return

        if orders.count < breaker.min_orders:
            return

        count_ratio = proposed_count / orders.count
        if count_ratio > breaker.max_refund_ratio:
            codes.append("E003")
            detail["refund_breaker"] = {
                **observed,
                "rule": "count_ratio",
                "ratio": round(count_ratio, 4),
                "threshold": breaker.max_refund_ratio,
            }
            return

        if orders.value > 0:
            value_ratio = proposed_value / orders.value
            if value_ratio > breaker.max_refund_value_ratio:
                codes.append("E003")
                detail["refund_breaker"] = {
                    **observed,
                    "rule": "value_ratio",
                    "ratio": round(value_ratio, 4),
                    "threshold": breaker.max_refund_value_ratio,
                }

    def _check_block_guard(
        self,
        ctx: GateContext,
        spec: ActionSpec,
        codes: list[str],
        detail: dict[str, Any],
    ) -> None:
        """The India-native control. See ``kya/reserve_pay.py`` — SIMULATED."""
        if spec.action is not Action.BLOCK_DEBIT:
            return

        if not spec.block_id:
            codes.append("E004")
            detail["block_guard"] = {"reason": "block_id_absent"}
            return

        check = self.blocks.check_debit(spec.block_id, spec.amount, now=ctx.now)
        detail["block_guard"] = {"simulated": True, **check.detail}

        if check.ok:
            spec.obligation_id = check.matched_obligation_id
            detail["block_guard"]["matched_obligation"] = check.matched_obligation_id
        elif check.code:
            codes.append(check.code)

    # --- post-decision booking ----------------------------------------------

    def commit(self, ctx: GateContext, envelope: DecisionEnvelope) -> None:
        """Book the money this request actually moved. ALLOW only.

        A stepped-up request that later completes is booked when it completes,
        not now — at this moment it has moved nothing, and counting it would
        charge the buyer's budget for a transaction that may never happen.
        """
        spec = ctx.notes.get(_SPEC_NOTE)
        if not isinstance(spec, ActionSpec):
            return
        if spec.action is Action.UNKNOWN or spec.amount <= 0:
            return

        for key in (
            agent_key(ctx.request.agent_id),
            agent_merchant_key(ctx.request.agent_id, spec.merchant_id),
        ):
            self.limits.record(key, spec.action, spec.amount, now=ctx.now)

        bundle = ctx.request.mandates
        if bundle is not None:
            self.limits.record(
                intent_key(bundle.intent.intent_id), spec.action, spec.amount, now=ctx.now
            )
            self.limits.record(
                principal_key(bundle.intent.principal_ref),
                spec.action,
                spec.amount,
                now=ctx.now,
            )

        if spec.action is Action.BLOCK_DEBIT and spec.block_id:
            self.blocks.apply_debit(
                spec.block_id, spec.amount, spec.obligation_id, now=ctx.now
            )
