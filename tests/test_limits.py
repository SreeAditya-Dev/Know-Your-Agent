"""Unit tests for the counters G4 is built on.

Tested apart from the gate because these are the only components in the inline
path whose behaviour depends on elapsed time. Every case here drives an
explicit clock: a rate limiter tested against wall time is a flaky test that
occasionally hides a real bug.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kya.enums import Action
from kya.limits import (
    LimitStore,
    TokenBucket,
    agent_key,
    agent_merchant_key,
    intent_key,
)

T0 = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> datetime:
        self.now += timedelta(**kwargs)
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock) -> LimitStore:
    return LimitStore(clock=clock)


class TestTokenBucket:
    def test_burst_up_to_capacity_then_refuses(self, store):
        key = agent_key("agent_a")
        assert all(store.try_consume(key, per_hour=3).allowed for _ in range(3))
        assert not store.try_consume(key, per_hour=3).allowed

    def test_refills_at_the_hourly_rate(self, store, clock):
        key = agent_key("agent_a")
        for _ in range(3):
            store.try_consume(key, per_hour=3)
        assert not store.try_consume(key, per_hour=3).allowed

        # 3/hr is one token per 20 minutes.
        clock.advance(minutes=20)
        assert store.try_consume(key, per_hour=3).allowed
        assert not store.try_consume(key, per_hour=3).allowed

    def test_retry_after_tells_the_caller_when_to_come_back(self, store):
        key = agent_key("agent_a")
        for _ in range(3):
            store.try_consume(key, per_hour=3)

        result = store.try_consume(key, per_hour=3)
        assert not result.allowed
        assert result.retry_after_seconds == pytest.approx(1200, abs=1)

    def test_never_accumulates_past_capacity(self, clock):
        bucket = TokenBucket(
            capacity=3, refill_per_second=1.0, tokens=0.0, updated_at=T0.timestamp()
        )
        far_future = (T0 + timedelta(days=1)).timestamp()
        assert bucket.available(far_future) == 3

    def test_scopes_are_independent(self, store):
        agent = agent_key("agent_a")
        pair = agent_merchant_key("agent_a", "merch_1")
        for _ in range(3):
            store.try_consume(agent, per_hour=3)

        assert not store.try_consume(agent, per_hour=3).allowed
        assert store.try_consume(pair, per_hour=3).allowed


class TestTierChangeTakesEffectImmediately:
    """The ladder has to be *observable*, which means a promotion must widen
    the limit on the very next request rather than at the next bucket epoch."""

    def test_promotion_raises_the_ceiling_at_once(self, store):
        key = agent_key("agent_a")
        for _ in range(3):
            store.try_consume(key, per_hour=3)
        assert not store.try_consume(key, per_hour=3).allowed

        assert store.try_consume(key, per_hour=20).allowed

    def test_demotion_clamps_credit_to_the_new_ceiling(self, store):
        key = agent_key("agent_a")
        store.try_consume(key, per_hour=500)  # 499 tokens left

        # Dropping to T0 must not leave 499 tokens of T3 credit in the bucket.
        result = store.try_consume(key, per_hour=3)
        assert result.allowed
        assert result.remaining <= 3


class TestSlidingWindow:
    def test_counts_and_values_inside_the_window(self, store):
        key = agent_key("agent_a")
        store.record(key, Action.PURCHASE, 1_000_00)
        store.record(key, Action.PURCHASE, 2_500_00)

        stats = store.stats(key, window_seconds=3600)
        assert stats.count == 2
        assert stats.value == 3_500_00

    def test_events_leave_the_window(self, store, clock):
        key = agent_key("agent_a")
        store.record(key, Action.PURCHASE, 1_000_00)

        clock.advance(minutes=61)
        assert store.stats(key, window_seconds=3600).count == 0

    def test_filters_by_action(self, store):
        key = agent_key("agent_a")
        store.record(key, Action.PURCHASE, 1_000_00)
        store.record(key, Action.REFUND, 400_00)

        purchases = store.stats(key, 3600, actions=(Action.PURCHASE,))
        refunds = store.stats(key, 3600, actions=(Action.REFUND,))
        assert purchases.value == 1_000_00
        assert refunds.value == 400_00

    def test_unknown_key_is_empty_not_an_error(self, store):
        stats = store.stats(intent_key("int_never_seen"), 3600)
        assert stats.count == 0 and stats.value == 0
