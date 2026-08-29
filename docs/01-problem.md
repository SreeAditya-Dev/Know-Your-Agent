# 01 — The Problem

## Everyone is building the buyer side

The 2026 agentic-commerce stack is crowded and converging fast:

| Layer | What ships today |
|---|---|
| Agent identity | Visa **Trusted Agent Protocol** (live 14 Oct 2025; Stripe, Adyen, Shopify, Worldpay, Checkout.com, Fiserv, Nuvei, Microsoft, Coinbase, Ant, Elavon, CyberSource) · Cloudflare **Web Bot Auth** (RFC 9421, in production at Cloudflare, AWS WAF, Akamai, HUMAN, Vercel) |
| User authorization | **AP2** (Google, 60+ partners) — Intent, Cart and Payment Mandates as W3C Verifiable Credentials |
| Checkout orchestration | **ACP** (OpenAI + Stripe, Apache-2.0, stable 2026-04-17) with Shared Payment Tokens · **UCP** (Google + Shopify) |
| Credential scoping | Mastercard Agentic Tokens · Skyfire KYAPay · Experian Agent Trust · MetaComp KYA Framework |
| Agent-side hardening | Open-source firewalls: Pipelock, mcp-firewall, agentfw |
| Indian rails | NPCI **Unified Agent Protocol** (in development, pending RBI approval) · Razorpay **UPI Reserve Pay** on NPCI's Single Block Multi Debit |

Building "verify the agent's signature" in 2026 is re-implementing a shipped product category. That is the first trap.

## The second trap: Razorpay already ships the obvious answers

Agent Studio launched eight production agents at FTX'26 — Dispute Responder, Subscription Recovery, two Abandoned Cart variants, Cashflow Forecaster, RTO Shield, RTO Insights, Settlement Insights — plus a no-code agent builder. Agentic UPI on Claude is live with Zomato, Swiggy and Zepto.

Track 01's example directions (conversational checkout, agent-readable catalog, upsell agent, campaign orchestrator) map almost 1:1 onto that. Rebuilding one of them adds nothing to a stack that already contains it.

## The actual gap: nobody clears the obligation

The **RAILS** paper ([arXiv 2606.08790](https://arxiv.org/html/2606.08790), June 2026) makes the decomposition precise. An agent transaction raises six separable questions:

| # | Question | Answered today by |
|---|---|---|
| 1 | **Authorization** — was the agent allowed to act? | AP2 mandates, TAP attestations |
| 2 | **Execution** — what did the agent actually do? | tool logs, partially |
| 3 | **Performance** — did the action satisfy the obligation? | **nothing in production** |
| 4 | **Attribution** — if it failed, who caused it? | **nothing in production** |
| 5 | **Loss** — what harm occurred? | insurance frameworks, after the fact |
| 6 | **Settlement** — what consequence follows? | payment rails settle *value*, not *obligation* |

The paper's own line:

> *"Payment rails establish transfer. They do not establish performance. Payment settles value transfer. Clearing settles obligation state."*

RAILS is a protocol proposal. It has no implementation. Questions 3, 4 and 6 are open in practice.

## Why this is urgent on Indian rails specifically

Razorpay's UPI Reserve Pay is built on NPCI's Single Block Multi Debit framework. From Razorpay's own description: a user blocks funds against a merchant, and then

> *"within that predefined boundary, the merchant can debit multiple times without requiring fresh authentication for each transaction."*

The boundary is three parameters: **total amount, time window, merchant scope.** That is a spending envelope. It is not an obligation check.

Nothing in that flow verifies that any individual debit corresponds to a real obligation the merchant actually incurred. One consent, many debits, no per-debit justification. That is a structurally new attack surface, and it is on rails Razorpay shipped this year.

The attack shape is already documented. Unit 42's retail-fraud research describes agents triggering refund primitives without a real shipping scan, and organised crime using bot farms to initiate ten thousand returns in an hour — draining a retailer's cash before a human notices. DataDome measured 7.9 billion AI-agent requests across January–February 2026, with PerplexityBot at a ~2.4% impersonation rate and over 16 million spoofed Meta-ExternalAgent requests in two months.

And NPCI's stated blocker, from the UAP reporting, is precisely question 4:

> *"How do we control a machine going rogue? We need all parties having that information if something goes wrong. You should be able to review it if something goes wrong."*

## The third asymmetry: inbound vs outbound

Razorpay Agent Studio has real guardrails — merchant-configured discount ceilings, approval gates over WhatsApp, review-first mode, one-tap disable, dark-pattern compliance, platform-level action validation.

Every one of those governs an **outbound** agent: one the merchant employs, running inside Razorpay's infrastructure, on merchant-authorized data.

None of it governs an **inbound** agent: a third-party AI buyer arriving from ChatGPT, Claude, Perplexity or an operator nobody has heard of, which the merchant does not control, cannot configure, and cannot switch off.

That asymmetry is the product gap.

## What KYA is

A merchant-side gateway that sits in front of the Razorpay Orders/Payments surface and makes a merchant safely transactable by machine buyers:

1. **Verifies the inbound agent** — RFC 9421 signatures, TAP/Web Bot Auth compatible, so existing agents work unmodified.
2. **Verifies the mandate chain** — AP2-shaped intent and cart mandates, and critically that the cart being *charged* is the cart that was *signed*.
3. **Bounds what the agent can do** — velocity, spend, refund-rate circuit breaker, and a per-debit obligation check on Reserve Pay blocks.
4. **Mints an obligation receipt** before capture, anchored into the Razorpay order record itself.
5. **Clears the obligation** against graded evidence, and reverses provisional settlements that fail verification.
6. **Answers the cold-start problem** with a trust ladder, so a new legitimate agent is bounded rather than blocked.

## What we claim, and what we do not

**We claim:** the first working implementation of obligation clearing bound to a real payment rail, and a measured demonstration of what identity-only defence fails to catch.

**We do not claim:** that agent identity verification is novel (it is a shipped category), that RAILS is our idea (it is cited), or that our Reserve Pay integration is live (it is a labelled simulation).

---

Next: [02 — Architecture](02-architecture.md)
