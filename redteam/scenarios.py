"""Scenario builders — one per session ``kind``.

Every attack here is a *perturbation of traffic that would otherwise pass*.
That is the discipline that makes the numbers mean something: exactly one thing
is wrong with each attack session, so the gate that fires is the gate being
measured, and a legitimate session that gets blocked is a real false positive
rather than a malformed request that deserved to fail.

The eleven attack classes track ``docs/03-threat-model.md`` one-to-one:

    A1  agent impersonation          A7  refund flood (bot-farm shape)
    A2  key substitution             A8  indirect prompt injection
    A3  replay                       A9  counterfeit merchant callback
    A4  mandate substitution         A10 Reserve Pay block drain (SIMULATED)
    A5  price / total tampering      A11 obligation--fulfilment mismatch
    A6  scope escalation

A11 is the only class caught off the money path — the purchase itself is
legitimate, and the mismatch only becomes visible at fulfilment — so it is
detected by the clearing layer, which exists only under B3.
"""

from __future__ import annotations

import base64
import uuid
from datetime import timedelta
from typing import Callable

from kya.canonical import now_utc
from kya.enums import Decision, RailType, Tier
from kya.simulation import (
    AgentIdentity,
    Principal,
    build_block_debit_request,
    build_refund_request,
    build_signed_request,
    content_digest,
    make_cart,
    make_mandates,
    make_obligation,
    resign_request,
)

from redteam.harness import Baseline, Outcome, Session, allow_outcome, new_sandbox

CLASS_NAMES: dict[str, str] = {
    "A1": "agent impersonation",
    "A2": "key substitution",
    "A3": "replay",
    "A4": "mandate substitution",
    "A5": "price tampering",
    "A6": "scope escalation",
    "A7": "refund flood",
    "A8": "indirect prompt injection",
    "A9": "counterfeit callback",
    "A10": "Reserve Pay block drain",
    "A11": "obligation mismatch",
}

#: Emitted in the comparison table, in this order.
CLASS_ORDER = list(CLASS_NAMES)


def _codes(env) -> list[str]:
    return list(env.reason_codes)


def _stopped(env) -> bool:
    from redteam.harness import STOPPING

    return env.decision in STOPPING


# --- legitimate traffic ------------------------------------------------------


