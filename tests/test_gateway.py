"""The gateway: decision → obligation → rail, and the double-charge defences.

The sequencing is what is under test here, not any single component. Each piece
works in isolation; the ways they can be wired wrongly are what cost a merchant
money.
"""

from __future__ import annotations

import pytest

from kya.enums import Decision, ObligationState
from kya.obligation.anchor import ANCHOR_KEY
from kya.rails.razorpay_client import RailError
from kya.simulation import (
    build_refund_request,
    build_signed_request,
    make_cart,
    make_mandates,
    resign_request,
)


class TestAllowedPurchase:
    def test_an_allowed_purchase_mints_an_obligation_and_creates_an_order(
        self, gateway, agent, principal
    ):
        cart = make_cart()
        result = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )

        assert result.envelope.decision is Decision.ALLOW
        assert result.obligation is not None
        assert result.order is not None
        assert result.order["amount"] == cart.total
        assert result.order["receipt"] == result.obligation.rail.ref
        assert result.anchor.ok

    def test_the_obligation_records_what_was_promised(
        self, gateway, agent, principal
    ):
        """The question no payment object answers."""
        cart = make_cart(items=[("SKU-TV-55", "TV 55 inch", 1, 45_000_00)])
        result = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart),
        )

        promised = result.obligation.promised
        assert [i.sku for i in promised.line_items] == ["SKU-TV-55"]
        assert promised.total == 45_000_00
        assert promised.delivery_window is not None
        assert promised.return_window_days == 7
        assert result.obligation.acceptance_criteria
        assert result.obligation.evidence_requirements

    def test_the_obligation_binds_back_to_the_signed_mandate_chain(
        self, gateway, agent, principal
    ):
        cart = make_cart()
        mandates = make_mandates(agent, principal, cart)
        result = gateway.create_order(build_signed_request(agent, mandates, cart))

        assert result.obligation.mandate_chain_hash == mandates.chain_hash()
        assert result.obligation.principal_ref == principal.principal_ref
        assert result.obligation.agent_key_id == agent.keypair.key_id

    def test_the_evidence_floor_comes_from_the_agents_tier(
        self, sandbox, agent, principal
    ):
        """Where the trust ladder reaches the clearing layer: a stranger's
        delivery has to be proved on stronger evidence than a regular's."""
        from kya.enums import Tier
        from kya.evidence import EvidenceClass

        gateway = sandbox.gateway()
        sandbox.set_tier(agent.agent_id, Tier.T0)
        cart = make_cart(items=[("SKU-A", "Item", 1, 500_00)])
        result = gateway.create_order(
            build_signed_request(
                agent, make_mandates(agent, principal, cart), cart
            )
        )

        assert result.obligation.admissibility_floor is EvidenceClass.REC

    def test_the_decision_envelope_names_the_obligation(
        self, gateway, agent, principal
    ):
        """The audit trail has to join the two. A decision that does not name
        what it authorised cannot be reviewed against what happened."""
        cart = make_cart()
        result = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )
        assert result.envelope.obligation_id == result.obligation.obligation_id

    def test_the_ledger_chain_stays_intact_across_many_orders(
        self, gateway, ledger, agent, principal
    ):
        for _ in range(8):
            cart = make_cart(items=[("SKU-A", "Item", 1, 500_00)])
            gateway.create_order(
                build_signed_request(
                    agent, make_mandates(agent, principal, cart), cart
                )
            )

        verification = ledger.verify()
        assert verification.ok
        assert verification.entries == 8


class TestRefusedActions:
    def test_a_denied_request_mints_nothing_and_creates_nothing(
        self, gateway, ledger, rail, agent, principal
    ):
        """The most important negative in the file. A blocked attack must not
        leave an obligation behind for a reconciler to later 'recover'."""
        cart = make_cart()
        mandates = make_mandates(agent, principal, cart)
        tampered = make_cart(items=[("SKU-PHONE-256", "Phone 256GB", 1, 1_00)])
        request = resign_request(
            agent, build_signed_request(agent, mandates, cart)
        )
        request.cart = tampered
        resign_request(agent, request)

        result = gateway.create_order(request)

        assert result.envelope.decision is Decision.DENY
        assert result.obligation is None
        assert result.order is None
        assert len(ledger) == 0
        assert rail.orders == {}

    def test_a_stepped_up_request_creates_no_order(
        self, sandbox, agent, principal
    ):
        """STEP_UP is not ALLOW. Money moves only on ALLOW."""
        from kya.enums import Tier

        gateway = sandbox.gateway()
        sandbox.set_tier(agent.agent_id, Tier.T0)
        cart = make_cart(items=[("SKU-A", "Item", 1, 50_000_00)])
        result = gateway.create_order(
            build_signed_request(
                agent,
                make_mandates(agent, principal, cart, max_amount=10_000_000_00),
                cart,
            )
        )

        assert result.envelope.decision is Decision.STEP_UP
        assert result.obligation is None
        assert sandbox.rail.orders == {}


