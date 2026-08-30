"""A well-behaved agent client, and the sandbox it calls into.

This produces *correct* traffic: valid RFC 9421 signatures, a coherent mandate
chain, a cart whose digest matches what was signed. The red-team suite builds
its attacks by perturbing the output of this module rather than by hand-rolling
malformed requests.

That distinction matters for the evaluation's credibility. An attack derived by
mutating one field of a request that would otherwise pass is a real attack. A
hand-written request that fails for three unrelated reasons at once proves
nothing about the gate that happened to fire first.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta  # noqa: F401  (timedelta used in signatures)

from kya.canonical import canonicalize, now_utc
from kya.crypto import KeyPair, keypair_from_seed, sign_payload
from kya.directory import AgentDirectory, StaticKeyFetcher
from kya.enums import ObligationState, RailType, Tier
from kya.gates.context import GateContext
from kya.gates.pipeline import Pipeline, default_pipeline
from kya.limits import LimitStore
from kya.gateway import Gateway
from kya.nonce import InMemoryNonceStore
from kya.obligation.ledger import ObligationLedger
from kya.obligation.receipt import MerchantIdentity, ReceiptMinter
from kya.passport import InMemoryPassportStore, PassportStore
from kya.policy import Policy, default_policy
from kya.rails.razorpay_client import FakeRazorpayClient
from kya.reserve_pay import BlockLedger
from kya.schemas import (
    AgentRequest,
    Cart,
    CartMandate,
    ClearingPassport,
    IntentConstraints,
    IntentMandate,
    LineItem,
    MandateBundle,
    ObligationReceipt,
    Promised,
    RailRef,
)

DEFAULT_AUTHORITY = "sandbox.kya.local"
#: Tier a sandbox agent starts at. High on purpose, so that a test aimed at
#: one gate is not silently failing on another gate's ceiling.
DEFAULT_SANDBOX_TIER = Tier.T3

DEFAULT_PATH = "/v1/agent/orders"
REFUNDS_PATH = "/v1/agent/refunds"
BLOCK_DEBIT_PATH = "/v1/agent/blocks/{block_id}/debit"
SIGNATURE_LABEL = "sig1"
COVERED_COMPONENTS = ("@method", "@authority", "@path", "content-digest")


def _seed(tag: str) -> bytes:
    """Deterministic 32-byte seed so fixtures reproduce across runs."""
    return hashlib.sha256(tag.encode("utf-8")).digest()


@dataclass(slots=True)
class AgentIdentity:
    agent_id: str
    origin: str
    keypair: KeyPair

    @classmethod
    def create(
        cls,
        agent_id: str,
        origin: str | None = None,
        key_seed_tag: str | None = None,
    ) -> AgentIdentity:
        """Build an identity. Keys derive from ``key_seed_tag or agent_id``.

        Passing a distinct ``key_seed_tag`` produces an identity that *claims*
        an agent_id and origin while holding a different keypair — which is
        precisely the impersonation case, and is not otherwise expressible
        because key derivation is deterministic in agent_id.
        """
        tag = key_seed_tag or agent_id
        return cls(
            agent_id=agent_id,
            origin=origin or f"https://{agent_id.replace('_', '-')}.example",
            keypair=keypair_from_seed(f"{tag}-key-1", _seed(tag)),
        )


@dataclass(slots=True)
class Principal:
    principal_ref: str
    keypair: KeyPair

    @classmethod
    def create(cls, principal_ref: str) -> Principal:
        return cls(
            principal_ref=principal_ref,
            keypair=keypair_from_seed(f"{principal_ref}-key-1", _seed(principal_ref)),
        )


@dataclass
class Sandbox:
    """Everything the pipeline needs, wired for tests and the eval harness."""

    policy: Policy = field(default_factory=default_policy)
    fetcher: StaticKeyFetcher = field(default_factory=StaticKeyFetcher)
    directory: AgentDirectory | None = None
    nonce_store: InMemoryNonceStore | None = None
    #: Cross-request counters and the SIMULATED block ledger. Built here rather
    #: than defaulted inside the gate so they share the sandbox clock — a gate
    #: metering on wall time while the test advances a fake clock measures
    #: nothing.
    limits: LimitStore | None = None
    #: The real hash-chained ledger, not a stand-in. G4's block guard reads it
    #: through the ``ObligationSource`` protocol, so the wiring the tests
    #: exercise is the wiring the gateway ships with.
    ledger: ObligationLedger | None = None
    #: Lets a deployment supply the configured merchant signer while the test
    #: harness retains its deterministic sandbox identity.
    merchant_identity: MerchantIdentity | None = None
    blocks: BlockLedger | None = None
    passport_store: PassportStore | None = None
    rail: FakeRazorpayClient | None = None
    pipeline: Pipeline | None = None
    principals: dict[str, dict[str, str]] = field(default_factory=dict)

    #: Overrides wall-clock time for every time-dependent component at once —
    #: directory TTLs, nonce expiry, mandate validity. Rolling one clock keeps
    #: them consistent; separate clocks drift and produce false failures.
    _now: datetime | None = field(default=None, repr=False)
    _seeded: set[str] = field(default_factory=set, repr=False)
    _merchant: MerchantIdentity | None = field(default=None, repr=False)
    _gateway: Gateway | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.nonce_store is None:
            self.nonce_store = InMemoryNonceStore(clock=self.clock)
        if self.directory is None:
            self.directory = AgentDirectory(self.fetcher, clock=self.clock)
        if self.limits is None:
            self.limits = LimitStore(clock=self.clock)
        if self.ledger is None:
            self.ledger = ObligationLedger(self.merchant, clock=self.clock)
        if self.blocks is None:
            self.blocks = BlockLedger(obligations=self.ledger, clock=self.clock)
        if self.passport_store is None:
            self.passport_store = InMemoryPassportStore(clock=self.clock)
        if self.rail is None:
            self.rail = FakeRazorpayClient()
        if self.pipeline is None:
            self.pipeline = default_pipeline(limits=self.limits, blocks=self.blocks)

    # --- time control --------------------------------------------------------

    def clock(self) -> datetime:
        return self._now if self._now is not None else now_utc()

    def set_time(self, when: datetime) -> None:
        self._now = when

    def advance(self, delta: timedelta) -> datetime:
        """Move the sandbox clock forward. Returns the new time."""
        self._now = self.clock() + delta
        return self._now

    @property
    def merchant(self) -> MerchantIdentity:
        """Deterministic sandbox merchant, so receipt hashes reproduce."""
        if self._merchant is None:
            self._merchant = self.merchant_identity or MerchantIdentity(
                merchant_id=self.policy.merchant_id,
                keypair=keypair_from_seed(
                    f"{self.policy.merchant_id}-obligation-key-1",
                    _seed(f"merchant-{self.policy.merchant_id}"),
                ),
            )
        return self._merchant

    def gateway(self, **kwargs) -> Gateway:
        """A gateway wired to this sandbox's pipeline, ledger and rail."""
        assert self.ledger is not None and self.rail is not None
        if self._gateway is None:
            self._gateway = Gateway(
                pipeline=self.pipeline,
                ledger=self.ledger,
                rail=self.rail,
                minter=ReceiptMinter(self.merchant, clock=self.clock),
                context_factory=self.context,
                clock=self.clock,
                **kwargs,
            )
        return self._gateway

    def register_agent(self, agent: AgentIdentity) -> AgentIdentity:
        self.fetcher.publish(
            agent.origin, agent.keypair.key_id, agent.keypair.public_b64u
        )
        return agent

    def register_principal(self, principal: Principal) -> Principal:
        self.principals.setdefault(principal.principal_ref, {})[
            principal.keypair.key_id
        ] = principal.keypair.public_b64u
        return principal

    def passport_for(
        self, agent_id: str, tier: Tier | None = None
    ) -> ClearingPassport:
        """Passports default to T3 in tests so tier ceilings do not mask the
        behaviour under test. Tier-specific cases set it explicitly.

        The tier is pinned rather than derived here: these are fixtures for
        gates that *consume* a tier, and making them earn one through simulated
        clearings would couple every G4 test to the ladder's thresholds.

        An explicit ``tier`` always wins, so a test can move the same agent up
        and down the ladder between calls. Passing nothing keeps whatever the
        agent already has — which is what lets ``set_tier`` stick for callers
        like the gateway, which builds a context without opinions about tier.
        """
        assert self.passport_store is not None
        passport = self.passport_store.get(agent_id)

        if agent_id not in self._seeded:
            self._seeded.add(agent_id)
            passport.tier = DEFAULT_SANDBOX_TIER if tier is None else tier
            self.passport_store.put(passport)
        elif tier is not None and passport.tier is not tier:
            passport.tier = tier
            self.passport_store.put(passport)

        return passport

    def set_tier(self, agent_id: str, tier: Tier) -> ClearingPassport:
        """Move an agent to a tier directly, to exercise ceilings."""
        return self.passport_for(agent_id, tier)

    def context(
        self,
        request: AgentRequest,
        tier: Tier | None = None,
        now: datetime | None = None,
    ) -> GateContext:
        assert self.directory is not None and self.nonce_store is not None
        return GateContext(
            request=request,
            policy=self.policy,
            passport=self.passport_for(request.agent_id, tier),
            directory=self.directory,
            nonce_store=self.nonce_store,
            principals=self.principals,
            now=now or self.clock(),
        )

    def evaluate(
        self,
        request: AgentRequest,
        tier: Tier | None = None,
        now: datetime | None = None,
    ):
        assert self.pipeline is not None
        return self.pipeline.evaluate(self.context(request, tier=tier, now=now))


