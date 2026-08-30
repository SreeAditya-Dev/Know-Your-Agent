"""Neon/Postgres storage for the append-only obligation ledger.

This backend deliberately implements the same public interface as
``ObligationLedger``. Gates, clearing, reconciliation and the MCP transport
therefore receive the durable ledger rather than a second persistence path
with subtly different business rules.
"""

from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Callable, Mapping

from kya.canonical import now_utc
from kya.obligation.ledger import ObligationLedger
from kya.obligation.receipt import MerchantIdentity


_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS obligation_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS obligations (
    seq                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    obligation_id      TEXT    NOT NULL,
    version            INTEGER NOT NULL,
    self_hash          TEXT    NOT NULL UNIQUE,
    prev_hash          TEXT    NOT NULL,
    agent_id           TEXT    NOT NULL,
    rail_type          TEXT    NOT NULL,
    rail_ref           TEXT    NOT NULL,
    mandate_chain_hash TEXT    NOT NULL,
    state              TEXT    NOT NULL,
    amount_due         BIGINT  NOT NULL,
    created_at         TEXT    NOT NULL,
    payload            TEXT    NOT NULL,
    UNIQUE(obligation_id, version)
);
CREATE INDEX IF NOT EXISTS ix_obligations_id ON obligations(obligation_id);
CREATE INDEX IF NOT EXISTS ix_obligations_rail ON obligations(rail_type, rail_ref);
CREATE INDEX IF NOT EXISTS ix_obligations_mandate ON obligations(mandate_chain_hash);

CREATE TABLE IF NOT EXISTS rail_bindings (
    obligation_id TEXT PRIMARY KEY,
    rail_id       TEXT NOT NULL,
    bound_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bindings_rail_id ON rail_bindings(rail_id);

INSERT INTO obligation_schema_migrations (version)
VALUES ('001_obligation_ledger') ON CONFLICT (version) DO NOTHING;
"""


class _PsycopgConnection:
    """Small DB-API adapter that lets the audited ledger implementation stay shared.

    SQLite uses qmark placeholders while psycopg uses ``%s``. All queries in
    the ledger are fixed application SQL, so translating bind markers here
    preserves parameterisation and avoids maintaining two copies of the gate
    facing ledger logic.
    """

    def __init__(self, connection) -> None:
        self._connection = connection

    def execute(self, query: str, params: tuple | None = None):
        translated = query.replace("?", "%s")
        return self._connection.execute(translated, params)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


class PostgresObligationLedger(ObligationLedger):
    """Postgres-backed hash chain, suitable for a Neon pooled endpoint.

    A transaction advisory lock prevents two application workers from reading
    the same tip and appending divergent chain heads. It is released on every
    commit or rollback and does not require a session-pinned connection, which
    keeps it compatible with Neon PgBouncer pooling.
    """

    def __init__(
        self,
        merchant: MerchantIdentity,
        *,
        connect_kwargs: Mapping[str, str],
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ImportError(
                "Postgres persistence requires the optional dependency. "
                "Install it with: pip install -e '.[postgres]'"
            ) from exc

        kwargs = dict(connect_kwargs)
        conninfo = kwargs.pop("conninfo", "")
        connection = psycopg.connect(conninfo, row_factory=dict_row, **kwargs)
        self.merchant = merchant
        self._clock = clock
        self._lock = RLock()
        self._conn = _PsycopgConnection(connection)
        self._conn.execute(_POSTGRES_SCHEMA)
        self._conn.commit()

    def append(self, receipt):
        with self._lock:
            # One lock for the one globally ordered chain, held only while the
            # current append transaction is open.
            self._conn.execute("SELECT pg_advisory_xact_lock(hashtext('kya_obligation_chain'))")
            return super().append(receipt)

    def _is_integrity_error(self, exc: Exception) -> bool:
        try:
            from psycopg import IntegrityError
        except ImportError:  # pragma: no cover - constructor already guards it
            return False
        return isinstance(exc, IntegrityError)
