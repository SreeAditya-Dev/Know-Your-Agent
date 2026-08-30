# Know-Your-Agent — Documentation

**An obligation-clearing gateway for agentic commerce on Razorpay rails.**

> Identity says who called. A mandate says they were allowed.
> Neither says the obligation was satisfied. KYA closes that loop.

Razorpay AI Buildathon 2026 · **Track 01 — AI Growth & Agentic Commerce**

---

## Read in this order

| # | Document | What it answers |
|---|---|---|
| 01 | [The Problem](01-problem.md) | Why merchant-side defence is the open gap, and why identity verification alone no longer is |
| 02 | [Architecture](02-architecture.md) | The two planes, the seven gates, degradation policy, latency budget |
| 03 | [Threat Model](03-threat-model.md) | 11 attack classes, which gate catches each, which baselines miss them |
| 04 | [Obligation Clearing](04-obligation-clearing.md) | Receipts, evidence grading, the verification mesh, finality and reversal |
| 05 | [Evaluation](05-evaluation.md) | Corpus design, the anti-rigging protocol, baselines, metrics we report |
| 06 | [API Reference](06-api.md) | Gateway endpoints, request headers, decision envelope, reason codes |
| 07 | [Limitations](07-limitations.md) | What KYA does not do, what is simulated, the honest exception list |

---

## The one-paragraph version

AI agents are becoming buyers. Every shipping standard — Visa's Trusted Agent Protocol, Cloudflare's Web Bot Auth, Google's AP2, OpenAI and Stripe's ACP — answers two questions: *is this agent who it claims to be*, and *was it authorized by a human*. None answers the third: **was the obligation the merchant took on actually satisfied?** On UPI Reserve Pay, where a single consent authorizes many debits without fresh authentication, that gap is a live cash-drain vector. KYA is a merchant-side gateway that verifies inbound agents, binds every debit to a signed obligation receipt anchored in Razorpay's own order record, verifies satisfaction against graded evidence before settlement becomes final, and reverses what fails.

## Design commitments

These are the claims the implementation is accountable to. Each is testable, and each is tested.

1. **No LLM output ever moves money.** The inline decision path is fully deterministic. Models run only in the async control plane, and their verdicts carry the lowest evidence class — structurally unable to clear a settlement alone.
2. **Fail-closed on evidence of wrongdoing, fail-soft on absence of evidence.** A bad signature denies. An unreachable key directory degrades to step-up, it does not deny.
3. **Decisions are idempotent.** The same request returns the same cached decision, never a re-evaluation.
4. **The audit trail is anchored outside our own database** — in the Razorpay order record — so it is verifiable by someone who does not trust us.
5. **A false positive is a bounded sale, not a lost one.** New agents are throttled onto a trust ladder rather than blocked.
6. **The evaluation corpus is frozen and hash-committed before any detector tuning.**

## Scope and ethics

Strictly defensive. Every attack in the red-team suite runs against our own sandbox merchant with our own test-mode keys. No weaponisable payloads against live protocol implementations are published in this repository.

## Status

**Day 4 implementation complete.** The verification core (G0–G3, adjudication, idempotent decision cache), bounded action envelope (G4, Clearing Passport, SIMULATED Reserve Pay block guard), obligation layer (signed receipts, append-only hash-chained ledger, Razorpay anchoring, webhook intake, reconciler), deterministic G5 content-threat gate, and clearing layer are built. The offline suite has 253 passing tests; measured data-plane p99 is 4.0 ms against a 50 ms budget.

Design commitment 1 is enforced structurally rather than by convention: the evidence lattice in `kya/evidence.py` is a genuine partial order, and `test_evidence_lattice.py` asserts that a SELF/SIGN-class verdict — which is all a model can declare — cannot satisfy a REC-class floor at any confidence.

Design commitment 2 — that the ladder is *observable*, not merely recorded — is enforced the same way. A promotion widens the token bucket on the very next request rather than at some later epoch, and `test_attacks_day2.py` asserts that the identical ₹5,000 purchase is stepped up at T0 and allowed at T2.

Graceful failure #1 is demonstrated end to end rather than described. The reconciler's defining property is negative — it never writes to the rail — and the tests assert it by inspecting which rail calls were made, not by reading the code. Recovery is possible at all because the obligation is minted and stored *before* the rail is called, committing to an order reference we chose ourselves; after a lost response that reference is the only handle on an order whose id we never learned.

Still to come: the red-team corpus, baselines, metrics, and the dashboard/API work. See [`../PLAN.md`](../PLAN.md) for the day-by-day plan and cut order.