# --- cart construction -------------------------------------------------------


def make_cart(
    merchant_id: str = "merch_sandbox_01",
    items: list[tuple[str, str, int, int]] | None = None,
    shipping: int = 0,
    tax: int = 0,
    category: str | None = "electronics",
) -> Cart:
    """Build an internally consistent cart. ``items`` are (sku, name, qty, unit_price)."""
    items = items or [("SKU-PHONE-256", "Phone 256GB", 1, 5_499_00)]
    line_items = [
        LineItem(sku=sku, name=name, qty=qty, unit_price=price)
        for sku, name, qty, price in items
    ]
    subtotal = sum(li.line_total for li in line_items)
    return Cart(
        merchant_id=merchant_id,
        line_items=line_items,
        subtotal=subtotal,
        shipping=shipping,
        tax=tax,
        total=subtotal + shipping + tax,
        category=category,
    )


# --- mandate construction ----------------------------------------------------


def make_mandates(
    agent: AgentIdentity,
    principal: Principal,
    cart: Cart,
    *,
    max_amount: int | None = None,
    allowed_merchants: list[str] | None = None,
    allowed_categories: list[str] | None = None,
    max_transactions: int | None = None,
    issued_at: datetime | None = None,
    intent_ttl: timedelta = timedelta(hours=1),
    cart_ttl: timedelta = timedelta(minutes=15),
) -> MandateBundle:
    """A correctly signed intent + cart mandate pair for ``cart``."""
    issued = issued_at or now_utc()

    intent = IntentMandate(
        intent_id=f"int_{uuid.uuid4().hex[:12]}",
        principal_ref=principal.principal_ref,
        agent_id=agent.agent_id,
        constraints=IntentConstraints(
            max_amount=max_amount if max_amount is not None else cart.total * 2,
            allowed_merchants=(
                allowed_merchants
                if allowed_merchants is not None
                else [cart.merchant_id]
            ),
            allowed_categories=allowed_categories,
            max_transactions=max_transactions,
        ),
        issued_at=issued,
        expires_at=issued + intent_ttl,
        signer_key_id=principal.keypair.key_id,
    )
    intent.signature = sign_payload(principal.keypair.private, intent.signing_payload())

    cart_mandate = CartMandate(
        cart_id=f"cart_{uuid.uuid4().hex[:12]}",
        intent_ref=intent.reference(),
        cart=cart,
        cart_hash=cart.content_hash(),
        merchant_id=cart.merchant_id,
        total=cart.total,
        issued_at=issued,
        expires_at=issued + cart_ttl,
        signer_key_id=agent.keypair.key_id,
    )
    cart_mandate.signature = sign_payload(
        agent.keypair.private, cart_mandate.signing_payload()
    )

    return MandateBundle(intent=intent, cart=cart_mandate)


