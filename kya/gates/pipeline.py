"""The inline pipeline.

Runs gates in order, short-circuits on a terminal denial, and produces the
decision envelope that accompanies every guarded action.

**Idempotency.** Decisions are cached on ``(agent_id, mandate_chain_hash,
idempotency_key)``. Re-presenting an identical request returns the cached
decision without re-running a single gate.

That matters more than it first appears. Agents retry aggressively and without
human judgement. Without decision caching, a retry after a network timeout
re-runs the gates against different counter state and can produce a *different*
answer — decision flapping — or mint a second obligation and drive a duplicate
charge. Idempotency at the decision layer, not merely at the payment layer, is
what makes this gateway safe to put in front of a machine caller.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from kya.enums import Decision, Gate, GateVerdict
from kya.limits import LimitStore
from kya.gates.base import BaseGate
from kya.gates.context import GateContext
from kya.gates.g0_replay import G0Replay
from kya.gates.g1_identity import G1Identity
from kya.gates.g2_mandate import G2Mandate
from kya.gates.g3_cart import G3Cart
from kya.gates.g4_envelope import G4Envelope
from kya.gates.g5_content import G5ContentThreat
from kya.gates.g6_adjudicate import adjudicate, explain
from kya.reasons import get as get_reason
from kya.reserve_pay import BlockLedger
from kya.schemas import DecisionEnvelope, GateResult


@dataclass(slots=True)
class _CachedDecision:
    envelope: DecisionEnvelope


class Pipeline:
    """Ordered gate runner with decision caching."""

    def __init__(self, gates: list[BaseGate]) -> None:
        self.gates = gates
        self._decisions: dict[tuple[str, str, str], _CachedDecision] = {}

    def evaluate(self, ctx: GateContext) -> DecisionEnvelope:
        key = self._cache_key(ctx)
        cached = self._decisions.get(key)
        if cached is not None:
            replay = cached.envelope.model_copy(deep=True)
            replay.idempotent_replay = True
            return replay

        started = time.perf_counter()
        results: list[GateResult] = []
        short_circuited = False

        for gate in self.gates:
            if short_circuited:
                results.append(
                    GateResult(
                        gate=gate.gate,
                        verdict=GateVerdict.SKIPPED,
                        detail={"reason": "short_circuit"},
                    )
                )
                continue

            result = gate.run(ctx)
            results.append(result)

            # Once a request is denied, further evaluation is wasted work and
            # would leak information about downstream controls.
            if result.verdict is GateVerdict.FAIL and any(
                get_reason(c).proposes is Decision.DENY for c in result.codes
            ):
                short_circuited = True

        decision, codes = adjudicate(ctx, results)
        envelope = DecisionEnvelope(
            decision_id=f"dec_{uuid.uuid4().hex[:16]}",
            decision=decision,
            agent_id=ctx.request.agent_id,
            tier=ctx.tier,
            reason_codes=list(dict.fromkeys(codes)),
            gate_trace=results,
            explanation=explain(decision, codes, results),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            policy_version=ctx.policy.version,
            decided_at=ctx.now,
        )

        # Book consumed state only once the decision is known. Gates that hold
        # no cross-request state no-op here.
        if decision is Decision.ALLOW:
            for gate in self.gates:
                gate.commit(ctx, envelope)

        self._decisions[key] = _CachedDecision(envelope=envelope)
        return envelope

    @staticmethod
    def _cache_key(ctx: GateContext) -> tuple[str, str, str]:
        mandate_hash = (
            ctx.request.mandates.chain_hash() if ctx.request.mandates else "-"
        )
        return (ctx.request.agent_id, mandate_hash, ctx.request.idempotency_key)

    def clear_cache(self) -> None:
        self._decisions.clear()


def default_pipeline(
    limits: LimitStore | None = None,
    blocks: BlockLedger | None = None,
) -> Pipeline:
    """Transport, identity, mandate, cart binding, bounds, content threat.

    G4 carries cross-request counters, so its stores are injectable: the eval
    harness and the sandbox need to drive them on a controlled clock, and a
    multi-process deployment needs them shared rather than per-worker.

    G5 is deliberately deterministic and local, so it is safe to run on the
    inline money path without affecting the model/network boundary.
    """
    return Pipeline(
        [
            G0Replay(),
            G1Identity(),
            G2Mandate(),
            G3Cart(),
            G4Envelope(limits=limits, blocks=blocks),
            G5ContentThreat(),
        ]
    )
