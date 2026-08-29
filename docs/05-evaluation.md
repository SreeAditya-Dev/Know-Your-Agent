# 05 — Evaluation

Two questions decide whether a defence like this is credible:

1. **What does a false positive actually cost?**
2. **Why should anyone believe a synthetic dataset?**

This document answers both before being asked.

## Corpus

| Segment | Count | Composition |
|---|---|---|
| Legitimate agent sessions | ~400 | Spread across T0–T3 tiers, amounts ₹200–₹2,00,000, multiple categories |
| — of which benign edge cases | ~80 | Price rounding, partial fulfilment, legitimate refunds, retry-after-timeout, delayed webhooks, legitimate cart revision within mandate scope |
| Attack sessions | ~120 | 11 classes from [03](03-threat-model.md), ~11 per class |

**The benign edge cases matter more than the attacks.** Anyone can build a detector that blocks obvious fraud; the difficulty is not blocking a legitimate agent that retried after a timeout, or one whose cart total moved ₹2 on a tax rounding boundary. Roughly a fifth of the legitimate corpus is deliberately adversarial *toward the detector's false-positive rate*, and those cases are where the reported FPR is actually earned.

Split: held-out test set never used for tuning.

## The anti-rigging protocol

Synthetic datasets are trivially riggable — author the attacks after building the detector and every number comes out perfect and meaningless. Our defence is procedural and checkable in git history:

1. **Attack classes derive from published taxonomies, not from us.** SoK ([arXiv 2604.15367](https://arxiv.org/pdf/2604.15367)), Unit 42's retail-fraud research, RAILS' threat section, DataDome's traffic report. The classes were fixed on Day 0, in [03](03-threat-model.md), before any gate existed.
2. **Corpus is committed, then its SHA-256 is committed separately.** Two commits, in that order.
3. **Detector tuning happens only in commits after the hash commit.**

So the answer to "how do we know you didn't tune against your own attacks" is not a claim. It is:

> The corpus hash is committed at `<sha>`. Every commit touching detector thresholds comes after it. `git log` is the audit.

We also report **which attacks we failed to block** (below). A suite with a 100% block rate is evidence of a rigged suite, not a good gateway.

## Baselines

Each baseline is a real configuration of the gateway, not a strawman — the same code path with gates disabled:

| | Gates active | Represents |
|---|---|---|
| **B0** | none | No gateway. The current default for most merchants |
| **B1** | G0, G1 | **Identity-only.** Visa TAP / Cloudflare Web Bot Auth — the shipped state of the art |
| **B2** | G0–G3 | Identity + mandate + cart binding. An AP2-equivalent implementation |
| **B3** | G0–G6 + clearing | Full KYA |

B1 is the honest comparison. It is what a merchant integrating Visa TAP today actually gets, and it is a genuinely good control — it stops impersonation, key substitution and replay outright. The measurement exists to show precisely where it stops helping.

## Metrics reported

**Detection**
- Per-class block rate across all 11 attack classes × 4 baselines
- Precision, recall, F1 on the held-out split
- Confusion matrix

**False-positive cost — decomposed, not blended**
- FPR on legitimate traffic, broken out by outcome: DENIED / STEP_UP / QUARANTINE
- **Denied ₹** — revenue actually refused (the real cost)
- **Stepped-up ₹** — revenue requiring principal re-auth (friction, recoverable)
- **Delayed ₹** — revenue quarantined then released (latency, not loss)

A blended FPR would conceal that the tier ladder converts most false positives from refusals into delays. The decomposition is both more honest and more favourable — which is why it is worth doing properly.

**Performance**
- Data-plane latency p50 / p95 / p99, LLM path disabled
- Assertion: **p99 < 50 ms**, proving money decisions never block on a model

**Clearing**
- Obligations cleared FINAL vs DISPUTED
- Reversal correctness: disputes that should have reversed and did; reversals that should not have fired
- Basis-class distribution of clearing decisions — how often does `REC`-class evidence actually arrive?

**Passport dynamics**
- Tier migration over the corpus run
- Time-to-T2 for a legitimate new agent (the cold-start cost, in transactions)

## Report format

`python -m redteam.run --all` emits a markdown table, committed to the repo and reproduced in the README:

```
ATTACK BLOCK RATE                    B0      B1      B2      B3
A1  agent impersonation             0.0%   100%    100%    100%
A2  key substitution                 ...
...
A10 reserve pay block drain         0.0%    0.0%    0.0%     ...
A11 obligation mismatch             0.0%    0.0%    0.0%     ...

LEGITIMATE TRAFFIC                   B0      B1      B2      B3
false positive rate                  ...
  └ denied                           ...
  └ stepped up                       ...
  └ quarantined                      ...

FP COST (₹)                          denied / stepped-up / delayed
LATENCY (ms)                         p50 / p95 / p99
CLEARING                             final / disputed / reversal accuracy
```

Numbers are filled in from actual runs. None are written in advance, and none appear in this document.

## Graceful failure demonstrations

Track 01 asks for one failure handled gracefully. We demonstrate two, because they fail in opposite directions and the pair is more informative than either alone.

**GF1 — payment succeeded, response lost.** The classic agentic double-charge. An agent creates an order, payment is captured, the response never arrives. A naive agent retries and pays twice.

KYA: obligation is open with no capture confirmation. The reconciler polls Razorpay payment state, finds the payment already captured, **does not retry**, binds the existing capture to the obligation, and emits clearing. Duplicate charges: zero.

**GF2 — key directory unreachable.** The availability failure that would tempt a fail-open. If the operator's JWKS endpoint is down, denying every agent request zeroes agent revenue for the outage.

KYA: serve stale-while-revalidate from cache; with no cache, degrade to STEP_UP rather than DENY. A *positive* signature failure still denies, even in degraded mode. Revenue preserved, safety preserved, and the audit trail records `I004 directory_unreachable_degraded` so the degradation is visible rather than silent.

## Exception list

A section of the final report is reserved for **attacks we did not block, and legitimate traffic we wrongly stopped**, each with a diagnosis. It is written from the actual run, not curated.

An unresolved exception presented openly scores higher than a perfect number nobody believes. Known candidates, expected to appear:

- Slow-burn velocity abuse that stays under every threshold across a long window
- Semantically valid but commercially absurd carts (correct SKU, plausible price, nonsense quantity)
- Collusive merchant + agent pairs — outside the trust model entirely, since we sit on the merchant's side
- Injection payloads phrased as legitimate product copy

---

Previous: [04 — Obligation Clearing](04-obligation-clearing.md) · Next: [06 — API Reference](06-api.md)
