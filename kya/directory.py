"""Agent key directory resolution, with stale-while-revalidate.

Web Bot Auth publishes agent keys at a well-known JWKS path on the origin named
by the ``Signature-Agent`` header. That makes the directory a *network
dependency* on the money path, which is exactly the situation the degradation
policy in docs/02 exists for.

The status codes this module returns encode a distinction G1 depends on:

* ``UNKNOWN_KEY`` — the directory answered, and does not publish this key.
  Positive evidence of wrongdoing. Deny.
* ``UNREACHABLE`` — we could not ask. Absence of evidence. Step up, do not deny.

Collapsing those two into "verification failed" would either zero out agent
revenue during a directory outage or wave through impersonation. Keeping them
apart is the whole point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from kya.canonical import now_utc
from kya.crypto import public_from_b64u

WELL_KNOWN_PATH = "/.well-known/http-message-signatures-directory"


class ResolveStatus(str, Enum):
    FOUND = "FOUND"
    STALE_HIT = "STALE_HIT"  # served past TTL because the origin was unreachable
    UNKNOWN_KEY = "UNKNOWN_KEY"  # directory answered; key is not published
    UNREACHABLE = "UNREACHABLE"  # could not ask, and nothing cached


@dataclass(slots=True)
class ResolveResult:
    status: ResolveStatus
    public_key: Ed25519PublicKey | None = None
    origin: str | None = None
    age_seconds: float = 0.0

    @property
    def usable(self) -> bool:
        return self.public_key is not None


class KeyFetcher(Protocol):
    """Fetches ``{key_id: base64url_public_key}`` for a directory origin.

    Raising any exception means *unreachable*. Returning a dict without the
    requested key means *not published*.
    """

    def __call__(self, origin: str) -> dict[str, str]: ...


@dataclass(slots=True)
class _CacheEntry:
    keys: dict[str, str]
    fetched_at: datetime


class AgentDirectory:
    """Resolves agent signing keys, caching with stale-while-revalidate."""

    def __init__(
        self,
        fetcher: KeyFetcher,
        ttl_seconds: int = 300,
        max_stale_seconds: int = 86_400,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._fetch = fetcher
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_stale = timedelta(seconds=max_stale_seconds)
        self._clock = clock
        self._cache: dict[str, _CacheEntry] = {}

    def resolve(self, origin: str, key_id: str) -> ResolveResult:
        now = self._clock()
        cached = self._cache.get(origin)
        fresh = cached is not None and (now - cached.fetched_at) < self._ttl

        if fresh:
            return self._from_entry(cached, key_id, origin, now)

        try:
            keys = self._fetch(origin)
        except Exception:
            # Could not ask. Fall back to cache if it is not absurdly old.
            if cached is not None and (now - cached.fetched_at) <= self._max_stale:
                result = self._from_entry(cached, key_id, origin, now)
                if result.status is ResolveStatus.FOUND:
                    result.status = ResolveStatus.STALE_HIT
                return result
            return ResolveResult(status=ResolveStatus.UNREACHABLE, origin=origin)

        entry = _CacheEntry(keys=dict(keys), fetched_at=now)
        self._cache[origin] = entry
        return self._from_entry(entry, key_id, origin, now)

    def _from_entry(
        self, entry: _CacheEntry, key_id: str, origin: str, now: datetime
    ) -> ResolveResult:
        age = (now - entry.fetched_at).total_seconds()
        encoded = entry.keys.get(key_id)
        if encoded is None:
            # The directory answered and this key is not in it.
            return ResolveResult(
                status=ResolveStatus.UNKNOWN_KEY, origin=origin, age_seconds=age
            )
        try:
            key = public_from_b64u(encoded)
        except Exception:
            return ResolveResult(
                status=ResolveStatus.UNKNOWN_KEY, origin=origin, age_seconds=age
            )
        return ResolveResult(
            status=ResolveStatus.FOUND,
            public_key=key,
            origin=origin,
            age_seconds=age,
        )

    def invalidate(self, origin: str) -> None:
        self._cache.pop(origin, None)


class StaticKeyFetcher:
    """In-memory fetcher for fixtures and tests.

    ``set_unreachable`` simulates a directory outage, which is how the GF2
    graceful-failure demonstration is driven.
    """

    def __init__(self, directories: dict[str, dict[str, str]] | None = None) -> None:
        self.directories: dict[str, dict[str, str]] = directories or {}
        self.unreachable: set[str] = set()
        self.call_count: int = 0

    def __call__(self, origin: str) -> dict[str, str]:
        self.call_count += 1
        if origin in self.unreachable:
            raise ConnectionError(f"simulated outage for {origin}")
        if origin not in self.directories:
            raise ConnectionError(f"no such directory {origin}")
        return self.directories[origin]

    def publish(self, origin: str, key_id: str, public_b64u: str) -> None:
        self.directories.setdefault(origin, {})[key_id] = public_b64u

    def withdraw(self, origin: str, key_id: str) -> None:
        """Rotate a key out. The directory still answers; the key is gone."""
        self.directories.get(origin, {}).pop(key_id, None)

    def set_unreachable(self, origin: str, down: bool = True) -> None:
        if down:
            self.unreachable.add(origin)
        else:
            self.unreachable.discard(origin)


class HttpKeyFetcher:
    """Fetches a JWKS-style directory over HTTPS."""

    def __init__(self, timeout: float = 2.0) -> None:
        self.timeout = timeout

    def __call__(self, origin: str) -> dict[str, str]:  # pragma: no cover - network
        import httpx

        url = origin.rstrip("/") + WELL_KNOWN_PATH
        response = httpx.get(url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()

        keys: dict[str, str] = {}
        for jwk in payload.get("keys", []):
            kid, x = jwk.get("kid"), jwk.get("x")
            if kid and x and jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519":
                keys[kid] = x
        return keys
