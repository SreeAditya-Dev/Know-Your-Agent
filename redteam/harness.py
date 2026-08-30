"""Baselines, and the machinery for running one session against one of them.

**The baselines are the argument.** Each is a real subset of the same gate
pipeline the gateway ships with — not a reimplementation, not a mock. B1 runs
exactly the identity and replay checks a merchant integrating Visa Trusted
Agent Protocol or Cloudflare Web Bot Auth gets today; B2 adds the AP2-shaped
mandate chain and cart binding; B3 is the whole thing. Running the identical
attack corpus through each and reporting what changes is the measurement the
whole project exists to produce.

    B0  no gateway            — nothing runs; every request is allowed
    B1  identity-only         — G0 replay + G1 identity            (TAP / Web Bot Auth)
    B2  + mandate             — G0 G1 + G2 mandate + G3 cart        (AP2 equivalent)
    B3  full KYA              — G0..G5 inline + the clearing layer

A session is one attempted action — a legitimate purchase, or an attack that
may take several requests to express (a refund flood is not visible in any
single request). It runs against a *fresh* sandbox for every baseline, so
cross-request state from one baseline never leaks into another, and the only
thing that differs between two runs of the same session is which gates were
watching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from kya.enums import Decision
from kya.gates.base import BaseGate
from kya.gates.g0_replay import G0Replay
from kya.gates.g1_identity import G1Identity
from kya.gates.g2_mandate import G2Mandate
from kya.gates.g3_cart import G3Cart
from kya.gates.g4_envelope import G4Envelope
from kya.gates.g5_content import G5ContentThreat
from kya.gates.pipeline import Pipeline
from kya.limits import LimitStore
from kya.policy import Policy, default_policy
from kya.reserve_pay import BlockLedger
from kya.simulation import AgentIdentity, Principal, Sandbox


class Baseline(str, Enum):
    """The four defence postures under comparison."""

    B0 = "B0"  # no gateway
    B1 = "B1"  # identity-only  (TAP / Web Bot Auth equivalent)
    B2 = "B2"  # + mandate      (AP2 equivalent)
    B3 = "B3"  # full KYA

    @property
    def label(self) -> str:
        return {
            "B0": "no gateway",
            "B1": "identity-only",
            "B2": "+ mandate",
            "B3": "full KYA",
        }[self.value]


#: Which inline gates each baseline runs. B0 runs none — it is the "no defence"
#: control, and modelling it as an empty pipeline keeps even the null case
#: honest rather than special-cased away.
def _gates(baseline: Baseline, limits: LimitStore, blocks: BlockLedger) -> list[BaseGate]:
    if baseline is Baseline.B0:
        return []
    if baseline is Baseline.B1:
        return [G0Replay(), G1Identity()]
    if baseline is Baseline.B2:
        return [G0Replay(), G1Identity(), G2Mandate(), G3Cart()]
    return [
        G0Replay(),
        G1Identity(),
        G2Mandate(),
        G3Cart(),
        G4Envelope(limits=limits, blocks=blocks),
        G5ContentThreat(),
    ]


def build_pipeline(
    baseline: Baseline, limits: LimitStore, blocks: BlockLedger
) -> Pipeline:
    return Pipeline(_gates(baseline, limits, blocks))


def new_sandbox(
    baseline: Baseline,
    policy: Policy | None = None,
    agent_id: str = "agent_shopper",
    principal_ref: str = "user_alice",
) -> tuple[Sandbox, AgentIdentity, Principal]:
    """A fresh sandbox wired for one baseline, with one registered agent+principal.

    The pipeline is rebuilt after construction so it shares the sandbox's own
    limits and block ledger — a G4 metering against a different store than the
    one the test drives would measure nothing.
    """
    sandbox = Sandbox(policy=policy or default_policy())
    sandbox.pipeline = build_pipeline(baseline, sandbox.limits, sandbox.blocks)
    agent = sandbox.register_agent(AgentIdentity.create(agent_id))
    principal = sandbox.register_principal(Principal.create(principal_ref))
    return sandbox, agent, principal


# --- outcomes ----------------------------------------------------------------


#: Decisions that mean the guarded action was actively halted. STEP_UP is
#: deliberately excluded: it is friction on a sale that still proceeds after
#: re-authentication, not a block, and counting it as a block would flatter
#: both our recall and — worse — our false-positive rate on legitimate traffic.
STOPPING = frozenset({Decision.DENY, Decision.QUARANTINE})


@dataclass(slots=True)
class Outcome:
    """What one session produced against one baseline."""

    decision: Decision
    #: The action was halted — either inline (DENY/QUARANTINE) or, for the
    #: obligation-mismatch class, disputed by the clearing layer after the fact.
    stopped: bool
    amount: int
    reason_codes: list[str] = field(default_factory=list)
    #: Every inline data-plane latency this session observed, in milliseconds.
    #: Used only from B3 legitimate ALLOWs, where it measures the real cost of
    #: the full gate stack on the money path.
    latencies: list[float] = field(default_factory=list)
    #: Clearing finality, set only for the obligation-mismatch class.
    clearing: str | None = None
    note: str = ""


def allow_outcome(amount: int) -> Outcome:
    """The B0 answer to everything, and the shape of an undefended success."""
    return Outcome(decision=Decision.ALLOW, stopped=False, amount=amount)


# --- sessions ----------------------------------------------------------------


@dataclass(slots=True)
class Session:
    """One labelled attempt, runnable against any baseline.

    ``spec`` is the frozen, serialisable description the corpus is built and
    hashed from; ``run`` is the closure that realises it. Two fields, because
    the corpus's credibility rests on the spec being fixed and inspectable
    before any detector was tuned, while the run is just how the spec is
    executed.
    """

    session_id: str
    label: str  # "LEGIT" | "ATTACK"
    attack_class: str | None
    run: Callable[[Baseline], Outcome]
    spec: dict

    @property
    def is_attack(self) -> bool:
        return self.label == "ATTACK"
