"""Verify the configured Neon/Postgres ledger connection without exposing secrets."""

from __future__ import annotations

from kya.config import ConfigError, load_settings
from kya.obligation.postgres import PostgresObligationLedger


def main() -> int:
    settings = load_settings()
    connect_kwargs = settings.postgres_connection_kwargs()
    if connect_kwargs is None:
        print("Postgres is not configured. Set KYA_DATABASE_URL or PGHOST/PGDATABASE/PGUSER/PGPASSWORD.")
        return 2

    ledger = PostgresObligationLedger(
        settings.merchant_identity(), connect_kwargs=connect_kwargs
    )
    try:
        check = ledger.verify()
        print(f"Postgres connected; obligation ledger is intact ({check.entries} entries).")
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - command entry point
    try:
        raise SystemExit(main())
    except (ConfigError, ImportError) as exc:
        print(f"Postgres connection check failed: {exc}")
        raise SystemExit(2)
