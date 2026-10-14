"""Phase-wise batting and bowling using over numbers (T20 convention)."""

from __future__ import annotations

import sqlite3
from typing import Any

from cricket_agent.analytics.statistics import compute_batting_stats, compute_bowling_stats
from cricket_agent.dismissals import BOWLER_WICKET_KINDS_SQL, NON_DISMISSAL_KINDS_SQL

# 0-based over numbers, T20: powerplay 1-6, middle 7-15, death 16-20.
T20_PHASES = {
    "powerplay": (0, 5),
    "middle": (6, 14),
    "death": (15, 19),
}


def _match_type(conn: sqlite3.Connection, match_id: str) -> str | None:
    row = conn.execute(
        "SELECT match_type FROM matches WHERE match_id = ?", (match_id,)
    ).fetchone()
    return row["match_type"] if row else None


def phase_breakdown(
    conn: sqlite3.Connection, player: str, match_ids: list[str]
) -> dict[str, Any]:
    if not match_ids:
        return {
            "available": False,
            "note": "Insufficient data: no matches in range.",
            "phases": {},
        }

    types = {_match_type(conn, mid) for mid in match_ids}
    if types != {"T20"}:
        return {
            "available": False,
            "note": (
                "Phase-wise splits are only defined here for T20 matches "
                "(powerplay overs 1-6, middle 7-15, death 16-20 using "
                "0-based over numbers). The selected sample includes "
                f"match type(s) {sorted(t for t in types if t)}. "
                "Not available for this range."
            ),
            "phases": {},
            "match_types": sorted(t for t in types if t),
        }

    phases: dict[str, Any] = {}
    for name, (lo, hi) in T20_PHASES.items():
        batting_rows = conn.execute(
            f"""
            SELECT * FROM deliveries
            WHERE batter = ? AND runs_wides = 0
              AND over_number BETWEEN ? AND ?
              AND match_id IN ({",".join("?" for _ in match_ids)})
            """,
            (player, lo, hi, *match_ids),
        ).fetchall()
        dismissals = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM wickets
            JOIN deliveries ON wickets.delivery_pk = deliveries.delivery_pk
            WHERE wickets.player_out = ?
              AND wickets.kind NOT IN ({NON_DISMISSAL_KINDS_SQL})
              AND deliveries.over_number BETWEEN ? AND ?
              AND deliveries.match_id IN ({",".join("?" for _ in match_ids)})
            """,
            (player, lo, hi, *match_ids),
        ).fetchone()["n"]
        innings = conn.execute(
            f"""
            SELECT COUNT(DISTINCT match_id || '-' || innings_index) AS n
            FROM deliveries
            WHERE batter = ? AND over_number BETWEEN ? AND ?
              AND match_id IN ({",".join("?" for _ in match_ids)})
            """,
            (player, lo, hi, *match_ids),
        ).fetchone()["n"]
        bowling_rows = conn.execute(
            f"""
            SELECT * FROM deliveries
            WHERE bowler = ? AND over_number BETWEEN ? AND ?
              AND match_id IN ({",".join("?" for _ in match_ids)})
            """,
            (player, lo, hi, *match_ids),
        ).fetchall()
        bowling_wickets = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM wickets
            JOIN deliveries ON wickets.delivery_pk = deliveries.delivery_pk
            WHERE deliveries.bowler = ?
              AND wickets.kind IN ({BOWLER_WICKET_KINDS_SQL})
              AND deliveries.over_number BETWEEN ? AND ?
              AND deliveries.match_id IN ({",".join("?" for _ in match_ids)})
            """,
            (player, lo, hi, *match_ids),
        ).fetchone()["n"]

        batting = (
            compute_batting_stats(batting_rows, dismissals, innings).to_dict()
            if batting_rows
            else None
        )
        bowling = (
            compute_bowling_stats(bowling_rows, bowling_wickets).to_dict()
            if bowling_rows
            else None
        )
        phases[name] = {
            "overs": f"{lo + 1}-{hi + 1}",
            "batting": batting,
            "bowling": bowling,
        }

    return {
        "available": True,
        "convention": "T20: powerplay 1-6, middle 7-15, death 16-20",
        "phases": phases,
        "note": None,
    }
