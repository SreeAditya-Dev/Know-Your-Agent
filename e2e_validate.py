"""End-to-end validation: full money path in-process + live HTTP server checks."""
import json
import httpx
from kya.simulation import (
    build_signed_request, make_cart, make_mandates, resign_request,
    standard_sandbox,
)
from kya.enums import Decision

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

# 4. Cart substitution: signed cart A, charged cart B -> DENY C001
cart_b = make_cart(items=[("SKU-CASE", "Phone case", 1, 1_00)])
req_b = build_signed_request(agent, mandates, cart_b)
req_b.cart = cart_b
req_b = resign_request(agent, req_b)
res4 = gw.create_order(req_b)
print("A4 SUBST :", res4.envelope.decision.value, res4.envelope.reason_codes)

# 5. Ledger integrity
v = sandbox.ledger.verify()
print("A5 LEDGER:", v)

# ---------- Part B: live HTTP server on :8331 ----------
BASE = "http://127.0.0.1:8331"
h = httpx.Client(timeout=30)

print("B1 HEALTH:", h.get(f"{BASE}/v1/health").json())
m = h.get(f"{BASE}/v1/metrics").json()
print("B2 METRICS keys:", sorted(m.keys()))

# 3. Tampered-cart attack over HTTP (server's own seeded agent keys are unknown
#    to us, so expect a schema-valid response with a deny/step-up decision)
try:
    r = h.post(f"{BASE}/v1/agent/inspect", json=json.loads(req_b.model_dump_json()))
    print("B3 ATTACK-over-HTTP:", r.status_code, r.json().get("decision", {}).get("decision"),
          r.json().get("decision", {}).get("reason_codes"))
except Exception as exc:
    print("B3 ATTACK-over-HTTP error:", exc)

# 4. Benchmark + simulation scenario endpoints
b = h.get(f"{BASE}/v1/benchmark").json()
print("B4 BENCHMARK sessions:", b.get("n_sessions"), "attacks:", b.get("n_attacks"))
sc = h.get(f"{BASE}/v1/simulation/scenarios").json()
print("B5 SCENARIOS:", [s.get("name", s) if isinstance(s, dict) else s for s in sc][:12])

# 5. Malformed body -> 422, unknown decision -> 404
print("B6 422 check:", h.post(f"{BASE}/v1/agent/orders", json={"agent_id": "x"}).status_code)
print("B7 404 check:", h.get(f"{BASE}/v1/decisions/does_not_exist").status_code)

