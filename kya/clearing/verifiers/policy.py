"""Policy verifier — merchant commitments the obligation itself implies.

Distinct from the constraint verifier, and the split is worth stating. The
constraint verifier asks whether the *goods* matched the promise. This one asks
whether the *conduct around the transaction* matched it: was more collected
than was owed, was the obligation still live when fulfilment was claimed, was
the delivery window honoured.

Both can fail independently. A merchant can deliver exactly the promised item
and still have over-collected, and the clearing decision reports the two
verdicts separately because they assign fault to different parties and imply
different remedies.

Like the constraint verifier, it has no class of its own and inherits from what
it read. Checking a policy rule against a self-reported value tells you only
what the self-report claimed.
"""

from __future__ import annotations

from kya.clearing.verifiers.base import VerificationContext, Verifier
from kya.enums import VerifierRole
from kya.evidence import EvidenceClass
from kya.obligation.receipt import CLAIM_AMOUNT_CHARGED, CLAIM_DELIVERED_AT
from kya.schemas import VerifierOutput


class PolicyVerifier(Verifier):
    role = VerifierRole.POLICY

    def verify(self, ctx: VerificationContext) -> VerifierOutput:
        obligation = ctx.obligation
        violations: list[str] = []
        cited: list[str] = []
        loss = 0

        # 1. Over-collection. The one policy breach with a directly computable
        #    loss figure, which is why it is checked first.
        amount = ctx.index.support(CLAIM_AMOUNT_CHARGED).best
        if amount is not None:
            cited.append(amount.item_id)
            collected = amount.value
            if isinstance(collected, int) and not isinstance(collected, bool):
                if collected > obligation.promised.total:
                    excess = collected - obligation.promised.total
                    loss += excess
                    violations.append(
                        f"collected {collected} against {obligation.promised.total} "
                        f"promised — {excess} paise over"
                    )

        # 2. Delivery window. A breach of a stated commitment, not of delivery
        #    itself: the goods arrived, the promise about *when* did not hold.
        window = obligation.promised.delivery_window
        delivered = ctx.index.support(CLAIM_DELIVERED_AT).best
        if window is not None and delivered is not None:
            cited.append(delivered.item_id)
            from kya.clearing.verifiers.constraint import _parse

            moment = _parse(delivered.value)
            if moment is not None and moment > window.to:
                late = moment - window.to
                violations.append(
                    f"delivered {late.days}d{late.seconds // 3600}h after the "
                    "promised window closed"
                )

        # 3. Fulfilment claimed against a dead obligation. Evidence arriving
        #    after expiry cannot resurrect it — otherwise the expiry window is
        #    advisory, and an obligation could be cleared indefinitely.
        if ctx.index.envelope.submitted_at > obligation.expires_at:
            violations.append(
                f"evidence submitted after the obligation expired "
                f"({obligation.expires_at.isoformat()})"
            )

        basis = ctx.index.basis_of(cited) if cited else EvidenceClass.SELF

        if violations:
            return self._out(
                "VIOLATED",
                1.0,
                basis,
                cited=cited,
                loss=loss,
                detail="; ".join(violations),
            )

        if not cited:
            return self._out(
                "INDETERMINATE",
                0.0,
                EvidenceClass.SELF,
                detail="no evidence bearing on policy compliance",
            )

        return self._out(
            "SATISFIED",
            1.0,
            basis,
            cited=cited,
            detail="no policy breach found in the submitted evidence",
        )
