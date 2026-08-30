"""Rate and spend accounting for the bounded action envelope.

Two primitives, chosen for different questions:

* a **token bucket** answers "is this agent calling too fast right now?" — it
  permits a burst up to capacity and then meters, which is the right shape for
  traffic that arrives in machine-timed clumps rather than human-paced ones;
* a **sliding window** answers "how much has this agent moved over the last
  hour?" — an exact event log, because a spend cap that is off by a partial
  bucket is a spend cap that cannot be reconciled against a ledger.

Both are keyed on tuples rather than strings so that a key is structurally
impossible to forge by embedding a separator in an agent id.

**Consumption is deliberately split.** Velocity is consumed by every request
that reaches G4, including ones later denied for unrelated reasons — a request
costs you capacity whether or not you liked it, which is what makes rate
limiting a flood defence. Spend is recorded only after a decision comes back
ALLOW, because a denied purchase spent nothing and must not eat the buyer's
budget. The gate does the first inline and the second in ``commit``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from kya.canonical import now_utc
from kya.enums import Action

#: A structured limit key. The leading element names the scope.
LimitKey = tuple[str, ...]

#: How long events are retained. Longer than any window we ask about, so a
#: query never sees a truncated history and silently under-counts.
DEFAULT_RETENTION_SECONDS = 24 * 3600


def agent_key(agent_id: str) -> LimitKey:
    """Across every merchant this gateway fronts."""
    return ("agent", agent_id)


def agent_merchant_key(agent_id: str, merchant_id: str) -> LimitKey:
    """One agent against one merchant.

    Same rate as the agent-level bucket but a narrower scope. In this
    single-merchant sandbox the two coincide; in front of several merchants the
    agent-level bucket is what stops one caller spending its whole allowance in
    one place, and this one is what each merchant is individually protected by.
    """
    return ("agent_merchant", agent_id, merchant_id)


def principal_key(principal_ref: str) -> LimitKey:
    """The human who delegated. Bounds a principal across all their agents."""
    return ("principal", principal_ref)


def intent_key(intent_id: str) -> LimitKey:
    """One delegation. Carries the intent mandate's own transaction cap."""
    return ("intent", intent_id)


# --- token bucket ------------------------------------------------------------


@dataclass(slots=True)
class TokenBucket:
    """Classic leaky bucket over wall-clock seconds.

    ``capacity`` and ``refill_per_second`` are re-supplied on every call rather
    than fixed at construction, because an agent's tier can change between two
    requests and the new ceiling must take effect immediately — that is the
    ladder being observable, which is the whole point of having one.
    """

    capacity: float
    refill_per_second: float
    tokens: float
    updated_at: float

    def _refill(self, now_ts: float) -> None:
        elapsed = max(0.0, now_ts - self.updated_at)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.updated_at = now_ts

    def reconfigure(self, capacity: float, refill_per_second: float) -> None:
        """Apply a new tier's limits. Credit moves with the ceiling.

        A promotion grants the added headroom immediately rather than making
        the agent wait for the wider bucket to refill. Without that, an agent
        promoted while throttled stays throttled — the ladder would be
        something the audit trail records rather than something the agent can
        observe, which defeats the point of having one.

        A demotion clamps in the same motion, so credit earned under a wider
        ceiling cannot be spent under a narrower one.
        """
        if capacity == self.capacity and refill_per_second == self.refill_per_second:
            return
        granted = max(0.0, capacity - self.capacity)
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = min(capacity, self.tokens + granted)

    def try_consume(self, now_ts: float, amount: float = 1.0) -> bool:
        self._refill(now_ts)
        if self.tokens < amount:
            return False
        self.tokens -= amount
        return True

    def available(self, now_ts: float) -> float:
        self._refill(now_ts)
        return self.tokens

    def retry_after(self, now_ts: float, amount: float = 1.0) -> float:
        """Seconds until ``amount`` tokens exist. Answers the client's real
        question — a bare denial tells an agent nothing except to retry now."""
        self._refill(now_ts)
        if self.tokens >= amount:
            return 0.0
        if self.refill_per_second <= 0:
            return float("inf")
        return (amount - self.tokens) / self.refill_per_second


