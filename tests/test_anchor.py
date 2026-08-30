"""Anchoring the obligation hash into the Razorpay order record.

The control being tested is not "we wrote a note". It is that a reviewer who
does not trust our database can take the receipt, recompute its hash with
nothing but the receipt in hand, and match it against a record we do not
control.
"""

from __future__ import annotations

from kya.enums import ObligationState, RailType
from kya.obligation import ANCHOR_KEY, anchor_notes, verify_anchor
from kya.obligation.anchor import ANCHOR_VERSION_KEY
from kya.simulation import (
    build_signed_request,
    make_cart,
    make_mandates,
    make_obligation,
)


def fake_order(obligation, **overrides):
    """An order shaped like Razorpay's, carrying the anchor.

    The parameter is named ``obligation`` rather than ``receipt`` because the
    order object has a ``receipt`` field of its own, and the two mean different
    things — ours is the merchant-side reference, not the obligation.
    """
    order = {
        "id": "order_test_0001",
        "amount": obligation.promised.total,
        "currency": "INR",
        "receipt": obligation.rail.ref,
        "notes": anchor_notes(obligation),
        "status": "created",
    }
    order.update(overrides)
    return order


class TestAnchorWriting:
    def test_the_note_carries_the_hash_and_the_version(
        self, ledger, agent, principal
    ):
        sealed = ledger.append(
            make_obligation(
                agent,
                principal,
                make_cart(),
                rail_type=RailType.RAZORPAY_ORDER,
                rail_ref="kya_ref",
            )
        )
        notes = anchor_notes(sealed)

        assert notes[ANCHOR_KEY] == sealed.self_hash
        assert notes[ANCHOR_VERSION_KEY] == "1"

    def test_merchant_notes_survive_alongside_the_anchor(self, gateway, agent, principal):
        """An anchor that silently discarded a merchant's own order metadata is
        an anchor the merchant switches off."""
        cart = make_cart()
        result = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart),
            extra_notes={"campaign": "diwali", "channel": "agent"},
        )

        assert result.order["notes"]["campaign"] == "diwali"
        assert result.order["notes"][ANCHOR_KEY] == result.obligation.self_hash


class TestIndependentVerification:
    """The dispute-reviewer path: receipt in one hand, order in the other."""

    def test_a_matching_receipt_and_order_verify(self, ledger, agent, principal):
        sealed = ledger.append(
            make_obligation(
                agent,
                principal,
                make_cart(),
                rail_type=RailType.RAZORPAY_ORDER,
                rail_ref="kya_ref",
            )
        )

        check = verify_anchor(sealed, fake_order(sealed))
        assert check.ok
        assert check.recomputed_hash == sealed.self_hash
        assert "verified" in check.summary()

    def test_verification_recomputes_rather_than_trusting_the_stored_hash(
        self, ledger, agent, principal
    ):
        """If a receipt's contents are edited and its ``self_hash`` field is
        edited to match, reading the stored hash would happily agree with
        itself. Recomputing is what actually catches it."""
        sealed = ledger.append(
            make_obligation(
                agent,
                principal,
                make_cart(),
                rail_type=RailType.RAZORPAY_ORDER,
                rail_ref="kya_ref",
            )
        )
        order = fake_order(sealed)

        forged = sealed.model_copy(deep=True)
        forged.promised.total = 1
        forged.self_hash = forged.compute_hash()  # internally consistent lie

        check = verify_anchor(forged, order)
        assert not check.ok
        assert "does not match" in check.reason

    def test_an_order_without_the_note_does_not_verify(
        self, ledger, agent, principal
    ):
        sealed = ledger.append(
            make_obligation(
                agent,
                principal,
                make_cart(),
                rail_type=RailType.RAZORPAY_ORDER,
                rail_ref="kya_ref",
            )
        )

        check = verify_anchor(sealed, fake_order(sealed, notes={}))
        assert not check.ok
        assert ANCHOR_KEY in check.reason

    def test_a_mismatched_order_reference_does_not_verify(
        self, ledger, agent, principal
    ):
        """The binding is bidirectional. The order names the obligation through
        its receipt field, and the obligation names the order through its rail
        ref; half a match is not a match."""
        sealed = ledger.append(
            make_obligation(
                agent,
                principal,
                make_cart(),
                rail_type=RailType.RAZORPAY_ORDER,
                rail_ref="kya_ref",
            )
        )

        check = verify_anchor(sealed, fake_order(sealed, receipt="kya_someone_else"))
        assert not check.ok
        assert "does not name" in check.reason

    def test_a_later_version_is_refused_rather_than_silently_failing(
        self, ledger, agent, principal
    ):
        """A reviewer handed the current version gets told *why* it cannot
        match, instead of a bare hash mismatch that reads like tampering."""
        sealed = ledger.append(
            make_obligation(
                agent,
                principal,
                make_cart(),
                rail_type=RailType.RAZORPAY_ORDER,
                rail_ref="kya_ref",
            )
        )
        order = fake_order(sealed)
        amended = ledger.amend(sealed.obligation_id, amount_due=0)

        check = verify_anchor(amended, order)
        assert not check.ok
        assert "version 1" in check.reason
        assert check.version == 2


class TestAnchorSurvivesTheObligationsLife:
    def test_the_anchor_still_verifies_after_state_changes(
        self, gateway, ledger, agent, principal
    ):
        """The reason version 1 is immutable. An obligation that has been paid,
        partially refunded and reversed is exactly the one someone disputes."""
        cart = make_cart()
        result = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )
        obligation_id = result.obligation.obligation_id

        ledger.amend(obligation_id, amount_due=0)
        ledger.amend(obligation_id, state=ObligationState.REVERSED)

        original = ledger.original(obligation_id)
        assert verify_anchor(original, result.order).ok

    def test_the_gateway_verifies_its_own_anchor_at_creation(
        self, gateway, agent, principal
    ):
        """Discovering at dispute time that the note never landed is
        discovering it far too late."""
        cart = make_cart()
        result = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )

        assert result.anchor is not None
        assert result.anchor.ok
