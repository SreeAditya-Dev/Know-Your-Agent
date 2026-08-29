"""Replay protection.

A nonce store answers one question: has this exact signed request been seen
before? Entries expire after twice the clock-skew window, because a signature
outside that window is already rejected by the timestamp check and retaining it
longer only grows the store.

``NonceStoreUnavailable`` exists so G0 can tell "definitely a replay" apart from
"cannot say", which drives different decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Protocol

from kya.canonical import now_utc


class NonceStoreUnavailable(RuntimeError):
    """The store could not be consulted. Replay cannot be ruled out."""


class NonceStore(Protocol):
    def check_and_record(self, nonce: str) -> bool:
        """True if the nonce is fresh (and now recorded); False if already seen."""
        ...


@dataclass(slots=True)
class _Seen:
    at: datetime


class InMemoryNonceStore:
    """Process-local nonce store with lazy expiry.

    Adequate for a single-process gateway and for the eval harness. A
    multi-process deployment would put this in Redis; the interface is the same.
    """

    def __init__(
        self,
        ttl_seconds: int = 600,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._seen: dict[str, _Seen] = {}
        self.available: bool = True

    def check_and_record(self, nonce: str) -> bool:
        if not self.available:
            raise NonceStoreUnavailable("nonce store marked unavailable")

        now = self._clock()
        self._expire(now)

        if nonce in self._seen:
            return False
        self._seen[nonce] = _Seen(at=now)
        return True

    def _expire(self, now: datetime) -> None:
        cutoff = now - self._ttl
        stale = [k for k, v in self._seen.items() if v.at < cutoff]
        for key in stale:
            del self._seen[key]

    def __len__(self) -> int:
        return len(self._seen)
