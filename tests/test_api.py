"""FastAPI delivery surface: dashboard and JSON views use real KYA state."""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from kya.api.app import create_app
from kya.api.state import KYAAppState
from kya.simulation import build_signed_request, make_cart, make_mandates


def _client() -> TestClient:
    return TestClient(create_app(KYAAppState.demo()))


def test_dashboard_and_navigation_render():
    client = _client()

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Agent transaction oversight" in response.text
    assert "Measured posture" in response.text

    assert client.get("/dashboard/metrics").status_code == 200
    assert client.get("/dashboard/quarantine").status_code == 200


def test_api_exposes_auditable_decision_and_ledger():
    client = _client()

    health = client.get("/v1/health")
    decisions = client.get("/v1/decisions")

    assert health.status_code == 200
    assert health.json()["mode"] == "sandbox"
    assert decisions.status_code == 200
    # The seeded demo now walks every decision shape, not just two records —
    # this asserts the variety, not a count that would need bumping every
    # time a scenario is added.
    seen = {item["decision"] for item in decisions.json()}
    assert {"ALLOW", "DENY", "QUARANTINE", "STEP_UP"} <= seen

    decision_id = decisions.json()[0]["decision_id"]
    record = client.get(f"/v1/decisions/{decision_id}")
    replay = client.get(f"/v1/decisions/{decision_id}/replay")
    ledger = client.get("/v1/ledger/verify")

    assert record.status_code == 200
    assert "gate_trace" in record.json()["decision"]
    assert replay.json()["replayable"] is False
    assert ledger.json()["ok"] is True


def test_signed_order_route_records_a_real_gateway_result():
    state = KYAAppState.demo()
    client = TestClient(create_app(state))
    cart = make_cart(items=[("SKU-CASE", "Case", 1, 599_00)])
    request = build_signed_request(
        state.agent, make_mandates(state.agent, state.principal, cart), cart
    )

    response = client.post("/v1/agent/orders", json=request.model_dump(mode="json"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"]["decision"] == "ALLOW"
    assert payload["order"]["amount"] == cart.total
    assert payload["obligation"]["self_hash"]


def test_seed_demo_covers_every_reason_code_family():
    """The dashboard is meant to be a scenario catalog, not just a smoke
    test — one example of every gate's DENY/QUARANTINE/STEP_UP family should
    be visible on first load, spread across dedicated identities so no two
    scenarios' counters interfere."""
    state = KYAAppState.demo()
    codes = {
        code
        for item in state.decisions.values()
        for code in item.envelope.reason_codes
    }
    expected = {
        "A002", "E005",  # two distinct STEP_UP paths
        "T001", "E001", "E003",  # three distinct QUARANTINE paths
        "I001", "I003", "R001", "C002", "C003", "C004", "T002", "E004",  # DENY
    }
    assert expected <= codes

    agent_ids = {item.envelope.agent_id for item in state.decisions.values()}
    assert len(agent_ids) >= 10, "scenarios should mostly use dedicated identities"


def test_seed_demo_includes_an_obligation_mismatch_disputed_at_clearing():
    """A11's shape: legitimate at purchase, disputed only once fulfilment
    evidence is examined — the one class no inline gate can see."""
    state = KYAAppState.demo()

    assert state.clearing_results, "expected at least one clearing result"
    result = next(iter(state.clearing_results.values()))
    assert result.disputed
    assert result.decision.performance_verdict == "VIOLATED"


def test_dashboard_renders_the_clearing_panel():
    client = _client()

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Obligation clearing" in response.text
    assert "DISPUTED" in response.text


def test_webhook_route_verifies_and_deduplicates_delivery():
    state = KYAAppState.demo()
    client = TestClient(create_app(state))
    order_id = next(iter(state.sandbox.rail.orders))
    payment = state.sandbox.rail.pay(order_id)
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {"entity": payment},
            "order": {"entity": state.sandbox.rail.orders[order_id]},
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(
        b"sandbox_webhook_secret", body, hashlib.sha256
    ).hexdigest()
    headers = {
        "x-razorpay-signature": signature,
        "x-razorpay-event-id": "evt_api_001",
        "content-type": "application/json",
    }

    first = client.post("/webhooks/razorpay", content=body, headers=headers)
    second = client.post("/webhooks/razorpay", content=body, headers=headers)

    assert first.status_code == 200
    assert first.json()["accepted"] is True
    assert second.json()["duplicate"] is True
