# Know-Your-Agent

**An obligation-clearing gateway for agentic commerce on Razorpay rails.**

> Identity says who called. A mandate says they were allowed.
> Neither says the obligation was satisfied. KYA closes that loop.

Razorpay AI Buildathon 2026 · **Track 01 — AI Growth & Agentic Commerce**

---

## The problem

AI agents are becoming buyers. Every shipping standard answers two questions — *is this agent who it claims to be* (Visa Trusted Agent Protocol, Cloudflare Web Bot Auth) and *was it authorized by a human* (AP2, ACP). None answers the third:

**Was the obligation the merchant took on actually satisfied?**

The RAILS paper ([arXiv 2606.08790](https://arxiv.org/html/2606.08790), Jun 2026) puts it precisely: *"Payment settles value transfer. Clearing settles obligation state."* It is a protocol proposal with no implementation.

On **UPI Reserve Pay**, built on NPCI's Single Block Multi Debit, this is not theoretical. A user blocks funds once, and then — in Razorpay's own words — *"the merchant can debit multiple times without requiring fresh authentication for each transaction."* The boundary is amount, time and merchant scope. **Nothing binds an individual debit to an obligation actually incurred.**

Unit 42 has documented the matching attack: agents triggering refunds without a real shipping scan, and bot farms initiating ten thousand returns in an hour to drain a retailer's cash before a human notices.

And there is an asymmetry nobody has closed. Razorpay Agent Studio has real guardrails — discount ceilings, approval gates, review-first mode, one-tap disable. Every one governs an **outbound** agent, one the merchant employs. Nothing governs an **inbound** agent: a third-party AI buyer the merchant does not control, cannot configure, and cannot switch off.

## What this is

A merchant-side gateway in front of the Razorpay Orders/Payments surface:

1. **Verifies the inbound agent** — RFC 9421 signatures, TAP / Web Bot Auth compatible, so existing agents work unmodified.
2. **Verifies the mandate chain** — AP2-shaped intent and cart mandates, and critically that the cart being *charged* is the cart that was *signed*.
3. **Bounds the agent** — velocity and spend caps, a refund-rate circuit breaker, and a per-debit obligation check on Reserve Pay blocks.
4. **Mints an obligation receipt** before capture, with its hash anchored into the Razorpay order record itself.
5. **Clears the obligation** against graded evidence, and reverses provisional settlements that fail.
6. **Solves cold start** with a trust ladder, so a new legitimate agent is bounded rather than blocked.

## Design commitments

Each is testable, and each is tested.

- **No LLM output ever moves money.** The inline path is deterministic. Models run only in the async control plane, and their verdicts carry the lowest evidence class — structurally unable to clear a settlement alone.
- **Fail closed on evidence of wrongdoing, fail soft on absence of evidence.** A bad signature denies. An unreachable key directory degrades to step-up.
- **Decisions are idempotent.** The same request returns the same cached decision, never a re-evaluation.
- **The audit trail is anchored outside our own database**, in the Razorpay order record, so it is verifiable by someone who does not trust us.
- **A false positive is a bounded sale, not a lost one.**
- **The evaluation corpus is frozen and hash-committed before any detector tuning.**

## Architecture

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
        + razorpay-mcp-server
```

## What identity-only defence misses

The core measurement. **B1 is the shipped state of the art** — what a merchant integrating Visa TAP gets today.

| Attack | B0 none | B1 identity-only | B2 + mandate | B3 full KYA |
|---|:--:|:--:|:--:|:--:|
| agent impersonation | ✗ | ✓ | ✓ | ✓ |
| key substitution | ✗ | ✓ | ✓ | ✓ |
| replay | ✗ | ✓ | ✓ | ✓ |
| mandate substitution | ✗ | ✗ | ✓ | ✓ |
| price tampering | ✗ | ✗ | ✓ | ✓ |
| scope escalation | ✗ | ✗ | ~ | ✓ |
| refund flood | ✗ | ✗ | ✗ | ✓ |
| indirect prompt injection | ✗ | ✗ | ✗ | ✓ |
| counterfeit callback | ✗ | ✗ | ✗ | ✓ |
| **Reserve Pay block drain** | ✗ | ✗ | ✗ | ✓ |
| **obligation mismatch** | ✗ | ✗ | ✗ | ✓ |

A correctly identified, correctly authorized agent can still substitute a cart, flood refunds, drain a block, and take delivery of something other than what was promised.

**These are now measured, not asserted.** Running the red-team harness over a frozen corpus of **530 sessions** (130 attacks across the 11 classes, 400 legitimate) gives:

| Posture | Attacks stopped | Recall | Precision | False-positive rate |
|---|:--:|:--:|:--:|:--:|
| B0 no gateway | 0/130 | 0% | 100% | 0.0% |
| B1 identity-only (TAP / Web Bot Auth) | 34/130 | 26% | 100% | 0.0% |
| B2 + mandate (AP2) | 73/130 | 56% | 100% | 0.0% |
| **B3 full KYA** | **123/130** | **95%** | **100%** | **0.0%** |

Identity-only defence — the shipped state of the art — stops one attack in four. The 7 attacks B3 does not stop are a declared, open exception list: a fluent prompt-injection paraphrase that carries no marker the deterministic content gate matches, and a counterfeit delivery that satisfies every recorded acceptance criterion (the semantic verifier flags it, but an LLM's opinion is `SELF`-class and cannot clear or dispute a settlement alone). Not one legitimate session is denied — false-positive cost is entirely stepped-up friction, ₹0 in refused revenue. Data-plane p99 is **~1.5 ms** against a 50 ms budget.

Reproduce it:

```bash
python -m redteam.run --verify     # corpus matches its committed SHA-256
python -m redteam.run --all        # prints the full table, metrics and exception list
```

See [`redteam/REPORT.md`](redteam/REPORT.md) for the generated report and [docs/05](docs/05-evaluation.md) for the anti-rigging protocol.

## Documentation

| # | Document |
|---|---|
| 01 | [The Problem](docs/01-problem.md) — why merchant-side defence is the open gap |
| 02 | [Architecture](docs/02-architecture.md) — two planes, seven gates, degradation policy, latency budget |
| 03 | [Threat Model](docs/03-threat-model.md) — 11 attack classes and which gate catches each |
| 04 | [Obligation Clearing](docs/04-obligation-clearing.md) — receipts, evidence grading, finality, reversal |
| 05 | [Evaluation](docs/05-evaluation.md) — corpus, anti-rigging protocol, baselines, metrics |
| 06 | [API Reference](docs/06-api.md) — endpoints, headers, decision envelope, reason codes |
| 07 | [Limitations](docs/07-limitations.md) — what is simulated, what we do not do, exception list |

## Status

**Day 6 complete — the gateway, clearing layer, measured red-team evaluation, FastAPI surface and operations dashboard are live.**

| Component | State |
|---|---|
| Evidence lattice (RAILS partial order) | ✅ implemented, poset laws tested |
| G0 transport & replay | ✅ |
| G1 identity (RFC 9421 / Ed25519) | ✅ |
| G2 mandate chain (AP2-shaped) | ✅ |
| G3 cart binding + field-level drift | ✅ |
| G4 bounded envelope · refund breaker · Reserve Pay guard | ✅ |
| Clearing Passport store + tier ladder | ✅ SQLite, durable |
| G6 adjudication + explainer | ✅ |
| Idempotent decision cache | ✅ |
| Obligation receipts + hash-chained ledger | ✅ append-only, tamper-evident |
| Razorpay anchoring (`order.notes.kya_obligation`) | ✅ verified against live `rzp_test_` |
| Webhook intake (signature-verified, deduplicated) | ✅ |
| Reconciler — graceful failure #1 | ✅ zero duplicate charges |
| AgentPay recovery planner | ✅ diagnose, bind verified captures, bounded re-check or human review |
| Clearing mesh, finality, reversal | ✅ implemented, driven end-to-end by the harness |
| G5 content threat | ✅ deterministic marker and callback-host checks |
| Red-team corpus + baselines B0–B3 + metrics | ✅ 530-session frozen corpus, `python -m redteam.run --all` |
| FastAPI gateway, audit routes and server-rendered dashboard | ✅ sandbox runtime, signed order route, evidence, webhooks and review queue |

**270 offline tests passing**, with four additional live test-mode Razorpay tests that require network access and credentials. Attack classes A1–A10 are covered by the inline gateway; obligation--fulfilment mismatch (A11) is handled by the clearing layer. The red-team harness (`redteam/`) runs the identical corpus through four defence postures and reports the comparison, precision/recall, decomposed false-positive cost, latency percentiles and an honest exception list; the corpus is frozen to a committed SHA-256 before any tuning, and the run refuses to report numbers against a corpus that does not match it.

Day 2 added the first gate that looks at an agent *across* requests — the only place the flood shapes are visible at all. Every request in that attack suite passes G0–G3 cleanly, so nothing is wrong with any single request, only with the sequence, which is precisely what identity-only defence cannot see.

Day 3 adds the artifact that makes "was the obligation satisfied?" answerable. An allowed purchase mints a signed Obligation Receipt recording what was promised — SKU, price, delivery window, return terms, plus the predicates that would satisfy it and the evidence class each one requires — *before* the rail is touched, and writes its hash into the Razorpay order's `notes`. Three properties follow:

- **The audit trail is verifiable by someone who does not trust us.** A reviewer with Razorpay dashboard access reads `notes.kya_obligation`, recomputes the hash from the receipt, and matches the two. The order's own timestamp proves the receipt predates capture. This is checked against a real test-mode order, re-fetched from Razorpay rather than read back from the create response.
- **The ledger is append-only.** State changes append a new version, so what version 1 promised is never rewritten — which is why the anchor still verifies after an obligation has been paid, partially refunded and reversed.
- **Payment does not satisfy an obligation.** A capture sets `amount_due` to zero and leaves the obligation OPEN. Collapsing the two would erase the distinction the project exists to make.

Day 4 completes the deterministic G5 boundary: instruction-shaped free text is quarantined without retaining the hostile content in the decision trace, and callback URLs must match the agent's configured exact-host allowlist. The same pass also adds the clearing mesh, finality, settlement and reversal implementation; no model or network call can influence the inline money decision.

The AgentPay Autopilot extension translates reconciler outcomes into a bounded recovery plan. A verified capture is bound to the existing obligation and completed without a retry; an existing but unpaid order is observed again; lookup uncertainty and rail outages are retried only as read-only checks; and an old missing order goes to human review rather than being recreated blindly. See `kya/autopilot.py`.

Day 6 adds `kya/api/`: a sandbox-safe FastAPI surface and server-rendered control dashboard at `/dashboard`. It records inspected decisions, exposes the signed order, evidence, ledger and webhook paths, renders the frozen B0–B3 benchmark, and provides a human review queue for quarantined calls. The API stores exact signed inputs and gate traces for audit; it does not re-execute historical requests because that would mutate replay and rate-limit state.

Measured data-plane latency over 2,000 requests through G0–G4, at a sustained 450 req/hr so every request is a real ALLOW rather than an early denial, LLM path absent by construction:

| p50 | p95 | p99 | budget |
|---|---|---|---|
| 1.4 ms | 3.1 ms | **4.0 ms** | 50 ms |

One thing only the live run could tell us: Razorpay's order *list* endpoint is eventually consistent, lagging 10-20 seconds behind order creation, while fetch-by-id is immediate. Since the reconciler recovers by looking an order up by our own reference, an early miss must not be read as "never created" — that conclusion, reached during a lost-response recovery, is what causes the double charge it exists to prevent. It now defers inside a 60-second grace window instead. See [docs/07](docs/07-limitations.md).

Reserve Pay / SBMD is a **labelled local simulation**; NPCI's Unified Agent Protocol has not launched and requires RBI approval. Razorpay Orders, Payments, Refunds and Webhooks are real, against `rzp_test_` keys. See [docs/07](docs/07-limitations.md) for the full honest scoping.

## Running it

```bash
pip install -e ".[dev]"
pytest tests/ -q
uvicorn kya.api.app:app --reload
```

No network or Razorpay credentials are needed. The agent directory, principals
and payment rail are all in-process fixtures, and the two tests that do need
live keys skip themselves without them.

**Prove the anchor against real Razorpay test mode:**

```bash
cp .env.example .env          # fill in rzp_test_ keys
python -m kya.live_check      # places one ₹100 test-mode order
pytest tests/test_live_razorpay.py -m live -v
```

`live_check` places one order through the full gateway, fetches it back from
Razorpay, and re-derives the obligation hash from the receipt alone. It prints
the order id so the same record can be opened in the Razorpay dashboard and
checked by hand. Without credentials it runs against the in-memory rail and
says so, so its output can never be mistaken for a live run.

## Ethics

Strictly defensive. Every attack scenario runs against our own sandbox merchant with our own test-mode keys. No weaponisable payloads against live protocol implementations are published here.

## References

RAILS ([arXiv 2606.08790](https://arxiv.org/html/2606.08790)) · SoK: Security of Autonomous LLM Agents in Agentic Commerce ([arXiv 2604.15367](https://arxiv.org/pdf/2604.15367)) · [Visa Trusted Agent Protocol](https://github.com/visa/trusted-agent-protocol) · [Cloudflare Web Bot Auth](https://blog.cloudflare.com/web-bot-auth/) · [AP2](https://ap2-protocol.org/specification/) · [ACP](https://github.com/agentic-commerce-protocol/agentic-commerce-protocol) · [Unit 42: Retail Fraud in the Age of Agentic AI](https://unit42.paloaltonetworks.com/retail-fraud-agentic-ai/) · [DataDome AI Traffic Report](https://datadome.co/threat-research/ai-traffic-report/) · [Razorpay UPI Reserve Pay](https://razorpay.com/blog/upi-reserve-pay/) · [razorpay-mcp-server](https://github.com/razorpay/razorpay-mcp-server)