# --- request signing ---------------------------------------------------------


def content_digest(body: dict) -> str:
    """RFC 9530 Content-Digest over the canonical body.

    Covering the body in the signature means a tampered cart breaks the
    signature at G1, before G3 ever has to reason about it. Two independent
    controls catch the same attack, which is the point.
    """
    raw = hashlib.sha256(canonicalize(body)).digest()
    return f"sha-256=:{base64.b64encode(raw).decode('ascii')}:"


def request_body(
    cart: Cart | None,
    mandates: MandateBundle | None,
    extra: dict | None = None,
) -> dict:
    """The signed body. One builder, so signing and re-signing cannot diverge.

    ``extra`` carries the action-specific payload — a refund amount, a block
    debit — which is covered by the signature exactly like the cart is. An
    amount that is not signed is an amount an intermediary can change.
    """
    body: dict = {
        "cart": cart.model_dump(mode="json") if cart is not None else None,
        "mandates": mandates.model_dump(mode="json") if mandates is not None else None,
    }
    if extra:
        body.update(extra)
    return body


def build_signed_request(
    agent: AgentIdentity,
    mandates: MandateBundle,
    cart: Cart,
    *,
    method: str = "POST",
    path: str = DEFAULT_PATH,
    authority: str = DEFAULT_AUTHORITY,
    created: datetime | None = None,
    expires_in: timedelta = timedelta(minutes=5),
    nonce: str | None = None,
    idempotency_key: str | None = None,
    free_text: dict[str, str] | None = None,
    callback_url: str | None = None,
    extra_body: dict | None = None,
) -> AgentRequest:
    """Produce a fully valid signed request. Attacks perturb what this returns."""
    created_at = created or now_utc()
    created_ts = int(created_at.timestamp())
    expires_ts = int((created_at + expires_in).timestamp())
    nonce = nonce or uuid.uuid4().hex

    body = request_body(cart, mandates, extra_body)
    digest_header = content_digest(body)

    components = " ".join(f'"{c}"' for c in COVERED_COMPONENTS)
    raw_params = (
        f"({components})"
        f";created={created_ts}"
        f";keyid=\"{agent.keypair.key_id}\""
        f";alg=\"ed25519\""
        f";nonce=\"{nonce}\""
        f";expires={expires_ts}"
        f";tag=\"web-bot-auth\""
    )
    signature_input = f"{SIGNATURE_LABEL}={raw_params}"

    request = AgentRequest(
        method=method,
        path=path,
        authority=authority,
        headers={"content-digest": digest_header},
        body=body,
        agent_id=agent.agent_id,
        idempotency_key=idempotency_key or uuid.uuid4().hex,
        signature_input_raw=signature_input,
        signature_agent=f'"{agent.origin}"',
        mandates=mandates,
        cart=cart,
        free_text=free_text or {},
        callback_url=callback_url,
        received_at=created_at,
    )

    signature = _sign_request(agent, request, raw_params)
    request.signature = f"{SIGNATURE_LABEL}=:{signature}:"
    return request


