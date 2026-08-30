"""The hash-chained obligation ledger.

Half these tests tamper with the database directly, which is the only way to
test a tamper-evident structure honestly. A chain that is only ever written
through its own API will of course verify; the question is whether it notices
when something reaches around it.
"""

from __future__ import annotations

import json

import pytest

from kya.enums import ObligationState, RailType
from kya.obligation.ledger import GENESIS_HASH, LedgerError
from kya.simulation import make_cart, make_obligation


def order_obligation(agent, principal, cart=None, **kwargs):
    return make_obligation(
        agent,
        principal,
        cart or make_cart(),
        rail_type=RailType.RAZORPAY_ORDER,
        rail_ref="kya_test_ref",
        **kwargs,
    )


class TestChaining:
    def test_the_first_entry_links_to_genesis(self, ledger, agent, principal):
        sealed = ledger.append(order_obligation(agent, principal))
        assert sealed.prev_hash == GENESIS_HASH
        assert sealed.self_hash == sealed.compute_hash()

    def test_each_entry_commits_to_the_previous_one(self, ledger, agent, principal):
        first = ledger.append(order_obligation(agent, principal))
        second = ledger.append(order_obligation(agent, principal))

        assert second.prev_hash == first.self_hash
        assert ledger.tip_hash() == second.self_hash

    def test_the_ledger_seals_receipts_the_minter_left_unsigned(
        self, ledger, agent, principal
    ):
        """Chaining and sealing belong to the ledger: ``prev_hash`` is a fact
        about where an entry landed, not about what was promised."""
        unsealed = order_obligation(agent, principal)
        unsealed.prev_hash = ""
        unsealed.merchant_signature = ""

        sealed = ledger.append(unsealed)
        assert sealed.merchant_signature
        assert ledger.verify().ok

    def test_appending_the_same_version_twice_is_refused(
        self, ledger, agent, principal
    ):
        receipt = order_obligation(agent, principal)
        ledger.append(receipt)
        with pytest.raises(LedgerError):
            ledger.append(receipt)

    def test_a_clean_chain_verifies(self, ledger, agent, principal):
        for _ in range(5):
            ledger.append(order_obligation(agent, principal))

        result = ledger.verify()
        assert result.ok
        assert result.entries == 5
        assert "intact" in result.summary()


class TestTamperEvidence:
    """Reaching around the API, which is what an attacker with database access
    would actually do."""

    def test_an_altered_payload_is_detected(self, ledger, agent, principal):
        sealed = ledger.append(order_obligation(agent, principal))
        for _ in range(2):
            ledger.append(order_obligation(agent, principal))

        payload = json.loads(
            ledger._conn.execute(
                "SELECT payload FROM obligations WHERE self_hash = ?",
                (sealed.self_hash,),
            ).fetchone()["payload"]
        )
        payload["promised"]["total"] = 1
        ledger._conn.execute(
            "UPDATE obligations SET payload = ? WHERE self_hash = ?",
            (json.dumps(payload), sealed.self_hash),
        )
        ledger._conn.commit()

        result = ledger.verify()
        assert not result.ok
        kinds = {f.kind for f in result.failures}
        assert "content_altered" in kinds
        assert "signature_invalid" in kinds

    def test_a_deleted_entry_is_as_detectable_as_a_modified_one(
        self, ledger, agent, principal
    ):
        """The property a plain audit log does not have. Deleting a row leaves
        the next row pointing at a hash that is no longer there."""
        ledger.append(order_obligation(agent, principal))
        middle = ledger.append(order_obligation(agent, principal))
        ledger.append(order_obligation(agent, principal))

        ledger._conn.execute(
            "DELETE FROM obligations WHERE self_hash = ?", (middle.self_hash,)
        )
        ledger._conn.commit()

        result = ledger.verify()
        assert not result.ok
        assert any(f.kind == "link_broken" for f in result.failures)

    def test_reordering_entries_breaks_the_chain(self, ledger, agent, principal):
        first = ledger.append(order_obligation(agent, principal))
        ledger.append(order_obligation(agent, principal))

        ledger._conn.execute(
            "UPDATE obligations SET seq = 99 WHERE self_hash = ?", (first.self_hash,)
        )
        ledger._conn.commit()

        assert not ledger.verify().ok

    def test_a_forged_signature_is_detected(self, ledger, agent, principal):
        sealed = ledger.append(order_obligation(agent, principal))

        payload = json.loads(
            ledger._conn.execute(
                "SELECT payload FROM obligations WHERE self_hash = ?",
                (sealed.self_hash,),
            ).fetchone()["payload"]
        )
        payload["merchant_signature"] = "A" * 86
        ledger._conn.execute(
            "UPDATE obligations SET payload = ? WHERE self_hash = ?",
            (json.dumps(payload), sealed.self_hash),
        )
        ledger._conn.commit()

        result = ledger.verify()
        assert not result.ok
        assert any(f.kind == "signature_invalid" for f in result.failures)
        # The self_hash excludes the signature, so the content check still
        # passes — the two controls cover different ground on purpose.
        assert not any(f.kind == "content_altered" for f in result.failures)

    def test_index_drift_from_the_payload_is_reported(
        self, ledger, agent, principal
    ):
        """Editing only the queryable column and leaving the hashed payload
        alone is the subtle version of tampering — a lookup lies while every
        hash still checks out."""
        sealed = ledger.append(order_obligation(agent, principal))
        ledger._conn.execute(
            "UPDATE obligations SET state = ? WHERE self_hash = ?",
            (ObligationState.SETTLED.value, sealed.self_hash),
        )
        ledger._conn.commit()

        result = ledger.verify()
        assert not result.ok
        assert any(f.kind == "index_drift" for f in result.failures)

    def test_verify_reports_every_break_not_just_the_first(
        self, ledger, agent, principal
    ):
        """A reviewer needs the extent of the tampering. One altered row and a
        rewritten tail look identical if you only report position."""
        sealed = [ledger.append(order_obligation(agent, principal)) for _ in range(4)]

        for victim in (sealed[1], sealed[2]):
            ledger._conn.execute(
                "UPDATE obligations SET amount_due = 7 WHERE self_hash = ?",
                (victim.self_hash,),
            )
        ledger._conn.commit()

        result = ledger.verify()
        assert not result.ok
        assert len({f.seq for f in result.failures}) >= 2


