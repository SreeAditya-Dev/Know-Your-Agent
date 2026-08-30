"""Wire and storage models. Frozen on Day 0.

Money is **integer paise** everywhere. No floats reach a hash or a ledger.

Signed objects expose ``signing_payload()``, which returns the canonicalizable
content the signature covers — always the object minus its own signature
fields. Signing and verification both go through that one method so the two
sides cannot drift.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kya.canonical import digest, now_utc
from kya.enums import (
    Decision,
    Finality,
    Gate,
    GateVerdict,
    ObligationState,
    RailType,
    Tier,
    VerifierRole,
)
from kya.evidence import EvidenceClass


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


# --- cart --------------------------------------------------------------------


class LineItem(Base):
    sku: str
    name: str
    qty: int = Field(gt=0)
    unit_price: int = Field(ge=0, description="paise")

    @property
    def line_total(self) -> int:
        return self.qty * self.unit_price


class Cart(Base):
    merchant_id: str
    line_items: list[LineItem] = Field(min_length=1)
    currency: Literal["INR"] = "INR"
    subtotal: int = Field(ge=0, description="paise")
    shipping: int = Field(default=0, ge=0)
    tax: int = Field(default=0, ge=0)
    total: int = Field(ge=0)
    category: str | None = None

    @model_validator(mode="after")
    def _check_arithmetic(self) -> Cart:
        """A cart that does not add up is rejected before it can be hashed.

        Catching this here means G3 never has to distinguish 'tampered' from
        'internally inconsistent' — the latter cannot exist.
        """
        expected_subtotal = sum(li.line_total for li in self.line_items)
        if self.subtotal != expected_subtotal:
            raise ValueError(
                f"subtotal {self.subtotal} != sum of line items {expected_subtotal}"
            )
        expected_total = self.subtotal + self.shipping + self.tax
        if self.total != expected_total:
            raise ValueError(
                f"total {self.total} != subtotal+shipping+tax {expected_total}"
            )
        return self

    def content_hash(self) -> str:
        """The hash a cart mandate commits to."""
        return digest(self)


# --- mandates (AP2-shaped) ---------------------------------------------------


class IntentConstraints(Base):
    """The bounds the human set when delegating."""

    max_amount: int = Field(gt=0, description="paise")
    allowed_merchants: list[str] = Field(default_factory=list)
    allowed_categories: list[str] | None = None
    max_transactions: int | None = Field(default=None, gt=0)


class IntentMandate(Base):
    """User → agent delegation. AP2's Intent Mandate, plain-JSON serialized."""

    intent_id: str
    principal_ref: str
    agent_id: str
    constraints: IntentConstraints
    issued_at: datetime
    expires_at: datetime
    signer_key_id: str
    signature: str = ""

    def signing_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="python")
        data.pop("signature", None)
        return data

    def reference(self) -> str:
        """Hash used by a cart mandate to point back at this intent."""
        return digest(self.signing_payload())

    def is_expired(self, at: datetime | None = None) -> bool:
        return (at or now_utc()) > self.expires_at


class CartMandate(Base):
    """Agent's assembled cart, bound to an intent. AP2's Cart Mandate.

    The cart contents are embedded, not merely hashed. That is what lets G3
    report *which field* drifted between signature and charge rather than only
    that the hashes disagreed — a dispute reviewer needs "the total moved by
    ₹100", not "hash mismatch".
    """

    cart_id: str
    intent_ref: str = Field(description="digest of the intent's signing payload")
    cart: Cart
    cart_hash: str = Field(description="digest of the canonical cart")
    merchant_id: str
    total: int = Field(ge=0, description="paise")
    currency: Literal["INR"] = "INR"
    issued_at: datetime
    expires_at: datetime
    signer_key_id: str
    signature: str = ""

    @model_validator(mode="after")
    def _check_self_consistency(self) -> CartMandate:
        """The mandate must agree with the cart it carries.

        Enforced at construction so no downstream gate has to consider a
        mandate that disagrees with itself.
        """
        actual = self.cart.content_hash()
        if self.cart_hash != actual:
            raise ValueError(f"cart_hash {self.cart_hash!r} != cart digest {actual!r}")
        if self.total != self.cart.total:
            raise ValueError(f"mandate total {self.total} != cart total {self.cart.total}")
        if self.merchant_id != self.cart.merchant_id:
            raise ValueError("mandate merchant_id != cart merchant_id")
        return self

    def signing_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="python")
        data.pop("signature", None)
        return data

    def is_expired(self, at: datetime | None = None) -> bool:
        return (at or now_utc()) > self.expires_at


