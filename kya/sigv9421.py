"""RFC 9421 HTTP Message Signatures — the subset Web Bot Auth and Visa TAP use.

Both specify Ed25519 over an RFC 9421 signature base, so implementing the base
construction faithfully is what lets agents built for those protocols call this
gateway without modification. Compatibility is the feature; a bespoke scheme
would defend nothing because no agent would adopt it.

Supported derived components: ``@method``, ``@authority``, ``@path``,
``@target-uri``. Regular headers are matched case-insensitively.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

from kya.schemas import AgentRequest, SignatureParams


class SignatureParseError(ValueError):
    """Raised when a signature header cannot be parsed."""


_LABEL_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=\s*(.+)$", re.DOTALL)
_INNER_LIST_RE = re.compile(r"^\((.*?)\)(.*)$", re.DOTALL)
_PARAM_RE = re.compile(r';\s*([a-z0-9_-]+)\s*=\s*("([^"]*)"|[^;]+)', re.IGNORECASE)
_COMPONENT_RE = re.compile(r'"([^"]+)"')


@dataclass(slots=True)
class ParsedSignature:
    label: str
    params: SignatureParams
    #: Raw signature bytes, decoded from the ``:base64:`` byte-sequence form.
    signature: bytes = b""
    #: The verbatim ``Signature-Input`` value for this label. RFC 9421 requires
    #: the signature base to echo it byte-for-byte, so it must be preserved
    #: rather than re-serialized from parsed fields.
    raw_params: str = field(default="")


def parse_signature_input(raw: str) -> dict[str, ParsedSignature]:
    """Parse a ``Signature-Input`` header value into one entry per label."""
    out: dict[str, ParsedSignature] = {}
    for segment in _split_labels(raw):
        m = _LABEL_RE.match(segment)
        if not m:
            raise SignatureParseError(f"malformed Signature-Input segment: {segment!r}")
        label, rest = m.group(1), m.group(2).strip()

        inner = _INNER_LIST_RE.match(rest)
        if not inner:
            raise SignatureParseError(
                f"Signature-Input {label!r} missing covered-component list"
            )
        components = _COMPONENT_RE.findall(inner.group(1))
        param_blob = inner.group(2)

        params: dict[str, str] = {}
        for pm in _PARAM_RE.finditer(param_blob):
            key = pm.group(1).lower()
            params[key] = (pm.group(3) if pm.group(3) is not None else pm.group(2)).strip()

        if "keyid" not in params:
            raise SignatureParseError(f"Signature-Input {label!r} missing keyid")
        if "created" not in params:
            raise SignatureParseError(f"Signature-Input {label!r} missing created")

        try:
            created = int(params["created"])
            expires = int(params["expires"]) if "expires" in params else None
        except ValueError as exc:
            raise SignatureParseError(f"non-integer timestamp in {label!r}") from exc

        out[label] = ParsedSignature(
            label=label,
            params=SignatureParams(
                key_id=params["keyid"],
                algorithm=params.get("alg", "ed25519").lower(),
                created=created,
                expires=expires,
                nonce=params.get("nonce"),
                tag=params.get("tag"),
                covered_components=components,
            ),
            raw_params=rest,
        )
    return out


def parse_signature_header(raw: str) -> dict[str, bytes]:
    """Parse a ``Signature`` header value: ``label=:base64:`` per entry."""
    out: dict[str, bytes] = {}
    for segment in _split_labels(raw):
        m = _LABEL_RE.match(segment)
        if not m:
            raise SignatureParseError(f"malformed Signature segment: {segment!r}")
        label, value = m.group(1), m.group(2).strip()
        if not (value.startswith(":") and value.endswith(":") and len(value) > 2):
            raise SignatureParseError(
                f"Signature {label!r} is not an RFC 8941 byte sequence"
            )
        try:
            out[label] = base64.b64decode(value[1:-1], validate=True)
        except Exception as exc:
            raise SignatureParseError(f"bad base64 in Signature {label!r}") from exc
    return out


def _split_labels(raw: str) -> list[str]:
    """Split on commas that sit outside quotes and parentheses."""
    segments: list[str] = []
    depth = 0
    in_quotes = False
    current: list[str] = []
    for ch in raw:
        if ch == '"':
            in_quotes = not in_quotes
        elif not in_quotes:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                segments.append("".join(current))
                current = []
                continue
        current.append(ch)
    if current and "".join(current).strip():
        segments.append("".join(current))
    return [s for s in segments if s.strip()]


def build_signature_base(request: AgentRequest, parsed: ParsedSignature) -> bytes:
    """Reconstruct the RFC 9421 signature base for ``parsed``.

    Each covered component contributes one ``"name": value`` line, and the
    final line is ``"@signature-params"`` carrying the verbatim
    ``Signature-Input`` value. There is no trailing newline.
    """
    headers_ci = {k.lower(): v for k, v in request.headers.items()}
    lines: list[str] = []

    for component in parsed.params.covered_components:
        name = component.lower()
        if name == "@method":
            value = request.method.upper()
        elif name == "@authority":
            value = request.authority
        elif name == "@path":
            value = request.path
        elif name == "@target-uri":
            proto = (
                headers_ci.get("x-forwarded-proto")
                or headers_ci.get("x-forwarded-scheme")
                or ("http" if any(h in request.authority for h in ("localhost", "127.0.0.1", ":8000", ":8080", ":3000")) else "https")
            )
            value = f"{proto}://{request.authority}{request.path}"
        elif name.startswith("@"):
            raise SignatureParseError(f"unsupported derived component {component!r}")
        else:
            if name not in headers_ci:
                raise SignatureParseError(
                    f"covered component {component!r} absent from request"
                )
            value = headers_ci[name].strip()
        lines.append(f'"{name}": {value}')

    lines.append(f'"@signature-params": {parsed.raw_params}')
    return "\n".join(lines).encode("utf-8")


def parse_signature_agent(raw: str | None) -> str | None:
    """Extract the directory origin from a ``Signature-Agent`` header.

    The header is an RFC 8941 string, so it arrives quoted.
    """
    if not raw:
        return None
    value = raw.strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        value = value[1:-1]
    return value or None
