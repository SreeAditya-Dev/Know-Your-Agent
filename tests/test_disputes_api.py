"""Tests for Disputes, Consent, Certificate, and Reputation API routes."""

import pytest
from fastapi.testclient import TestClient

from kya.api.app import create_app
from kya.api.state import KYAAppState


@pytest.fixture
def client() -> TestClient:
    app = create_app(KYAAppState.demo(seed=True))
    return TestClient(app)


def test_api_list_disputes(client: TestClient) -> None:
    resp = client.get("/v1/disputes")
    assert resp.status_code == 200
    disputes = resp.json()
    assert isinstance(disputes, list)
    assert len(disputes) >= 1

    first = disputes[0]
    assert "package_id" in first
    assert "assigned_fault" in first
    assert "outcome" in first


def test_api_get_dispute_detail(client: TestClient) -> None:
    list_resp = client.get("/v1/disputes")
    disputes = list_resp.json()
    assert len(disputes) > 0
    dispute_id = disputes[0]["dispute_id"]

    resp = client.get(f"/v1/disputes/{dispute_id}")
    assert resp.status_code == 200
    pkg = resp.json()
    assert pkg["dispute_id"] == dispute_id
    assert "representment_brief_markdown" in pkg
    assert "liability_verdict" in pkg


def test_api_get_reputation(client: TestClient) -> None:
    resp = client.get("/v1/reputation/agent_demo")
    assert resp.status_code == 200
    rep = resp.json()
    assert "credit_score" in rep
    assert "risk_band" in rep
    assert 0 <= rep["credit_score"] <= 1000


def test_api_cross_rail_normalize(client: TestClient) -> None:
    resp = client.post(
        "/v1/cross-rail/normalize",
        json={
            "token_type": "stripe_spt",
            "token_id": "spt_api_test_123",
            "agent_id": "agent_api_shopper",
            "principal_ref": "user_api_1",
            "amount": 750000,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is True
    assert data["token"]["token_type"] == "stripe_spt"
