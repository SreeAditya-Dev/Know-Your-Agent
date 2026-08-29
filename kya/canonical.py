"""Deterministic serialization and hashing.

Cart binding (G3) rests on both parties computing the same hash over the same
logical cart. That only works if serialization is canonical, so this module
implements a JCS-style (RFC 8785) canonical form: object keys sorted by code
point, no insignificant whitespace, UTF-8, and no floats anywhere.

Money is integer paise throughout the system precisely so that canonicalization
never has to reason about float formatting, which is the usual source of
hash mismatches between independent implementations.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any


class CanonicalizationError(ValueError):
    """Raised when a value cannot be canonically serialized."""


def _prepare(value: Any) -> Any:
    """Normalize a value into JSON-canonicalizable primitives."""
    if value is None or isinstance(value, (str, bool, int)):
        # bool before int matters conceptually but json handles both
        return value
    if isinstance(value, float):
        raise CanonicalizationError(
            "floats are not canonicalizable; money must be integer paise"
        )
    if isinstance(value, datetime):
        # Always UTC, always second precision, always Z-suffixed.
        dt = value.astimezone(timezone.utc).replace(microsecond=0)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dict):
        return {str(k): _prepare(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_prepare(v) for v in value]
    if hasattr(value, "model_dump"):  # pydantic BaseModel
        return _prepare(value.model_dump(mode="python"))
    if hasattr(value, "value"):  # enum
        return _prepare(value.value)
    raise CanonicalizationError(f"cannot canonicalize {type(value).__name__}")


def canonicalize(value: Any) -> bytes:
    """Return the canonical UTF-8 byte serialization of ``value``."""
    return json.dumps(
        _prepare(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(value: Any) -> str:
    """SHA-256 over the canonical form, as lowercase hex.

    Used for cart hashes, mandate references and ledger chaining.
    """
    return hashlib.sha256(canonicalize(value)).hexdigest()


def digest_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def b64u_encode(raw: bytes) -> str:
    """base64url without padding — the form used in signatures and headers."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64u_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def now_utc() -> datetime:
    """Single source of truth for 'now'. Tests monkeypatch this."""
    return datetime.now(timezone.utc)
