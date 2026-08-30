# 08 — Pitch Script

Day 7 deliverable. A five-minute demo video script, timed to the beats the plan committed to, using only artifacts that already exist in this repo — nothing here requires building anything new. Numbers below are pulled live from `redteam/results.json` on 2026-08-30 and should be re-pulled the day of recording, since the corpus can be re-run (see the pre-recording checklist).

**Do not re-record numbers into the script by hand.** Run `python -m redteam.run --all` right before recording and read the printed table on camera, or screen-capture it — a number spoken from memory that drifts from what `redteam/REPORT.md` says on disk is exactly the kind of inconsistency a panel catches.

---

## Pre-recording checklist

- [ ] `pytest tests/ -q` — full suite green (285 tests as of this writing)
- [ ] `python -m redteam.run --verify` — corpus hash matches the committed one
- [ ] `python -m redteam.run --all` — regenerate `redteam/REPORT.md` fresh, confirm the headline numbers you're about to say out loud
- [ ] `cp .env.example .env`, fill in `rzp_test_` keys — needed for the live-purchase beat
- [ ] `python -m kya.live_check` — confirm it places a real test-mode order and prints an order id you can open in the Razorpay dashboard live, on camera
- [ ] `uvicorn kya.api.app:app --reload` — dashboard running at `localhost:8000/dashboard`, seeded demo records visible
- [ ] Screen recording at 1080p minimum; test-mode Razorpay dashboard open in a second tab, logged in, ready to alt-tab to
- [ ] Close anything showing real credentials, real emails, or unrelated tabs before recording

---

## Beat 1 — Hook (0:00–0:30)

**On screen:** title card, then the Track 01 example-directions list (checkout drop-off recovery, failed-subscription recovery, chargeback evidence responder…) with a red strike-through, then Razorpay's own eight shipped Agent Studio agents.

**Say:**

> Razorpay already shipped eight production commerce agents this year — dispute response, subscription recovery, abandoned-cart, cashflow forecasting. If we build another one of those, the panel isn't comparing us to other students. They're comparing us to Razorpay's own shipped product, and we lose that comparison automatically.
>
> So we didn't build an agent. We built the thing that has to exist *before* any of those agents can be trusted with a machine buyer: a way to tell whether the obligation a merchant took on was actually satisfied. Identity says who called. A mandate says they were allowed. Neither says the obligation was kept. That's the gap. This is Know-Your-Agent.

## Beat 2 — Live legitimate purchase (0:30–1:30)

**On screen:** dashboard `localhost:8000/dashboard`, then a terminal running `python -m kya.live_check` against real `rzp_test_` keys, then the Razorpay test-mode dashboard opened live to show the resulting order.

**Say:**

> Here's an AI buyer agent — correctly identified, correctly authorized — making a real purchase against Razorpay's test-mode API. Watch what happens before the money moves: the gateway verifies the RFC 9421 signature, walks the AP2-shaped mandate chain, binds the cart being charged to the cart that was signed, checks velocity and spend bounds — all in under two milliseconds, no model in the loop.
>
> On allow, it mints a signed obligation receipt — what was promised, SKU, price, delivery window — *before* touching the rail, and writes its hash into the order's own `notes` field. [switch to Razorpay dashboard] Here's that same order, in Razorpay's own dashboard, with `kya_obligation` sitting right there in the notes. I can recompute that hash from the receipt alone and it matches — this audit trail is verifiable by someone who doesn't trust us at all, because it's anchored in Razorpay's own record, not ours.

## Beat 3 — Live attack blocked, with reason codes (1:30–3:00)

**On screen:** the dashboard's decision-inspect view, or a terminal invoking the gateway directly with one of the red-team attack builders (`redteam/scenarios.py`) — recommend the mandate-substitution attack (A4), since it is the thesis stated as an attack: a fully genuine signature and an intact mandate chain wrapped around a substituted cart.

**Say:**

