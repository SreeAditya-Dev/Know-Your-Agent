"""G6 — adjudication.

Combines gate findings into one decision. Deliberately not a flat AND:

* Each cited reason *proposes* an outcome; the most restrictive proposal wins.
* An UNKNOWN or DEGRADED gate can never resolve toward ALLOW.
* Tier modulates the amount-based step-up threshold, so the same transaction
  is treated differently for a first-contact agent than for an established one.

Tier does **not** modulate integrity failures. A T3 agent presenting a
tampered cart is denied exactly as a T0 agent would be — trust earned through
good behaviour buys larger limits, never permission to break the binding.
"""

from __future__ import annotations

from kya.enums import Decision, GateVerdict
from kya.gates.context import GateContext
from kya.reasons import get as get_reason
from kya.schemas import GateResult


def adjudicate(
    ctx: GateContext, results: list[GateResult]
) -> tuple[Decision, list[str]]:
    """Return the terminal decision and the reason codes supporting it."""
    codes: list[str] = []
    for result in results:
        codes.extend(result.codes)

    decision = Decision.ALLOW
    for code in codes:
        proposed = get_reason(code).proposes
        if proposed.rank > decision.rank:
            decision = proposed

    # Uncertainty never resolves toward allow.
    unresolved = [r for r in results if r.verdict is GateVerdict.UNKNOWN]
    if unresolved and decision.rank < Decision.STEP_UP.rank:
        decision = Decision.STEP_UP
        codes.append("A001")

    # Amount-based step-up, scaled by earned trust. Only applied to requests
    # that would otherwise pass; there is no point stepping up a denial.
    if decision is Decision.ALLOW:
        cart = ctx.request.cart
        threshold = ctx.tier_policy.step_up_above
        if cart is not None and cart.total > threshold:
            decision = Decision.STEP_UP
            codes.append("A002")

    return decision, codes


def explain(decision: Decision, codes: list[str], results: list[GateResult]) -> str:
    """Reviewer-facing prose, generated *from* the reason codes.

    This is presentation, never inference. The decision is already made by the
    time this runs, and nothing here can change it — which is why an LLM may
    later replace this function without touching the money path.
    """
    if decision is Decision.ALLOW:
        return "Allowed: agent identity, mandate chain and cart binding all verified."

    reasons = [get_reason(c) for c in dict.fromkeys(codes)]
    if not reasons:
        return f"{decision.value}: no reason recorded."

    governing = max(reasons, key=lambda r: (r.proposes.rank, int(r.severity)))
    lines = [f"{decision.value}: {governing.summary}"]

    detail = _detail_for(governing.code, results)
    if detail:
        lines.append(detail)

    others = [r for r in reasons if r.code != governing.code]
    if others:
        lines.append(
            "Also cited: " + ", ".join(f"{r.code} ({r.slug})" for r in others) + "."
        )
    return " ".join(lines)


def _detail_for(code: str, results: list[GateResult]) -> str:
    """Turn a gate's structured detail into a sentence, where we can do so
    faithfully. Anything we cannot phrase precisely is left out rather than
    approximated."""
    for result in results:
        if code not in result.codes:
            continue
        d = result.detail

        if code in {"C001", "C002", "C003"}:
            drift = d.get("drift", {})
            if "total" in drift:
                t = drift["total"]
                return (
                    f"The signed total was {_rupees(t['signed'])} but "
                    f"{_rupees(t['charged'])} was presented for charge, a "
                    f"difference of {_rupees(abs(t['delta']))}."
                )
            if drift.get("skus_added") or drift.get("skus_removed"):
                return (
                    f"Items changed after signing — added: "
                    f"{drift.get('skus_added') or 'none'}, "
                    f"removed: {drift.get('skus_removed') or 'none'}."
                )
        if code == "C004":
            if d.get("violated") == "max_amount":
                return (
                    f"The buyer's mandate capped spend at {_rupees(d['limit'])}; "
                    f"{_rupees(d['actual'])} was attempted."
                )
            return f"Constraint breached: {d.get('violated')}."
        if code == "R002":
            return f"Request clock drift was {d.get('drift_seconds')}s."
        if code == "M006":
            return (
                f"The mandate delegates to {d.get('delegated_to')}, but "
                f"{d.get('calling_agent')} made the call."
            )
        if code == "M002":
            return "The cart mandate references a different intent than the one supplied."
        if code == "I003":
            return f"Key {d.get('key_id')} is not published by {d.get('origin')}."
        if code == "E005":
            return (
                f"{_rupees(d.get('tier_ceiling', {}).get('amount', 0))} is above the "
                f"{d.get('tier', '?')} ceiling of "
                f"{_rupees(d.get('tier_ceiling', {}).get('tier_spend_cap', 0))}."
            )
        if code == "E001":
            if "delegated_transactions" in d:
                dt = d["delegated_transactions"]
                return (
                    f"The buyer authorised {dt['limit']} transaction(s) on this "
                    f"mandate and {dt['already_used']} have been used."
                )
            scopes = d.get("velocity", [])
            if scopes:
                first = scopes[0]
                return (
                    f"Rate limit for this tier is {first['limit_per_hour']}/hour; "
                    f"capacity returns in {first['retry_after_seconds']:.0f}s."
                )
        if code == "E002":
            spend = d.get("spend", {})
            return (
                f"{_rupees(spend.get('spent_in_window', 0))} already spent in the last "
                f"hour against a {_rupees(spend.get('tier_spend_cap', 0))} cap; "
                f"{_rupees(spend.get('requested', 0))} more was requested."
            )
        if code == "E003":
            rb = d.get("refund_breaker", {})
            if rb.get("rule") == "refunds_exceed_orders":
                return (
                    f"{rb.get('refunds_including_this')} refund(s) against "
                    f"{rb.get('orders')} order(s) in the window. Refunds cannot "
                    "outnumber orders."
                )
            return (
                f"Refund rate {rb.get('ratio')} is above the permitted "
                f"{rb.get('threshold')} over {rb.get('orders')} orders."
            )
        if code == "E004":
            guard = d.get("block_guard", {})
            reasons = {
                "no_open_obligation": (
                    "The block was valid and funded, but no open obligation covers "
                    "this debit — the funds were authorised, nothing was owed."
                ),
                "amount_exceeds_due": (
                    f"The debit of {_rupees(guard.get('amount', 0))} exceeds the "
                    f"{_rupees(guard.get('max_amount_due', 0))} still owed on the "
                    "matching obligation."
                ),
                "block_revoked": "The buyer has revoked this block.",
                "block_expired": "The block's authorisation window has closed.",
                "unknown_block": "No such block exists.",
            }
            return reasons.get(str(guard.get("reason")), "")
        if code == "E006":
            guard = d.get("block_guard", {})
            return (
                f"{_rupees(guard.get('amount', 0))} was requested against "
                f"{_rupees(guard.get('available', 0))} still available on a "
                f"{_rupees(guard.get('reserved', 0))} block."
            )
    return ""


def _rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"
