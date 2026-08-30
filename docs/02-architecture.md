# 02 — Architecture

## Position

KYA is a **policy decision point in front of the merchant's commerce endpoints** — a sidecar, not a rewrite. A merchant adopting it changes a base URL, not an architecture. Agents that already speak TAP or Web Bot Auth work unmodified, because the signature verification follows RFC 9421 exactly.

## Two planes

```
   AI Buyer Agent (untrusted — ChatGPT / Claude / Perplexity / unknown operator)
                        │  signed HTTP (RFC 9421) + AP2-shaped mandate bundle
                        ▼
╔═══════════════════════════════════════════════════════════════════════════╗
║  KYA GATEWAY                                                              ║
║                                                                           ║
║  DATA PLANE — inline, deterministic, no LLM, p99 budget 50 ms             ║
║    G0 Transport/Replay → G1 Identity → G2 Mandate Chain → G3 Cart Binding ║
║    → G4 Bounded Envelope → G5 Content Threat → G6 Adjudicate              ║
║       ⇒ ALLOW | STEP_UP | QUARANTINE | DENY  (+ reason codes)             ║
║                                                                           ║
║  OBLIGATION LEDGER — append-only, hash-chained                            ║
║    mint Obligation Receipt ⟶ anchor its hash into Razorpay order.notes    ║
║                                                                           ║
║  CONTROL PLANE — async, LLM-assisted, off the money path                  ║
║    Evidence Envelope → Verification Mesh → Clearing Decision (PROVISIONAL)║
║    → Finality Rules → FINAL | DISPUTED → Settlement / Reversal            ║
║    → Clearing Passport update ⟶ feeds G4 on the agent's next request      ║
╚════════════════════╤═══════════════════════════════╤══════════════════════╝
                     ▼                               ▼
        Razorpay test-mode REST              Reserve Pay Block Ledger
        Orders / Payments / Refunds          (SIMULATED — labelled as such)
        / Webhooks / Settlements
        + razorpay-mcp-server (agent tool surface)
```

The separation is the headline design claim: **no LLM output ever moves money.** Everything on the inline path is deterministic code. Models appear only in the control plane, and their verdicts carry the lowest evidence class, so they are structurally incapable of clearing a settlement by themselves. This is not a policy we promise to follow; it is a property of the aggregator, and it is tested.

## The seven gates

Every gate emits a stable machine reason code. Codes are frozen in `kya/reasons.py` before any gate is written, because they are the shared vocabulary of the audit trail, the dashboard, the explainer and the metrics table.

### G0 — Transport & Replay
Timestamp skew window ±300 s. Nonce cache with TTL of twice the skew window. `Signature-Input` `created` / `expires` validation.

`R001 replay_nonce_reused` · `R002 timestamp_skew` · `R003 signature_expired`

### G1 — Agent Identity
Reconstruct the RFC 9421 signature base from the covered components. Resolve the key directory from the `Signature-Agent` header domain, fetch JWKS, cache. Verify Ed25519 against the pinned `keyid`. Handle key rotation.

`I001 signature_absent` · `I002 signature_invalid` · `I003 unknown_key` · `I004 directory_unreachable_degraded`

### G2 — Mandate Chain
Verify the Intent Mandate (user→agent delegation, constraints, TTL) and the Cart Mandate signature. Check chain integrity: `cart.intent_ref == hash(intent)`, and that the intent signer is the registered principal.

`M001 mandate_absent` · `M002 chain_broken` · `M003 mandate_expired` · `M004 principal_mismatch`

### G3 — Cart Binding
The gate that catches mandate substitution and price tampering. Canonicalise the cart with deterministic JCS-style serialization, then assert `hash(charged_cart) == cart_mandate.cart_hash`. On mismatch, report **field-level drift** — SKU, quantity, unit price, currency, total, shipping, tax — so the audit trail says *what* changed, not merely that something did. Then check constraint satisfaction against the intent: total ≤ `max_price`, merchant ∈ `allowed_merchants`, category permitted.

`C001 cart_hash_mismatch` · `C002 price_drift` · `C003 sku_substitution` · `C004 constraint_violation`

### G4 — Bounded Action Envelope
Token bucket for velocity and sliding window for spend, keyed independently on agent, principal, and the (agent × merchant) pair. A **refund-rate circuit breaker** trips when the refund-to-order ratio crosses threshold over a window, quarantining that agent's refunds. Tier ceilings come from the Clearing Passport.

**The Reserve Pay block guard is the India-native gate.** For each debit against a block, require a matching open obligation:

```
∃ obligation o :  o.block_ref == debit.block_ref
                ∧ o.state == OPEN
                ∧ debit.amount ≤ o.amount_due
                ∧ Σ(debits on block) ≤ block.reserved
```

