"""Shared enumerations. Frozen on Day 0 — everything downstream imports from here.

A mid-build change to any value in this module invalidates stored decisions,
ledger entries and eval runs, so treat these as a wire format.
"""

from __future__ import annotations

from enum import Enum, IntEnum


class Gate(str, Enum):
    """The seven inline gates, in pipeline order."""

    G0 = "G0"  # transport & replay
    G1 = "G1"  # agent identity
    G2 = "G2"  # mandate chain
    G3 = "G3"  # cart binding
    G4 = "G4"  # bounded action envelope
    G5 = "G5"  # content threat
    G6 = "G6"  # adjudication


class Severity(IntEnum):
    """How much weight a reason carries in adjudication.

    Ordered so that ``max()`` over a set of reasons yields the governing one.
    """

    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class Decision(str, Enum):
    """Terminal outcome of the inline pipeline."""

    ALLOW = "ALLOW"
    STEP_UP = "STEP_UP"  # principal re-authentication required
    QUARANTINE = "QUARANTINE"  # held for human review
    DENY = "DENY"

    @property
    def rank(self) -> int:
        """Restrictiveness ordering. Adjudication takes the max."""
        return {"ALLOW": 0, "STEP_UP": 1, "QUARANTINE": 2, "DENY": 3}[self.value]


class GateVerdict(str, Enum):
    """Per-gate outcome.

    DEGRADED means the gate could not reach a dependency and applied its
    documented degradation policy; it is distinct from FAIL, which means the
    gate positively observed something wrong.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    DEGRADED = "DEGRADED"
    SKIPPED = "SKIPPED"  # short-circuited by an earlier terminal failure
    UNKNOWN = "UNKNOWN"  # never resolves toward ALLOW


class Tier(str, Enum):
    """Clearing Passport trust tier. Drives G4 ceilings and evidence floors."""

    T0 = "T0"  # unknown — first contact
    T1 = "T1"  # seen
    T2 = "T2"  # established
    T3 = "T3"  # trusted

    @property
    def level(self) -> int:
        return int(self.value[1])


class Finality(str, Enum):
    """Clearing decision lifecycle (RAILS §finality)."""

    PROVISIONAL = "PROVISIONAL"
    FINAL = "FINAL"
    DISPUTED = "DISPUTED"


class RailType(str, Enum):
    """Which payment rail an obligation is bound to."""

    RAZORPAY_ORDER = "razorpay_order"
    RESERVE_PAY_BLOCK = "reserve_pay_block"  # SIMULATED — see docs/07


class ObligationState(str, Enum):
    OPEN = "OPEN"  # minted, not yet fully settled
    SETTLED = "SETTLED"  # cleared FINAL
    REVERSED = "REVERSED"  # cleared DISPUTED and reversed
    EXPIRED = "EXPIRED"


class VerifierRole(str, Enum):
    """Verification mesh participants (RAILS §verification mesh)."""

    CONSTRAINT = "constraint"
    RECEIPT = "receipt"
    SEMANTIC = "semantic"  # LLM — capped at SELF/SIGN by construction
    POLICY = "policy"
