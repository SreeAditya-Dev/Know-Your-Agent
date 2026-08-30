"""G5 -- deterministic content-threat checks.

Inbound agents may carry catalog copy, coupon text, addresses, and callback
URLs supplied by an untrusted party.  Those fields can be consumed by a
merchant-side agent after checkout, making them an indirect prompt-injection
carrier even when the payment request itself is valid.

This gate intentionally uses only local deterministic checks.  A classifier
may enrich a quarantined item later, but no model or network call is allowed
to influence an inline money decision.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from kya.enums import Gate
from kya.gates.base import BaseGate
from kya.gates.context import GateContext
from kya.schemas import GateResult


_URL_ADAPTER = TypeAdapter(AnyHttpUrl)

# These are deliberately narrow instruction-shaped markers rather than a
# general toxicity filter.  Product descriptions can legitimately discuss
# prompts, policies, or security; an instruction to override them is the
# threat this inline gate can establish deterministically.
_INJECTION_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:ignore|disregard|forget|override)\s+"
        r"(?:all\s+|any\s+|the\s+)?(?:previous|prior|above)\s+"
        r"(?:instructions?|prompts?|rules?)\b"
    ),
    re.compile(
        r"\b(?:ignore|disregard|override|bypass)\b.{0,48}\b"
        r"(?:system|developer)\s+(?:instructions?|prompt|message)\b"
    ),
    re.compile(
        r"\b(?:reveal|disclose|print|show|exfiltrate|upload|send)\b.{0,64}\b"
        r"(?:system\s+prompt|developer\s+instructions?|api\s*keys?|"
        r"credentials?|secrets?)\b"
    ),
    re.compile(
        r"\b(?:do\s+not|don't)\s+(?:follow|obey)\b.{0,48}\b"
        r"(?:instructions?|rules?|guardrails?)\b"
    ),
)


def _normalise_text(value: str) -> str:
    """Make simple obfuscation unable to bypass a deterministic marker.

    Compatibility characters are folded, zero-width format characters are
    removed, and whitespace is collapsed.  Raw untrusted text is never copied
    into the decision trace.
    """
    normalised = unicodedata.normalize("NFKC", value).casefold()
    normalised = "".join(
        "" if unicodedata.category(char) == "Cf" else char
        for char in normalised
    )
    return re.sub(r"\s+", " ", normalised).strip()


def _callback_host(value: str) -> str | None:
    """Return a canonical HTTP(S) callback host, or ``None`` if unsafe.

    Pydantic's URL parser is local-only and gives the hostname after handling
    URL syntax such as ports and user-info.  User-info is rejected explicitly:
    a callback URL has no legitimate need to embed credentials.
    """
    try:
        parsed = _URL_ADAPTER.validate_python(value)
    except ValidationError:
        return None

    if parsed.username is not None or parsed.password is not None:
        return None
    host = parsed.host
    return host.lower().rstrip(".") if host else None


class G5ContentThreat(BaseGate):
    """Check agent-supplied text and callback destinations before routing."""

    gate = Gate.G5

    def evaluate(self, ctx: GateContext) -> GateResult:
        codes: list[str] = []
        detail: dict[str, object] = {}

        marked_fields = sorted(
            field
            for field, value in ctx.request.free_text.items()
            if any(marker.search(_normalise_text(value)) for marker in _INJECTION_MARKERS)
        )
        if marked_fields:
            codes.append("T001")
            detail["injection"] = {
                "fields": marked_fields,
                "field_count": len(marked_fields),
            }

        callback_url = ctx.request.callback_url
        if callback_url is not None:
            host = _callback_host(callback_url)
            allowed_hosts = {
                configured.strip().lower().rstrip(".")
                for configured in ctx.policy.registered_callback_domains.get(
                    ctx.request.agent_id, []
                )
                if configured.strip()
            }
            if host is None or host not in allowed_hosts:
                codes.append("T002")
                detail["callback"] = {
                    "host": host or "invalid",
                    "allowlist_configured": bool(allowed_hosts),
                }

        if codes:
            return self._fail(*codes, **detail)
        return self._pass()