class TestVersioning:
    def test_state_changes_append_rather_than_mutate(
        self, ledger, agent, principal
    ):
        sealed = ledger.append(order_obligation(agent, principal))
        amended = ledger.amend(
            sealed.obligation_id, state=ObligationState.SETTLED, amount_due=0
        )

        assert amended.version == 2
        assert len(ledger.history(sealed.obligation_id)) == 2
        assert ledger.verify().ok

    def test_version_one_is_never_rewritten(self, ledger, agent, principal):
        """What the anchor pins must survive every later state change,
        or the anchor stops verifying exactly when someone bothers to check."""
        sealed = ledger.append(order_obligation(agent, principal))
        original_hash = sealed.self_hash

        ledger.amend(sealed.obligation_id, amount_due=0)
        ledger.amend(sealed.obligation_id, state=ObligationState.REVERSED)

        original = ledger.original(sealed.obligation_id)
        assert original.version == 1
        assert original.self_hash == original_hash
        assert original.compute_hash() == original_hash

    def test_an_amendment_preserves_the_promise(self, ledger, agent, principal):
        cart = make_cart(items=[("SKU-Z", "Thing", 2, 250_00)])
        sealed = ledger.append(order_obligation(agent, principal, cart))

        amended = ledger.amend(sealed.obligation_id, state=ObligationState.REVERSED)

        assert amended.promised.total == sealed.promised.total
        assert [i.sku for i in amended.promised.line_items] == ["SKU-Z"]
        assert amended.mandate_chain_hash == sealed.mandate_chain_hash

    def test_current_returns_the_latest_version(self, ledger, agent, principal):
        sealed = ledger.append(order_obligation(agent, principal))
        ledger.amend(sealed.obligation_id, amount_due=0)

        current = ledger.current(sealed.obligation_id)
        assert current.version == 2
        assert current.amount_due == 0

    def test_amending_an_unknown_obligation_is_refused(self, ledger):
        with pytest.raises(LedgerError):
            ledger.amend("obl_nope", state=ObligationState.SETTLED)

    def test_amount_due_cannot_go_negative(self, ledger, agent, principal):
        sealed = ledger.append(order_obligation(agent, principal))
        with pytest.raises(LedgerError):
            ledger.amend(sealed.obligation_id, amount_due=-1)


class TestLookups:
    def test_open_for_block_satisfies_the_obligation_source_protocol(
        self, ledger, agent, principal
    ):
        """G4's block guard reads the ledger through this method. If the real
        ledger did not satisfy it, the guard would be tested against a
        stand-in and shipped against nothing."""
        cart = make_cart(items=[("SKU-B", "Item", 1, 1_000_00)])
        sealed = ledger.append(
            make_obligation(
                agent,
                principal,
                cart,
                rail_type=RailType.RESERVE_PAY_BLOCK,
                rail_ref="blk_test",
            )
        )

        assert [o.obligation_id for o in ledger.open_for_block("blk_test")] == [
            sealed.obligation_id
        ]

    def test_a_settled_obligation_leaves_the_open_set(
        self, ledger, agent, principal
    ):
        cart = make_cart(items=[("SKU-B", "Item", 1, 1_000_00)])
        sealed = ledger.append(
            make_obligation(
                agent,
                principal,
                cart,
                rail_type=RailType.RESERVE_PAY_BLOCK,
                rail_ref="blk_test",
            )
        )
        ledger.amend(sealed.obligation_id, state=ObligationState.SETTLED)

        assert ledger.open_for_block("blk_test") == []

    def test_lookup_by_mandate_chain_finds_only_open_obligations(
        self, ledger, agent, principal
    ):
        sealed = ledger.append(
            order_obligation(agent, principal, mandate_chain_hash="chain_abc")
        )
        assert (
            ledger.open_for_mandate_chain("chain_abc").obligation_id
            == sealed.obligation_id
        )

        ledger.amend(sealed.obligation_id, state=ObligationState.REVERSED)
        assert ledger.open_for_mandate_chain("chain_abc") is None

    def test_rail_binding_round_trips(self, ledger, agent, principal):
        sealed = ledger.append(order_obligation(agent, principal))
        assert ledger.rail_id_for(sealed.obligation_id) is None

        ledger.bind_rail(sealed.obligation_id, "order_xyz")
        assert ledger.rail_id_for(sealed.obligation_id) == "order_xyz"
        assert ledger.by_rail_id("order_xyz").obligation_id == sealed.obligation_id

    def test_binding_is_outside_the_chain_by_design(
        self, ledger, agent, principal
    ):
        """The rail assigns its id after the order exists, so it cannot be part
        of what was signed before the order existed. Recording it must not
        disturb the chain."""
        sealed = ledger.append(order_obligation(agent, principal))
        tip_before = ledger.tip_hash()

        ledger.bind_rail(sealed.obligation_id, "order_xyz")

        assert ledger.tip_hash() == tip_before
        assert ledger.verify().ok
