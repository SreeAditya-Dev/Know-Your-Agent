"""Receipt verifier — reconciles the obligation against what the rail says.

The only verifier whose verdicts routinely carry `REC` class, and it carries it
for one reason: the evidence it reads was **fetched from Razorpay by us**, not
submitted to us. An agent can hand over a JSON object indistinguishable from a
Razorpay payment, and that object is `SELF`-class, because the agent could have
written it. The same object pulled over our own credentials is a receipt from a
system with no stake in the outcome.

So this verifier does not accept any item offered for the amount claim. It
filters to rail-sourced ones, and reports INDETERMINATE if there are none —
even when the envelope is full of agent-supplied assertions saying exactly what
it wants to hear.

It verifies payment, not delivery. Confirming that money moved is precisely
what a payment rail can attest to, and confirming that a phone arrived is
precisely what it cannot.
"""

from __future__ import annotations

from kya.clearing.evidence import effective_class
from kya.clearing.verifiers.base import VerificationContext, Verifier
from kya.enums import RailType, VerifierRole
from kya.evidence import EvidenceClass, leq
from kya.obligation.receipt import CLAIM_AMOUNT_CHARGED
from kya.schemas import EvidenceItem, VerifierOutput


class ReceiptVerifier(Verifier):
    role = VerifierRole.RECEIPT

    def verify(self, ctx: VerificationContext) -> VerifierOutput:
        obligation = ctx.obligation

        if obligation.rail.type is not RailType.RAZORPAY_ORDER:
            # A simulated block ledger is our own record, not a third party's.
            # Grading it `REC` would be laundering our database into a receipt.
            return self._out(
                "INDETERMINATE",
                0.0,
                EvidenceClass.SELF,
                detail=f"rail {obligation.rail.type.value} issues no external receipt",
            )

        receipts = self._rail_sourced(ctx)
        if not receipts:
            return self._out(
                "INDETERMINATE",
                0.0,
                EvidenceClass.SELF,
                detail=(
                    "no rail-sourced receipt for amount_charged; agent-supplied "
                    "assertions do not substitute for one"
                ),
            )

        best = max(receipts, key=lambda i: i.item_id)
        net = _as_paise(best.value)
        promised = obligation.promised.total
        cited = [best.item_id]

        if net is None:
            return self._out(
                "INDETERMINATE",
                0.0,
                EvidenceClass.REC,
                cited=cited,
                detail=f"unreadable amount on rail receipt: {best.value!r}",
            )

        if net > promised:
            return self._out(
                "VIOLATED",
                1.0,
                EvidenceClass.REC,
                cited=cited,
                loss=net - promised,
                detail=(
                    f"rail reports {net} paise collected against {promised} "
                    "promised — over-collection"
                ),
            )

        if net < promised:
            # Not a violation. An authorised-but-uncaptured or partially
            # captured order is an ordinary intermediate state, and calling it
            # a breach would reverse settlements on timing.
            return self._out(
                "INDETERMINATE",
                0.5,
                EvidenceClass.REC,
                cited=cited,
                detail=f"partially settled: {net} of {promised} paise",
            )

        return self._out(
            "SATISFIED",
            1.0,
            EvidenceClass.REC,
            cited=cited,
            detail=f"rail confirms {net} paise captured, matching the obligation",
        )

    @staticmethod
    def _rail_sourced(ctx: VerificationContext) -> list[EvidenceItem]:
        """Items for the amount claim that actually reach `REC`.

        The class test does the filtering rather than a source-string match, so
        an item that *came* from the rail but was relayed through the agent —
        and therefore collapsed to `SELF` under the meet rule — is excluded
        here too, without this verifier needing to know about provenance.
        """
        return [
            item
            for item in ctx.index.support(CLAIM_AMOUNT_CHARGED).items
            if leq(EvidenceClass.REC, effective_class(item))
        ]


def _as_paise(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
