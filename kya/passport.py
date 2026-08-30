"""Clearing Passport storage and the trust ladder's movement rules.

`kya/policy.py` holds what each tier *permits*. This module holds how an agent
*arrives* at a tier, and where that record is kept between requests. The two
are separated because the first is merchant configuration and the second is
earned history — a merchant may raise a ceiling, but nobody edits an agent's
record of what it did.

**Tier is a pure function of the passport's counters.** There is no hidden
state and no path dependence: recomputing from the counters always yields the
same tier, so a reviewer can check the tier the same way the gateway derived
it. It also makes demotion immediate and multi-step. An agent that accumulates
disputes does not walk back down one rung per incident; it lands wherever its
current record puts it, which for a burst of disputes is the bottom.

That asymmetry is intentional. Promotion should be slow because it is a claim
about future behaviour. Demotion should be fast because it is a report of
behaviour already observed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from kya.canonical import now_utc
from kya.enums import Tier
from kya.schemas import ClearingPassport


@dataclass(frozen=True, slots=True)
class TierRule:
    """What an agent must show to hold a tier."""

    tier: Tier
    min_cleared: int
    max_dispute_rate: float
    max_basis_drift: int


#: Evaluated top down; the first rule an agent satisfies is its tier.
#:
#: The thresholds are deliberately unremarkable — the defensible part is not
#: the numbers but that they are declared here, in one table, rather than
#: scattered through the code that consumes them.
LADDER: tuple[TierRule, ...] = (
    TierRule(Tier.T3, min_cleared=100, max_dispute_rate=0.01, max_basis_drift=0),
    TierRule(Tier.T2, min_cleared=20, max_dispute_rate=0.05, max_basis_drift=1),
    TierRule(Tier.T1, min_cleared=1, max_dispute_rate=0.20, max_basis_drift=2),
    TierRule(Tier.T0, min_cleared=0, max_dispute_rate=1.0, max_basis_drift=10**9),
)

#: Basis drift is a verifier declaring a higher evidence class than it can
#: support — RAILS calls it LAUNDER-BASIS. It is a claim about the integrity of
#: the evidence itself, not a delivery that went wrong, so enough of it floors
#: an agent outright regardless of how well its other counters read.
BASIS_DRIFT_HARD_FLOOR = 3


def recompute_tier(passport: ClearingPassport) -> Tier:
    """The agent's tier, derived from its counters alone."""
    if passport.basis_drift_events >= BASIS_DRIFT_HARD_FLOOR:
        return Tier.T0

    rate = passport.dispute_rate
    for rule in LADDER:
        if (
            passport.cleared_count >= rule.min_cleared
            and rate <= rule.max_dispute_rate
            and passport.basis_drift_events <= rule.max_basis_drift
        ):
            return rule.tier
    return Tier.T0


class PassportStore(Protocol):
    """Where passports live between requests."""

    def get(self, agent_id: str) -> ClearingPassport:
        """The agent's passport, minting a fresh T0 one on first contact."""
        ...

    def put(self, passport: ClearingPassport) -> ClearingPassport: ...

    def all(self) -> list[ClearingPassport]: ...


class _StoreMixin:
    """Outcome recording, shared by every backend.

    Each method re-derives the tier after touching the counters, so a store
    cannot end up holding a tier its own counters do not justify.
    """

    def get(self, agent_id: str) -> ClearingPassport:  # pragma: no cover - interface
        raise NotImplementedError

    def put(self, passport: ClearingPassport) -> ClearingPassport:  # pragma: no cover
        raise NotImplementedError

    def _clock(self) -> datetime:  # pragma: no cover - interface
        raise NotImplementedError

    def record_cleared(self, agent_id: str, value: int = 0) -> ClearingPassport:
        passport = self.get(agent_id)
        passport.cleared_count += 1
        passport.total_cleared_value += value
        return self._settle(passport)

    def record_disputed(self, agent_id: str) -> ClearingPassport:
        passport = self.get(agent_id)
        passport.disputed_count += 1
        return self._settle(passport)

    def record_basis_drift(self, agent_id: str) -> ClearingPassport:
        passport = self.get(agent_id)
        passport.basis_drift_events += 1
        return self._settle(passport)

    def touch(self, agent_id: str) -> ClearingPassport:
        """Record that the agent was seen, without judging the outcome."""
        return self._settle(self.get(agent_id))

    def _settle(self, passport: ClearingPassport) -> ClearingPassport:
        passport.tier = recompute_tier(passport)
        passport.last_seen = self._clock()
        return self.put(passport)


