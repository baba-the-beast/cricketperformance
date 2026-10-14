"""Run specialist analysts in parallel, then the coordinator."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from cricket_agent.agents import coordinator as coordinator_mod
from cricket_agent.agents.narrator import narrate
from cricket_agent.agents.specialists import batting_analyst, bowling_analyst, fielding_analyst
from cricket_agent.analytics.player_profile import build_player_profile
from cricket_agent.database import connect
from cricket_agent.memory import (
    create_analysis_session,
    load_analysis_session,
    save_coordinator,
    save_player_snapshot,
    set_agent_run,
)

AnalystFn = Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]]


def _source_db_path(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    return (row["file"] or "").strip()


def _prepare_worker_db_path(source: sqlite3.Connection) -> tuple[Path, Path | None]:
    """Path each specialist thread should open, plus an optional temp file to delete.

    File-backed databases are opened directly (one connection per worker).
    In-memory databases cannot be shared across connections, so the caller
    snapshots them to a temporary file on the main thread first.
    """
    existing = _source_db_path(source)
    if existing:
        return Path(existing), None

    fd, name = tempfile.mkstemp(prefix="cricket-agent-", suffix=".db")
    os.close(fd)
    tmp = Path(name)
    replica = connect(tmp)
    try:
        source.backup(replica)
        replica.commit()
    finally:
        replica.close()
    return tmp, tmp


def _run_specialist_on_own_connection(
    db_path: Path,
    analyst: AnalystFn,
    profiles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    worker = connect(db_path)
    try:
        return [analyst(worker, profile["player"], profile) for profile in profiles]
    finally:
        worker.close()


def run_specialists_parallel(
    source: sqlite3.Connection,
    profiles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Execute batting, bowling, and fielding analysts concurrently.

    Each specialist uses its own SQLite connection. Session/memory writes
    stay on the caller's connection after this returns.
    """
    db_path, tmp_to_clean = _prepare_worker_db_path(source)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            batting_future = pool.submit(
                _run_specialist_on_own_connection, db_path, batting_analyst, profiles
            )
            bowling_future = pool.submit(
                _run_specialist_on_own_connection, db_path, bowling_analyst, profiles
            )
            fielding_future = pool.submit(
                _run_specialist_on_own_connection, db_path, fielding_analyst, profiles
            )
            batting_reports = batting_future.result()
            bowling_reports = bowling_future.result()
            fielding_reports = fielding_future.result()
    finally:
        if tmp_to_clean is not None:
            tmp_to_clean.unlink(missing_ok=True)

    return batting_reports, bowling_reports, fielding_reports



def run_analysis(
    conn: sqlite3.Connection,
    players: list[str],
    question: str,
    *,
    selected_match: str | None = None,
    last_n_matches: int | None = None,
    opponent: str | None = None,
) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    create_analysis_session(
        conn,
        session_id,
        question,
        selected_players=players,
        selected_match=selected_match,
        last_n_matches=last_n_matches,
        opponent=opponent,
    )

    for name in ("Batting Analyst", "Bowling Analyst", "Fielding Analyst", "Coordinator"):
        set_agent_run(conn, session_id, name, "QUEUED")

    profiles = []
    for player in players:
        profile = build_player_profile(conn, player, last_n_matches=last_n_matches)
        profiles.append(profile)
        save_player_snapshot(
            conn,
            player,
            {
                "matches_analyzed": profile["matches_considered"],
                "batting": profile["batting"],
                "bowling": profile["bowling"],
                "recent_form": {
                    "batting": profile["batting_form"]["trend"],
                    "bowling": profile["bowling_form"]["trend"],
                },
                "role": profile["role"]["primary"],
                "matchups": profile["matchups"],
                "confidence": profile["confidence"],
            },
        )

    for name in ("Batting Analyst", "Bowling Analyst", "Fielding Analyst"):
        set_agent_run(conn, session_id, name, "RUNNING")

    batting_reports, bowling_reports, fielding_reports = run_specialists_parallel(
        conn, profiles
    )

    def _status_for(reports: list[dict[str, Any]]) -> str:
        if any(r.get("confidence") == "LOW" for r in reports):
            return "LOW CONFIDENCE"
        return "COMPLETED"

    set_agent_run(conn, session_id, "Batting Analyst", _status_for(batting_reports), {"reports": batting_reports})
    set_agent_run(conn, session_id, "Bowling Analyst", _status_for(bowling_reports), {"reports": bowling_reports})
    set_agent_run(conn, session_id, "Fielding Analyst", _status_for(fielding_reports), {"reports": fielding_reports})

    set_agent_run(conn, session_id, "Coordinator", "RUNNING")
    report = coordinator_mod.synthesize(
        question=question,
        players=players,
        batting_reports=batting_reports,
        bowling_reports=bowling_reports,
        fielding_reports=fielding_reports,
        role_reports=[p["role"] for p in profiles],
        opponent=opponent,
        last_n_matches=last_n_matches,
    )
    # The verdict above is deterministic; the LLM only explains it (or the
    # rule-based summary stands in when no model is available).
    report["narrative"] = narrate(report)
    save_coordinator(conn, session_id, report)
    set_agent_run(conn, session_id, "Coordinator", "COMPLETED", report)

    stored = load_analysis_session(conn, session_id)
    return {
        "session_id": session_id,
        "profiles": profiles,
        "report": report,
        "session": stored,
        "historical_memory": [
            {"player": p["player"], "confidence": p["confidence"]} for p in profiles
        ],
        "current_question": question,
        "channel": "in-process",
        "channel_note": (
            "Specialists called Python analytics functions in this process. "
            "This was not an MCP stdio round-trip."
        ),
    }