class MandateBundle(Base):
    intent: IntentMandate
    cart: CartMandate

    def chain_hash(self) -> str:
        """Single digest binding the whole delegation chain, stored on the receipt."""
        return digest(
            {
                "intent": self.intent.signing_payload(),
                "cart": self.cart.signing_payload(),
            }
        )


# --- inbound request ---------------------------------------------------------


class SignatureParams(Base):
    """Parsed RFC 9421 ``Signature-Input`` parameters."""

    key_id: str
    algorithm: str = "ed25519"
    created: int
    expires: int | None = None
    nonce: str | None = None
    tag: str | None = None
    covered_components: list[str] = Field(default_factory=list)


class AgentRequest(Base):
    """Everything the inline pipeline evaluates about one inbound call."""

    method: str
    path: str
    authority: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)

    agent_id: str
    idempotency_key: str

    signature: str | None = None
    signature_input_raw: str | None = None
    signature_agent: str | None = None

    mandates: MandateBundle | None = None
    cart: Cart | None = None

    #: Free-text fields the agent supplied, inspected by G5.
    free_text: dict[str, str] = Field(default_factory=dict)
    callback_url: str | None = None

    received_at: datetime = Field(default_factory=now_utc)


# --- obligation --------------------------------------------------------------


class DeliveryWindow(Base):
    from_: datetime = Field(alias="from")
    to: datetime

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class Promised(Base):
    """What the merchant actually committed to. The thing no payment object records."""

    line_items: list[LineItem]
    total: int = Field(ge=0)
    currency: Literal["INR"] = "INR"
    delivery_window: DeliveryWindow | None = None
    return_window_days: int = Field(default=0, ge=0)
    cancellation_terms: str = ""


class AcceptanceCriterion(Base):
    """One predicate that fulfilment must satisfy (RAILS ``Ac``)."""

    claim: str
    op: Literal["equals", "contains", "lte", "gte", "within_window"]
    expected: Any


class EvidenceRequirement(Base):
    """Minimum admissibility for a given claim (RAILS ``E_req``)."""

    claim: str
    min_class: EvidenceClass


class RailRef(Base):
    type: RailType
    ref: str
    #: True whenever the rail is not a live integration. Surfaced in API
    #: responses and the dashboard so a simulated rail can never be mistaken
    #: for a real one.
    simulated: bool = False


class ObligationReceipt(Base):
    """Signed record of what was promised, minted before capture."""

    obligation_id: str
    version: int = 1

    principal_ref: str
    agent_id: str
    agent_key_id: str
    merchant_id: str

    promised: Promised
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    evidence_requirements: list[EvidenceRequirement] = Field(default_factory=list)
    admissibility_floor: EvidenceClass = EvidenceClass.REC

    mandate_chain_hash: str
    rail: RailRef

    created_at: datetime
    expires_at: datetime
    state: ObligationState = ObligationState.OPEN
    amount_due: int = Field(ge=0, description="paise still owed against this obligation")

    prev_hash: str = ""
    self_hash: str = ""
    merchant_signature: str = ""

    def signing_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="python")
        for volatile in ("self_hash", "merchant_signature"):
            data.pop(volatile, None)
        return data

    def compute_hash(self) -> str:
        return digest(self.signing_payload())

    def anchor_note(self) -> dict[str, str]:
        """The payload written into the Razorpay order's ``notes``.

        This is what makes the audit trail verifiable by someone who does not
        trust our database.
        """
        return {"kya_obligation": self.self_hash, "kya_version": str(self.version)}


# --- evidence & clearing -----------------------------------------------------


class EvidenceItem(Base):
    item_id: str
    claim: str
    value: Any
    declared_class: EvidenceClass
    #: Provenance chain, weakest link wins (``meet``).
    provenance: list[EvidenceClass] = Field(default_factory=list)
    source: str
    observed_at: datetime


class EvidenceEnvelope(Base):
    obligation_hash: str
    submitted_at: datetime
    items: list[EvidenceItem] = Field(default_factory=list)
    signature: str = ""

    def signing_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="python")
        data.pop("signature", None)
        return data


