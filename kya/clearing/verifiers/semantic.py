"""Semantic verifier — the LLM, and the cap that makes it safe to have.

This is the weakest component in the system, deliberately and irreducibly. It
is also the clearest statement of the project's central claim, so it is worth
being exact about what is being asserted.

**A model's opinion is `SELF`-class evidence.** Not because we chose to be
cautious about LLMs, but because that is what the class means: an unverified
report by a party with no independent standing. The model did not witness the
delivery and did not process the transaction; it read a description and formed
a view. `class Verifier` enforces `max_basis` after the fact, so nothing this
verifier returns can exceed `SIGN` — and it reaches `SIGN` only when everything
it read was itself signed.

The consequence is structural, not procedural. An obligation with `φO = REC`
cannot be cleared by this verifier at any confidence, because the aggregator
gives weight zero to any verdict below the floor. There is no configuration
that turns this off and no threshold that overrides it. *A model's opinion can
never, by itself, clear a settlement.*

What it is genuinely good for is the case in RAILS' worked example: the courier
manifest says a parcel was delivered, and the photo shows a phone case where a
phone was promised. Nothing deterministic catches that. The model raises the
flag; `REC`-class evidence is what makes the flag actionable. That division of
labour is the design.

Two judges ship. ``KeywordJudge`` is deterministic and offline, and is what the
tests and the eval harness run against — a suite whose results depend on a
remote model is a suite that cannot be reproduced. ``NvidiaJudge`` calls a real
model and is opt-in. Both are capped identically, which is the point: the cap
is a property of the verifier, not of how good the judge is.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from kya.clearing.verifiers.base import VerificationContext, Verifier
from kya.enums import VerifierRole
from kya.evidence import EvidenceClass
from kya.schemas import ObligationReceipt, VerifierOutput

#: Claim carrying free-text or image descriptions of what actually arrived.
CLAIM_DELIVERY_DESCRIPTION = "delivery_description"


@dataclass(slots=True)
class Judgement:
    verdict: str  # SATISFIED | VIOLATED | INDETERMINATE
    confidence: float
    rationale: str


class Judge(Protocol):
    """Decides whether a description matches what was promised."""

    def assess(self, obligation: ObligationReceipt, description: str) -> Judgement: ...


class KeywordJudge:
    """Deterministic stand-in. Offline, reproducible, and quite weak.

    Token overlap between the description and the promised item names and SKUs.
    Being weak is not a problem to be fixed here: this verifier's output is
    capped below most obligations' floors, so the difference between a crude
    judge and a good one is a difference in *flag quality*, never in whether a
    settlement clears.
    """

    #: Overlap below this reads as a mismatch worth flagging.
    MISMATCH_BELOW = 0.34

    def assess(self, obligation: ObligationReceipt, description: str) -> Judgement:
        promised_tokens = set()
        for line in obligation.promised.line_items:
            promised_tokens |= _tokens(line.name) | _tokens(line.sku)
        promised_tokens -= _STOPWORDS

        observed = _tokens(description)
        if not promised_tokens or not observed:
            return Judgement("INDETERMINATE", 0.0, "nothing comparable to match on")

        overlap = len(promised_tokens & observed) / len(promised_tokens)
        if overlap >= 0.67:
            return Judgement(
                "SATISFIED",
                round(min(1.0, overlap), 2),
                f"description matches {overlap:.0%} of the promised item's terms",
            )
        if overlap < self.MISMATCH_BELOW:
            return Judgement(
                "VIOLATED",
                round(1.0 - overlap, 2),
                (
                    f"description shares only {overlap:.0%} of the promised item's "
                    f"terms; expected {sorted(promised_tokens)}, saw {sorted(observed)}"
                ),
            )
        return Judgement(
            "INDETERMINATE",
            round(overlap, 2),
            f"partial match ({overlap:.0%}) — not enough either way",
        )


class NvidiaJudge:
    """A real model, over NVIDIA's OpenAI-compatible endpoint. Opt-in.

    Off the money path by construction: this runs in the async control plane,
    and the inline pipeline's latency test asserts that nothing reachable from
    a gate can reach a network call.

    Every failure mode — no key, timeout, malformed reply, refusal — resolves to
    INDETERMINATE. A judge that raises would take out the verifier; a judge that
    guesses on a failed call would be inventing evidence.
    """

    BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
    SYSTEM = (
        "You compare a delivery description against what a merchant promised. "
        "Reply with JSON only: "
        '{"verdict": "SATISFIED"|"VIOLATED"|"INDETERMINATE", '
        '"confidence": 0.0-1.0, "rationale": "one sentence"}. '
        "VIOLATED means the described item is not the promised item. "
        "INDETERMINATE means the description is too vague to tell."
    )

    def __init__(
        self,
        api_key: str,
        model: str = "meta/llama-3.3-70b-instruct",
        timeout: float = 20.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def assess(self, obligation: ObligationReceipt, description: str) -> Judgement:
        if not self.api_key:
            return Judgement("INDETERMINATE", 0.0, "no NVIDIA_API_KEY configured")

        promised = ", ".join(
            f"{line.qty} x {line.name} (SKU {line.sku})"
            for line in obligation.promised.line_items
        )
        try:
            import httpx

            response = httpx.post(
                self.BASE_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0.0,
                    "max_tokens": 200,
                    "messages": [
                        {"role": "system", "content": self.SYSTEM},
                        {
                            "role": "user",
                            "content": (
                                f"PROMISED: {promised}\n"
                                f"DELIVERY DESCRIPTION: {description}"
                            ),
                        },
                    ],
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 - see class docstring
            return Judgement(
                "INDETERMINATE", 0.0, f"judge unavailable: {type(exc).__name__}"
            )

        return _parse_judgement(content)


def _parse_judgement(content: str) -> Judgement:
    """Read the model's reply, distrustfully.

    Models wrap JSON in prose and fences. An unparseable reply is
    INDETERMINATE, never a guess — the failure of a `SELF`-class judge to
    answer clearly is not itself evidence about the delivery.
    """
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return Judgement("INDETERMINATE", 0.0, "judge returned no JSON object")
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return Judgement("INDETERMINATE", 0.0, "judge returned malformed JSON")

    verdict = str(parsed.get("verdict", "")).upper()
    if verdict not in ("SATISFIED", "VIOLATED", "INDETERMINATE"):
        return Judgement("INDETERMINATE", 0.0, f"judge returned verdict {verdict!r}")

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    return Judgement(
        verdict,
        max(0.0, min(1.0, confidence)),
        str(parsed.get("rationale", ""))[:300],
    )


class SemanticVerifier(Verifier):
    role = VerifierRole.SEMANTIC

    #: The line that carries the argument. Enforced by ``Verifier._apply_cap``
    #: after the subclass has spoken, so it holds regardless of what this class
    #: or any future subclass tries to declare.
    max_basis = EvidenceClass.SIGN

    def __init__(self, judge: Judge | None = None) -> None:
        self.judge = judge or KeywordJudge()

    def verify(self, ctx: VerificationContext) -> VerifierOutput:
        support = ctx.index.support(CLAIM_DELIVERY_DESCRIPTION)
        best = support.best
        if best is None:
            return self._out(
                "INDETERMINATE",
                0.0,
                EvidenceClass.SELF,
                detail="no delivery description to assess",
            )

        judgement = self.judge.assess(ctx.obligation, str(best.value))

        # Basis is the meet of what was read with the SIGN cap. Reading a
        # `REC`-class courier manifest does not make the model's reading of it
        # `REC` — the opinion is still the model's.
        basis = ctx.index.basis_of([best.item_id])

        loss = (
            ctx.obligation.promised.total if judgement.verdict == "VIOLATED" else 0
        )
        return self._out(
            judgement.verdict,  # type: ignore[arg-type]
            judgement.confidence,
            basis,
            cited=[best.item_id],
            loss=loss,
            detail=judgement.rationale,
        )


_STOPWORDS = {"the", "a", "an", "of", "and", "with", "for", "in", "inch", "new"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 1}


def judge_from_env() -> Judge:
    """An ``NvidiaJudge`` when a key is configured, else the offline judge.

    Defaulting to offline keeps the test suite and the eval harness
    reproducible. The semantic verifier being the weakest link is acceptable;
    the eval's numbers moving because a remote model was retrained is not.
    """
    from kya.config import Settings

    settings = Settings()
    key = getattr(settings, "nvidia_api_key", "")
    return NvidiaJudge(key) if key else KeywordJudge()
