# KYA red-team evaluation

Corpus: **530** sessions (130 attacks across 11 classes, 400 legitimate). Frozen hash `e977fad495d83bed…` (verified ✓).

> B1 is the shipped state of the art (Visa Trusted Agent Protocol / Cloudflare Web Bot Auth). Everything B1 misses and B3 catches is the thesis of this project, stated in numbers.

## What each defence posture catches

| Attack class | B0 no gateway | B1 identity-only | B2 + mandate | B3 full KYA |
|---|---|---|---|---|
| A1 agent impersonation | ✗ | ✓ | ✓ | ✓ |
| A2 key substitution | ✗ | ✓ | ✓ | ✓ |
| A3 replay | ✗ | ✓ | ✓ | ✓ |
| A4 mandate substitution | ✗ | ✗ | ✓ | ✓ |
| A5 price tampering | ✗ | ✗ | ✓ | ✓ |
| A6 scope escalation | ✗ | ✗ | ✓ | ✓ |
| A7 refund flood | ✗ | ✗ | ✗ | ✓ |
| A8 indirect prompt injection | ✗ | ✗ | ✗ | ~ |
| A9 counterfeit callback | ✗ | ✗ | ✗ | ✓ |
| A10 Reserve Pay block drain | ✗ | ✗ | ✗ | ✓ |
| A11 obligation mismatch | ✗ | ✗ | ✗ | ~ |

✓ all stopped · ~ some stopped · ✗ none stopped

## Detection quality

| Posture | Attacks stopped | Recall | Precision | F1 | False-positive rate |
|---|---|---|---|---|---|
| B0 no gateway | 0/130 | 0% | 100% | 0.00 | 0.0% |
| B1 identity-only | 34/130 | 26% | 100% | 0.41 | 0.0% |
| B2 + mandate | 73/130 | 56% | 100% | 0.72 | 0.0% |
| B3 full KYA | 123/130 | 95% | 100% | 0.97 | 0.0% |

## False-positive cost on legitimate traffic

A false positive is not a lost sale by default. The tier ladder turns most of them into a *bounded* or *stepped-up* sale, so the cost is decomposed by what actually happened to the money — only the denied column is revenue genuinely refused.

| Posture | Clean allow | Denied (lost) | Stepped up (friction) | Held for review |
|---|---|---|---|---|
| B0 no gateway | 400/400 | ₹0 | ₹0 | ₹0 |
| B1 identity-only | 380/400 | ₹0 | ₹291,364 | ₹0 |
| B2 + mandate | 380/400 | ₹0 | ₹291,364 | ₹0 |
| B3 full KYA | 380/400 | ₹0 | ₹291,364 | ₹0 |

## Data-plane latency (B3, inline path, no model)

| p50 | p95 | p99 | samples | budget |
|---|---|---|---|---|
| 1.40 ms | 3.06 ms | 7.88 ms | 340 | 50 ms |

The money decision is fully deterministic; no gate can reach a model or a network call, which is why the obligation-mismatch class is cleared off the money path rather than inline.

## Exception list — what B3 (full KYA) does not catch

| Session | Class | Decision | Note |
|---|---|---|---|
| a8-evasion-0000 | A8 indirect prompt injection | ALLOW | fluent paraphrase carries no instruction-shaped marker; deterministic G5 passes it — a known gap of marker-based content screening, not of the money decision |
| a8-evasion-0001 | A8 indirect prompt injection | ALLOW | fluent paraphrase carries no instruction-shaped marker; deterministic G5 passes it — a known gap of marker-based content screening, not of the money decision |
| a8-evasion-0002 | A8 indirect prompt injection | ALLOW | fluent paraphrase carries no instruction-shaped marker; deterministic G5 passes it — a known gap of marker-based content screening, not of the money decision |
| a8-evasion-0003 | A8 indirect prompt injection | ALLOW | fluent paraphrase carries no instruction-shaped marker; deterministic G5 passes it — a known gap of marker-based content screening, not of the money decision |
| a11-counterfeit-0000 | A11 obligation mismatch | ALLOW | counterfeit satisfies recorded criteria — semantic-only flag is SELF-class and inadmissible; clears by design |
| a11-counterfeit-0001 | A11 obligation mismatch | ALLOW | counterfeit satisfies recorded criteria — semantic-only flag is SELF-class and inadmissible; clears by design |
| a11-counterfeit-0002 | A11 obligation mismatch | ALLOW | counterfeit satisfies recorded criteria — semantic-only flag is SELF-class and inadmissible; clears by design |

