"""Shared pytest fixtures for the Cricket Team Performance Agent test suite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cricket_agent.database import connect, create_schema, insert_match
from cricket_agent.ingestion.parser import parse_match_file

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture()
def real_db() -> sqlite3.Connection:
    """The ingested database at DATABASE_PATH.

    Skips (instead of failing) on a fresh checkout where nothing has been
    ingested yet; the file-exists check stops sqlite3 creating an empty one.
    """
    from cricket_agent.config import DATABASE_PATH

    if not Path(DATABASE_PATH).is_file():
        pytest.skip(f"needs an ingested database at {DATABASE_PATH}")
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        has_matches = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='matches'"
        ).fetchone() and conn.execute("SELECT 1 FROM matches LIMIT 1").fetchone()
    except sqlite3.Error:
        has_matches = False
    if not has_matches:
        conn.close()
        pytest.skip(f"needs an ingested database at {DATABASE_PATH}")
    yield conn
    conn.close()


@pytest.fixture()
def populated_db() -> sqlite3.Connection:
    """An in-memory SQLite database pre-loaded with the two valid fixture matches.

    Using ":memory:" keeps tests fast and guarantees no test ever touches
    the real data/processed/cricket.db file.
    """
    conn = connect(Path(":memory:"))
    create_schema(conn)
    for filename in ("match_1001.json", "match_1002.json"):
        match = parse_match_file(FIXTURES_DIR / filename)
        insert_match(conn, match)
    yield conn
    conn.close()