def _sign_request(agent: AgentIdentity, request: AgentRequest, raw_params: str) -> str:
    """Sign the RFC 9421 base, returning standard base64 for the wire form."""
    from kya.sigv9421 import ParsedSignature, build_signature_base, parse_signature_input

    parsed: ParsedSignature = parse_signature_input(
        f"{SIGNATURE_LABEL}={raw_params}"
    )[SIGNATURE_LABEL]
    base = build_signature_base(request, parsed)
    raw = agent.keypair.private.sign(base)
    return base64.b64encode(raw).decode("ascii")


def resign_request(agent: AgentIdentity, request: AgentRequest) -> AgentRequest:
    """Re-sign after mutation.

    Used by attacks that legitimately control the signing key — an agent
    tampering with its *own* cart is signing honestly and must still be caught,
    by G3 rather than G1.
    """
    assert request.signature_input_raw is not None
    raw_params = request.signature_input_raw.split("=", 1)[1]

    extra = {k: v for k, v in request.body.items() if k not in ("cart", "mandates")}
    body = request_body(request.cart, request.mandates, extra)
    request.body = body
    request.headers["content-digest"] = content_digest(body)

    request.signature = f"{SIGNATURE_LABEL}=:{_sign_request(agent, request, raw_params)}:"
    return request


