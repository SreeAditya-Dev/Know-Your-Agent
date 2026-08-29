# 03 — Threat Model

## Scope and ethics

**Strictly defensive.** Every scenario in this suite runs against our own sandbox merchant, using our own `rzp_test_` keys, against our own gateway. This repository publishes no weaponisable payloads against live protocol implementations. Attack scenarios are described at the level of *class and mechanism* — enough to reproduce the defence, not enough to hand anyone a tool.

## Trust boundaries

| Party | Trust | Reasoning |
|---|---|---|
| **Inbound agent** | Untrusted | May be impersonating a known operator. DataDome measured ~2.4% impersonation on PerplexityBot and >16M spoofed Meta-ExternalAgent requests in Jan–Feb 2026 |
| **Agent operator's key directory** | Semi-trusted | Authoritative for keys, but reachable over the network and therefore an availability dependency, not a guarantee |
| **Principal (human)** | Trusted via mandate | Only as far as the signed delegation reaches |
| **Catalog / coupon content** | Untrusted | Indirect prompt-injection carrier |
| **Merchant backend** | Trusted | Inside the boundary |
| **Razorpay APIs** | Trusted | Authoritative for payment state; treated as REC-class evidence |
| **Fulfilment evidence** | Graded, not trusted | Admissibility class determines weight — see [04](04-obligation-clearing.md) |

## Sources

The taxonomy is drawn from published work, deliberately not invented for this project:

- **SoK: Security of Autonomous LLM Agents in Agentic Commerce** — [arXiv 2604.15367](https://arxiv.org/pdf/2604.15367): prompt injection, MITM, indirect injection, transaction manipulation, memory poisoning, unauthorized fund access, negotiation exploitation, compliance evasion.
- **Unit 42, *Retail Fraud in the Age of Agentic AI*** — payload poisoning in gift cards, logic hijacking in returns flows, scripted refund chains, AI-friendly counterfeit storefronts.
- **RAILS** — [arXiv 2606.08790](https://arxiv.org/html/2606.08790): the obligation/performance gap, and the LAUNDER-BASIS attack on evidence grading.
- **DataDome AI Traffic Report** (Jan–Feb 2026) — impersonation base rates.

Using published taxonomies rather than self-authored ones is also the first half of the anti-rigging argument in [05](05-evaluation.md).

## The eleven attack classes

| # | Attack | Mechanism | Caught by | Reason code |
|---|---|---|---|---|
| A1 | **Agent impersonation** | Request claims a known operator's user-agent with no valid signature, or a signature from an unregistered key | G1 | `I001` / `I002` / `I003` |
| A2 | **Key substitution / directory poisoning** | Signature verifies against a key the operator's directory does not actually publish; or a rotated-out key is reused | G1 | `I003` |
| A3 | **Replay** | A previously valid signed request is resent — captured nonce reuse, or a stale `created` timestamp | G0 | `R001` / `R002` / `R003` |
| A4 | **Mandate substitution** | A genuinely signed cart mandate is presented alongside a *different* cart at charge time | G3 | `C001` |
| A5 | **Price / total tampering** | Line items match but unit price, shipping, tax or total drift between signature and charge | G3 | `C002` |
| A6 | **Scope escalation** | Charge exceeds the intent's `max_price`, targets a merchant outside `allowed_merchants`, or a disallowed category | G3 / G4 | `C004` / `E005` |
| A7 | **Refund flood** | Bot-farm shaped burst of refund requests intended to drain cash faster than human review — the Unit 42 scenario | G4 | `E003` |
| A8 | **Indirect prompt injection** | Instructions embedded in catalog copy, coupon codes, or free-text order fields, aimed at the merchant's own downstream agents | G5 | `T001` |
| A9 | **Counterfeit merchant callback** | Webhook or return URL pointing at an attacker-controlled domain to spoof fulfilment or payment confirmation | G5 | `T002` |
| A10 | **Reserve Pay block drain** | Many small debits against one authorized block with no matching obligation — the SBMD-specific attack | G4 | `E004` |
| A11 | **Obligation–fulfilment mismatch** | Payment succeeds and clears every identity and mandate check, but what was delivered is not what was promised | Clearing | `DISPUTED` |

## What the layers actually buy you

This is the core argument of the project, stated as a table. **B1 is the shipped state of the art** — Visa TAP, Cloudflare Web Bot Auth, and the identity layer of every major agentic-commerce stack.

| Attack | B0 none | B1 identity-only | B2 + mandate (AP2-equivalent) | B3 full KYA |
|---|:--:|:--:|:--:|:--:|
| A1 impersonation | ✗ | ✓ | ✓ | ✓ |
| A2 key substitution | ✗ | ✓ | ✓ | ✓ |
| A3 replay | ✗ | ✓ | ✓ | ✓ |
| A4 mandate substitution | ✗ | ✗ | ✓ | ✓ |
| A5 price tampering | ✗ | ✗ | ✓ | ✓ |
| A6 scope escalation | ✗ | ✗ | ~ | ✓ |
| A7 refund flood | ✗ | ✗ | ✗ | ✓ |
| A8 indirect injection | ✗ | ✗ | ✗ | ✓ |
| A9 counterfeit callback | ✗ | ✗ | ✗ | ✓ |
| A10 block drain | ✗ | ✗ | ✗ | ✓ |
| A11 obligation mismatch | ✗ | ✗ | ✗ | ✓ |

Read the B1 column. A verified agent identity is a real and valuable control — and it stops none of A4 through A11. **A correctly identified, correctly authorized agent can still substitute a cart, flood refunds, drain a block, and take delivery of something other than what was promised.** That is the gap, and populating this table with measured numbers rather than checkmarks is what [05](05-evaluation.md) does.

The `~` on A6/B2 is deliberate: AP2 mandates carry constraints, so a well-implemented mandate check catches *some* scope escalation. It does not catch escalation expressed through velocity across many individually-compliant transactions. That is a G4 property.

## Non-goals

Named explicitly so the boundary is legible, and so nothing here reads as an unstated claim:

- **We do not defend the agent.** Agent-side hardening — memory poisoning, model jailbreaks, tool-chain compromise — is Pipelock's and agentfw's territory. KYA assumes the agent may be fully compromised and defends the merchant anyway.
- **We do not do network-layer bot management.** No IP reputation, no TLS fingerprinting, no CAPTCHA. Cloudflare does that better.
- **We do not solve consumer dispute liability.** We produce the artifact that makes liability *arguable* — a replayable obligation record. Who pays is a policy question.
- **We do not verify physical-world ground truth.** RAILS is explicit that clearing adjudicates on evidence; what actually happened in the world remains outside the protocol's reach. See [07](07-limitations.md).

---

Previous: [02 — Architecture](02-architecture.md) · Next: [04 — Obligation Clearing](04-obligation-clearing.md)
