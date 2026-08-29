# 04 — Obligation Clearing

This is the part of KYA that does not exist anywhere else in production. Everything in [02](02-architecture.md)'s data plane is a well-defended version of a known idea. This document is the new thing.

## The distinction that motivates it

From RAILS ([arXiv 2606.08790](https://arxiv.org/html/2606.08790)):

> **Authorization ≠ Clearing.** *"A human can authorize a travel agent to book a flight; the agent may book the wrong airport. Authorization establishes permitted agency. Clearing determines fulfilled obligation."*
>
> **Payment ≠ Clearing.** *"Payment settles value transfer. Clearing settles obligation state."*

An agent transaction can pass every identity check, carry a perfectly valid mandate chain, charge exactly the signed amount — and still deliver the wrong thing. Payment rails record that money moved. Nothing records whether the promise was kept.

## Obligation Receipt

Minted at the moment of ALLOW, **before capture**. Field names follow RAILS' Obligation Object so a reviewer can trace the lineage.

```python
ObligationReceipt:
    obligation_id: str
    version: int

    principal_ref: str          # the human who delegated
    agent_id: str
    agent_key_id: str           # which key signed — survives rotation
    merchant_id: str

    promised:                   # what was actually committed to
        line_items: [{sku, name, qty, unit_price}]
        total: int              # paise
        currency: str
        delivery_window: {from, to}
        return_window_days: int
        cancellation_terms: str

    acceptance_criteria: [Predicate]              # RAILS Ac
    evidence_requirements: [{claim, min_class}]   # RAILS E_req
    admissibility_floor: EvidenceClass            # RAILS φO

    mandate_chain_hash: str     # binds back to the signed intent + cart
    rail: {type: "razorpay_order" | "reserve_pay_block", ref: str}

    created_at: datetime
    expires_at: datetime

    prev_hash: str              # hash chain over the ledger
    self_hash: str
    merchant_signature: str
```

The receipt answers a question no payment object can: **what did the merchant actually promise?** SKU, price, delivery window and return terms, captured at commitment time, signed, and immutable.

## Anchoring — making the trail verifiable by someone who does not trust us

A tamper-evident log inside our own database proves very little to a dispute reviewer. We control the database.

So at order creation, `self_hash` is written into the Razorpay order's `notes` field:

```json
{ "notes": { "kya_obligation": "<self_hash>", "kya_version": "1" } }
```

The consequence is disproportionate to the effort. Razorpay's order record is immutable, timestamped, and outside our control. A reviewer holding nothing but Razorpay dashboard access can:

1. Read `notes.kya_obligation` from the order.
2. Take the obligation receipt we produce.
3. Recompute its hash independently.
4. Confirm they match — and that the order timestamp proves the receipt existed **before** capture.

That converts "trust our logs" into "verify against Razorpay's". It costs one line of code, which is why it is on Day 3 and not deferred.

## Evidence grading

Evidence is classified on RAILS' partial order. It is a **partial** order, not a ranking:

```
SELF ⪯ SIGN ⪯ {WIT, REC} ⪯ ATT ⪯ PROOF
```

| Class | Meaning | Example in KYA |
|---|---|---|
| `SELF` | Unverified self-report by the acting party | Agent asserts "delivered" |
| `SIGN` | Cryptographically signed by the acting party | Signed agent statement — non-repudiation, not truth |
| `WIT` | Third-party witness signature | Courier attests to handover |
| `REC` | Signed receipt from a non-interested external system | **Razorpay payment / refund / settlement object** |
| `ATT` | Attestation from a trusted execution environment | Out of scope here |
| `PROOF` | Cryptographic proof | Out of scope here |

`WIT` and `REC` are deliberately incomparable: a witness attests to having *observed* an event; a receipt records that an external system *processed* a transaction. Neither dominates.

**Composition rules** (from RAILS, implemented as specified):
- Multi-hop provenance takes the **meet** — the weakest link in the chain.
- A single verifier's basis is the **meet** of the items it relied on.
- The aggregate basis across surviving verifiers is the **join** — the strongest of them.

## The Verification Mesh

Four verifiers run independently. Each returns a verdict, a confidence, a **declared basis class**, a loss estimate, and a role tag.

| Verifier | Method | Basis class it can produce |
|---|---|---|
| **Constraint** | Deterministic evaluation of acceptance predicates | inherits from the evidence it reads |
| **Receipt** | Reconciles against Razorpay payment/refund/settlement objects and shipping webhooks | `REC` |
| **Semantic** | LLM judges whether delivery evidence describes the promised SKU | **`SELF` / `SIGN` only** |
| **Policy** | Merchant policy, dark-patterns guidelines, DPDPA | inherits |

### Why the LLM is capped at the bottom class

This is the single most important line in the design.

The aggregator enforces the admissibility floor: **any verdict whose basis class is below `φO` receives weight zero.** An obligation with `φO = REC` therefore cannot be cleared by the semantic verifier alone, no matter how confident the model is, because an LLM's opinion is `SELF`-class evidence by construction.

Stated plainly: **a model's opinion can never, by itself, clear a settlement.** Not because we chose to be cautious, but because the type system of the evidence lattice forbids it. The LLM is genuinely useful — it catches "the courier photo shows a phone case, the obligation promised a phone" — but it can only ever *corroborate* or *raise a dispute*. It cannot release money.

RAILS calls the alternative out directly: *"A judge gives an opinion, not a clearing decision."*

## Clearing Decision and finality

The aggregator emits a Clearing Decision carrying a performance verdict, a policy verdict, a fault assignment, the aggregate basis class, a confidence, and a finality status that starts at **PROVISIONAL**.

Finality requires all four conjuncts:

```
φ(CD, t, ε) =  cls(B) ⪰ φO                  evidence meets the floor
             ∧ c ≥ c_min                     confidence threshold
             ∧ NoUnresolvedConflict(V, ε)    verifiers do not contradict
             ∧ t ≥ t_emit + τ_appeal         appeal window elapsed
```

- All four hold → **FINAL**. Settlement stands.
- Any fails → **DISPUTED**. Reversal via the Razorpay Refunds API, or a Reserve Pay block release.

This yields RAILS' soundness property: *no financially material settlement is supported by evidence below the obligation's declared admissibility floor.* It is testable, and it is tested — verification step 6 in [`../PLAN.md`](../PLAN.md) submits deliberately under-class evidence and asserts the reversal fires.

### What soundness does *not* buy

Stated honestly, following the paper:

- It does not give **verifier correctness** — a verifier can honestly declare its basis and still be wrong on that basis.
- It does not give **ground truth** — the protocol adjudicates on evidence; what happened in the world remains outside its reach.
- It does not validate **`φO` itself** — whether the merchant set the right floor is a governance question, not a protocol one.

## Clearing Passport — and the cold-start answer

Each agent accumulates a passport: cleared count, disputed count, basis-drift events, current tier. It feeds G4 on the agent's next request, closing the loop from outcomes back to admission control.

| Tier | Spend cap | Velocity | Evidence floor | Step-up above |
|---|---|---|---|---|
| **T0** unknown | ₹2,000 | 3/hr | `REC` | ₹1,000 |
| **T1** seen | ₹10,000 | 20/hr | `REC` | ₹5,000 |
| **T2** established | ₹50,000 | 100/hr | `SIGN` | ₹25,000 |
| **T3** trusted | ₹2,00,000 | 500/hr | `SIGN` | ₹1,00,000 |

The cold-start problem is real and widely noted: to be trusted an agent needs a track record, and to build a track record it needs to be trusted. Reputation-based systems (which ACP relies on) deadlock new entrants.

The ladder breaks the deadlock by making the first interaction **bounded rather than refused**. A brand-new agent transacts immediately — capped at ₹2,000, three per hour, with a high evidence floor. Successful clearings move it up; disputes and basis drift move it down.

**This is also the false-positive story, and it is the honest answer to the hardest question anyone can ask of a gateway like this.** A false positive against a legitimate new agent is not a lost sale. It is a *bounded* sale, or a *stepped-up* one. So FP cost is reported decomposed:

- **Denied ₹** — revenue actually refused. The real cost.
- **Stepped-up ₹** — revenue that required principal re-auth. Friction, recoverable.
- **Delayed ₹** — revenue held in quarantine and later released. Latency, not loss.

A single blended false-positive rate would hide that distinction. Decomposing it is both more honest and more favourable.

## Worked example — A11, obligation mismatch

1. Agent `agent_x` passes G0–G6. Cart: 1 × `SKU-PHONE-256`, ₹54,999, delivery by 3 Sep.
2. Receipt minted: `φO = REC`, acceptance criteria include `delivered_sku == SKU-PHONE-256`. Hash anchored into `order.notes.kya_obligation`.
3. Payment captured. Settlement is **PROVISIONAL**.
4. Fulfilment evidence arrives: courier webhook (`REC`) plus a delivery photo the agent describes as the phone (`SELF`).
5. Mesh runs. Receipt verifier confirms delivery occurred — `REC`. Semantic verifier reads the photo as a *phone case*, not the phone — flags mismatch, but at `SELF`.
6. Constraint verifier evaluates `delivered_sku == SKU-PHONE-256` against the courier manifest: **fails**, basis `REC`.
7. Conflict between verifiers is unresolved; the performance verdict fails at `REC`, which meets the floor.
8. Clearing Decision → **DISPUTED**. Reversal issued via Razorpay Refunds. Passport records a dispute; tier drops.

Note what carried the decision: the constraint verifier at `REC`, not the LLM. The LLM raised the flag. The receipt-class evidence made it actionable. That division of labour is the whole design.

---

Previous: [03 — Threat Model](03-threat-model.md) · Next: [05 — Evaluation](05-evaluation.md)
