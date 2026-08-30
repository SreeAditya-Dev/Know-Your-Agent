"""Append-only, hash-chained obligation ledger.

Two properties, and both are load-bearing for the dispute story:

**Nothing is ever mutated.** An obligation that settles, is reversed, or has its
balance reduced does not have its row updated — a new *version* is appended.
The receipt for version 1 stays exactly as it was minted, which is what makes
the anchor written into Razorpay's order record valid forever. A ledger that
edited rows would invalidate its own anchor on the first state change.

**Every entry commits to the one before it.** ``prev_hash`` is inside the hashed
payload, so removing, reordering or altering any entry breaks every hash after
it. Deletion is as detectable as modification, which is the property a
plain audit log does not have.

What this does and does not prove. Tamper-*evidence* is not tamper-*proofing*:
an attacker with write access to this database can rewrite the whole chain from
any point and re-seal it, provided they also hold the merchant signing key. The
control that survives that is the anchor — the version-1 hash sits inside
Razorpay's immutable order record, outside our control, so a rewritten local
chain no longer matches it. The chain makes local tampering visible; the anchor
makes it unfixable.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping

from kya.canonical import now_utc
from kya.crypto import sign_payload, verify_payload
from kya.enums import ObligationState, RailType
from kya.obligation.receipt import MerchantIdentity
from kya.schemas import ObligationReceipt

#: The chain's origin. Every entry has a ``prev_hash``, including the first, so
#: verification never needs a special case for position zero — and an attacker
#: cannot pass off a truncated chain as a complete one by deleting the head.
GENESIS_HASH = "0" * 64


class LedgerError(RuntimeError):
    """An append that would corrupt the chain was refused."""


def _synchronized(method):
    """Serialize access to one SQLite connection across FastAPI workers."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


@dataclass(slots=True)
class ChainFailure:
    seq: int
    obligation_id: str
    kind: str
    detail: str


@dataclass(slots=True)
class ChainVerification:
    """The answer to `GET /v1/ledger/verify`."""

    ok: bool
    entries: int
    tip_hash: str
    failures: list[ChainFailure] = field(default_factory=list)

    @property
    def first_break(self) -> int | None:
        return self.failures[0].seq if self.failures else None

    def summary(self) -> str:
        if self.ok:
            return f"chain intact: {self.entries} entries, tip {self.tip_hash[:16]}"
        return (
            f"chain BROKEN at seq {self.first_break}: "
            + "; ".join(f"{f.kind} ({f.detail})" for f in self.failures[:3])
        )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS obligations (
    seq                INTEGER PRIMARY KEY AUTOINCREMENT,
    obligation_id      TEXT    NOT NULL,
    version            INTEGER NOT NULL,
    self_hash          TEXT    NOT NULL UNIQUE,
    prev_hash          TEXT    NOT NULL,
    agent_id           TEXT    NOT NULL,
    rail_type          TEXT    NOT NULL,
    rail_ref           TEXT    NOT NULL,
    mandate_chain_hash TEXT    NOT NULL,
    state              TEXT    NOT NULL,
    amount_due         INTEGER NOT NULL,
    created_at         TEXT    NOT NULL,
    payload            TEXT    NOT NULL,
    UNIQUE(obligation_id, version)
);
CREATE INDEX IF NOT EXISTS ix_obligations_id      ON obligations(obligation_id);
CREATE INDEX IF NOT EXISTS ix_obligations_rail    ON obligations(rail_type, rail_ref);
CREATE INDEX IF NOT EXISTS ix_obligations_mandate ON obligations(mandate_chain_hash);

