"""Shared Postgres fixture for the live-database tests.

DSN sources, in order: SETLEDGER_PG_DSN, ~/.config/setledger/tiger_dsn, then an embedded
throwaway Postgres 16 + pgvector via `pgserver` (no TimescaleDB there, so Timescale-only
checks skip on it). Skips cleanly when none is available.
"""
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def pg_dsn():
    if os.environ.get("SETLEDGER_PG_DSN"):
        yield os.environ["SETLEDGER_PG_DSN"], "env"
        return
    f = Path.home() / ".config" / "setledger" / "tiger_dsn"
    if f.exists() and f.read_text().strip():
        yield f.read_text().strip(), "tiger"
        return
    try:
        import pgserver
    except ImportError:
        pytest.skip("no Postgres DSN and pgserver not installed")
    srv = pgserver.get_server(tempfile.mkdtemp(prefix="sl_pg_"), cleanup_mode="delete")
    try:
        yield srv.get_uri(), "pgserver"
    finally:
        srv.cleanup()
