"""Consent Ledger & Evidence Chain.

Captures granular human delegation constraints (spend limits, merchants,
categories, temporal validity) and binds them cryptographically to the agent's
mandates and the payment rail.

Provides the proof necessary to defeat "I didn't authorize my agent" friendly
fraud disputes.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from kya.canonical import digest, now_utc
from kya.crypto import verify, verify_payload
from kya.enums import Gate, GateVerdict
from kya.reasons import L001, L002, M003, M004, M005
from kya.schemas import (
    Cart,
    ConsentRecord,
    GateResult,
    IntentMandate,
    MandateBundle,
)


def create_consent_record(
    mandates: MandateBundle,
    anchored_rail_ref: str | None = None,
) -> ConsentRecord:
    """Mint a hash-anchored ConsentRecord from a verified MandateBundle."""
    intent = mandates.intent
    cart = mandates.cart
    chain_hash = mandates.chain_hash()

    consent_id = f"cst_{uuid.uuid4().hex[:16]}"
    record = ConsentRecord(
        consent_id=consent_id,
        principal_ref=intent.principal_ref,
        agent_id=intent.agent_id,
        intent_id=intent.intent_id,
        intent_hash=intent.reference(),
        cart_id=cart.cart_id,
        cart_hash=cart.cart_hash,
        mandate_chain_hash=chain_hash,
        constraints=intent.constraints.model_copy(deep=True),
        delegation_signature=intent.signature,
        issued_at=intent.issued_at,
        expires_at=intent.expires_at,
        anchored_rail_ref=anchored_rail_ref,
    )
    record.consent_hash = record.compute_hash()
    return record


class ConsentLedger:
    """Durable append-only store for human buyer consent records."""

    def __init__(
        self,
        db_path: Path | str = ":memory:",
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._db_path = str(db_path)
        self._clock = clock
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS consent_records (
                    consent_id TEXT PRIMARY KEY,
                    principal_ref TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    intent_hash TEXT NOT NULL,
                    cart_id TEXT NOT NULL,
                    cart_hash TEXT NOT NULL,
                    mandate_chain_hash TEXT NOT NULL,
                    max_amount INTEGER NOT NULL,
                    allowed_merchants TEXT,
                    allowed_categories TEXT,
                    delegation_signature TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    anchored_rail_ref TEXT,
                    consent_hash TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_consent_principal ON consent_records(principal_ref)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_consent_agent ON consent_records(agent_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_consent_chain_hash ON consent_records(mandate_chain_hash)"
            )

    def record(
        self,
        mandates: MandateBundle,
        anchored_rail_ref: str | None = None,
    ) -> ConsentRecord:
        """Store and index a new consent record."""
        record = create_consent_record(mandates, anchored_rail_ref=anchored_rail_ref)
        import json

        raw_json = json.dumps(record.model_dump(mode="json"), sort_keys=True)
        now_iso = self._clock().isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO consent_records (
                    consent_id, principal_ref, agent_id, intent_id, intent_hash,
                    cart_id, cart_hash, mandate_chain_hash, max_amount,
                    allowed_merchants, allowed_categories, delegation_signature,
                    issued_at, expires_at, anchored_rail_ref, consent_hash,
                    raw_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.consent_id,
                    record.principal_ref,
                    record.agent_id,
                    record.intent_id,
                    record.intent_hash,
                    record.cart_id,
                    record.cart_hash,
                    record.mandate_chain_hash,
                    record.constraints.max_amount,
                    ",".join(record.constraints.allowed_merchants),
                    ",".join(record.constraints.allowed_categories or []),
                    record.delegation_signature,
                    record.issued_at.isoformat(),
                    record.expires_at.isoformat(),
                    record.anchored_rail_ref,
                    record.consent_hash,
                    raw_json,
                    now_iso,
                ),
            )
        return record

    def get_by_id(self, consent_id: str) -> ConsentRecord | None:
        row = self._conn.execute(
            "SELECT raw_json FROM consent_records WHERE consent_id = ?",
            (consent_id,),
        ).fetchone()
        if not row:
            return None
        import json

        data = json.loads(row["raw_json"])
        return ConsentRecord.model_validate(data)

    def get_by_chain_hash(self, chain_hash: str) -> ConsentRecord | None:
        row = self._conn.execute(
            "SELECT raw_json FROM consent_records WHERE mandate_chain_hash = ?",
            (chain_hash,),
        ).fetchone()
        if not row:
            return None
        import json

        data = json.loads(row["raw_json"])
        return ConsentRecord.model_validate(data)

    def get_by_agent(self, agent_id: str) -> list[ConsentRecord]:
        rows = self._conn.execute(
            "SELECT raw_json FROM consent_records WHERE agent_id = ? ORDER BY issued_at DESC",
            (agent_id,),
        ).fetchall()
        import json

        return [ConsentRecord.model_validate(json.loads(r["raw_json"])) for r in rows]

    def verify_consent(
        self,
        record: ConsentRecord,
        charged_cart: Cart | None = None,
        at: datetime | None = None,
    ) -> tuple[bool, list[str]]:
        """Cryptographically and logically verify that a transaction was within consent bounds.

        Returns (is_valid, list_of_reasons_or_violations).
        """
        now = at or self._clock()
        violations: list[str] = []

        # 1. Temporal window
        if now > record.expires_at:
            violations.append(f"Consent expired at {record.expires_at.isoformat()}")

        # 2. Re-compute hash
        computed_hash = record.compute_hash()
        if record.consent_hash != computed_hash:
            violations.append("Consent record hash mismatch (tampered record)")

        # 3. Check against charged cart if provided
        if charged_cart is not None:
            if charged_cart.total > record.constraints.max_amount:
                violations.append(
                    f"Cart total {charged_cart.total} paise exceeds max_amount constraint {record.constraints.max_amount} paise"
                )

            if record.constraints.allowed_merchants:
                if charged_cart.merchant_id not in record.constraints.allowed_merchants:
                    violations.append(
                        f"Merchant {charged_cart.merchant_id} not in allowed_merchants {record.constraints.allowed_merchants}"
                    )

            if record.constraints.allowed_categories and charged_cart.category:
                if charged_cart.category not in record.constraints.allowed_categories:
                    violations.append(
                        f"Category {charged_cart.category} not in allowed_categories {record.constraints.allowed_categories}"
                    )

        if violations:
            return False, violations
        return True, [L001.code]
