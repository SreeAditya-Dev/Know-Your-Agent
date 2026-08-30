"""Clearing Passport: the ladder's movement rules and its storage.

The property worth protecting here is that tier is a *pure function of the
counters*. It is what lets a reviewer check a tier the same way the gateway
derived it, and it is what makes demotion immediate rather than a slow walk
back down one rung per incident.
"""

from __future__ import annotations

import pytest

from kya.enums import Tier
from kya.passport import (
    BASIS_DRIFT_HARD_FLOOR,
    InMemoryPassportStore,
    SqlitePassportStore,
    recompute_tier,
)
from kya.schemas import ClearingPassport


def passport(**kwargs) -> ClearingPassport:
    return ClearingPassport(agent_id="agent_a", **kwargs)


class TestLadder:
    def test_first_contact_is_t0_not_refused(self):
        """The cold-start answer: an unknown agent has a tier, so it can
        transact bounded rather than waiting for a record it cannot build."""
        assert recompute_tier(passport()) is Tier.T0

    @pytest.mark.parametrize(
        "cleared,expected",
        [(1, Tier.T1), (19, Tier.T1), (20, Tier.T2), (99, Tier.T2), (100, Tier.T3)],
    )
    def test_clean_history_promotes_at_the_documented_thresholds(
        self, cleared, expected
    ):
        assert recompute_tier(passport(cleared_count=cleared)) is expected

    def test_disputes_hold_an_agent_down(self):
        """60 clean clearings would be T2, but a 10% dispute rate is above
        T2's tolerance, so the agent sits at T1."""
        assert (
            recompute_tier(passport(cleared_count=60, disputed_count=6)) is Tier.T1
        )

    def test_demotion_is_immediate_and_multi_step(self):
        """A burst of disputes drops an agent straight to the bottom rather
        than one rung at a time. Promotion is a claim about the future and
        should be slow; demotion reports the past and should not be."""
        established = passport(cleared_count=150)
        assert recompute_tier(established) is Tier.T3

        established.disputed_count = 60
        assert recompute_tier(established) is Tier.T0

    def test_basis_drift_floors_an_otherwise_perfect_agent(self):
        """Basis drift is a verifier claiming better evidence than it holds —
        an integrity claim, not a delivery that went wrong. Enough of it caps
        the agent regardless of how good the rest of the record looks."""
        drifting = passport(
            cleared_count=1_000, basis_drift_events=BASIS_DRIFT_HARD_FLOOR
        )
        assert recompute_tier(drifting) is Tier.T0

    def test_tier_is_a_pure_function_of_the_counters(self):
        """Two agents with identical counters must hold identical tiers,
        whatever order they arrived in."""
        a = passport(cleared_count=40, disputed_count=1)
        b = passport(cleared_count=40, disputed_count=1)
        b.tier = Tier.T0  # stale stored value
        assert recompute_tier(a) is recompute_tier(b)


@pytest.fixture(params=["memory", "sqlite"])
def store(request):
    if request.param == "memory":
        return InMemoryPassportStore()
    return SqlitePassportStore(":memory:")


class TestStore:
    """Both backends must be indistinguishable through the interface."""

    def test_unknown_agent_gets_a_fresh_t0_passport(self, store):
        p = store.get("agent_new")
        assert p.agent_id == "agent_new"
        assert p.tier is Tier.T0
        assert p.cleared_count == 0

    def test_clearings_accumulate_and_promote(self, store):
        for _ in range(20):
            store.record_cleared("agent_a", value=1_000_00)

        p = store.get("agent_a")
        assert p.cleared_count == 20
        assert p.total_cleared_value == 20_000_00
        assert p.tier is Tier.T2

    def test_a_dispute_demotes_on_the_spot(self, store):
        for _ in range(20):
            store.record_cleared("agent_a")
        assert store.get("agent_a").tier is Tier.T2

        for _ in range(3):
            store.record_disputed("agent_a")
        assert store.get("agent_a").tier is Tier.T1

    def test_stored_tier_always_matches_its_own_counters(self, store):
        """A store cannot end up holding a tier its counters do not justify."""
        for _ in range(30):
            store.record_cleared("agent_a")
        store.record_basis_drift("agent_a")

        p = store.get("agent_a")
        assert p.tier is recompute_tier(p)

    def test_reads_do_not_alias_stored_state(self, store):
        """A caller mutating what it read must not silently rewrite history."""
        store.record_cleared("agent_a")
        borrowed = store.get("agent_a")
        borrowed.cleared_count = 9_999

        assert store.get("agent_a").cleared_count == 1


class TestDurability:
    def test_a_passport_survives_a_restart(self, tmp_path):
        """Without this the ladder resets on every deploy and no agent ever
        accumulates the record the ladder exists to reward."""
        path = tmp_path / "passports.db"

        first = SqlitePassportStore(path)
        for _ in range(25):
            first.record_cleared("agent_a", value=500_00)
        first.close()

        second = SqlitePassportStore(path)
        reopened = second.get("agent_a")
        assert reopened.cleared_count == 25
        assert reopened.total_cleared_value == 12_500_00
        assert reopened.tier is Tier.T2
        second.close()
