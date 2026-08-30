"""Constraint verifier — did fulfilment satisfy the acceptance predicates?

The only verifier that reads the obligation's own acceptance criteria, which
makes it the one that can answer the question the whole project is built
around: not "was this paid for", but "was what was promised delivered".

It has no basis class of its own. It inherits from whatever it read, which is
correct and is the reason a deterministic checker cannot launder weak evidence
into a strong verdict: evaluating a predicate perfectly against a self-reported
value still only tells you what the self-report said.

A claim with no evidence produces INDETERMINATE rather than VIOLATED. Nothing
arriving is not the same as the wrong thing arriving — a courier that has not
filed its manifest yet is not a merchant who failed to deliver, and treating
the two alike would reverse settlements on paperwork lag.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from kya.clearing.verifiers.base import VerificationContext, Verifier
from kya.enums import VerifierRole
from kya.evidence import EvidenceClass, meet_all
from kya.schemas import AcceptanceCriterion, VerifierOutput


class ConstraintVerifier(Verifier):
    role = VerifierRole.CONSTRAINT

    def verify(self, ctx: VerificationContext) -> VerifierOutput:
        criteria = ctx.obligation.acceptance_criteria
        if not criteria:
            return self._out(
                "INDETERMINATE",
                0.0,
                EvidenceClass.SELF,
                detail="obligation declares no acceptance criteria",
            )

        cited: list[str] = []
        failures: list[str] = []
        unproven: list[str] = []

        for criterion in criteria:
            support = ctx.index.support(criterion.claim)
            best = support.best
            if best is None:
                unproven.append(criterion.claim)
                continue

            cited.append(best.item_id)
            if not _evaluate(criterion, best.value):
                failures.append(
                    f"{criterion.claim} {criterion.op} {criterion.expected!r} "
                    f"but observed {best.value!r}"
                )

        basis = meet_all(
            [
                ctx.index.basis_of([item_id])
                for item_id in cited
            ]
        ) if cited else EvidenceClass.SELF

        if failures:
            # A failed predicate is positive evidence of non-performance, so it
            # is reported at full confidence: the check itself is exact, and
            # whether it *counts* is the aggregator's admissibility question,
            # not a matter of how sure this verifier is.
            return self._out(
                "VIOLATED",
                1.0,
                basis,
                cited=cited,
                loss=ctx.obligation.promised.total,
                detail="; ".join(failures),
            )

        if unproven:
            return self._out(
                "INDETERMINATE",
                0.0,
                basis,
                cited=cited,
                detail=f"no evidence for: {', '.join(sorted(unproven))}",
            )

        return self._out(
            "SATISFIED",
            1.0,
            basis,
            cited=cited,
            detail=f"all {len(criteria)} acceptance criteria met",
        )


def _evaluate(criterion: AcceptanceCriterion, observed: Any) -> bool:
    """Apply one predicate. Anything unevaluable is a failure to satisfy.

    Returning False for a malformed comparison rather than raising keeps a
    badly typed piece of evidence from taking the verifier out entirely — but
    it deliberately does not read as satisfaction either.
    """
    expected = criterion.expected

    try:
        if criterion.op == "equals":
            return observed == expected
        if criterion.op == "contains":
            if isinstance(observed, str):
                return str(expected) in observed
            return expected in observed
        if criterion.op == "lte":
            return observed <= expected
        if criterion.op == "gte":
            return observed >= expected
        if criterion.op == "within_window":
            return _within_window(observed, expected)
    except (TypeError, ValueError):
        return False
    return False


def _within_window(observed: Any, expected: Any) -> bool:
    if not isinstance(expected, dict):
        return False
    moment = _parse(observed)
    start = _parse(expected.get("from"))
    end = _parse(expected.get("to"))
    if moment is None or start is None or end is None:
        return False
    return start <= moment <= end


def _parse(value: Any) -> datetime | None:
    """ISO-8601 in, aware UTC out.

    Windows travel as strings because receipts round-trip through JSON and a
    datetime in a predicate would change the receipt hash on the way back —
    see the note in ``kya/obligation/receipt.py``.
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