class InMemoryPassportStore(_StoreMixin):
    """Process-local store, used by tests and the eval harness."""

    def __init__(self, clock: Callable[[], datetime] = now_utc) -> None:
        self._clock_fn = clock
        self._rows: dict[str, ClearingPassport] = {}

    def _clock(self) -> datetime:
        return self._clock_fn()

    def get(self, agent_id: str) -> ClearingPassport:
        existing = self._rows.get(agent_id)
        if existing is not None:
            return existing.model_copy(deep=True)
        now = self._clock()
        return ClearingPassport(agent_id=agent_id, first_seen=now, last_seen=now)

    def put(self, passport: ClearingPassport) -> ClearingPassport:
        self._rows[passport.agent_id] = passport.model_copy(deep=True)
        return passport

    def all(self) -> list[ClearingPassport]:
        return [p.model_copy(deep=True) for p in self._rows.values()]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS passports (
    agent_id            TEXT PRIMARY KEY,
    tier                TEXT NOT NULL,
    cleared_count       INTEGER NOT NULL DEFAULT 0,
    disputed_count      INTEGER NOT NULL DEFAULT 0,
    basis_drift_events  INTEGER NOT NULL DEFAULT 0,
    total_cleared_value INTEGER NOT NULL DEFAULT 0,
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL
);
"""


class SqlitePassportStore(_StoreMixin):
    """Durable store. SQLite because a passport that does not survive a restart
    reopens the cold-start problem the ladder exists to close: every agent would
    be T0 again after every deploy, and earned trust would never accumulate."""

    def __init__(
        self,
        path: str | Path = ":memory:",
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._clock_fn = clock
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _clock(self) -> datetime:
        return self._clock_fn()

    def get(self, agent_id: str) -> ClearingPassport:
        row = self._conn.execute(
            "SELECT * FROM passports WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            now = self._clock()
            return ClearingPassport(agent_id=agent_id, first_seen=now, last_seen=now)
        return ClearingPassport(
            agent_id=row["agent_id"],
            tier=Tier(row["tier"]),
            cleared_count=row["cleared_count"],
            disputed_count=row["disputed_count"],
            basis_drift_events=row["basis_drift_events"],
            total_cleared_value=row["total_cleared_value"],
            first_seen=datetime.fromisoformat(row["first_seen"]),
            last_seen=datetime.fromisoformat(row["last_seen"]),
        )

    def put(self, passport: ClearingPassport) -> ClearingPassport:
        self._conn.execute(
            """
            INSERT INTO passports (agent_id, tier, cleared_count, disputed_count,
                                   basis_drift_events, total_cleared_value,
                                   first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                tier                = excluded.tier,
                cleared_count       = excluded.cleared_count,
                disputed_count      = excluded.disputed_count,
                basis_drift_events  = excluded.basis_drift_events,
                total_cleared_value = excluded.total_cleared_value,
                last_seen           = excluded.last_seen
            """,
            (
                passport.agent_id,
                passport.tier.value,
                passport.cleared_count,
                passport.disputed_count,
                passport.basis_drift_events,
                passport.total_cleared_value,
                passport.first_seen.isoformat(),
                passport.last_seen.isoformat(),
            ),
        )
        self._conn.commit()
        return passport

    def all(self) -> list[ClearingPassport]:
        rows = self._conn.execute("SELECT agent_id FROM passports ORDER BY agent_id")
        return [self.get(row["agent_id"]) for row in rows.fetchall()]

    def close(self) -> None:
        self._conn.close()
