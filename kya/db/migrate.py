"""Database migration manager for SQLite and Neon/Postgres backends.

Runs idempotent migrations for:
- Clearing Passport store (passports)
- Obligation Ledger (obligations, rail_bindings)
- Migration tracking (schema_migrations)
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kya.canonical import now_utc
from kya.config import load_settings


SQLITE_MIGRATIONS = {
    "001_initial_schema": """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version    TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS passports (
        agent_id            TEXT PRIMARY KEY,
        tier                TEXT NOT NULL,
        cleared_count       INTEGER NOT NULL DEFAULT 0,
        disputed_count      INTEGER NOT NULL DEFAULT 0,
        basis_drift_events  INTEGER NOT NULL DEFAULT 0,
        total_cleared_value INTEGER NOT NULL DEFAULT 0,
        first_seen          TEXT NOT NULL,
        last_seen           TEXT NOT NULL
    );

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
    CREATE INDEX IF NOT EXISTS ix_obligations_id ON obligations(obligation_id);
    CREATE INDEX IF NOT EXISTS ix_obligations_rail ON obligations(rail_type, rail_ref);
    CREATE INDEX IF NOT EXISTS ix_obligations_mandate ON obligations(mandate_chain_hash);

    CREATE TABLE IF NOT EXISTS rail_bindings (
        obligation_id TEXT PRIMARY KEY,
        rail_id       TEXT NOT NULL,
        bound_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_bindings_rail_id ON rail_bindings(rail_id);
    """
}

POSTGRES_MIGRATIONS = {
    "001_initial_schema": """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version    TEXT PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS passports (
        agent_id            TEXT PRIMARY KEY,
        tier                TEXT NOT NULL,
        cleared_count       BIGINT NOT NULL DEFAULT 0,
        disputed_count      BIGINT NOT NULL DEFAULT 0,
        basis_drift_events  BIGINT NOT NULL DEFAULT 0,
        total_cleared_value BIGINT NOT NULL DEFAULT 0,
        first_seen          TIMESTAMPTZ NOT NULL,
        last_seen           TIMESTAMPTZ NOT NULL
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
    """
}


@dataclass
class MigrationResult:
    backend: str
    target: str
    applied: list[str]
    tables: list[str]
    counts: dict[str, int]


def migrate_sqlite(db_path: str | Path) -> MigrationResult:
    """Run migrations on a SQLite database."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    applied_migrations: list[str] = []
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        # Ensure migration table exists
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        existing = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

        for version, sql in SQLITE_MIGRATIONS.items():
            if version not in existing:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, now_utc().isoformat()),
                )
                applied_migrations.append(version)
        conn.commit()

        # Gather table info
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        counts: dict[str, int] = {}
        for t in tables:
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

    return MigrationResult(
        backend="sqlite",
        target=str(path),
        applied=applied_migrations,
        tables=tables,
        counts=counts,
    )


def migrate_postgres(connect_kwargs: dict[str, Any]) -> MigrationResult:
    """Run migrations on a PostgreSQL / Neon database."""
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise ImportError(
            "psycopg is required for Postgres migrations: pip install -e '.[postgres]'"
        ) from exc

    applied_migrations: list[str] = []
    with psycopg.connect(**connect_kwargs, autocommit=True, row_factory=dict_row) as conn:
        # Ensure migration table exists
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations")
            existing = {row["version"] for row in cur.fetchall()}

            for version, sql in POSTGRES_MIGRATIONS.items():
                if version not in existing:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                        (version, now_utc()),
                    )
                    applied_migrations.append(version)

            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
            tables = [r["table_name"] for r in cur.fetchall()]
            counts: dict[str, int] = {}
            for t in tables:
                cur.execute(f"SELECT COUNT(*) AS c FROM {t}")
                counts[t] = cur.fetchone()["c"]

    target_desc = connect_kwargs.get("host", connect_kwargs.get("conninfo", "postgres"))
    return MigrationResult(
        backend="postgres",
        target=str(target_desc),
        applied=applied_migrations,
        tables=tables,
        counts=counts,
    )


def run_migrations() -> list[MigrationResult]:
    """Migrate all configured backends (SQLite default + Postgres if configured)."""
    settings = load_settings()
    results: list[MigrationResult] = []

    # 1. Always ensure SQLite database is migrated
    sqlite_result = migrate_sqlite(settings.kya_db_path)
    results.append(sqlite_result)

    # 2. If Postgres is configured, migrate it too
    pg_kwargs = settings.postgres_connection_kwargs()
    if pg_kwargs is not None:
        pg_result = migrate_postgres(pg_kwargs)
        results.append(pg_result)

    return results


def main() -> int:
    results = run_migrations()
    for res in results:
        print(f"[{res.backend.upper()}] Database at {res.target}")
        if res.applied:
            print(f"  Applied migrations: {', '.join(res.applied)}")
        else:
            print("  Schema is up to date (no pending migrations).")
        print("  Tables:")
        for tbl, count in res.counts.items():
            print(f"    - {tbl}: {count} rows")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
