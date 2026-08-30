"""The MCP tool surface, exercised as an MCP client would exercise it.

Calls go through ``mcp.call_tool`` rather than the module's Python functions
directly, so these tests exercise the same schema validation and dispatch a
real MCP client hits — not merely the functions underneath it.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp")

from kya.enums import Decision
from kya.rails import mcp_adapter
from kya.simulation import (
    build_refund_request,
    build_signed_request,
    make_cart,
    make_mandates,
)


@pytest.fixture(autouse=True)
def _fresh_state():
    """One isolated sandbox per test — the module otherwise memoises a
    singleton, and cross-request counters (velocity, spend) must not leak
    between tests."""
    mcp_adapter.reset_state()
    yield
    mcp_adapter.reset_state()


def _payload(result):
    """``call_tool`` returns ``(content_blocks, structured_output)`` when the
    tool's return type is annotated (as every tool here is). The structured
    form is already the dict our tools return; falling back to parsing the
    first text block keeps this working if that shape ever changes."""
    if isinstance(result, tuple):
        _blocks, structured = result
        if isinstance(structured, dict):
            return structured
        blocks = _blocks
    else:
        blocks = result
    return json.loads(blocks[0].text)


async def _call(name: str, **kwargs) -> dict:
    return _payload(await mcp_adapter.mcp.call_tool(name, kwargs))


@pytest.mark.asyncio
async def test_tool_surface_is_advertised():
    tools = await mcp_adapter.mcp.list_tools()
    names = {t.name for t in tools}
    assert {
        "agent_purchase",
        "agent_refund",
        "get_decision",
        "get_obligation",
        "verify_ledger",
    } <= names


@pytest.mark.asyncio
async def test_a_correctly_signed_purchase_is_allowed_and_anchored():
    state = mcp_adapter._get_state()
    cart = make_cart(items=[("SKU-CASE", "Phone case", 1, 499_00)])
    request = build_signed_request(
        state.agent, make_mandates(state.agent, state.principal, cart), cart
    )

    out = await _call("agent_purchase", request=request.model_dump(mode="json"))

    assert out["decision"]["decision"] == "ALLOW"
    assert out["obligation"]["self_hash"]
    assert out["order"]["notes"]["kya_obligation"] == out["obligation"]["self_hash"]


@pytest.mark.asyncio
async def test_an_unsigned_purchase_is_denied_not_silently_rejected():
    state = mcp_adapter._get_state()
    cart = make_cart()
    request = build_signed_request(
        state.agent, make_mandates(state.agent, state.principal, cart), cart
    )
    request.signature = None
    request.signature_input_raw = None

    out = await _call("agent_purchase", request=request.model_dump(mode="json"))

    assert out["decision"]["decision"] == "DENY"
    assert "I001" in out["decision"]["reason_codes"]
    assert out["obligation"] is None


@pytest.mark.asyncio
async def test_a_tampered_cart_is_denied_at_binding_not_identity():
    """The heart of the thesis, over the MCP surface too: a genuine signature
    and an intact mandate chain around a substituted cart is still caught."""
    state = mcp_adapter._get_state()
    signed_cart = make_cart(items=[("SKU-CASE", "Phone case", 1, 499_00)])
    mandates = make_mandates(
        state.agent, state.principal, signed_cart, max_amount=100_000_00
    )
    substituted = make_cart(items=[("SKU-TV-55", "55in TV", 1, 64_999_00)])
    request = build_signed_request(state.agent, mandates, substituted)

    out = await _call("agent_purchase", request=request.model_dump(mode="json"))

    assert out["decision"]["decision"] == "DENY"
    assert set(out["decision"]["reason_codes"]) & {"C001", "C002", "C003"}


@pytest.mark.asyncio
async def test_get_decision_and_get_obligation_round_trip():
    state = mcp_adapter._get_state()
    cart = make_cart()
    request = build_signed_request(
        state.agent, make_mandates(state.agent, state.principal, cart), cart
    )
    purchase = await _call("agent_purchase", request=request.model_dump(mode="json"))
    decision_id = purchase["decision"]["decision_id"]
    obligation_id = purchase["decision"]["obligation_id"]

    decision = await _call("get_decision", decision_id=decision_id)
    obligation = await _call("get_obligation", obligation_id=obligation_id)

    assert decision["decision"]["decision_id"] == decision_id
    assert obligation["current"]["obligation_id"] == obligation_id
    assert len(obligation["history"]) >= 1


@pytest.mark.asyncio
async def test_unknown_lookups_report_an_error_not_a_crash():
    decision = await _call("get_decision", decision_id="dec_does_not_exist")
    obligation = await _call("get_obligation", obligation_id="obl_does_not_exist")

    assert "error" in decision
    assert "error" in obligation


@pytest.mark.asyncio
async def test_verify_ledger_reports_a_clean_chain():
    state = mcp_adapter._get_state()
    cart = make_cart()
    request = build_signed_request(
        state.agent, make_mandates(state.agent, state.principal, cart), cart
    )
    await _call("agent_purchase", request=request.model_dump(mode="json"))

    out = await _call("verify_ledger")

    assert out["ok"] is True
    assert out["entries"] >= 1
    assert out["failures"] == []


@pytest.mark.asyncio
async def test_refund_flood_trips_the_breaker_over_mcp_too():
    """G4's cross-request refund breaker is stateful on the sandbox, which the
    MCP surface shares with the purchase tool — a flood is visible here
    exactly as it is over HTTP."""
    state = mcp_adapter._get_state()
    cart = make_cart(items=[("SKU-A", "Item", 1, 500_00)])
    mandates = make_mandates(state.agent, state.principal, cart)
    request = build_refund_request(state.agent, mandates, cart, amount=500_00)

    out = await _call(
        "agent_refund", request=request.model_dump(mode="json"),
        payment_id="pay_never_existed", amount=500_00,
    )

    assert out["decision"]["decision"] == "QUARANTINE"
    assert "E003" in out["decision"]["reason_codes"]
