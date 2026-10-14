"""Signature-keyed cache for whole-database aggregates (aggregate_cache table)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

# One lock per cache key: concurrent requests for the same cold aggregate
# wait for the first computation instead of each scanning 11M+ deliveries.
_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()

# How long a cache write may wait for an ingestion's write lock before the
# value is served uncached (the connection default is 30 seconds).
_WRITE_BUSY_TIMEOUT_MS = 1000


def _dataset_signature(conn: sqlite3.Connection) -> str:
    """Changes whenever matches are added, revised, or a dataset is re-imported.

    insert_match deletes and re-inserts a revised match's deliveries, and
    deliveries uses AUTOINCREMENT, so its sqlite_sequence value moves on any
    re-ingest even when the match count does not.
    """
    matches = conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"]
    try:
        row = conn.execute(
            "SELECT imported_at FROM dataset_metadata WHERE metadata_key = 'current'"
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    try:
        seq = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'deliveries'"
        ).fetchone()
    except sqlite3.OperationalError:  # nothing inserted yet
        seq = None
    try:
        # Bumped by triggers on players, so in-place metadata edits
        # (country, role, date of birth) invalidate cached answers too.
        rev = conn.execute("SELECT revision FROM data_revision WHERE id = 1").fetchone()
    except sqlite3.OperationalError:  # database created before data_revision
        rev = None
    return (
        f"{matches}|{seq[0] if seq else 0}|{row['imported_at'] if row else ''}"
        f"|{rev[0] if rev else 0}"
    )


dataset_signature = _dataset_signature


def _lock_for(key: str) -> threading.Lock:
    with _key_locks_guard:
        return _key_locks.setdefault(key, threading.Lock())


lock_for = _lock_for


def _database_file(conn: sqlite3.Connection) -> str:
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main":
            return row[2] or ""
    return ""


def _write(conn: sqlite3.Connection, key: str, signature: str, payload: str) -> None:
    """Store a cache row without committing or rolling back the caller's transaction."""
    params = (key, signature, payload, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    sql = "INSERT OR REPLACE INTO aggregate_cache (cache_key, signature, payload_json, updated_at) VALUES (?, ?, ?, ?)"
    if conn.in_transaction:
        # The caller holds uncommitted writes (and the write lock): committing
        # or rolling back here would change their transaction, and another
        # connection would only wait for the lock. Serve uncached instead.
        return
    path = _database_file(conn)
    if path:
        # A separate connection keeps cache bookkeeping out of the caller's
        # connection state entirely.
        writer = sqlite3.connect(path, timeout=_WRITE_BUSY_TIMEOUT_MS / 1000)
        try:
            writer.execute(sql, params)
            writer.commit()
        finally:
            writer.close()
    else:
        # In-memory database: only reachable through this connection.
        try:
            conn.execute(sql, params)
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
            raise


def _read(conn: sqlite3.Connection, key: str, signature: str) -> tuple[bool, Any]:
    row = conn.execute(
        "SELECT signature, payload_json FROM aggregate_cache WHERE cache_key = ?", (key,)
    ).fetchone()
    if row and row["signature"] == signature:
        return True, json.loads(row["payload_json"])
    return False, None


def cached(conn: sqlite3.Connection, key: str, compute: Callable[[], Any]) -> Any:
    """Return a whole-database aggregate, recomputing only after the data changes.

    On the full Cricsheet archive these scans take minutes; the answer only
    changes on ingestion, so it is stored in aggregate_cache.
    """
    signature = _dataset_signature(conn)
    try:
        hit, value = _read(conn, key, signature)
    except sqlite3.OperationalError:  # older DB without the cache table
        return compute()
    if hit:
        return value

    with _lock_for(key):
        # Another request may have filled the cache while we waited.
        hit, value = _read(conn, key, signature)
        if hit:
            return value
        # Round-trip through JSON on a miss too, so callers get the same
        # shapes (string keys, lists not tuples) whether or not the cache was warm.
        payload = json.dumps(compute())
        value = json.loads(payload)
        try:
            _write(conn, key, signature, payload)
        except sqlite3.OperationalError as exc:
            # Read-only database, or an ingestion holds the write lock:
            # still answer, just without caching.
            logger.warning("Could not cache aggregate %r: %s", key, exc)
        return value