# --- action-specific requests ------------------------------------------------
#
# A refund or a block debit still carries the full mandate chain and the cart it
# refers to. That is not ceremony: it is how the agent proves *which* order it
# is acting against, and it keeps G0-G3 identical across every action so the
# only thing that changes between a purchase and a refund is the accounting.


def build_refund_request(
    agent: AgentIdentity,
    mandates: MandateBundle,
    cart: Cart,
    amount: int,
    *,
    payment_id: str = "pay_sandbox_0001",
    path: str = REFUNDS_PATH,
    **kwargs,
) -> AgentRequest:
    """A guarded refund of ``amount`` paise against an earlier order."""
    return build_signed_request(
        agent,
        mandates,
        cart,
        path=path,
        extra_body={"refund": {"amount": amount, "payment_id": payment_id}},
        **kwargs,
    )


def build_block_debit_request(
    agent: AgentIdentity,
    mandates: MandateBundle,
    cart: Cart,
    block_id: str,
    amount: int,
    **kwargs,
) -> AgentRequest:
    """A debit against a SIMULATED Reserve Pay block."""
    return build_signed_request(
        agent,
        mandates,
        cart,
        path=BLOCK_DEBIT_PATH.format(block_id=block_id),
        extra_body={"debit": {"amount": amount, "block_id": block_id}},
        **kwargs,
    )


# --- obligations -------------------------------------------------------------


def make_obligation(
    agent: AgentIdentity,
    principal: Principal,
    cart: Cart,
    *,
    rail_type: RailType = RailType.RESERVE_PAY_BLOCK,
    rail_ref: str = "",
    amount_due: int | None = None,
    mandate_chain_hash: str = "",
    created_at: datetime | None = None,
    ttl: timedelta = timedelta(days=7),
    state: ObligationState = ObligationState.OPEN,
) -> ObligationReceipt:
    """A minimal obligation receipt, enough to back a debit.

    Day 3 mints these from the pipeline and chains them. This builder exists so
    the block guard can be exercised before that lands — the guard's question is
    "does an open obligation cover this debit?", and it does not care who minted
    the answer.
    """
    created = created_at or now_utc()
    receipt = ObligationReceipt(
        obligation_id=f"obl_{uuid.uuid4().hex[:12]}",
        principal_ref=principal.principal_ref,
        agent_id=agent.agent_id,
        agent_key_id=agent.keypair.key_id,
        merchant_id=cart.merchant_id,
        promised=Promised(line_items=list(cart.line_items), total=cart.total),
        mandate_chain_hash=mandate_chain_hash,
        rail=RailRef(
            type=rail_type,
            ref=rail_ref,
            simulated=rail_type is RailType.RESERVE_PAY_BLOCK,
        ),
        created_at=created,
        expires_at=created + ttl,
        state=state,
        amount_due=cart.total if amount_due is None else amount_due,
    )
    receipt.self_hash = receipt.compute_hash()
    return receipt


def standard_sandbox(
    *,
    ledger: ObligationLedger | None = None,
    merchant: MerchantIdentity | None = None,
) -> tuple[Sandbox, AgentIdentity, Principal]:
    """The common fixture: one registered agent, one registered principal."""
    sandbox = Sandbox(ledger=ledger, merchant_identity=merchant)
    agent = sandbox.register_agent(AgentIdentity.create("agent_shopper"))
    principal = sandbox.register_principal(Principal.create("user_alice"))
    return sandbox, agent, principal
