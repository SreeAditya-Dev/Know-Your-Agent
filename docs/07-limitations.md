# 07 — Limitations

Written before implementation, and updated with measured results. An unresolved exception presented openly scores higher than a perfect number nobody believes.

## What is simulated

**Reserve Pay / SBMD is a local simulation.** NPCI's Single Block Multi Debit is not available to a test-mode developer account, and the Unified Agent Protocol has not launched — it is in development at NPCI and requires RBI approval before it goes live.

So `kya/reserve_pay.py` models block-and-multi-debit semantics against a local ledger: reserve, debit, release, expiry. It is labelled `SIMULATED` in code, in API responses, in the dashboard and in the demo video.

What this does and does not prove: the **block guard logic** is real and tested — a debit with no backing obligation is rejected, and cumulative debits cannot exceed the reserve. What is not proven is integration against live NPCI rails. The control is demonstrated; the plumbing is not.

**Razorpay Orders, Payments, Refunds and Webhooks are real**, against `rzp_test_` keys. The anchoring in `order.notes` is real and independently verifiable in the Razorpay dashboard.

## Measured constraints in the live integration

**Razorpay's order *list* endpoint is eventually consistent.** Measured against live test mode on 30 Aug 2026: a newly created order is not findable by `receipt` for roughly 10-20 seconds, while `fetch_order` by id returns it immediately.

This matters more than it sounds, because the reconciler's recovery path is a lookup by our own reference — precisely the call that lags. Treating an early miss as "this order was never created" would produce that conclusion at the exact moment it is most dangerous: a lost response, where anyone acting on it re-places an order that already exists.

So the reconciler will not conclude absence inside a 60-second grace window (`PROPAGATION_GRACE_SECONDS`); it reports `lookup_too_soon` and defers. The cost of waiting is a later reconciliation. The cost of concluding early is a double charge.

Worth stating plainly because it is the kind of thing no amount of testing against a fake would ever have surfaced — the fake answered lookups instantly and agreed with our assumptions.

## What we do not implement

- **Full W3C Verifiable Credentials / JSON-LD.** Mandates are AP2-*shaped* — same fields, same signing semantics, same chain integrity — but serialized as plain signed JSON rather than JSON-LD with a full VC proof suite. The verification logic is equivalent; the wire format is simplified. A production version would use the real VC stack.
- **A real agent key directory.** We publish JWKS for our synthetic test agents. There is no integration with Visa's operated directory, which requires programme enrolment.
- **`ATT` and `PROOF` evidence classes.** The lattice supports them; no verifier produces them. TEE attestation and zero-knowledge proofs are out of scope for seven days.
- **Human verifier.** RAILS lists human review as a verifier class. We model it as the quarantine queue rather than a mesh participant.
- **Multi-merchant / marketplace topologies.** One merchant, one gateway.
- **Full durable control plane.** Neon persists the append-only obligation
  ledger and rail bindings when configured. The dashboard's decision feed,
  nonce cache, velocity counters and clearing passports remain process-local
  demo stores; a production deployment needs durable, shared implementations
  for those components too.
- **A live bridge to Razorpay's `razorpay-mcp-server`.** `kya/rails/mcp_adapter.py` exposes KYA's own guarded actions over MCP, gated identically to the HTTP API. It does not proxy Razorpay's separate 35+-tool MCP server against the raw Orders/Payments API — bridging two processes and two auth models honestly was out of scope for this window.

## Structural limits — things this design cannot fix

**We adjudicate on evidence, not on reality.** RAILS is explicit and we inherit the limitation: *"what happened in the world remains outside reach."* If every piece of evidence says the right item was delivered and it was not, KYA clears the obligation. A courier who signs a false receipt defeats the receipt verifier.

**Soundness is not correctness.** The soundness property guarantees no settlement rests on evidence below the declared floor. It does **not** guarantee verifiers are right, that attestors are honest, or that the merchant set a sensible `φO`. Those are governance questions.

**We sit on the merchant's side.** A colluding merchant-and-agent pair is outside the trust model entirely. We defend the merchant from agents, not the buyer from the merchant.

**The semantic verifier is the weakest component, by construction.** That is intentional — it is capped at `SELF`/`SIGN` class precisely because an LLM is not a reliable witness. It contributes flags, never clearings. Saying this out loud is stronger than hiding it.

**Tier ladders are gameable in principle.** An agent could build reputation on small legitimate transactions and then defect at T3. Mitigations — velocity caps that scale sublinearly with tier, basis-drift detection, dispute-triggered demotion — reduce but do not eliminate this. RAILS names the general form (LAUNDER-BASIS) and leaves defences outlined rather than specified.

## Expected failure modes in the eval

Anticipated before the run; the actual list is generated from measured results and will replace this one.

| Expected gap | Why it is hard |
|---|---|
| Slow-burn velocity abuse | Stays under every threshold across a long window. Detecting it requires cross-session behavioural modelling we do not do |
| Commercially absurd but valid carts | Correct SKU, plausible unit price, nonsense quantity. Passes every cryptographic check; needs merchant-specific business rules |
| Injection phrased as legitimate product copy | The deterministic matcher will miss it. The async LLM check may catch it, but only after the fact |
| First-transaction fraud from a T0 agent | Bounded to ₹2,000 by the ladder, not prevented. The ladder caps loss; it does not stop it |
| Legitimate cart revision mid-session | An agent that legitimately re-prices after a stock change looks like price drift. Expected source of false positives |

## Honest scoping note

This is a seven-day build (29 Aug – 5 Sep 2026). The cut order, documented in [`../PLAN.md`](../PLAN.md), was: MCP wrapper → dashboard → semantic verifier → G5 content threat. All four shipped; nothing on that list was actually cut.

If components are missing at submission, they are listed here with what was cut and why — not quietly omitted.

## What would come next

- Real VC serialization and directory federation
- `ATT`-class evidence from a TEE-backed fulfilment attestor
- Cross-merchant passport federation, which is where the cold-start problem actually gets solved — an agent's reputation should be portable, and today it is not
- Live SBMD integration once UAP clears RBI approval

---

Previous: [06 — API Reference](06-api.md) · Back to [index](README.md)