-- Operational index only, deliberately outside the hash chain. The rail
-- assigns its own identifier after the order exists, so it cannot be part of
-- what was signed before the order existed. The authoritative binding is
-- bidirectional and lives outside this database: the order carries our
-- reference in its `receipt` field and our hash in `notes.kya_obligation`.
-- Tampering with this table changes a lookup, not a fact.
CREATE TABLE IF NOT EXISTS rail_bindings (
    obligation_id TEXT PRIMARY KEY,
    rail_id       TEXT NOT NULL,
    bound_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bindings_rail_id ON rail_bindings(rail_id);
"""


class ObligationLedger:
    """SQLite-backed chain. Also serves as the ``ObligationSource`` G4 reads."""

    def __init__(
        self,
        merchant: MerchantIdentity,
        path: str | Path = ":memory:",
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.merchant = merchant
        self._clock = clock
        # A FastAPI sync route runs in a worker thread. The ledger is shared by
        # those routes, so SQLite's default same-thread restriction would turn
        # a valid API read into a 500. The RLock below serializes all access to
        # this one connection, including nested calls such as amend -> append.
        self._lock = RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # --- writing -------------------------------------------------------------

    @_synchronized
    def append(self, receipt: ObligationReceipt) -> ObligationReceipt:
        """Chain, seal and store a receipt. Returns the sealed copy.

        Sealing happens here rather than at mint because ``prev_hash`` is a
        fact about the ledger, not about the promise. A receipt that chose its
        own position could disagree with where it actually landed.
        """
        sealed = receipt.model_copy(deep=True)
        sealed.prev_hash = self.tip_hash()
        sealed.self_hash = sealed.compute_hash()
        sealed.merchant_signature = sign_payload(
            self.merchant.keypair.private, sealed.signing_payload()
        )

        try:
            self._conn.execute(
                """
                INSERT INTO obligations (obligation_id, version, self_hash, prev_hash,
                                         agent_id, rail_type, rail_ref,
                                         mandate_chain_hash, state, amount_due,
                                         created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sealed.obligation_id,
                    sealed.version,
                    sealed.self_hash,
                    sealed.prev_hash,
                    sealed.agent_id,
                    sealed.rail.type.value,
                    sealed.rail.ref,
                    sealed.mandate_chain_hash,
                    sealed.state.value,
                    sealed.amount_due,
                    sealed.created_at.isoformat(),
                    json.dumps(sealed.model_dump(mode="json")),
                ),
            )
        except Exception as exc:
            if not self._is_integrity_error(exc):
                raise
            self._conn.rollback()
            raise LedgerError(
                f"refusing to append {sealed.obligation_id} v{sealed.version}: {exc}"
            ) from exc

        self._conn.commit()
        return sealed

    @_synchronized
    def amend(
        self,
        obligation_id: str,
        *,
        state: ObligationState | None = None,
        amount_due: int | None = None,
        now: datetime | None = None,
    ) -> ObligationReceipt:
        """Record a state change as a new version.

        The promise itself is never rewritten — only the fields that describe
        where the obligation stands. What was undertaken is history; how much
        of it remains outstanding is current.
        """
        current = self.current(obligation_id)
        if current is None:
            raise LedgerError(f"unknown obligation {obligation_id!r}")

        amended = current.model_copy(deep=True)
        amended.version = current.version + 1
        if state is not None:
            # Coerced rather than assigned. Pydantic does not validate on
            # assignment, so a caller passing the string "SETTLED" would store
            # a receipt whose state field is not an ObligationState — readable,
            # hashable, and wrong in a way nothing downstream would notice.
            amended.state = ObligationState(state)
        if amount_due is not None:
            if amount_due < 0:
                raise LedgerError("amount_due cannot go negative")
            amended.amount_due = amount_due
        amended.created_at = now or self._clock()
        amended.prev_hash = ""
        amended.self_hash = ""
        amended.merchant_signature = ""
        return self.append(amended)

    @_synchronized
    def bind_rail(
        self, obligation_id: str, rail_id: str, now: datetime | None = None
    ) -> None:
        """Record the identifier the rail assigned. Index only — see schema."""
        self._conn.execute(
            """
            INSERT INTO rail_bindings (obligation_id, rail_id, bound_at)
            VALUES (?, ?, ?)
            ON CONFLICT(obligation_id) DO UPDATE SET
                rail_id  = excluded.rail_id,
                bound_at = excluded.bound_at
            """,
            (obligation_id, rail_id, (now or self._clock()).isoformat()),
        )
        self._conn.commit()

    # --- reading -------------------------------------------------------------

    @_synchronized
    def tip_hash(self) -> str:
        row = self._conn.execute(
            "SELECT self_hash FROM obligations ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["self_hash"] if row else GENESIS_HASH

    @_synchronized
    def __len__(self) -> int:
        return int(
            self._conn.execute("SELECT COUNT(*) AS n FROM obligations").fetchone()["n"]
        )

    @_synchronized
    def current(self, obligation_id: str) -> ObligationReceipt | None:
        """Latest version — where the obligation stands now."""
        row = self._conn.execute(
            """
            SELECT payload FROM obligations
            WHERE obligation_id = ? ORDER BY version DESC LIMIT 1
            """,
            (obligation_id,),
        ).fetchone()
        return _load(row)

    @_synchronized
    def original(self, obligation_id: str) -> ObligationReceipt | None:
        """Version 1 — what was promised, and the version the anchor pins.

        Anchor verification must use this. Handing a reviewer the current
        version and asking them to match it against the anchored hash would
        fail the moment the obligation changed state, which is exactly when
        anyone bothers to check.
        """
        row = self._conn.execute(
            "SELECT payload FROM obligations WHERE obligation_id = ? AND version = 1",
            (obligation_id,),
        ).fetchone()
        return _load(row)

    @_synchronized
    def history(self, obligation_id: str) -> list[ObligationReceipt]:
        rows = self._conn.execute(
            "SELECT payload FROM obligations WHERE obligation_id = ? ORDER BY version",
            (obligation_id,),
        ).fetchall()
        return [_load(r) for r in rows]  # type: ignore[misc]

    @_synchronized
    def entries(self) -> list[ObligationReceipt]:
        """The whole chain in append order."""
        rows = self._conn.execute(
            "SELECT payload FROM obligations ORDER BY seq"
        ).fetchall()
        return [_load(r) for r in rows]  # type: ignore[misc]

    @_synchronized
    def by_rail_ref(
        self, rail_type: RailType, rail_ref: str
    ) -> ObligationReceipt | None:
        row = self._conn.execute(
            """
            SELECT obligation_id FROM obligations
            WHERE rail_type = ? AND rail_ref = ? ORDER BY seq LIMIT 1
            """,
            (rail_type.value, rail_ref),
        ).fetchone()
        return self.current(row["obligation_id"]) if row else None

    @_synchronized
    def by_rail_id(self, rail_id: str) -> ObligationReceipt | None:
        """Resolve by the identifier the rail assigned — the webhook path."""
        row = self._conn.execute(
            "SELECT obligation_id FROM rail_bindings WHERE rail_id = ?", (rail_id,)
        ).fetchone()
        return self.current(row["obligation_id"]) if row else None

    @_synchronized
    def rail_id_for(self, obligation_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT rail_id FROM rail_bindings WHERE obligation_id = ?",
            (obligation_id,),
        ).fetchone()
        return row["rail_id"] if row else None

    @_synchronized
    def open_for_mandate_chain(self, mandate_chain_hash: str) -> ObligationReceipt | None:
        """An open obligation already covering this exact signed cart.

        The double-charge defence for an agent that retries with a fresh
        idempotency key. It cannot forge a second cart mandate — that would
        need the principal's key — so the chain hash is the same, and the same
        promise must not be minted twice.
        """
        rows = self._conn.execute(
            """
            SELECT DISTINCT obligation_id FROM obligations
            WHERE mandate_chain_hash = ? ORDER BY seq
            """,
            (mandate_chain_hash,),
        ).fetchall()
        for row in rows:
            receipt = self.current(row["obligation_id"])
            if receipt is not None and receipt.state is ObligationState.OPEN:
                return receipt
        return None

    @_synchronized
    def open_for_block(self, block_ref: str) -> list[ObligationReceipt]:
        """``ObligationSource`` conformance — what G4's block guard reads."""
        rows = self._conn.execute(
            """
            SELECT DISTINCT obligation_id FROM obligations
            WHERE rail_type = ? AND rail_ref = ? ORDER BY seq
            """,
            (RailType.RESERVE_PAY_BLOCK.value, block_ref),
        ).fetchall()

        open_receipts = []
        for row in rows:
            receipt = self.current(row["obligation_id"])
            if receipt is not None and receipt.state is ObligationState.OPEN:
                open_receipts.append(receipt)
        return open_receipts

    @_synchronized
    def open_obligations(self) -> list[ObligationReceipt]:
        rows = self._conn.execute(
            "SELECT DISTINCT obligation_id FROM obligations ORDER BY seq"
        ).fetchall()
        found = [self.current(r["obligation_id"]) for r in rows]
        return [r for r in found if r is not None and r.state is ObligationState.OPEN]

    # --- integrity -----------------------------------------------------------

    @_synchronized
    def verify(self) -> ChainVerification:
        """Walk the chain and report every break found.

        Reports *all* failures rather than stopping at the first. A reviewer
        asking whether the ledger was tampered with needs the extent, not just
        the earliest symptom — one altered row and a rewritten tail look
        identical if you only report position.
        """
        rows = self._conn.execute(
            "SELECT * FROM obligations ORDER BY seq"
        ).fetchall()

        failures: list[ChainFailure] = []
        expected_prev = GENESIS_HASH
        tip = GENESIS_HASH

        for row in rows:
            seq = row["seq"]
            oid = row["obligation_id"]

            try:
                receipt = ObligationReceipt.model_validate(json.loads(row["payload"]))
            except Exception as exc:  # pragma: no cover - corrupted payload
                failures.append(
                    ChainFailure(seq, oid, "payload_unreadable", str(exc)[:120])
                )
                expected_prev = row["self_hash"]
                tip = row["self_hash"]
                continue

            if receipt.prev_hash != expected_prev:
                failures.append(
                    ChainFailure(
                        seq,
                        oid,
                        "link_broken",
                        f"prev_hash {receipt.prev_hash[:16]} != {expected_prev[:16]}",
                    )
                )

            recomputed = receipt.compute_hash()
            if recomputed != receipt.self_hash:
                failures.append(
                    ChainFailure(
                        seq,
                        oid,
                        "content_altered",
                        f"recomputed {recomputed[:16]} != stored {receipt.self_hash[:16]}",
                    )
                )

            if not verify_payload(
                self.merchant.keypair.public,
                receipt.signing_payload(),
                receipt.merchant_signature,
            ):
                failures.append(
                    ChainFailure(seq, oid, "signature_invalid", "merchant seal failed")
                )

            # The indexed columns are a query convenience; the payload is the
            # record. Drift between them is either a bug or a partial tamper,
            # and both are worth naming.
            for column, actual in (
                ("state", receipt.state.value),
                ("amount_due", receipt.amount_due),
                ("rail_ref", receipt.rail.ref),
                ("version", receipt.version),
                ("self_hash", receipt.self_hash),
            ):
                if row[column] != actual:
                    failures.append(
                        ChainFailure(
                            seq,
                            oid,
                            "index_drift",
                            f"{column}: column {row[column]!r} != payload {actual!r}",
                        )
                    )

            expected_prev = receipt.self_hash
            tip = receipt.self_hash

        return ChainVerification(
            ok=not failures, entries=len(rows), tip_hash=tip, failures=failures
        )

    @_synchronized
    def close(self) -> None:
        self._conn.close()

    def _is_integrity_error(self, exc: Exception) -> bool:
        """Backend hook used by the Postgres implementation."""
        return isinstance(exc, sqlite3.IntegrityError)


def _load(row: Mapping[str, Any] | None) -> ObligationReceipt | None:
    if row is None:
        return None
    return ObligationReceipt.model_validate(json.loads(row["payload"]))
