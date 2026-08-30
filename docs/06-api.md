# 06 — API Reference

Design reference, frozen Day 0. Endpoint shapes here are the contract the implementation is written against.

## Compatibility stance

KYA speaks **RFC 9421 HTTP Message Signatures** exactly as specified. Agents already implementing Visa TAP or Cloudflare Web Bot Auth work against this gateway **unmodified** — same `Signature`, `Signature-Input` and `Signature-Agent` headers, same Ed25519 verification, same directory resolution.

That is deliberate. A merchant-side control that requires every inbound agent to adopt a bespoke protocol defends nothing, because no agent will adopt it. Compatibility is a feature, not an accident.

The mandate bundle is **AP2-shaped**: Intent Mandate and Cart Mandate as signed JSON, carrying the same semantics as AP2's verifiable credentials. We do not implement the full W3C VC / JSON-LD stack in a seven-day build — see [07](07-limitations.md).

## Request headers

| Header | Source | Required | Purpose |
|---|---|---|---|
| `Signature` | RFC 9421 | yes | Ed25519 signature over the signature base |
| `Signature-Input` | RFC 9421 | yes | Covered components, `keyid`, `created`, `expires`, `nonce`, `tag` |
| `Signature-Agent` | Web Bot Auth | yes | Domain publishing the agent's JWKS |
| `Idempotency-Key` | KYA | yes | Client-generated; scopes the cached decision |
| `KYA-Intent-Mandate` | KYA | yes | Base64url JSON, or in body |
| `KYA-Cart-Mandate` | KYA | yes | Base64url JSON, or in body |

## Decision envelope

Every guarded endpoint returns this alongside its result. It is the audit trail's user-facing face — reason codes, per-gate trace, and a natural-language explanation **generated from the codes**, never the source of them.

```json
{
  "decision_id": "dec_...",
  "decision": "ALLOW | STEP_UP | QUARANTINE | DENY",
  "agent_id": "agent_...",
  "tier": "T0",
  "reason_codes": ["C002"],
  "gate_trace": [
    {"gate": "G0", "verdict": "PASS", "ms": 0.9},
    {"gate": "G1", "verdict": "PASS", "ms": 1.4, "key_id": "..."},
    {"gate": "G2", "verdict": "PASS", "ms": 1.1},
    {"gate": "G3", "verdict": "FAIL", "ms": 2.0,
     "codes": ["C002"],
     "drift": {"field": "total", "signed": 549900, "charged": 559900}},
    {"gate": "G4", "verdict": "SKIPPED", "reason": "short_circuit"}
  ],
  "explanation": "Blocked: the cart total presented at charge time was ₹5,599.00, but the mandate the buyer signed committed to ₹5,499.00. The ₹100.00 difference was not authorized.",
  "obligation_id": null,
  "idempotent_replay": false,
  "latency_ms": 5.4
}
```

`idempotent_replay: true` marks a cached decision returned for a repeated `Idempotency-Key`. The gates did not re-run.

## Endpoints

### Guarded money actions

```
POST /v1/agent/orders
```
Creates a Razorpay order behind the full gate pipeline. On ALLOW: mints an Obligation Receipt, anchors `self_hash` into `order.notes.kya_obligation`, creates the order, returns `{decision, order, obligation}`.

```
POST /v1/agent/refunds
```
Guarded refund. Subject to G4's refund-rate circuit breaker (`E003`). Refunds against a DISPUTED obligation bypass the breaker — reversal is a system action, not an agent action.

```
POST /v1/agent/blocks/{block_id}/debit
```
**SIMULATED — Reserve Pay / SBMD.** Debit against a reserved block. Enforces the block guard: every debit must map to an open obligation with sufficient `amount_due`, and cumulative debits must not exceed `block.reserved`. Unmatched debits return `E004 block_debit_unbacked`.

> This endpoint models NPCI's Single Block Multi Debit semantics against a local ledger. It is **not** a live Reserve Pay integration, and is labelled `SIMULATED` in code, responses and UI.

### Clearing

```
POST /v1/evidence
```
Submit fulfilment evidence against an obligation. Body carries evidence items, each with a declared class (`SELF`/`SIGN`/`WIT`/`REC`) and provenance chain. Triggers async mesh evaluation.

```
GET  /v1/obligations/{obligation_id}
GET  /v1/obligations/{obligation_id}/clearing
POST /v1/obligations/{obligation_id}/dispute
```
Retrieve a receipt, its clearing decision and finality state, or raise a dispute manually.

### Audit

```
GET /v1/decisions/{decision_id}
GET /v1/decisions/{decision_id}/replay
```
Full decision record; `/replay` returns the stored signed input and gate trace for audit. Historical re-execution is deliberately disabled because it would mutate nonce and rate-limit state, making the replay itself alter the evidence it is meant to inspect.

