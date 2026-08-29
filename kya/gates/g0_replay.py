"""G0 — transport and replay.

Parses the RFC 9421 signature headers (G1 reuses the result) and answers a
narrow question: is this a *fresh* request, or one we have already honoured?

When the signature carries no explicit nonce, the signature bytes themselves
serve as the replay key. A valid Ed25519 signature over a signature base that
includes ``created`` is already unique per request, so replaying one is
detectable without the agent having to opt in to nonces.
"""

from __future__ import annotations

from kya.canonical import digest_bytes
from kya.enums import Gate
from kya.gates.base import BaseGate
from kya.gates.context import GateContext
from kya.nonce import NonceStoreUnavailable
from kya.schemas import GateResult
from kya.sigv9421 import SignatureParseError, parse_signature_header, parse_signature_input


class G0Replay(BaseGate):
    gate = Gate.G0

    def evaluate(self, ctx: GateContext) -> GateResult:
        req = ctx.request

        if not req.signature or not req.signature_input_raw:
            # Nothing to replay-check. G1 owns the "unsigned request" verdict.
            return self._pass(signature_present=False)

        try:
            inputs = parse_signature_input(req.signature_input_raw)
            signatures = parse_signature_header(req.signature)
        except SignatureParseError as exc:
            # Unparseable headers are a positive defect, not a missing one.
            return self._fail("I002", parse_error=str(exc)[:200])

        label = next((lb for lb in inputs if lb in signatures), None)
        if label is None:
            return self._fail(
                "I002",
                parse_error="no label present in both Signature-Input and Signature",
            )

        parsed = inputs[label]
        parsed.signature = signatures[label]
        ctx.parsed_signature = parsed

        now_ts = int(ctx.now.timestamp())
        skew = ctx.policy.clock_skew_seconds

        if abs(now_ts - parsed.params.created) > skew:
            return self._fail(
                "R002",
                created=parsed.params.created,
                now=now_ts,
                drift_seconds=now_ts - parsed.params.created,
                allowed_skew=skew,
            )

        if parsed.params.expires is not None and now_ts > parsed.params.expires:
            return self._fail(
                "R003", expires=parsed.params.expires, now=now_ts
            )

        replay_key = parsed.params.nonce or digest_bytes(parsed.signature)
        scoped = f"{req.agent_id}:{parsed.params.key_id}:{replay_key}"

        try:
            fresh = ctx.nonce_store.check_and_record(scoped)
        except NonceStoreUnavailable:
            # Cannot prove non-replay. Degrade rather than guess either way.
            return self._degraded("R004", replay_key=replay_key[:16])

        if not fresh:
            return self._fail(
                "R001",
                replay_key=replay_key[:16],
                nonce_supplied=parsed.params.nonce is not None,
            )

        return self._pass(
            label=label,
            key_id=parsed.params.key_id,
            created=parsed.params.created,
            nonce_supplied=parsed.params.nonce is not None,
        )
