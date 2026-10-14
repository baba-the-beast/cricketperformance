"""Recent-form series built from the same batting/bowling formulas as Phase 1."""

from __future__ import annotations

import sqlite3
from typing import Any

from cricket_agent.analytics.statistics import compute_batting_stats, compute_bowling_stats
from cricket_agent.dismissals import BOWLER_WICKET_KINDS_SQL, NON_DISMISSAL_KINDS_SQL


def _match_ids_for_player(conn: sqlite3.Connection, player: str) -> list[tuple[str, str | None]]:
    rows = conn.execute(
        """
        SELECT m.match_id, m.match_date
        FROM matches m
        WHERE m.match_id IN (
            SELECT match_id FROM deliveries WHERE batter = ?
            UNION
            SELECT match_id FROM deliveries WHERE bowler = ?
        )
        ORDER BY m.match_date ASC, m.match_id ASC
        """,
        (player, player),
    ).fetchall()
    return [(r["match_id"], r["match_date"]) for r in rows]


def batting_form_by_match(
    conn: sqlite3.Connection,
    player: str,
    match_ids: list[str] | None = None,
    *,
    last_n: int | None = None,
) -> list[dict[str, Any]]:
    """Per-match batting figures, oldest first.

    last_n keeps only the most recent n matches in which the player
    batted; it walks the window newest-first and stops early, so
    a career-length window costs n matches of queries, not hundreds.
    """
    if match_ids is None:
        match_ids = [m for m, _ in _match_ids_for_player(conn, player)]
    series: list[dict[str, Any]] = []
    for match_id in reversed(match_ids):
        if last_n is not None and len(series) >= last_n:
            break
        batting_rows = conn.execute(
            """
            SELECT * FROM deliveries
            WHERE batter = ? AND runs_wides = 0 AND match_id = ?
            """,
            (player, match_id),
        ).fetchall()
        if not batting_rows:
            continue
        innings_count = conn.execute(
            """
            SELECT COUNT(DISTINCT match_id || '-' || innings_index) AS n
            FROM deliveries
            WHERE batter = ? AND match_id = ?
            """,
            (player, match_id),
        ).fetchone()["n"]
        dismissals = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM wickets
            JOIN deliveries ON wickets.delivery_pk = deliveries.delivery_pk
            WHERE wickets.player_out = ? 
              AND wickets.kind NOT IN ({NON_DISMISSAL_KINDS_SQL})
              AND deliveries.match_id = ?
            """,
            (player, match_id),
        ).fetchone()["n"]
        stats = compute_batting_stats(batting_rows, dismissals, innings_count).to_dict()
        date = conn.execute(
            "SELECT match_date FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()
        series.append(
            {
                "match_id": match_id,
                "match_date": date["match_date"] if date else None,
                "runs": stats["runs"],
                "balls_faced": stats["balls_faced"],
                "strike_rate": stats["strike_rate"],
                "dismissals": stats["dismissals"],
                "fours": stats["fours"],
                "sixes": stats["sixes"],
            }
        )
    series.reverse()
    return series


def bowling_form_by_match(
    conn: sqlite3.Connection,
    player: str,
    match_ids: list[str] | None = None,
    *,
    last_n: int | None = None,
) -> list[dict[str, Any]]:
    """Per-match bowling figures, oldest first.

    last_n keeps only the most recent n matches in which the player
    bowled; it walks the window newest-first and stops early, so
    a career-length window costs n matches of queries, not hundreds.
    """
    if match_ids is None:
        match_ids = [m for m, _ in _match_ids_for_player(conn, player)]
    series: list[dict[str, Any]] = []
    for match_id in reversed(match_ids):
        if last_n is not None and len(series) >= last_n:
            break
        bowling_rows = conn.execute(
            "SELECT * FROM deliveries WHERE bowler = ? AND match_id = ?",
            (player, match_id),
        ).fetchall()
        if not bowling_rows:
            continue
        wickets = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM wickets
            JOIN deliveries ON wickets.delivery_pk = deliveries.delivery_pk
            WHERE deliveries.bowler = ?
              AND wickets.kind IN ({BOWLER_WICKET_KINDS_SQL})
              AND deliveries.match_id = ?
            """,
            (player, match_id),
        ).fetchone()["n"]
        stats = compute_bowling_stats(bowling_rows, wickets).to_dict()
        date = conn.execute(
            "SELECT match_date FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()
        series.append(
            {
                "match_id": match_id,
                "match_date": date["match_date"] if date else None,
                "wickets": stats["wickets"],
                "runs_conceded": stats["runs_conceded"],
                "legal_deliveries": stats["legal_deliveries"],
                "economy": stats["economy"],
                "dot_balls": stats["dot_balls"],
            }
        )
    series.reverse()
    return series


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return variance ** 0.5


def summarize_batting_form(series: list[dict[str, Any]], last_n: int = 5) -> dict[str, Any]:
    window = series[-last_n:] if last_n else series
    runs = [float(p["runs"]) for p in window]
    strike_rates = [float(p["strike_rate"]) for p in window if p["strike_rate"] is not None]
    rolling_avg = _mean(runs)
    consistency = _stdev(runs)
    trend = _trend_statement(runs, "batting output (runs)")
    recent_sr = _mean(strike_rates)
    return {
        "last_n": len(window),
        "matches": window,
        "rolling_average_runs": rolling_avg,
        "consistency_stdev_runs": consistency,
        "recent_strike_rate": recent_sr,
        "trend": trend,
        "available": bool(window),
        "note": (
            None
            if window
            else "Insufficient data: this player has no batting innings in the selected sample."
        ),
    }


def summarize_bowling_form(series: list[dict[str, Any]], last_n: int = 5) -> dict[str, Any]:
    window = series[-last_n:] if last_n else series
    wickets = [float(p["wickets"]) for p in window]
    economies = [float(p["economy"]) for p in window if p["economy"] is not None]
    trend = _trend_statement(economies, "bowling economy", lower_is_better=True)
    return {
        "last_n": len(window),
        "matches": window,
        "recent_wickets": wickets,
        "rolling_average_wickets": _mean(wickets),
        "recent_economy": _mean(economies),
        "consistency_stdev_economy": _stdev(economies),
        "trend": trend,
        "available": bool(window),
        "note": (
            None
            if window
            else "Insufficient data: this player has no bowling in the selected sample."
        ),
    }


def _trend_statement(
    values: list[float], label: str, *, lower_is_better: bool = False
) -> dict[str, Any]:
    if len(values) < 2:
        return {
            "direction": "insufficient",
            "statement": (
                f"Insufficient data to describe a {label} trend "
                "(need at least two matches in the sample)."
            ),
        }
    first_half = _mean(values[: max(1, len(values) // 2)])
    second_half = _mean(values[max(1, len(values) // 2) :])
    assert first_half is not None and second_half is not None
    delta = second_half - first_half
    improved = delta < 0 if lower_is_better else delta > 0
    declined = delta > 0 if lower_is_better else delta < 0
    if abs(delta) < 1e-9:
        direction = "stable"
        statement = f"{label.capitalize()} is essentially unchanged across the selected sample."
    elif improved:
        direction = "improving"
        statement = f"Recent {label} has improved over the selected sample."
    else:
        direction = "declining"
        statement = f"Recent {label} has declined over the selected sample."
        if declined:
            pass
    return {
        "direction": direction,
        "statement": statement,
        "first_half_mean": first_half,
        "second_half_mean": second_half,
    }
