"""G1 — agent identity.

Verifies an RFC 9421 Ed25519 signature against the key the agent's own
directory publishes. This is the layer Visa TAP and Cloudflare Web Bot Auth
already ship, so agents built for either work here unmodified.

The gate's real content is the branch structure, not the cryptography. A
directory that *answers and does not list the key* is evidence of impersonation
and denies. A directory that *cannot be reached* is an availability failure and
steps up. Collapsing the two would either hand impersonators a free pass during
an outage or zero out agent revenue whenever a third party's DNS wobbles.
"""

from __future__ import annotations

from kya.crypto import verify_bytes
from kya.directory import ResolveStatus
from kya.enums import Gate
from kya.gates.base import BaseGate
from kya.gates.context import GateContext
from kya.schemas import GateResult
from kya.sigv9421 import (
    SignatureParseError,
    build_signature_base,
    parse_signature_agent,
)

_SUPPORTED_ALGORITHMS = {"ed25519"}


class G1Identity(BaseGate):
    gate = Gate.G1

    def evaluate(self, ctx: GateContext) -> GateResult:
        req = ctx.request

        if not req.signature or not req.signature_input_raw:
            return self._fail("I001")

        parsed = ctx.parsed_signature
        if parsed is None:
            # G0 could not parse the headers and already cited the defect.
            return self._fail("I002", parse_error="signature headers unparsed")

        if parsed.params.algorithm not in _SUPPORTED_ALGORITHMS:
            return self._fail("I002", algorithm=parsed.params.algorithm)

        origin = parse_signature_agent(req.signature_agent)
        if not origin:
            return self._fail("I005")

        resolution = ctx.directory.resolve(origin, parsed.params.key_id)

        if resolution.status is ResolveStatus.UNKNOWN_KEY:
            # The directory answered. This key is not one of theirs.
            return self._fail(
                "I003", origin=origin, key_id=parsed.params.key_id
            )

        if resolution.status is ResolveStatus.UNREACHABLE:
            # We could not ask. Absence of evidence, not evidence of fraud.
            return self._degraded("I004", origin=origin)

        try:
            base = build_signature_base(req, parsed)
        except SignatureParseError as exc:
            return self._fail("I002", base_error=str(exc)[:200])

        assert resolution.public_key is not None  # guaranteed by status
        if not verify_bytes(resolution.public_key, base, parsed.signature):
            # Positive evidence of tampering or impersonation. Denies even when
            # the key came from a stale cache during an outage.
            return self._fail(
                "I002",
                origin=origin,
                key_id=parsed.params.key_id,
                stale_key=resolution.status is ResolveStatus.STALE_HIT,
            )

        ctx.agent_public_key = resolution.public_key

        if resolution.status is ResolveStatus.STALE_HIT:
            # The signature verified, so identity is established; we simply
            # proved it against a cached key during an outage. That passes, but
            # it is recorded so the degradation is visible rather than silent.
            ctx.identity_degraded = True
            return self._pass(
                origin=origin,
                key_id=parsed.params.key_id,
                served_from="stale_cache",
                age_seconds=round(resolution.age_seconds, 1),
            )

        return self._pass(
            origin=origin,
            key_id=parsed.params.key_id,
            age_seconds=round(resolution.age_seconds, 1),
        )
