"""The obligation layer — what was promised, recorded so it can be checked later.

Payment objects record that money moved. Nothing in a payment rail records what
the merchant undertook to do in exchange. That gap is why a transaction can
pass every identity and mandate check and still end in a dispute nobody can
adjudicate: there is no artifact stating what "satisfied" would have looked
like.

Three pieces:

* ``receipt`` — minting. Turns an allowed cart into a statement of what was
  promised, with the predicates that would satisfy it and the class of evidence
  required to prove each one.
* ``ledger`` — an append-only, hash-chained store. Obligations are never
  mutated; a state change appends a new version, so the whole history is
  replayable and tampering is detectable.
* ``anchor`` — writes the receipt hash into the Razorpay order's ``notes``, and
  verifies it back. This is what converts "trust our logs" into "verify against
  Razorpay's".
"""

from kya.obligation.anchor import (
    ANCHOR_KEY,
    ANCHOR_VERSION_KEY,
    AnchorCheck,
    anchor_notes,
    verify_anchor,
)
from kya.obligation.ledger import (
    GENESIS_HASH,
    ChainVerification,
    LedgerError,
    ObligationLedger,
)
from kya.obligation.receipt import (
    MerchantIdentity,
    ReceiptMinter,
    derive_acceptance_criteria,
    derive_evidence_requirements,
)

__all__ = [
    "ANCHOR_KEY",
    "ANCHOR_VERSION_KEY",
    "AnchorCheck",
    "anchor_notes",
    "verify_anchor",
    "GENESIS_HASH",
    "ChainVerification",
    "LedgerError",
    "ObligationLedger",
    "MerchantIdentity",
    "ReceiptMinter",
    "derive_acceptance_criteria",
    "derive_evidence_requirements",
]
