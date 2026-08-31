"""Reason code registry. Frozen on Day 0 — the shared vocabulary of the system.

Every gate emits codes from this table. They are the common language of the
audit trail, the dashboard, the natural-language explainer and the metrics
report, which is why they are defined once, before any gate exists, and why
the codes themselves are stable identifiers rather than free text.

The explainer generates prose *from* these codes. It never produces a decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from kya.enums import Decision, Gate, Severity


@dataclass(frozen=True, slots=True)
class Reason:
    """One machine-stable reason a gate can cite."""

    code: str
    gate: Gate
    slug: str
    severity: Severity
    summary: str
    #: What this reason argues for on its own. Adjudication (G6) may escalate
    #: further but never relaxes below this.
    proposes: Decision

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.code} {self.slug}"


def _r(
    code: str,
    gate: Gate,
    slug: str,
    severity: Severity,
    summary: str,
    proposes: Decision,
) -> Reason:
    return Reason(code, gate, slug, severity, summary, proposes)


# --- G0 · transport & replay -------------------------------------------------

R001 = _r(
    "R001", Gate.G0, "replay_nonce_reused", Severity.CRITICAL,
    "This exact signed request was already presented. Replaying a valid "
    "signature is how a captured request becomes a second charge.",
    Decision.DENY,
)
R002 = _r(
    "R002", Gate.G0, "timestamp_skew", Severity.HIGH,
    "The request timestamp falls outside the accepted clock-skew window.",
    Decision.DENY,
)
R003 = _r(
    "R003", Gate.G0, "signature_expired", Severity.HIGH,
    "The signature's validity window has already closed.",
    Decision.DENY,
)
R004 = _r(
    "R004", Gate.G0, "nonce_store_unavailable", Severity.MEDIUM,
    "Replay protection is degraded: the nonce store could not be reached, so "
    "non-replay cannot be proven.",
    Decision.STEP_UP,
)

# --- G1 · agent identity -----------------------------------------------------

I001 = _r(
    "I001", Gate.G1, "signature_absent", Severity.HIGH,
    "The request carries no HTTP message signature, so the calling agent is "
    "unidentified.",
    Decision.DENY,
)
I002 = _r(
    "I002", Gate.G1, "signature_invalid", Severity.CRITICAL,
    "The signature did not verify against the agent's published key. This is "
    "positive evidence of impersonation or tampering, not merely missing proof.",
    Decision.DENY,
)
I003 = _r(
    "I003", Gate.G1, "unknown_key", Severity.CRITICAL,
    "The signing key is not published by the directory the agent named. The "
    "key may have been rotated out, or the directory answer substituted.",
    Decision.DENY,
)
I004 = _r(
    "I004", Gate.G1, "directory_unreachable_degraded", Severity.MEDIUM,
    "The agent's key directory could not be reached and no cached key was "
    "available. Identity is unproven, but absence of evidence is not evidence "
    "of fraud, so the request is stepped up rather than denied.",
    Decision.STEP_UP,
)
I005 = _r(
    "I005", Gate.G1, "signature_agent_missing", Severity.HIGH,
    "No Signature-Agent header, so there is no directory to resolve the "
    "signing key against.",
    Decision.DENY,
)

# --- G2 · mandate chain ------------------------------------------------------

M001 = _r(
    "M001", Gate.G2, "mandate_absent", Severity.HIGH,
    "No mandate bundle was supplied, so there is nothing showing a human "
    "authorised this agent to spend.",
    Decision.DENY,
)
M002 = _r(
    "M002", Gate.G2, "chain_broken", Severity.CRITICAL,
    "The cart mandate does not reference the intent mandate presented with it. "
    "The delegation chain does not join up.",
    Decision.DENY,
)
M003 = _r(
    "M003", Gate.G2, "mandate_expired", Severity.HIGH,
    "The mandate's validity window has closed. Authority to spend has lapsed.",
    Decision.DENY,
)
M004 = _r(
    "M004", Gate.G2, "principal_mismatch", Severity.CRITICAL,
    "The mandate was signed by a party other than the registered principal.",
    Decision.DENY,
)
M005 = _r(
    "M005", Gate.G2, "mandate_signature_invalid", Severity.CRITICAL,
    "A mandate signature did not verify.",
    Decision.DENY,
)
M006 = _r(
    "M006", Gate.G2, "agent_not_delegated", Severity.CRITICAL,
    "The intent mandate delegates to a different agent than the one calling.",
    Decision.DENY,
)

# --- G3 · cart binding -------------------------------------------------------

C001 = _r(
    "C001", Gate.G3, "cart_hash_mismatch", Severity.CRITICAL,
    "The cart being charged is not the cart that was signed. A valid mandate "
    "was presented alongside different contents.",
    Decision.DENY,
)
C002 = _r(
    "C002", Gate.G3, "price_drift", Severity.CRITICAL,
    "Monetary fields moved between signature and charge.",
    Decision.DENY,
)
C003 = _r(
    "C003", Gate.G3, "sku_substitution", Severity.CRITICAL,
    "The items being charged for differ from the items that were authorised.",
    Decision.DENY,
)
C004 = _r(
    "C004", Gate.G3, "constraint_violation", Severity.HIGH,
    "The charge breaches a constraint the buyer set in their intent — amount "
    "ceiling, permitted merchant, or permitted category.",
    Decision.DENY,
)
C005 = _r(
    "C005", Gate.G3, "cart_total_inconsistent", Severity.HIGH,
    "The cart's stated total does not equal its own line items plus shipping "
    "and tax.",
    Decision.DENY,
)

# --- G4 · bounded action envelope --------------------------------------------

E001 = _r(
    "E001", Gate.G4, "velocity_exceeded", Severity.HIGH,
    "The agent has exceeded its permitted request rate for this tier.",
    Decision.QUARANTINE,
)
E002 = _r(
    "E002", Gate.G4, "spend_cap", Severity.HIGH,
    "The agent has exceeded its permitted spend over the rolling window.",
    Decision.QUARANTINE,
)
E003 = _r(
    "E003", Gate.G4, "refund_breaker_open", Severity.CRITICAL,
    "The refund-rate circuit breaker has tripped for this agent. Refunds are "
    "held pending human review.",
    Decision.QUARANTINE,
)
E004 = _r(
    "E004", Gate.G4, "block_debit_unbacked", Severity.CRITICAL,
    "A debit was attempted against a reserved block with no matching open "
    "obligation. The funds were authorised, but nothing was owed.",
    Decision.DENY,
)
E005 = _r(
    "E005", Gate.G4, "tier_ceiling", Severity.MEDIUM,
    "The amount exceeds the ceiling for this agent's trust tier. New agents "
    "are bounded rather than blocked; the transaction can proceed with "
    "principal re-authentication.",
    Decision.STEP_UP,
)
E006 = _r(
    "E006", Gate.G4, "block_reserve_exhausted", Severity.HIGH,
    "Cumulative debits would exceed the amount reserved on this block.",
    Decision.DENY,
)

# --- G5 · content threat -----------------------------------------------------

T001 = _r(
    "T001", Gate.G5, "injection_marker", Severity.HIGH,
    "Agent-supplied free text contains instruction-shaped content, consistent "
    "with an indirect prompt injection aimed at downstream automation.",
    Decision.QUARANTINE,
)
T002 = _r(
    "T002", Gate.G5, "callback_domain_unregistered", Severity.CRITICAL,
    "A callback or webhook URL points at a domain not registered for this "
    "agent and merchant.",
    Decision.DENY,
)

# --- G6 · adjudication -------------------------------------------------------

A001 = _r(
    "A001", Gate.G6, "gate_unresolved", Severity.MEDIUM,
    "A gate could not reach a determination. Uncertainty never resolves "
    "toward allow.",
    Decision.STEP_UP,
)
A002 = _r(
    "A002", Gate.G6, "policy_step_up_threshold", Severity.LOW,
    "The amount is above the tier's step-up threshold, so the buyer is asked "
    "to re-authenticate.",
    Decision.STEP_UP,
)

# --- Liability Arbitration & Dispute Representment ---------------------------

L001 = _r(
    "L001", Gate.G6, "consent_chain_verified", Severity.INFO,
    "The consent ledger confirms the human principal cryptographically signed the "
    "intent mandate and the cart mandate strictly adhered to all constraints.",
    Decision.ALLOW,
)
L002 = _r(
    "L002", Gate.G6, "intent_constraint_breached", Severity.CRITICAL,
    "The agent breached one or more buyer intent constraints (amount, merchant, "
    "category, or temporal validity window).",
    Decision.DENY,
)
L003 = _r(
    "L003", Gate.G6, "delivery_admissible_verified", Severity.INFO,
    "Fulfilment evidence satisfies all acceptance criteria at or above the "
    "declared admissibility floor (REC/ATT/PROOF).",
    Decision.ALLOW,
)
L004 = _r(
    "L004", Gate.G6, "merchant_fulfilment_failed", Severity.CRITICAL,
    "The merchant failed to prove delivery of promised line items or breached the "
    "committed delivery window without valid extension.",
    Decision.DENY,
)
L005 = _r(
    "L005", Gate.G6, "friendly_fraud_detected", Severity.HIGH,
    "Buyer principal fully authorized transaction and merchant delivered compliant "
    "obligation; chargeback claim is unfounded friendly fraud.",
    Decision.ALLOW,
)
L006 = _r(
    "L006", Gate.G6, "agent_rogue_execution", Severity.CRITICAL,
    "The autonomous buyer agent executed an action unprompted or with tampered "
    "parameters without user delegation.",
    Decision.DENY,
)
L007 = _r(
    "L007", Gate.G6, "evidence_package_generated", Severity.INFO,
    "Tamper-evident dispute representment package successfully synthesized and "
    "anchored against the payment rail.",
    Decision.ALLOW,
)
L008 = _r(
    "L008", Gate.G6, "split_liability_assigned", Severity.MEDIUM,
    "Contributory fault detected across multiple transaction parties.",
    Decision.STEP_UP,
)


#: Every reason, keyed by code. Iteration order is definition order.
REGISTRY: dict[str, Reason] = {
    r.code: r
    for r in (
        R001, R002, R003, R004,
        I001, I002, I003, I004, I005,
        M001, M002, M003, M004, M005, M006,
        C001, C002, C003, C004, C005,
        E001, E002, E003, E004, E005, E006,
        T001, T002,
        A001, A002,
        L001, L002, L003, L004, L005, L006, L007, L008,
    )
}


def get(code: str) -> Reason:
    """Look up a reason by code. Raises on an unknown code, deliberately —
    an unrecognised code in an audit trail is a bug, not a display problem."""
    try:
        return REGISTRY[code]
    except KeyError:  # pragma: no cover
        raise KeyError(f"unknown reason code {code!r}") from None


def by_gate(gate: Gate) -> list[Reason]:
    return [r for r in REGISTRY.values() if r.gate is gate]


def governing(codes: list[str]) -> Reason | None:
    """The reason that should drive the decision: most restrictive proposal,
    breaking ties on severity."""
    if not codes:
        return None
    return max(
        (get(c) for c in codes),
        key=lambda r: (r.proposes.rank, int(r.severity)),
    )
