from __future__ import annotations

import pytest

from kya.config import ConfigError, Settings


def test_neon_libpq_variables_build_ssl_and_channel_binding_connection():
    settings = Settings(
        _env_file=None,
        PGHOST="ep-example-pooler.neon.tech",
        PGDATABASE="neondb",
        PGUSER="neondb_owner",
        PGPASSWORD="test-password",
        PGSSLMODE="require",
        PGCHANNELBINDING="require",
    )

    kwargs = settings.postgres_connection_kwargs()

    assert kwargs == {
        "host": "ep-example-pooler.neon.tech",
        "dbname": "neondb",
        "user": "neondb_owner",
        "password": "test-password",
        "sslmode": "require",
        "channel_binding": "require",
    }


def test_postgres_is_opt_in_when_no_connection_settings_exist():
    assert Settings(_env_file=None).postgres_connection_kwargs() is None


def test_partial_postgres_configuration_is_rejected():
    settings = Settings(_env_file=None, PGHOST="ep-example-pooler.neon.tech")

    with pytest.raises(ConfigError, match="incomplete"):
        settings.postgres_connection_kwargs()