> Now the same agent, same real signature, same real mandate — but the cart it's presenting for charge isn't the cart it signed. This is the attack identity-only defence cannot see, because nothing about the *identity* is wrong.
>
> [show gate trace] G1, identity: pass. G2, mandate chain: pass. G3, cart binding: fail — `C003`, SKU substitution. Denied, with the exact field that drifted and by how much. This is the reason code an operator reads, not a black-box "no."
>
> And it isn't one trick. We ran this against eleven attack classes from the published SoK on agentic-commerce security and Unit 42's retail-fraud research — refund floods shaped like the bot farms Unit 42 documented, a simulated Reserve Pay block drain, indirect prompt injection through catalog text, counterfeit callback domains. [cut to `redteam/REPORT.md` table] Here's what each defence posture catches. B1 — identity-only, what a merchant integrating Visa's Trusted Agent Protocol gets today — stops one attack in four. Our full gateway stops 95%.

## Beat 4 — Graceful failure (3:00–3:45)

**On screen:** terminal: kill the webhook listener mid-payment (or replay the recorded reconciler test scenario), restart, show the reconciler log line, then show zero duplicate order in the Razorpay dashboard.

**Say:**

> Track 01 asks for one failure handled gracefully, so here it is, live rather than described. A payment succeeds, but the response is lost before our service sees it — the exact failure mode that causes double charges in production systems. [kill/restart] The reconciler polls Razorpay directly, finds the payment was actually captured, and *does not retry*. It binds the existing capture to the obligation that was already open, and clears it. Zero duplicate charge. [show dashboard] One order, one charge, one receipt — even though our own service crashed in the middle.
>
> This works because the obligation is minted *before* the rail is ever called, committing to a reference we chose ourselves. After a lost response, that reference is the only handle we have on an order whose id we never learned — and it's enough.

## Beat 5 — Metrics table (3:45–4:45)

**On screen:** `redteam/REPORT.md` rendered full-screen, or the dashboard's `/dashboard/metrics` page.

**Say:**

> Everything so far has been one example. Here's the measured version, over a corpus of 530 sessions — 130 attacks across eleven classes, 400 legitimate purchases — frozen and SHA-256 committed before we tuned a single detector, so this number can't be quietly rigged after the fact.
>
> Identity-only defence: 26% recall. Add the AP2-shaped mandate layer: 56%. Full KYA: 95% — at 100% precision, and zero rupees of legitimate revenue actually refused. Every false positive on real traffic is friction — a step-up, a held-for-review — never a lost sale, because the trust ladder bounds a new agent instead of blocking it.
>
> And we're not claiming a perfect number nobody would believe. Here's our own exception list: a fluent prompt-injection paraphrase our deterministic content gate misses, and a convincing counterfeit delivery that satisfies every acceptance criterion we can check deterministically — because an LLM's opinion is the weakest class of evidence in our system, and it can never, by itself, clear a settlement. We're showing you exactly where that costs us.

## Beat 6 — Close (4:45–5:00)

**On screen:** the B1→B3 comparison table one more time, then the repo README's core diagram.

**Say:**

> Every shipping agent-commerce standard today answers "who is this" and "were they allowed." None of them answer "was what was promised actually delivered." That's not a gap in Razorpay's product — it's a gap in the industry, and it's the one NPCI itself named as unsolved. Know-Your-Agent is the merchant's side of that answer: bounded, explainable, gated, and measured against the state of the art it's meant to improve on.

---

## Notes for whoever records this

- Every beat above maps to something that actually runs in this repo today — nothing is staged or faked. If a beat's artifact has drifted (a different tool wraps the mandate-substitution attack, the dashboard route moved), fix the *script* to match the *code*, never the reverse.
- Keep test-mode order ids and any real dashboard screenshots free of anything that isn't already public in this repo — no stray browser tabs, no personal email visible in the Razorpay account switcher.
- If recording runs long, the safest beat to compress is 4 (graceful failure) to 30s — show the "before" and "after" state without narrating the mechanism in full; the mechanism is in `docs/07-limitations.md` and `docs/04-obligation-clearing.md` for anyone who asks in the Q&A.

---

Previous: [07 — Limitations](07-limitations.md) · Back to [index](README.md)
