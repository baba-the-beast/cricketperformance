"""Long-term player snapshots and analysis-session persistence in SQLite."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_player_snapshot(conn: sqlite3.Connection, player: str, snapshot: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO player_memory (player_name, snapshot_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(player_name) DO UPDATE SET
            snapshot_json = excluded.snapshot_json,
            updated_at = excluded.updated_at
        """,
        (player, json.dumps(snapshot), _now()),
    )
    conn.commit()


def load_player_snapshot(conn: sqlite3.Connection, player: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT snapshot_json, updated_at FROM player_memory WHERE player_name = ?",
        (player,),
    ).fetchone()
    if not row:
        return None
    data = json.loads(row["snapshot_json"])
    data["memory_updated_at"] = row["updated_at"]
    return data


def list_player_memory(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT player_name, updated_at FROM player_memory ORDER BY updated_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def create_analysis_session(
    conn: sqlite3.Connection,
    session_id: str,
    question: str,
    *,
    selected_players: list[str],
    selected_match: str | None = None,
    last_n_matches: int | None = None,
    opponent: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO analysis_sessions (
            session_id, question, selected_players, selected_match,
            last_n_matches, opponent, coordinator_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            session_id,
            question,
            json.dumps(selected_players),
            selected_match,
            last_n_matches,
            opponent,
            _now(),
        ),
    )
    conn.commit()


def set_agent_run(
    conn: sqlite3.Connection,
    session_id: str,
    agent_name: str,
    status: str,
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_runs (session_id, agent_name, status, payload_json, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            session_id,
            agent_name,
            status,
            json.dumps(payload) if payload is not None else None,
            _now(),
        ),
    )
    conn.commit()


def save_coordinator(conn: sqlite3.Connection, session_id: str, report: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE analysis_sessions SET coordinator_json = ? WHERE session_id = ?",
        (json.dumps(report), session_id),
    )
    conn.commit()


def load_analysis_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM analysis_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row:
        return None
    runs = conn.execute(
        """
        SELECT agent_name, status, payload_json, updated_at
        FROM agent_runs
        WHERE session_id = ?
        ORDER BY run_pk ASC
        """,
        (session_id,),
    ).fetchall()
    return {
        "session_id": row["session_id"],
        "question": row["question"],
        "selected_players": json.loads(row["selected_players"] or "[]"),
        "selected_match": row["selected_match"],
        "last_n_matches": row["last_n_matches"],
        "opponent": row["opponent"],
        "created_at": row["created_at"],
        "coordinator": json.loads(row["coordinator_json"]) if row["coordinator_json"] else None,
        "agent_runs": [
            {
                "agent": r["agent_name"],
                "status": r["status"],
                "payload": json.loads(r["payload_json"]) if r["payload_json"] else None,
                "updated_at": r["updated_at"],
            }
            for r in runs
        ],
    }


def recent_sessions(conn: sqlite3.Connection, limit: int = 8) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT session_id, question, created_at FROM analysis_sessions
        ORDER BY created_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
