"""The SIMULATED Reserve Pay block guard, tested at the ledger.

These cases isolate the conjunct the real rail does not have. SBMD bounds a
block by amount, time and merchant; every debit below is *within* those bounds,
and the ones that must fail do so only because nothing was owed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kya.enums import ObligationState, RailType
from kya.reserve_pay import BlockLedger, InMemoryObligations
from kya.simulation import (
    AgentIdentity,
    Principal,
    make_cart,
    make_obligation,
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
def obligations() -> InMemoryObligations:
    return InMemoryObligations()


@pytest.fixture
def ledger(obligations, clock) -> BlockLedger:
    return BlockLedger(obligations=obligations, clock=clock)


@pytest.fixture
def parties():
    return AgentIdentity.create("agent_shopper"), Principal.create("user_alice")


@pytest.fixture
def block(ledger):
    return ledger.create_block(
        principal_ref="user_alice", merchant_id="merch_sandbox_01", reserved=50_000_00
    )


def back_with_obligation(obligations, parties, block, amount: int, **kwargs):
    agent, principal = parties
    cart = make_cart(items=[("SKU-X", "Item", 1, amount)])
    return obligations.add(
        make_obligation(
            agent,
            principal,
            cart,
            rail_type=RailType.RESERVE_PAY_BLOCK,
            rail_ref=block.block_id,
            created_at=T0,
            **kwargs,
        )
    )


class TestTheGuard:
    def test_a_debit_backed_by_an_open_obligation_is_allowed(
        self, ledger, obligations, parties, block
    ):
        obligation = back_with_obligation(obligations, parties, block, 5_000_00)

        check = ledger.check_debit(block.block_id, 5_000_00)
        assert check.ok
        assert check.matched_obligation_id == obligation.obligation_id

    def test_a_debit_with_nothing_owed_is_denied(self, ledger, block):
        """The whole thesis, in one assertion. The block is valid, unexpired,
        for the right merchant, and has funds. The rail would settle this. It
        is denied because no obligation was ever incurred."""
        check = ledger.check_debit(block.block_id, 5_000_00)
        assert not check.ok
        assert check.code == "E004"
        assert check.detail["reason"] == "no_open_obligation"

    def test_a_debit_larger_than_what_is_owed_is_denied(
        self, ledger, obligations, parties, block
    ):
        back_with_obligation(obligations, parties, block, 1_000_00)

        check = ledger.check_debit(block.block_id, 5_000_00)
        assert not check.ok
        assert check.code == "E004"
        assert check.detail["reason"] == "amount_exceeds_due"
        assert check.detail["max_amount_due"] == 1_000_00

    def test_a_settled_obligation_no_longer_backs_a_debit(
        self, ledger, obligations, parties, block
    ):
        """Re-presenting a debit for work already paid for is the replay of
        this rail, and the obligation's state is what closes it."""
        back_with_obligation(
            obligations, parties, block, 5_000_00, state=ObligationState.SETTLED
        )

        check = ledger.check_debit(block.block_id, 5_000_00)
        assert not check.ok
        assert check.code == "E004"


class TestBlockDrain:
    def test_repeated_backed_debits_cannot_exceed_the_reserve(
        self, ledger, obligations, parties, block
    ):
        """Even with an obligation behind each one, cumulative debits stop at
        what was actually reserved."""
        small = ledger.create_block("user_alice", "merch_sandbox_01", reserved=10_000_00)
        back_with_obligation(obligations, parties, small, 10_000_00)

        first = ledger.check_debit(small.block_id, 6_000_00)
        assert first.ok
        ledger.apply_debit(small.block_id, 6_000_00, first.matched_obligation_id)

        second = ledger.check_debit(small.block_id, 6_000_00)
        assert not second.ok
        assert second.code == "E006"
        assert second.detail["available"] == 4_000_00

    def test_the_drain_shape_is_a_sequence_of_individually_valid_debits(
        self, ledger, block
    ):
        """Ten debits, each well formed, each inside the block's amount, time
        and merchant bounds — the exact traffic SBMD cannot refuse. Every one
        is denied here for the same reason: nothing was owed."""
        denials = [ledger.check_debit(block.block_id, 4_000_00) for _ in range(10)]

        assert all(not d.ok and d.code == "E004" for d in denials)
        assert ledger.get(block.block_id).debited == 0


class TestBlockLifecycle:
    def test_unknown_block_is_denied(self, ledger):
        check = ledger.check_debit("blk_does_not_exist", 100_00)
        assert not check.ok and check.detail["reason"] == "unknown_block"

    def test_revoked_block_is_denied(self, ledger, obligations, parties, block):
        back_with_obligation(obligations, parties, block, 5_000_00)
        ledger.revoke(block.block_id)

        check = ledger.check_debit(block.block_id, 5_000_00)
        assert not check.ok and check.detail["reason"] == "block_revoked"

    def test_expired_block_is_denied(
        self, ledger, obligations, parties, block, clock
    ):
        back_with_obligation(obligations, parties, block, 5_000_00)
        clock.advance(days=8)

        check = ledger.check_debit(block.block_id, 5_000_00)
        assert not check.ok and check.detail["reason"] == "block_expired"

    def test_checking_does_not_move_the_ledger(self, ledger, obligations, parties, block):
        """Check and apply are separate so a debit cleared here but denied by a
        later gate never books."""
        back_with_obligation(obligations, parties, block, 5_000_00)

        ledger.check_debit(block.block_id, 5_000_00)
        assert ledger.get(block.block_id).debited == 0
        assert ledger.debits_for(block.block_id) == []

    def test_applying_books_the_debit_against_the_obligation(
        self, ledger, obligations, parties, block
    ):
        obligation = back_with_obligation(obligations, parties, block, 5_000_00)
        check = ledger.check_debit(block.block_id, 5_000_00)

        debit = ledger.apply_debit(
            block.block_id, 5_000_00, check.matched_obligation_id
        )
        assert debit.obligation_id == obligation.obligation_id
        assert ledger.get(block.block_id).debited == 5_000_00
        assert ledger.get(block.block_id).available == 45_000_00


class TestLabelling:
    def test_the_ledger_declares_itself_simulated(self, ledger):
        """Claiming a live SBMD integration is the one thing that could sink an
        otherwise honest submission. The label is asserted, not just written in
        a docstring."""
        assert BlockLedger.SIMULATED is True

    def test_block_backed_obligations_carry_the_simulated_flag(
        self, obligations, parties, block
    ):
        obligation = back_with_obligation(obligations, parties, block, 1_000_00)
        assert obligation.rail.simulated is True