class TestDoubleChargeDefences:
    def test_a_retry_with_the_same_idempotency_key_returns_the_same_order(
        self, gateway, ledger, rail, agent, principal
    ):
        cart = make_cart()
        request = build_signed_request(
            agent, make_mandates(agent, principal, cart), cart
        )

        first = gateway.create_order(request)
        for _ in range(5):
            replay = gateway.create_order(request)
            assert replay.replayed
            assert replay.order["id"] == first.order["id"]
            assert replay.obligation.obligation_id == first.obligation.obligation_id

        assert len(rail.orders) == 1
        assert len(ledger) == 1

    def test_a_retry_with_a_fresh_idempotency_key_is_still_caught(
        self, gateway, ledger, rail, agent, principal
    ):
        """The harder case. A naive agent that regenerates its idempotency key
        on retry defeats decision caching entirely — but it cannot forge a
        second cart mandate, so the mandate chain hash still identifies the
        purchase as the one already made."""
        cart = make_cart()
        mandates = make_mandates(agent, principal, cart)

        first = gateway.create_order(build_signed_request(agent, mandates, cart))
        second = gateway.create_order(build_signed_request(agent, mandates, cart))

        assert second.obligation.obligation_id == first.obligation.obligation_id
        assert len(rail.orders) == 1
        assert len(ledger) == 1

    def test_a_genuinely_new_purchase_is_not_mistaken_for_a_retry(
        self, gateway, ledger, rail, agent, principal
    ):
        """The false-positive side. A buyer ordering the same items again
        presents a *new* cart mandate, and must get a second order."""
        cart = make_cart()

        first = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )
        second = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )

        assert second.obligation.obligation_id != first.obligation.obligation_id
        assert len(rail.orders) == 2
        assert len(ledger) == 2


class TestRailFailure:
    def test_a_lost_response_still_leaves_a_recoverable_obligation(
        self, gateway, ledger, rail, agent, principal
    ):
        """The ordering that makes graceful failure possible. Had the order
        been created before the obligation, this would leave money in flight
        with nothing local pointing at it."""
        rail.drop_responses = True
        cart = make_cart()

        result = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )

        assert result.envelope.decision is Decision.ALLOW
        assert result.order is None
        assert result.rail_error is not None
        assert result.needs_reconciliation

        # The obligation exists, is open, and names the reference the rail was
        # asked to record — which is how it will be found again.
        assert result.obligation.state is ObligationState.OPEN
        assert len(ledger) == 1
        assert len(rail.orders) == 1
        assert list(rail.orders.values())[0]["receipt"] == result.obligation.rail.ref

    def test_the_anchor_landed_even_though_the_response_did_not(
        self, gateway, rail, agent, principal
    ):
        rail.drop_responses = True
        cart = make_cart()

        result = gateway.create_order(
            build_signed_request(agent, make_mandates(agent, principal, cart), cart)
        )

        order = list(rail.orders.values())[0]
        assert order["notes"][ANCHOR_KEY] == result.obligation.self_hash


class TestGuardedRefund:
    def test_an_allowed_refund_reaches_the_rail(
        self, gateway, rail, agent, principal
    ):
        cart = make_cart(items=[("SKU-A", "Item", 1, 1_000_00)])
        for _ in range(10):
            c = make_cart(items=[("SKU-A", "Item", 1, 1_000_00)])
            gateway.create_order(
                build_signed_request(agent, make_mandates(agent, principal, c), c)
            )

        order_id = next(iter(rail.orders))
        payment = rail.pay(order_id)

        mandates = make_mandates(agent, principal, cart)
        result = gateway.submit_refund(
            build_refund_request(agent, mandates, cart, 1_000_00),
            payment_id=payment["id"],
            amount=1_000_00,
        )

        assert result.envelope.decision is Decision.ALLOW
        assert result.refund["amount"] == 1_000_00
        assert rail.payments[payment["id"]]["amount_refunded"] == 1_000_00

    def test_a_refund_the_breaker_blocked_never_reaches_the_rail(
        self, gateway, rail, agent, principal
    ):
        """G4 quarantines a refund flood. The gateway must not then call the
        rail anyway — a guard that logs its objection and proceeds is not a
        guard."""
        cart = make_cart()
        mandates = make_mandates(agent, principal, cart)

        result = gateway.submit_refund(
            build_refund_request(agent, mandates, cart, 1_000_00),
            payment_id="pay_whatever",
            amount=1_000_00,
        )

        assert result.envelope.decision is Decision.QUARANTINE
        assert "E003" in result.envelope.reason_codes
        assert result.refund is None
        assert rail.refunds == {}

    def test_a_full_refund_reverses_the_obligation(
        self, gateway, ledger, rail, agent, principal
    ):
        cart = make_cart(items=[("SKU-A", "Item", 1, 1_000_00)])
        mandates = make_mandates(agent, principal, cart)
        created = gateway.create_order(build_signed_request(agent, mandates, cart))

        for _ in range(9):
            c = make_cart(items=[("SKU-A", "Item", 1, 1_000_00)])
            gateway.create_order(
                build_signed_request(agent, make_mandates(agent, principal, c), c)
            )

        payment = rail.pay(created.order["id"])
        gateway.submit_refund(
            build_refund_request(agent, mandates, cart, 1_000_00),
            payment_id=payment["id"],
            amount=1_000_00,
        )

        current = ledger.current(created.obligation.obligation_id)
        assert current.state is ObligationState.REVERSED
        assert current.version == 2
        assert ledger.verify().ok


class TestRailInvariants:
    def test_the_fake_refuses_an_over_refund_like_the_real_api(self, rail):
        """A fake more permissive than production turns the suite into a source
        of false confidence."""
        order = rail.create_order(1_000_00, "kya_x", {})
        payment = rail.pay(order["id"])

        rail.refund(payment["id"], 600_00)
        with pytest.raises(RailError):
            rail.refund(payment["id"], 600_00)

    def test_a_live_key_is_refused_at_construction(self):
        """The one configuration mistake that could move real money."""
        from kya.rails.razorpay_client import LiveRazorpayClient

        with pytest.raises(RailError, match="test mode"):
            LiveRazorpayClient("rzp_live_abcdef", "secret")
