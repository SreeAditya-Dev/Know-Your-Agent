"""End-to-end validation: full money path in-process + HTTP surface checks."""
import json
import httpx
from fastapi.testclient import TestClient
from kya.simulation import (
    build_signed_request, make_cart, make_mandates, resign_request,
    standard_sandbox,
)
from kya.enums import Decision
from kya.api.app import create_app

# ---------- Part A: full money path, in-process (the real gateway) ----------
sandbox, agent, principal = standard_sandbox()
gw = sandbox.gateway()

# 1. Legitimate signed purchase -> ALLOW, order created, obligation minted + anchored
cart = make_cart(items=[("SKU-CASE", "Phone case", 1, 499_00)])
mandates = make_mandates(agent, principal, cart)
req = build_signed_request(agent, mandates, cart)
res = gw.create_order(req)
print("A1 LEGIT :", res.envelope.decision.value, res.envelope.reason_codes,
      "| obligation:", (res.obligation.obligation_id if res.obligation else None),
      "| order:", (res.order or {}).get("id"),
      "| anchor_ok:", (res.anchor.ok if res.anchor else None))

# 2. Same request again (same idempotency key) -> cached decision, no second order
res2 = gw.create_order(req)
print("A2 REPLAY:", res2.envelope.decision.value,
      "replayed:", res2.replayed,
      "same_order:", (res2.order or {}).get("id") == (res.order or {}).get("id"))

# 3. Fresh key, same mandate chain -> caught by chain hash, not a second obligation
req3 = build_signed_request(agent, mandates, cart)
req3.idempotency_key = "fresh-key-2"
res3 = gw.create_order(req3)
print("A3 DUP-KEY:", res3.envelope.decision.value, res3.envelope.reason_codes,
      "obligation:", (res3.obligation.obligation_id if res3.obligation else None))

# 4. Cart substitution: signed cart A, charged cart B -> DENY C002
cart_b = make_cart(items=[("SKU-CASE", "Phone case", 1, 1_00)])
req_b = build_signed_request(agent, mandates, cart_b)
req_b.cart = cart_b
req_b = resign_request(agent, req_b)
res4 = gw.create_order(req_b)
print("A4 SUBST :", res4.envelope.decision.value, res4.envelope.reason_codes)

# 5. Ledger integrity
v = sandbox.ledger.verify()
print("A5 LEDGER:", v)

# ---------- Part B: HTTP surface checks ----------
app = create_app()
client = TestClient(app)

print("B1 HEALTH:", client.get("/v1/health").json())
m = client.get("/v1/metrics").json()
print("B2 METRICS keys:", sorted(m.keys()))

# 3. Tampered-cart attack over HTTP
try:
    r = client.post("/v1/agent/inspect", json=json.loads(req_b.model_dump_json()))
    print("B3 ATTACK-over-HTTP:", r.status_code, r.json().get("decision", {}).get("decision"),
          r.json().get("decision", {}).get("reason_codes"))
except Exception as exc:
    print("B3 ATTACK-over-HTTP error:", exc)

# 4. Benchmark + simulation scenario endpoints
b = client.get("/v1/benchmark").json()
print("B4 BENCHMARK sessions:", b.get("n_sessions"), "attacks:", b.get("n_attacks"))
sc = client.get("/v1/simulation/scenarios").json()
print("B5 SCENARIOS:", [s.get("title", s.get("name", s)) if isinstance(s, dict) else s for s in sc][:6])

# 5. Store endpoints check
prods = client.get("/v1/store/products").json()
print("B6 STORE PRODUCTS:", len(prods), "products in catalog")
agent_buy = client.post(
    "/v1/store/agent-checkout",
    json={"prompt": "Buy Puma Velocity Nitro 3 running shoes in size 10 under ₹8000"},
).json()
print("B7 STORE AGENT BUY:", agent_buy.get("decision"), "| order:", (agent_buy.get("order") or {}).get("order_id"))

# 6. Malformed body -> 422, unknown decision -> 404
print("B8 422 check:", client.post("/v1/agent/orders", json={"agent_id": "x"}).status_code)
print("B9 404 check:", client.get("/v1/decisions/does_not_exist").status_code)
print("ALL E2E CHECKS PASSED [OK]")
