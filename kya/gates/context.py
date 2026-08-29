"""Mutable state threaded through the gate pipeline.

Gates communicate by writing resolved artifacts onto the context rather than by
returning them, so that a later gate can use an earlier gate's work (G2 needs
the key G1 resolved) without the pipeline having to know about the dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from kya.canonical import now_utc
from kya.directory import AgentDirectory
from kya.enums import Tier
from kya.nonce import NonceStore
from kya.policy import Policy, TierPolicy
from kya.schemas import AgentRequest, ClearingPassport
from kya.sigv9421 import ParsedSignature


@dataclass(slots=True)
class GateContext:
    request: AgentRequest
    policy: Policy
    passport: ClearingPassport
    directory: AgentDirectory
    nonce_store: NonceStore
    #: principal_ref -> {key_id: base64url public key}. The registry of humans
    #: who may delegate. A real deployment resolves this from the merchant's
    #: customer records; a dict is the honest stand-in.
    principals: dict[str, dict[str, str]] = field(default_factory=dict)
    now: datetime = field(default_factory=now_utc)

    # --- populated as gates run ---------------------------------------------
    parsed_signature: ParsedSignature | None = None
    agent_public_key: Ed25519PublicKey | None = None
    #: True when identity was established from a stale cache entry during a
    #: directory outage. Recorded so degradation is visible, not silent.
    identity_degraded: bool = False
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def tier(self) -> Tier:
        return self.passport.tier

    @property
    def tier_policy(self) -> TierPolicy:
        return self.policy.tier_policy(self.passport.tier)