```
GET /v1/ledger/verify
```
Walks the hash chain and reports integrity. Also re-verifies anchored hashes against live Razorpay order records.

### Webhooks and dashboard

```
POST /webhooks/razorpay
```
Signature-verified Razorpay events. Feeds the receipt verifier (`REC` class) and the reconciler.

```
GET /dashboard
GET /dashboard/decisions/{id}
GET /dashboard/metrics
GET /dashboard/quarantine
```
Server-rendered. Live sandbox decision feed, per-decision gate trace inspection, frozen benchmark metrics, and the human review queue.

### MCP tool surface

```
python -m kya.rails.mcp_adapter
```

`kya/rails/mcp_adapter.py` exposes the same guarded actions over MCP (stdio transport), so an MCP-speaking agent — Claude Desktop, Claude Code, or a custom agent framework — reaches this merchant's money actions only through the gate pipeline, not around it. The MCP layer is a transport, not a bypass: every tool call still requires a fully RFC 9421-signed `AgentRequest` with an intact mandate chain, and an unsigned or tampered call is denied by the same gate that would deny it over HTTP.

| Tool | Money-moving | Equivalent HTTP route |
|---|:--:|---|
| `agent_purchase(request)` | yes | `POST /v1/agent/orders` |
| `agent_refund(request, payment_id, amount)` | yes | `POST /v1/agent/refunds` |
| `get_decision(decision_id)` | no | `GET /v1/decisions/{id}` |
| `get_obligation(obligation_id)` | no | `GET /v1/obligations/{id}` |
| `verify_ledger()` | no | `GET /v1/ledger/verify` |

This is a KYA-native MCP server, not a proxy for Razorpay's own `razorpay-mcp-server` (a separate Go binary exposing 35+ tools directly against the raw Orders/Payments API). Bridging the two honestly — two processes, two auth models — is out of scope for this build window; see [07](07-limitations.md). What ships is the thing Track 01 actually asks for: an agentic tool surface with the gateway's gates in the loop.

## Reason codes

Frozen in `kya/reasons.py` on Day 0, imported everywhere. Stable identifiers — the vocabulary shared by the audit trail, the dashboard, the explainer and the metrics table.

| Code | Gate | Meaning |
|---|---|---|
| `R001` | G0 | `replay_nonce_reused` |
| `R002` | G0 | `timestamp_skew` |
| `R003` | G0 | `signature_expired` |
| `R004` | G0 | `nonce_store_unavailable` |
| `I001` | G1 | `signature_absent` |
| `I002` | G1 | `signature_invalid` |
| `I003` | G1 | `unknown_key` |
| `I004` | G1 | `directory_unreachable_degraded` |
| `I005` | G1 | `signature_agent_missing` |
| `M001` | G2 | `mandate_absent` |
| `M002` | G2 | `chain_broken` |
| `M003` | G2 | `mandate_expired` |
| `M004` | G2 | `principal_mismatch` |
| `M005` | G2 | `mandate_signature_invalid` |
| `M006` | G2 | `agent_not_delegated` |
| `C001` | G3 | `cart_hash_mismatch` |
| `C002` | G3 | `price_drift` |
| `C003` | G3 | `sku_substitution` |
| `C004` | G3 | `constraint_violation` |
| `C005` | G3 | `cart_total_inconsistent` |
| `E001` | G4 | `velocity_exceeded` |
| `E002` | G4 | `spend_cap` |
| `E003` | G4 | `refund_breaker_open` |
| `E004` | G4 | `block_debit_unbacked` |
| `E005` | G4 | `tier_ceiling` |
| `E006` | G4 | `block_reserve_exhausted` |
| `T001` | G5 | `injection_marker` |
| `T002` | G5 | `callback_domain_unregistered` |
| `A001` | G6 | `gate_unresolved` |
| `A002` | G6 | `policy_step_up_threshold` |

Degradation codes (`R004`, `I004`) propose STEP_UP rather than DENY: they mean a control could not be *checked*, not that it was failed. Fail closed on evidence of wrongdoing, fail soft on absence of evidence.

## Configuration

`policies/merchant_policy.yaml` — spend and velocity ceilings, refund-breaker thresholds, admissibility floors per category, appeal window, registered callback domains.

`policies/tiers.yaml` — the T0–T3 ladder and promotion/demotion rules.

Policy is data, not code. A merchant changes limits by editing YAML, and every decision records which policy version produced it.

---

Previous: [05 — Evaluation](05-evaluation.md) · Next: [07 — Limitations](07-limitations.md)
