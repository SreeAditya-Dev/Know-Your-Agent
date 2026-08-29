"""Gate base class.

``evaluate`` returns the finding; ``run`` wraps it with timing and turns an
unexpected exception into ``UNKNOWN`` rather than a 500. A crashing gate must
never resolve toward ALLOW, so an internal error degrades the request instead
of silently passing it.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from kya.enums import Gate, GateVerdict
from kya.gates.context import GateContext
from kya.schemas import GateResult


class BaseGate(ABC):
    gate: Gate

    @abstractmethod
    def evaluate(self, ctx: GateContext) -> GateResult:
        """Inspect the context and return a finding. Must not raise."""

    def run(self, ctx: GateContext) -> GateResult:
        started = time.perf_counter()
        try:
            result = self.evaluate(ctx)
        except Exception as exc:  # pragma: no cover - defensive
            result = GateResult(
                gate=self.gate,
                verdict=GateVerdict.UNKNOWN,
                codes=["A001"],
                detail={"error": type(exc).__name__, "message": str(exc)[:200]},
            )
        result.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return result

    # --- construction helpers -----------------------------------------------

    def _pass(self, **detail: Any) -> GateResult:
        return GateResult(gate=self.gate, verdict=GateVerdict.PASS, detail=detail)

    def _fail(self, *codes: str, **detail: Any) -> GateResult:
        return GateResult(
            gate=self.gate, verdict=GateVerdict.FAIL, codes=list(codes), detail=detail
        )

    def _degraded(self, *codes: str, **detail: Any) -> GateResult:
        return GateResult(
            gate=self.gate,
            verdict=GateVerdict.DEGRADED,
            codes=list(codes),
            detail=detail,
        )