def _legit_purchase(spec: dict) -> Session:
    amount = spec["amount"]
    tier = Tier(spec["tier"])
    category = spec["category"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(amount)
        sandbox, agent, principal = new_sandbox(baseline)
        cart = make_cart(
            items=[(spec["sku"], spec["name"], 1, amount)], category=category
        )
        mandates = make_mandates(agent, principal, cart, max_amount=amount * 2)
        env = sandbox.evaluate(build_signed_request(agent, mandates, cart), tier=tier)
        return Outcome(
            decision=env.decision,
            stopped=_stopped(env),
            amount=amount,
            reason_codes=_codes(env),
            latencies=[env.latency_ms],
        )

    return Session(spec["id"], "LEGIT", None, run, spec)


def _legit_refund_after_orders(spec: dict) -> Session:
    """The refund-breaker's false-positive guard: one refund against many real
    orders is ordinary retail and must clear."""
    amount = spec["amount"]
    n_orders = spec["orders"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(amount)
        sandbox, agent, principal = new_sandbox(baseline)
        for _ in range(n_orders):
            cart = make_cart(items=[("SKU-A", "Item", 1, amount)])
            mandates = make_mandates(agent, principal, cart, max_amount=amount * 4)
            sandbox.evaluate(build_signed_request(agent, mandates, cart))
        cart = make_cart(items=[("SKU-A", "Item", 1, amount)])
        mandates = make_mandates(agent, principal, cart, max_amount=amount * 4)
        env = sandbox.evaluate(build_refund_request(agent, mandates, cart, amount))
        return Outcome(
            decision=env.decision,
            stopped=_stopped(env),
            amount=amount,
            reason_codes=_codes(env),
        )

    return Session(spec["id"], "LEGIT", None, run, spec)


def _legit_retry(spec: dict) -> Session:
    """A well-behaved agent retrying an identical request. Idempotency must
    return the same ALLOW, not re-evaluate into a different answer."""
    amount = spec["amount"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(amount)
        sandbox, agent, principal = new_sandbox(baseline)
        cart = make_cart(items=[("SKU-A", "Item", 1, amount)])
        mandates = make_mandates(agent, principal, cart, max_amount=amount * 2)
        request = build_signed_request(agent, mandates, cart)
        first = sandbox.evaluate(request)
        env = sandbox.evaluate(request)  # identical retry
        stopped = _stopped(first) or _stopped(env)
        return Outcome(
            decision=env.decision,
            stopped=stopped,
            amount=amount,
            reason_codes=_codes(env),
        )

    return Session(spec["id"], "LEGIT", None, run, spec)


def _legit_high_value_stepup(spec: dict) -> Session:
    """Legitimate traffic above the tier's step-up threshold. This is not a
    lost sale — it is friction — and it exists in the corpus so the false-
    positive cost can be decomposed into denied vs merely stepped-up rupees."""
    amount = spec["amount"]
    tier = Tier(spec["tier"])

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(amount)
        sandbox, agent, principal = new_sandbox(baseline)
        cart = make_cart(items=[("SKU-HV", "High value item", 1, amount)])
        mandates = make_mandates(agent, principal, cart, max_amount=amount * 2)
        env = sandbox.evaluate(build_signed_request(agent, mandates, cart), tier=tier)
        return Outcome(
            decision=env.decision,
            stopped=_stopped(env),
            amount=amount,
            reason_codes=_codes(env),
        )

    return Session(spec["id"], "LEGIT", None, run, spec)


# --- A1 · agent impersonation ------------------------------------------------


def _a1(spec: dict) -> Session:
    variant = spec["variant"]
    amount = spec["amount"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(amount)
        sandbox, agent, principal = new_sandbox(baseline)
        cart = make_cart(items=[("SKU-A", "Item", 1, amount)])

        if variant == "unpublished_key":
            impostor = AgentIdentity.create(
                agent.agent_id, origin=agent.origin, key_seed_tag="impostor"
            )
            mandates = make_mandates(impostor, principal, cart, max_amount=amount * 2)
            request = build_signed_request(impostor, mandates, cart)
        else:
            mandates = make_mandates(agent, principal, cart, max_amount=amount * 2)
            request = build_signed_request(agent, mandates, cart)
            if variant == "unsigned":
                request.signature = None
                request.signature_input_raw = None
            elif variant == "tampered_sig":
                label, _, wire = request.signature.partition("=")
                raw = bytearray(base64.b64decode(wire.strip(":")))
                raw[0] ^= 0xFF
                request.signature = (
                    f"{label}=:{base64.b64encode(bytes(raw)).decode()}:"
                )
            elif variant == "missing_sig_agent":
                request.signature_agent = None

        env = sandbox.evaluate(request)
        return _attack_outcome(env, amount)

    return Session(spec["id"], "ATTACK", "A1", run, spec)


# --- A2 · key substitution ---------------------------------------------------


def _a2(spec: dict) -> Session:
    variant = spec["variant"]
    amount = spec["amount"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(amount)
        sandbox, agent, principal = new_sandbox(baseline)
        cart = make_cart(items=[("SKU-A", "Item", 1, amount)])

        if variant == "rotated_out":
            mandates = make_mandates(agent, principal, cart, max_amount=amount * 2)
            request = build_signed_request(agent, mandates, cart)
            sandbox.fetcher.withdraw(agent.origin, agent.keypair.key_id)
            sandbox.directory.invalidate(agent.origin)
        else:  # other_directory
            other = sandbox.register_agent(AgentIdentity.create("agent_other"))
            impersonator = AgentIdentity.create(
                agent.agent_id, origin=other.origin, key_seed_tag="impostor"
            )
            mandates = make_mandates(
                impersonator, principal, cart, max_amount=amount * 2
            )
            request = build_signed_request(impersonator, mandates, cart)

        env = sandbox.evaluate(request)
        return _attack_outcome(env, amount)

    return Session(spec["id"], "ATTACK", "A2", run, spec)


# --- A3 · replay -------------------------------------------------------------


def _a3(spec: dict) -> Session:
    variant = spec["variant"]
    amount = spec["amount"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(amount)
        sandbox, agent, principal = new_sandbox(baseline)
        cart = make_cart(items=[("SKU-A", "Item", 1, amount)])
        mandates = make_mandates(agent, principal, cart, max_amount=amount * 2)

        if variant == "replay":
            request = build_signed_request(agent, mandates, cart)
            sandbox.evaluate(request)  # first, legitimate
            request.idempotency_key = uuid.uuid4().hex  # fresh key, same signature
            env = sandbox.evaluate(request)
        elif variant == "stale_ts":
            long_ago = now_utc() - timedelta(hours=2)
            request = build_signed_request(
                agent, mandates, cart, created=long_ago, expires_in=timedelta(hours=3)
            )
            env = sandbox.evaluate(request, now=now_utc())
        else:  # expired_sig
            created = now_utc() - timedelta(seconds=200)
            request = build_signed_request(
                agent, mandates, cart, created=created, expires_in=timedelta(seconds=30)
            )
            env = sandbox.evaluate(request, now=now_utc())

        return _attack_outcome(env, amount)

    return Session(spec["id"], "ATTACK", "A3", run, spec)


# --- A4 · mandate substitution -----------------------------------------------


def _a4(spec: dict) -> Session:
    variant = spec["variant"]
    amount = spec["amount"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(amount)
        sandbox, agent, principal = new_sandbox(baseline)

        if variant == "cart_substitution":
            signed_cart = make_cart(items=[("SKU-CASE", "Phone case", 1, 499_00)])
            mandates = make_mandates(
                agent, principal, signed_cart, max_amount=100_000_00
            )
            substituted = make_cart(items=[("SKU-TV-55", "55in TV", 1, amount)])
            request = build_signed_request(agent, mandates, substituted)
        elif variant == "cross_intent":
            cart = make_cart(items=[("SKU-A", "Item", 1, amount)])
            bundle_a = make_mandates(agent, principal, cart)
            bundle_b = make_mandates(agent, principal, cart)
            bundle_a.cart = bundle_b.cart  # both real, chain does not join
            request = build_signed_request(agent, bundle_a, cart)
        elif variant == "delegated_other":
            other = sandbox.register_agent(AgentIdentity.create("agent_other"))
            cart = make_cart(items=[("SKU-A", "Item", 1, amount)])
            mandates = make_mandates(other, principal, cart)
            from kya.crypto import sign_payload

            mandates.cart.signer_key_id = agent.keypair.key_id
            mandates.cart.signature = sign_payload(
                agent.keypair.private, mandates.cart.signing_payload()
            )
            request = build_signed_request(agent, mandates, cart)
        elif variant == "unregistered_principal":
            stranger = Principal.create("user_mallory")  # never registered
            cart = make_cart(items=[("SKU-A", "Item", 1, amount)])
            mandates = make_mandates(agent, stranger, cart)
            request = build_signed_request(agent, mandates, cart)
        else:  # expired_mandate
            cart = make_cart(items=[("SKU-A", "Item", 1, amount)])
            issued = now_utc() - timedelta(hours=3)
            mandates = make_mandates(
                agent,
                principal,
                cart,
                issued_at=issued,
                intent_ttl=timedelta(minutes=30),
            )
            request = build_signed_request(agent, mandates, cart)

        env = sandbox.evaluate(request)
        return _attack_outcome(env, amount)

    return Session(spec["id"], "ATTACK", "A4", run, spec)


# --- A5 · price / total tampering --------------------------------------------


def _a5(spec: dict) -> Session:
    variant = spec["variant"]
    amount = spec["amount"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(amount)
        sandbox, agent, principal = new_sandbox(baseline)

        if variant == "inflate_total":
            signed = make_cart(items=[("SKU-PHONE-256", "Phone 256GB", 1, amount)])
            mandates = make_mandates(agent, principal, signed, max_amount=100_000_00)
            tampered = make_cart(
                items=[("SKU-PHONE-256", "Phone 256GB", 1, amount + 100_00)]
            )
            request = build_signed_request(agent, mandates, tampered)
        elif variant == "inflate_shipping":
            signed = make_cart(items=[("SKU-A", "Item", 1, amount)], shipping=0)
            mandates = make_mandates(agent, principal, signed, max_amount=100_000_00)
            tampered = make_cart(items=[("SKU-A", "Item", 1, amount)], shipping=999_00)
            request = build_signed_request(agent, mandates, tampered)
        elif variant == "qty_change":
            signed = make_cart(items=[("SKU-A", "Item", 1, amount)])
            mandates = make_mandates(agent, principal, signed, max_amount=100_000_00)
            tampered = make_cart(items=[("SKU-A", "Item", 3, amount)])
            request = build_signed_request(agent, mandates, tampered)
        else:  # sku_swap
            signed = make_cart(items=[("SKU-CASE", "Case", 1, amount)])
            mandates = make_mandates(agent, principal, signed, max_amount=100_000_00)
            tampered = make_cart(items=[("SKU-TV-55", "TV", 1, amount)])
            request = build_signed_request(agent, mandates, tampered)

        env = sandbox.evaluate(request)
        return _attack_outcome(env, amount)

    return Session(spec["id"], "ATTACK", "A5", run, spec)


# --- A6 · scope escalation ---------------------------------------------------


def _a6(spec: dict) -> Session:
    variant = spec["variant"]
    amount = spec["amount"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(amount)
        sandbox, agent, principal = new_sandbox(baseline)

        if variant == "above_ceiling":
            cart = make_cart(items=[("SKU-TV-55", "TV", 1, amount)])
            mandates = make_mandates(agent, principal, cart, max_amount=amount // 2)
            request = build_signed_request(agent, mandates, cart)
        elif variant == "merchant_outside":
            cart = make_cart(items=[("SKU-A", "Item", 1, amount)], merchant_id="merch_elsewhere")
            mandates = make_mandates(
                agent, principal, cart, allowed_merchants=["merch_sandbox_01"]
            )
            request = build_signed_request(agent, mandates, cart)
        else:  # category_outside
            cart = make_cart(items=[("SKU-A", "Item", 1, amount)], category="alcohol")
            mandates = make_mandates(
                agent, principal, cart, allowed_categories=["electronics", "books"]
            )
            request = build_signed_request(agent, mandates, cart)

        env = sandbox.evaluate(request)
        return _attack_outcome(env, amount)

    return Session(spec["id"], "ATTACK", "A6", run, spec)


# --- A7 · refund flood -------------------------------------------------------


def _a7(spec: dict) -> Session:
    variant = spec["variant"]
    unit = spec["amount"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(unit)
        sandbox, agent, principal = new_sandbox(baseline)

        n_orders = 0 if variant == "no_orders" else spec["orders"]
        for _ in range(n_orders):
            cart = make_cart(items=[("SKU-A", "Item", 1, unit)])
            mandates = make_mandates(agent, principal, cart, max_amount=unit * 4)
            sandbox.evaluate(build_signed_request(agent, mandates, cart))

        # The flood. The breaker must open while it runs, not after.
        floods = spec.get("refunds", 1)
        stopped = False
        last = None
        codes: list[str] = []
        for _ in range(floods):
            cart = make_cart(items=[("SKU-A", "Item", 1, unit)])
            mandates = make_mandates(agent, principal, cart, max_amount=unit * 4)
            env = sandbox.evaluate(build_refund_request(agent, mandates, cart, unit))
            last = env
            if _stopped(env):
                stopped = True
                codes = _codes(env)
                break
        assert last is not None
        return Outcome(
            decision=last.decision if not stopped else Decision.QUARANTINE,
            stopped=stopped,
            amount=unit * max(floods, 1),
            reason_codes=codes or _codes(last),
        )

    return Session(spec["id"], "ATTACK", "A7", run, spec)


# --- A8 · indirect prompt injection ------------------------------------------


def _a8(spec: dict) -> Session:
    field = spec["field"]
    payload = spec["payload"]
    amount = spec["amount"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(amount)
        sandbox, agent, principal = new_sandbox(baseline)
        cart = make_cart(items=[("SKU-A", "Item", 1, amount)])
        mandates = make_mandates(agent, principal, cart, max_amount=amount * 2)
        request = build_signed_request(
            agent, mandates, cart, free_text={field: payload}
        )
        env = sandbox.evaluate(request)
        outcome = _attack_outcome(env, amount)
        if spec.get("evasion") and not outcome.stopped:
            outcome.note = (
                "fluent paraphrase carries no instruction-shaped marker; "
                "deterministic G5 passes it — a known gap of marker-based "
                "content screening, not of the money decision"
            )
        return outcome

    return Session(spec["id"], "ATTACK", "A8", run, spec)


# --- A9 · counterfeit merchant callback --------------------------------------


def _a9(spec: dict) -> Session:
    callback = spec["callback_url"]
    amount = spec["amount"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(amount)
        sandbox, agent, principal = new_sandbox(baseline)
        cart = make_cart(items=[("SKU-A", "Item", 1, amount)])
        mandates = make_mandates(agent, principal, cart, max_amount=amount * 2)
        request = build_signed_request(
            agent, mandates, cart, callback_url=callback
        )
        env = sandbox.evaluate(request)
        return _attack_outcome(env, amount)

    return Session(spec["id"], "ATTACK", "A9", run, spec)


# --- A10 · Reserve Pay block drain (SIMULATED) -------------------------------


def _a10(spec: dict) -> Session:
    variant = spec["variant"]
    reserved = spec["reserved"]
    debit = spec["debit"]

    def run(baseline: Baseline) -> Outcome:
        if baseline is Baseline.B0:
            return allow_outcome(debit)
        sandbox, agent, principal = new_sandbox(baseline)
        block = sandbox.blocks.create_block(
            principal_ref=principal.principal_ref,
            merchant_id="merch_sandbox_01",
            reserved=reserved,
        )
        cart = make_cart(items=[("SKU-A", "Item", 1, debit)])
        mandates = make_mandates(agent, principal, cart, max_amount=10_000_000_00)

        if variant == "over_reserve":
            # An obligation exists, but the debit exceeds what remains reserved.
            sandbox.ledger.append(
                make_obligation(
                    agent,
                    principal,
                    cart,
                    rail_type=RailType.RESERVE_PAY_BLOCK,
                    rail_ref=block.block_id,
                )
            )
        # variant == "unbacked": no obligation at all — the drain shape.

        env = sandbox.evaluate(
            build_block_debit_request(agent, mandates, cart, block.block_id, debit)
        )
        return _attack_outcome(env, debit)

    return Session(spec["id"], "ATTACK", "A10", run, spec)


# --- A11 · obligation--fulfilment mismatch -----------------------------------


def _a11(spec: dict) -> Session:
    amount = spec["amount"]
    variant = spec.get("variant", "mismatch")

    def run(baseline: Baseline) -> Outcome:
        # Only B3 carries a clearing layer; under every other posture the
        # purchase is legitimate and the mismatch is never examined.
        if baseline is not Baseline.B3:
            return allow_outcome(amount)

        from kya.clearing.evidence import envelope, from_rail
        from kya.clearing.service import ClearingService
        from kya.obligation.receipt import (
            CLAIM_AMOUNT_CHARGED,
            CLAIM_DELIVERED_AT,
            CLAIM_DELIVERED_SKUS,
            iso,
        )

        sandbox, agent, principal = new_sandbox(baseline)
        # T1 floor is REC, so REC-class delivery evidence is admissible and can
        # carry a VIOLATED verdict to finality.
        sandbox.set_tier(agent.agent_id, Tier.T1)
        gateway = sandbox.gateway()

        cart = make_cart(items=[("SKU-PHONE-256", "Phone 256GB", 1, amount)])
        mandates = make_mandates(agent, principal, cart, max_amount=amount * 2)
        result = gateway.create_order(build_signed_request(agent, mandates, cart))

        if result.obligation is None:  # pragma: no cover - defensive
            return allow_outcome(amount)

        obligation = result.obligation
        service = ClearingService(
            ledger=sandbox.ledger,
            rail=sandbox.rail,
            blocks=sandbox.blocks,
            passports=sandbox.passport_store,
            policy=sandbox.policy,
            clock=sandbox.clock,
        )

        if variant == "counterfeit_passes":
            # The honest limitation, straight out of the semantic verifier's
            # design. The recorded acceptance criteria are the deterministic
            # ones — right SKU, right amount, delivered in window — and REC
            # evidence satisfies every one of them. The item was a convincing
            # counterfeit, which only an LLM-class read would flag, and an
            # LLM's opinion is SELF-class and cannot clear or dispute a
            # settlement alone. So this clears. It is a real miss, and it is
            # the miss the design consciously accepts rather than hides.
            window = obligation.promised.delivery_window
            delivered_at = iso(window.from_) if window is not None else None
            items = [
                from_rail(
                    "rec_delivered_sku",
                    CLAIM_DELIVERED_SKUS,
                    "SKU-PHONE-256",
                    source="courier_manifest",
                ),
                from_rail(
                    "rec_amount",
                    CLAIM_AMOUNT_CHARGED,
                    amount,
                    source="razorpay",
                ),
            ]
            if delivered_at is not None:
                items.append(
                    from_rail(
                        "rec_delivered_at",
                        CLAIM_DELIVERED_AT,
                        delivered_at,
                        source="courier_manifest",
                    )
                )
            ev = envelope(obligation.self_hash, items)
            clearing = service.submit(obligation.obligation_id, ev, execute=False)
            return Outcome(
                decision=Decision.ALLOW,
                stopped=clearing.disputed,
                amount=amount,
                reason_codes=[],
                clearing=clearing.decision.finality.value,
                note="counterfeit satisfies recorded criteria — semantic-only "
                "flag is SELF-class and inadmissible; clears by design",
            )

        # Default: the courier confirms delivery of the wrong item — REC-class
        # evidence, fetched by us, that no deterministic gate inline could ever
        # have seen.
        ev = envelope(
            obligation.self_hash,
            [
                from_rail(
                    "rec_delivered_sku",
                    CLAIM_DELIVERED_SKUS,
                    spec["wrong_sku"],
                    source="courier_manifest",
                )
            ],
        )
        clearing = service.submit(obligation.obligation_id, ev, execute=False)
        return Outcome(
            decision=Decision.ALLOW,  # the inline purchase was, correctly, allowed
            stopped=clearing.disputed,
            amount=amount,
            reason_codes=["performance_violated"] if clearing.disputed else [],
            clearing=clearing.decision.finality.value,
            note="caught by clearing layer, not an inline gate",
        )

    return Session(spec["id"], "ATTACK", "A11", run, spec)


# --- shared ------------------------------------------------------------------


def _attack_outcome(env, amount: int) -> Outcome:
    return Outcome(
        decision=env.decision,
        stopped=env.decision in {Decision.DENY, Decision.QUARANTINE},
        amount=amount,
        reason_codes=list(env.reason_codes),
    )


#: Dispatch from a frozen spec's ``kind`` to its builder.
BUILDERS: dict[str, Callable[[dict], Session]] = {
    "legit_purchase": _legit_purchase,
    "legit_refund_after_orders": _legit_refund_after_orders,
    "legit_retry": _legit_retry,
    "legit_high_value_stepup": _legit_high_value_stepup,
    "A1": _a1,
    "A2": _a2,
    "A3": _a3,
    "A4": _a4,
    "A5": _a5,
    "A6": _a6,
    "A7": _a7,
    "A8": _a8,
    "A9": _a9,
    "A10": _a10,
    "A11": _a11,
}


def build(spec: dict) -> Session:
    """Realise one frozen spec into a runnable session."""
    return BUILDERS[spec["kind"]](spec)
