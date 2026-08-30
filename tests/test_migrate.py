from __future__ import annotations

import sqlite3
from pathlib import Path

from kya.db.migrate import migrate_sqlite, run_migrations


def test_sqlite_migration_creates_tables(tmp_path: Path):
    db_file = tmp_path / "test_kya.db"
    res = migrate_sqlite(db_file)

    assert res.backend == "sqlite"
    assert "001_initial_schema" in res.applied
    assert set(res.tables) >= {"passports", "obligations", "rail_bindings", "schema_migrations"}

    # Second run is idempotent
    res2 = migrate_sqlite(db_file)
    assert res2.applied == []


def test_run_migrations_succeeds_for_default_sqlite(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "app.db"
    monkeypatch.setenv("KYA_DB_PATH", str(test_db))
    monkeypatch.delenv("KYA_DATABASE_URL", raising=False)
    monkeypatch.delenv("PGHOST", raising=False)

    results = run_migrations()
    assert len(results) >= 1
    assert results[0].backend == "sqlite"
    assert test_db.exists()