@dataclass(slots=True)
class ConsumeResult:
    allowed: bool
    key: LimitKey
    remaining: float
    retry_after_seconds: float
    capacity: float


# --- sliding window ----------------------------------------------------------


@dataclass(slots=True)
class Event:
    at: float
    action: Action
    amount: int


@dataclass(slots=True)
class WindowStats:
    count: int = 0
    value: int = 0

    def __bool__(self) -> bool:  # pragma: no cover - display helper
        return bool(self.count)


class LimitStore:
    """Process-local counters for velocity and spend.

    Single-process, like the nonce store, and for the same reason: the eval
    harness and the demo gateway are one process. A multi-process deployment
    moves both to Redis without changing either interface.
    """

    def __init__(
        self,
        clock: Callable[[], datetime] = now_utc,
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
    ) -> None:
        self._clock = clock
        self._retention = retention_seconds
        self._buckets: dict[LimitKey, TokenBucket] = {}
        self._events: dict[LimitKey, deque[Event]] = {}

    def _now_ts(self, now: datetime | None = None) -> float:
        return (now or self._clock()).timestamp()

    # --- velocity ------------------------------------------------------------

    def try_consume(
        self,
        key: LimitKey,
        per_hour: int,
        now: datetime | None = None,
        amount: float = 1.0,
    ) -> ConsumeResult:
        """Take one token from ``key``'s bucket at the given hourly rate."""
        now_ts = self._now_ts(now)
        capacity = float(max(per_hour, 0))
        refill = capacity / 3600.0

        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(
                capacity=capacity,
                refill_per_second=refill,
                tokens=capacity,
                updated_at=now_ts,
            )
            self._buckets[key] = bucket
        else:
            bucket.reconfigure(capacity, refill)

        allowed = bucket.try_consume(now_ts, amount)
        return ConsumeResult(
            allowed=allowed,
            key=key,
            remaining=round(bucket.available(now_ts), 4),
            retry_after_seconds=round(bucket.retry_after(now_ts, amount), 2),
            capacity=capacity,
        )

    # --- spend ---------------------------------------------------------------

    def record(
        self,
        key: LimitKey,
        action: Action,
        amount: int,
        now: datetime | None = None,
    ) -> None:
        now_ts = self._now_ts(now)
        events = self._events.setdefault(key, deque())
        events.append(Event(at=now_ts, action=action, amount=amount))
        self._prune(events, now_ts)

    def stats(
        self,
        key: LimitKey,
        window_seconds: int,
        now: datetime | None = None,
        actions: Iterable[Action] | None = None,
    ) -> WindowStats:
        """Count and value of matching events inside the trailing window."""
        now_ts = self._now_ts(now)
        events = self._events.get(key)
        if not events:
            return WindowStats()

        self._prune(events, now_ts)
        cutoff = now_ts - window_seconds
        wanted = set(actions) if actions is not None else None

        stats = WindowStats()
        for event in events:
            if event.at < cutoff:
                continue
            if wanted is not None and event.action not in wanted:
                continue
            stats.count += 1
            stats.value += event.amount
        return stats

    def _prune(self, events: deque[Event], now_ts: float) -> None:
        cutoff = now_ts - self._retention
        while events and events[0].at < cutoff:
            events.popleft()

    # --- introspection -------------------------------------------------------

    def reset(self) -> None:
        self._buckets.clear()
        self._events.clear()

    def snapshot(self, key: LimitKey, window_seconds: int) -> dict[str, object]:
        """Counter state for the audit trail and the dashboard."""
        now_ts = self._now_ts()
        bucket = self._buckets.get(key)
        stats = self.stats(key, window_seconds)
        return {
            "key": list(key),
            "tokens_remaining": round(bucket.available(now_ts), 2) if bucket else None,
            "window_count": stats.count,
            "window_value": stats.value,
        }
