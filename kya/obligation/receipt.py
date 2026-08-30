"""Minting an Obligation Receipt.

The receipt is written at the moment of ALLOW and **before capture**. That
ordering is the whole point: a record produced after the money moved is a
reconstruction, and a reconstruction is exactly what a disputing counterparty
will refuse to accept. Minting first means the statement of what was promised
predates the payment that was taken for it, and the anchor proves the ordering
to someone who does not trust us.

Field names follow RAILS' Obligation Object so the lineage is legible to a
reviewer who knows the paper.

**Acceptance criteria are derived, not authored.** A merchant asked to write
predicates by hand writes none, and an obligation with no acceptance criteria
cannot be cleared or disputed — it is a receipt for nothing. So they are
generated from the cart, which is the only thing that is definitely true at
mint time, and a merchant may extend them.

One constraint on those predicates is load-bearing and easy to get wrong:
``expected`` values must be **JSON primitives**. Receipts round-trip through
storage as JSON, and the hash is computed over the parsed form. A ``datetime``
put in an ``expected`` field would come back as a string and change the hash,
silently breaking the chain. Windows are therefore stored as ISO strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from kya.canonical import now_utc
from kya.crypto import KeyPair
from kya.evidence import EvidenceClass
from kya.schemas import (
    AcceptanceCriterion,
    Cart,
    DeliveryWindow,
    EvidenceRequirement,
    ObligationReceipt,
    Promised,
    RailRef,
)

#: Claim names. Stable identifiers, like reason codes — the verification mesh
#: matches evidence to criteria on these strings, so they are a wire format.
CLAIM_DELIVERED_SKUS = "delivered_skus"
CLAIM_DELIVERED_QTY = "delivered_qty"
CLAIM_AMOUNT_CHARGED = "amount_charged"
CLAIM_DELIVERED_AT = "delivered_at"

#: Fallback lifetime when the merchant commits to no delivery window at all.
DEFAULT_OBLIGATION_TTL = timedelta(days=30)

#: Grace added after the return window closes, so late disputes still land
#: against an open obligation rather than an expired one.
EXPIRY_GRACE = timedelta(days=2)


@dataclass(frozen=True, slots=True)
class MerchantIdentity:
    """Who is making the promise, and the key that seals it."""

    merchant_id: str
    keypair: KeyPair


def iso(moment: datetime) -> str:
    """Second-precision UTC, matching the canonicalizer's datetime form.

    Using the same rendering here as ``kya.canonical`` means a window written
    into a predicate and the same window hashed inside the receipt agree
    character for character.
    """
    return (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def derive_acceptance_criteria(
    promised: Promised,
) -> list[AcceptanceCriterion]:
    """The predicates that would make this obligation satisfied.

    Deliberately modest. Each one is something an external system can actually
    produce evidence about — a courier manifest names SKUs, a payment object
    names an amount. A predicate nothing can testify to is a predicate that
    guarantees an INDETERMINATE verdict, which helps nobody.
    """
    criteria: list[AcceptanceCriterion] = [
        AcceptanceCriterion(
            claim=CLAIM_AMOUNT_CHARGED, op="equals", expected=promised.total
        )
    ]

    for item in promised.line_items:
        criteria.append(
            AcceptanceCriterion(
                claim=CLAIM_DELIVERED_SKUS, op="contains", expected=item.sku
            )
        )
        if item.qty > 1:
            criteria.append(
                AcceptanceCriterion(
                    claim=f"{CLAIM_DELIVERED_QTY}:{item.sku}",
                    op="gte",
                    expected=item.qty,
                )
            )

    if promised.delivery_window is not None:
        criteria.append(
            AcceptanceCriterion(
                claim=CLAIM_DELIVERED_AT,
                op="within_window",
                expected={
                    "from": iso(promised.delivery_window.from_),
                    "to": iso(promised.delivery_window.to),
                },
            )
        )

    return criteria


def derive_evidence_requirements(
    promised: Promised, floor: EvidenceClass
) -> list[EvidenceRequirement]:
    """Minimum admissibility per claim (RAILS ``E_req``).

    The amount charged is always held to ``REC`` regardless of the obligation's
    floor, because a Razorpay payment object exists for every transaction and
    there is no reason to accept a weaker basis for a fact an external system
    already attests to. Delivery claims fall back to the obligation's floor,
    which is where the tier ladder shows up: an established agent's deliveries
    need a weaker basis than a first-contact agent's.
    """
    requirements = [
        EvidenceRequirement(claim=CLAIM_AMOUNT_CHARGED, min_class=EvidenceClass.REC),
        EvidenceRequirement(claim=CLAIM_DELIVERED_SKUS, min_class=floor),
    ]
    if promised.delivery_window is not None:
        requirements.append(
            EvidenceRequirement(claim=CLAIM_DELIVERED_AT, min_class=floor)
        )
    return requirements


class ReceiptMinter:
    """Builds unchained, unsealed receipts. The ledger chains and seals them.

    The split matters: ``prev_hash`` is a property of the ledger's tip, not of
    the promise, and a receipt that computed its own chain position would be
    able to disagree with the ledger about where it sits.
    """

    def __init__(
        self,
        merchant: MerchantIdentity,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.merchant = merchant
        self._clock = clock

    def mint(
        self,
        *,
        obligation_id: str,
        agent_id: str,
        agent_key_id: str,
        principal_ref: str,
        cart: Cart,
        mandate_chain_hash: str,
        rail: RailRef,
        admissibility_floor: EvidenceClass = EvidenceClass.REC,
        delivery_window: DeliveryWindow | None = None,
        return_window_days: int = 7,
        cancellation_terms: str = "",
        amount_due: int | None = None,
        now: datetime | None = None,
    ) -> ObligationReceipt:
        created = now or self._clock()
        promised = Promised(
            line_items=[item.model_copy(deep=True) for item in cart.line_items],
            total=cart.total,
            currency=cart.currency,
            delivery_window=delivery_window,
            return_window_days=return_window_days,
            cancellation_terms=cancellation_terms,
        )

        return ObligationReceipt(
            obligation_id=obligation_id,
            version=1,
            principal_ref=principal_ref,
            agent_id=agent_id,
            agent_key_id=agent_key_id,
            merchant_id=self.merchant.merchant_id,
            promised=promised,
            acceptance_criteria=derive_acceptance_criteria(promised),
            evidence_requirements=derive_evidence_requirements(
                promised, admissibility_floor
            ),
            admissibility_floor=admissibility_floor,
            mandate_chain_hash=mandate_chain_hash,
            rail=rail,
            created_at=created,
            expires_at=_expiry(created, promised),
            amount_due=cart.total if amount_due is None else amount_due,
        )


def _expiry(created: datetime, promised: Promised) -> datetime:
    """When the obligation stops being live.

    Derived from what was promised rather than fixed, so an obligation outlives
    the window in which it could still be disputed. A receipt that expires
    before the return window closes is useless exactly when it is needed.
    """
    if promised.delivery_window is not None:
        return (
            promised.delivery_window.to
            + timedelta(days=promised.return_window_days)
            + EXPIRY_GRACE
        )
    if promised.return_window_days:
        return created + timedelta(days=promised.return_window_days) + EXPIRY_GRACE
    return created + DEFAULT_OBLIGATION_TTL