An unmatched debit is denied. This is the control SBMD does not have today: it converts a spending envelope into a per-debit obligation check.

`E001 velocity_exceeded` · `E002 spend_cap` · `E003 refund_breaker_open` · `E004 block_debit_unbacked` · `E005 tier_ceiling`

### G5 — Content Threat
Indirect prompt-injection markers in agent-supplied free text (order notes, address lines, coupon codes, customer fields). Callback and webhook URLs must resolve to a domain registered for that agent × merchant pair, defeating counterfeit-merchant callbacks. Inline detection is deterministic only; LLM classification runs asynchronously on quarantined items.

`T001 injection_marker` · `T002 callback_domain_unregistered`

### G6 — Adjudication
Severity-weighted combination modulated by tier — deliberately not a flat AND. A `C002 price_drift` of ₹2 on a T3 agent is not the same event as the same drift on a T0 agent, and the decision surface should say so. Emits the final decision plus the complete gate trace.

Outcomes: **ALLOW** · **STEP_UP** (principal re-auth) · **QUARANTINE** (hold for human review) · **DENY**.

## Idempotency

Decisions are keyed on `(agent_id, mandate_hash, idempotency_key)`. Re-presenting an identical request returns the **cached decision**; it is never re-evaluated.

This matters more than it looks. Agents retry aggressively and without human judgement. Without decision caching, a retry after a network timeout can re-run the gates against a different counter state and produce a different answer — decision flapping — or worse, mint a second obligation and drive a duplicate charge. Idempotency at the decision layer, not just the payment layer, is what makes the gateway safe to put in front of a machine caller.

## Degradation policy

Every gate declares what it does when its dependency is unavailable. The governing principle: **fail closed on evidence of wrongdoing, fail soft on absence of evidence.**

| Gate | Dependency | On dependency failure | Rationale |
|---|---|---|---|
| G0 | nonce cache | → STEP_UP | Cannot prove non-replay; replay is cheap to attempt |
| G1 | JWKS directory | stale-while-revalidate; no cache → STEP_UP | Absence of evidence is not evidence of fraud. A directory outage must not zero out agent revenue |
| G2 | principal registry | stale cache; no cache → STEP_UP | Same |
| G3 | none (pure compute) | cannot fail | Deterministic and local by design |
| G4 | passport store | → treat as **T0**, the most restrictive tier | Safe default is *bounded*, not *denied* |
| G5 | classifier | skip inline, tag for async review, ALLOW | Injection is not directly a money-loss vector at this gate |
| G6 | — | any gate returning UNKNOWN → STEP_UP | Never resolve uncertainty toward ALLOW |

A **positive signature failure always denies**, in every degraded mode. Degradation relaxes the treatment of *missing* evidence, never of *contradicted* evidence.

## Latency budget

The data plane must not make a checkout wait. Target p99 < 50 ms with headroom:

| Gate | Typical | Notes |
|---|---|---|
| G0 | ~1 ms | local nonce cache lookup |
| G1 | ~1.5 ms | Ed25519 verify ≈ 0.5 ms; JWKS cached |
| G2 | ~1 ms | two signature verifications |
| G3 | ~2 ms | canonicalise + SHA-256 |
| G4 | ~2 ms | counter reads |
| G5 | ~5 ms | deterministic matchers |
| G6 | <1 ms | combination logic |
| **Total** | **~13 ms** | leaves ~37 ms of headroom to p99 |

The LLM path is excluded from this budget by construction, and the latency test asserts this — proving money decisions never wait on a model.

## Component layout

```
kya/
├─ reasons.py            all reason codes — frozen first, imported everywhere
├─ schemas.py            pydantic models — frozen first
├─ gates/                g0_replay · g1_identity · g2_mandate · g3_cart
│                        g4_envelope · g5_content · g6_adjudicate
├─ limits.py             token bucket · sliding window (G4's counters)
├─ passport.py           Clearing Passport store · tier movement rules
├─ reserve_pay.py        SIMULATED SBMD block ledger · unbacked-debit guard
├─ policy.py             tier ladder · breaker thresholds (data, not code)
├─ obligation/           receipt (minting) · ledger (hash chain) · anchor
├─ rails/                razorpay_client (live + fake) · webhooks
├─ gateway.py            decision → obligation → rail orchestration
├─ reconcile.py          lost-response recovery; never writes to the rail
├─ clearing/             evidence · mesh · verifiers/ · finality · reversal
├─ api/                  routes · dashboard
├─ live_check.py         runnable anchor proof against test-mode Razorpay
└─ config.py             environment settings (secrets and paths only)
```

`reasons.py` and `schemas.py` are frozen on Day 0 before any gate is implemented. In a seven-day build, a mid-week schema change is the failure mode that costs a day.

---

Previous: [01 — The Problem](01-problem.md) · Next: [03 — Threat Model](03-threat-model.md)
