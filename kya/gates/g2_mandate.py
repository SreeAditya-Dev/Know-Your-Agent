"""G2 — mandate chain.

Establishes that a human delegated this spend to this agent, and that the two
mandates presented actually join up. Checks, in order of how cheaply they fail:

1. a bundle is present at all
2. the intent delegates to the agent that is calling
3. neither mandate has expired
4. the cart mandate references *this* intent
5. the intent was signed by a key registered to the named principal
6. both signatures verify

Step 4 is the one that matters. An attacker who obtains a real intent mandate
and pairs it with a cart mandate minted under a different intent has a bundle
where every individual signature is valid. Only the cross-reference catches it.
"""

from __future__ import annotations

from kya.crypto import verify_payload
from kya.enums import Gate
from kya.gates.base import BaseGate
from kya.gates.context import GateContext
from kya.schemas import GateResult


class G2Mandate(BaseGate):
    gate = Gate.G2

    def evaluate(self, ctx: GateContext) -> GateResult:
        bundle = ctx.request.mandates
        if bundle is None:
            return self._fail("M001")

        intent, cart_mandate = bundle.intent, bundle.cart

        if intent.agent_id != ctx.request.agent_id:
            return self._fail(
                "M006",
                delegated_to=intent.agent_id,
                calling_agent=ctx.request.agent_id,
            )

        if intent.is_expired(ctx.now):
            return self._fail(
                "M003", which="intent", expires_at=intent.expires_at.isoformat()
            )
        if cart_mandate.is_expired(ctx.now):
            return self._fail(
                "M003", which="cart", expires_at=cart_mandate.expires_at.isoformat()
            )

        expected_ref = intent.reference()
        if cart_mandate.intent_ref != expected_ref:
            return self._fail(
                "M002",
                cart_references=cart_mandate.intent_ref[:16],
                intent_digest=expected_ref[:16],
            )

        principal_keys = ctx.principals.get(intent.principal_ref)
        if not principal_keys or intent.signer_key_id not in principal_keys:
            return self._fail(
                "M004",
                principal_ref=intent.principal_ref,
                signer_key_id=intent.signer_key_id,
            )

        from kya.crypto import public_from_b64u

        try:
            principal_key = public_from_b64u(principal_keys[intent.signer_key_id])
        except Exception:
            return self._fail("M004", principal_ref=intent.principal_ref)

        if not verify_payload(principal_key, intent.signing_payload(), intent.signature):
            return self._fail("M005", which="intent")

        if ctx.agent_public_key is None:
            # G1 could not resolve the agent's key (directory outage) and has
            # already cited I004. Do not manufacture a second failure for the
            # same root cause; the pipeline's step-up still applies.
            return self._degraded(reason="agent key unresolved upstream")

        if cart_mandate.signer_key_id != (
            ctx.parsed_signature.params.key_id if ctx.parsed_signature else None
        ):
            return self._fail(
                "M005",
                which="cart",
                detail="cart mandate signed by a different key than the request",
            )

        if not verify_payload(
            ctx.agent_public_key, cart_mandate.signing_payload(), cart_mandate.signature
        ):
            return self._fail("M005", which="cart")

        return self._pass(
            principal_ref=intent.principal_ref,
            intent_id=intent.intent_id,
            cart_id=cart_mandate.cart_id,
        )