class VerifierOutput(Base):
    """One mesh verifier's independent finding."""

    role: VerifierRole
    verdict: Literal["SATISFIED", "VIOLATED", "INDETERMINATE"]
    confidence: float = Field(ge=0.0, le=1.0)
    #: The class of evidence this verifier actually relied on. The aggregator
    #: trusts this declaration; RAILS calls the alternative LAUNDER-BASIS.
    declared_basis: EvidenceClass
    #: Ids of the evidence items the verifier actually read. Declaring what you
    #: relied on is what makes the basis claim *checkable*: the mesh recomputes
    #: the meet of these items and flags a verifier claiming better evidence
    #: than it cited. Without it, ``declared_basis`` is an unfalsifiable
    #: assertion and LAUNDER-BASIS has no detector.
    cited_items: list[str] = Field(default_factory=list)
    loss_estimate: int = Field(default=0, ge=0, description="paise")
    detail: str = ""


class ClearingDecision(Base):
    obligation_hash: str
    performance_verdict: Literal["SATISFIED", "VIOLATED", "INDETERMINATE"]
    policy_verdict: Literal["SATISFIED", "VIOLATED", "INDETERMINATE"]
    fault: str | None = None
    aggregate_basis: EvidenceClass
    confidence: float = Field(ge=0.0, le=1.0)
    loss_estimate: int = Field(default=0, ge=0)
    verifier_outputs: list[VerifierOutput] = Field(default_factory=list)
    #: Verifiers excluded for failing the admissibility floor, kept for audit.
    excluded_verifiers: list[VerifierRole] = Field(default_factory=list)
    finality: Finality = Finality.PROVISIONAL
    emitted_at: datetime
    finalized_at: datetime | None = None
    decision_hash: str = ""


class SettlementInstruction(Base):
    clearing_hash: str
    principal_amount: int = Field(default=0)
    refund_amount: int = Field(default=0)
    penalty_amount: int = Field(default=0)
    reputation_delta: int = Field(default=0)
    rail: RailRef
    executed: bool = False
    executed_ref: str | None = None


class ClearingPassport(Base):
    """Per-agent cross-transaction reliability record. Feeds G4."""

    agent_id: str
    tier: Tier = Tier.T0
    cleared_count: int = 0
    disputed_count: int = 0
    basis_drift_events: int = 0
    total_cleared_value: int = 0
    first_seen: datetime = Field(default_factory=now_utc)
    last_seen: datetime = Field(default_factory=now_utc)

    @property
    def dispute_rate(self) -> float:
        total = self.cleared_count + self.disputed_count
        return self.disputed_count / total if total else 0.0


# --- reserve pay (SIMULATED) -------------------------------------------------


class ReservePayBlock(Base):
    """A Single Block Multi Debit reservation.

    SIMULATED — models NPCI SBMD semantics against a local ledger. See docs/07.
    """

    block_id: str
    principal_ref: str
    merchant_id: str
    reserved: int = Field(gt=0, description="paise")
    debited: int = Field(default=0, ge=0)
    created_at: datetime
    expires_at: datetime
    revoked: bool = False

    @property
    def available(self) -> int:
        return max(0, self.reserved - self.debited)


class BlockDebit(Base):
    debit_id: str
    block_ref: str
    obligation_id: str | None = None
    amount: int = Field(gt=0)
    requested_at: datetime


# --- decision output ---------------------------------------------------------


class GateResult(Base):
    gate: Gate
    verdict: GateVerdict
    codes: list[str] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: float = 0.0

    @field_validator("codes")
    @classmethod
    def _known_codes(cls, codes: list[str]) -> list[str]:
        from kya.reasons import REGISTRY

        for code in codes:
            if code not in REGISTRY:
                raise ValueError(f"unknown reason code {code!r}")
        return codes


class DecisionEnvelope(Base):
    """Returned alongside every guarded action — the audit trail's public face."""

    decision_id: str
    decision: Decision
    agent_id: str
    tier: Tier
    reason_codes: list[str] = Field(default_factory=list)
    gate_trace: list[GateResult] = Field(default_factory=list)
    explanation: str = ""
    obligation_id: str | None = None
    idempotent_replay: bool = False
    latency_ms: float = 0.0
    policy_version: str = "v1"
    decided_at: datetime = Field(default_factory=now_utc)

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW
