"""KYA's own agent tool surface, exposed over MCP.

The point of this module is narrow and specific: an AI buyer that speaks MCP —
Claude Desktop, Claude Code, or a custom agent framework — should not be able
to reach this sandbox merchant's money actions except through the same seven-
gate pipeline the HTTP API enforces. MCP is a transport here, not a bypass.
Every tool below that moves money still requires a fully RFC 9421-signed
``AgentRequest`` with an intact AP2-shaped mandate chain, exactly as if the
calling agent had hit ``POST /v1/agent/orders`` directly — the MCP server does
no signing, holds no agent keys, and cannot manufacture the authority a tool
call needs.

**This is not a proxy for Razorpay's own ``razorpay-mcp-server``.** That is a
separate process exposing 35+ tools directly over the raw Orders/Payments
API, with its own auth model; bridging the two honestly is out of scope for
this window. What ships instead is the thing the plan actually asks for: the
merchant's own guarded tool surface, so a buyer agent's MCP client sees KYA's
gates in the loop rather than an unguarded path to the rail. Read-only tools
(decision and obligation lookup, ledger integrity) carry no such requirement,
because there is nothing to gate on a lookup.

Run standalone for a real MCP client (Claude Desktop, Claude Code):

    python -m kya.rails.mcp_adapter

which speaks stdio, the transport those clients expect from a local server
entry.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from kya.api.state import KYAAppState
from kya.schemas import AgentRequest

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - optional dependency
    raise ImportError(
        "kya.rails.mcp_adapter requires the `mcp` package. Install the "
        "project's optional MCP extra: pip install -e '.[mcp]'"
    ) from exc


mcp = FastMCP(
    "know-your-agent",
    instructions=(
        "Guarded checkout surface for a Razorpay test-mode sandbox merchant. "
        "Every purchase or refund call runs the full inline pipeline — RFC "
        "9421 identity, AP2-shaped mandate chain, cart binding, velocity and "
        "spend bounds, content-threat screening — before anything reaches "
        "the payment rail. A request that is not correctly signed and "
        "mandated is denied with a reason code, not silently rejected as a "
        "malformed call. Purchases that are allowed mint a signed obligation "
        "receipt, anchored into the Razorpay order, before the rail is "
        "touched."
    ),
)

#: One demo state per process. A real deployment would resolve the merchant
#: from the connection/session rather than holding a singleton, but this
#: sandbox is single-merchant by construction — see `kya/simulation.py`.
_state: KYAAppState | None = None


def _get_state() -> KYAAppState:
    global _state
    if _state is None:
        # `seed=False`: an MCP client's first call should be its own, not a
        # pre-loaded demo record it did not ask for.
        _state = KYAAppState.demo(seed=False)
    return _state


def reset_state() -> None:
    """Drop the singleton. Tests use this to get an isolated sandbox."""
    global _state
    _state = None


def _model(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


@mcp.tool()
def agent_purchase(request: AgentRequest) -> dict[str, Any]:
    """Attempt a guarded purchase against the sandbox merchant.

    ``request`` must be a fully signed AgentRequest: ``signature``,
    ``signature_input_raw`` and ``signature_agent`` set from this agent's own
    Ed25519 key, and ``mandates``/``cart`` populated with an intact,
    intent-to-cart delegation chain. An unsigned, tampered or mismatched
    request is denied by the corresponding gate (G0-G5) rather than accepted
    as a well-formed call that merely fails validation.

    Returns the decision envelope — ALLOW / STEP_UP / QUARANTINE / DENY, the
    reason codes that produced it, and a reviewer-facing explanation — and,
    on ALLOW, the minted obligation receipt and the Razorpay test-mode order
    its hash was anchored into.
    """
    state = _get_state()
    result = state.create_order(request)
    return {
        "decision": _model(result.envelope),
        "obligation": _model(result.obligation),
        "order": result.order,
        "rail_error": result.rail_error,
        "replayed": result.replayed,
    }


@mcp.tool()
def agent_refund(
    request: AgentRequest, payment_id: str, amount: int
) -> dict[str, Any]:
    """Attempt a guarded refund of ``amount`` paise against ``payment_id``.

    Runs the same inline pipeline as a purchase, plus G4's refund-rate
    circuit breaker: a flood of refund attempts trips QUARANTINE regardless
    of how well-formed any individual request is, which is exactly the
    bot-farm shape this tool exists to be safe against.
    """
    state = _get_state()
    result = state.gateway.submit_refund(request, payment_id, amount)
    return {
        "decision": _model(result.envelope),
        "obligation": _model(result.obligation),
        "refund": result.refund,
        "rail_error": result.rail_error,
    }


@mcp.tool()
def get_decision(decision_id: str) -> dict[str, Any]:
    """Look up a previously rendered decision by id, for audit.

    Read-only. Returns the original signed request alongside the decision
    envelope, so a reviewer can see exactly what was presented and why it was
    ruled on the way it was.
    """
    state = _get_state()
    item = state.decisions.get(decision_id)
    if item is None:
        return {"error": f"unknown decision {decision_id!r}"}
    return {"request": _model(item.request), "decision": _model(item.envelope)}


@mcp.tool()
def get_obligation(obligation_id: str) -> dict[str, Any]:
    """Look up an obligation's current state and full version history.

    Read-only. The obligation ledger is append-only, so ``history`` is never
    rewritten — it is the record of what was promised, in the order it was
    promised, independent of what happened to the obligation afterward.
    """
    state = _get_state()
    current = state.sandbox.ledger.current(obligation_id)
    if current is None:
        return {"error": f"unknown obligation {obligation_id!r}"}
    return {
        "current": _model(current),
        "history": [_model(v) for v in state.sandbox.ledger.history(obligation_id)],
        "rail_id": state.sandbox.ledger.rail_id_for(obligation_id),
    }


@mcp.tool()
def verify_ledger() -> dict[str, Any]:
    """Walk the obligation ledger's hash chain and report any break found.

    Read-only, and the honest version of "trust the audit trail" — this
    recomputes the chain rather than asserting it holds.
    """
    state = _get_state()
    check = state.sandbox.ledger.verify()
    return {
        "ok": check.ok,
        "entries": check.entries,
        "tip_hash": check.tip_hash,
        "failures": [asdict(failure) for failure in check.failures],
    }


def main() -> None:  # pragma: no cover - process entry point
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
