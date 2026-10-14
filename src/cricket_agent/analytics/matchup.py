"""Batter-versus-bowler aggregates from stored deliveries only."""

from __future__ import annotations

import sqlite3
from typing import Any

from cricket_agent.analytics.confidence import matchup_confidence
from cricket_agent.exceptions import PlayerNotFoundError
from cricket_agent.dismissals import BOWLER_WICKET_KINDS_SQL, NON_DISMISSAL_KINDS_SQL


def batter_vs_bowler(
    conn: sqlite3.Connection, batter: str, bowler: str, match_ids: list[str] | None = None
) -> dict[str, Any]:
    sql = """
        SELECT * FROM deliveries
        WHERE batter = ? AND bowler = ?
    """
    params: list[Any] = [batter, bowler]
    if match_ids:
        sql += f" AND match_id IN ({','.join('?' for _ in match_ids)})"
        params.extend(match_ids)

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        # Distinguish unknown names from a real pair with zero overlap.
        any_batter = conn.execute(
            "SELECT 1 FROM deliveries WHERE batter = ? LIMIT 1", (batter,)
        ).fetchone()
        any_bowler = conn.execute(
            "SELECT 1 FROM deliveries WHERE bowler = ? LIMIT 1", (bowler,)
        ).fetchone()
        if not any_batter:
            raise PlayerNotFoundError(batter)
        if not any_bowler:
            raise PlayerNotFoundError(bowler)
        return {
            "batter": batter,
            "bowler": bowler,
            "available": False,
            "balls_faced": 0,
            "note": (
                f"Insufficient data: {batter} and {bowler} have no recorded "
                "deliveries against each other in the current dataset."
            ),
            "confidence": matchup_confidence(balls=0, dismissals=0),
        }

    faced = [r for r in rows if int(r["runs_wides"]) == 0]
    runs = sum(int(r["runs_batter"]) for r in faced)
    balls = len(faced)
    dots = sum(1 for r in faced if int(r["runs_batter"]) == 0 and int(r["runs_total"]) == 0)
    fours = sum(1 for r in faced if int(r["runs_batter"]) == 4)
    sixes = sum(1 for r in faced if int(r["runs_batter"]) == 6)
    dismissals = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM wickets
        JOIN deliveries ON wickets.delivery_pk = deliveries.delivery_pk
        WHERE deliveries.batter = ? AND deliveries.bowler = ?
          AND wickets.player_out = ?
          AND wickets.kind NOT IN ({NON_DISMISSAL_KINDS_SQL})
        """
        + (
            f" AND deliveries.match_id IN ({','.join('?' for _ in match_ids)})"
            if match_ids
            else ""
        ),
        (batter, bowler, batter, *(match_ids or [])),
    ).fetchone()["n"]

    strike_rate = (runs / balls * 100) if balls else None
    dot_pct = (dots / balls * 100) if balls else None
    confidence = matchup_confidence(balls=balls, dismissals=dismissals)

    return {
        "batter": batter,
        "bowler": bowler,
        "available": True,
        "deliveries_including_wides": len(rows),
        "balls_faced": balls,
        "runs": runs,
        "dismissals": dismissals,
        "strike_rate": strike_rate,
        "dot_percentage": dot_pct,
        "fours": fours,
        "sixes": sixes,
        "confidence": confidence,
        "note": None if confidence["level"] != "LOW" else confidence["reason"],
    }


def player_vs_team(
    conn: sqlite3.Connection, player: str, opponent_team: str
) -> dict[str, Any]:
    """A player's aggregate batting and bowling record against one named team.

    "Against" is resolved via the opponent's known roster (the `players`
    table), not via match winner/loser -- a player can face part of a
    squad across several matches even if the team lineup changed. If the
    opponent team is not in the `players` table at all, this is reported
    as unavailable rather than silently returning zeros.
    """
    from cricket_agent.analytics.statistics import compute_batting_stats, compute_bowling_stats

    roster = [
        r["player_name"]
        for r in conn.execute(
            "SELECT player_name FROM players WHERE team = ?", (opponent_team,)
        ).fetchall()
    ]
    if not roster:
        return {
            "player": player,
            "opponent_team": opponent_team,
            "available": False,
            "note": (
                f"No roster is recorded for '{opponent_team}' in this dataset, "
                "so a team-level matchup cannot be computed."
            ),
        }

    placeholders = ",".join("?" for _ in roster)

    batting_rows = conn.execute(
        f"SELECT * FROM deliveries WHERE batter = ? AND runs_wides = 0 "
        f"AND bowler IN ({placeholders})",
        (player, *roster),
    ).fetchall()
    innings_count = conn.execute(
        f"SELECT COUNT(DISTINCT match_id || '-' || innings_index) AS n "
        f"FROM deliveries WHERE batter = ? AND bowler IN ({placeholders})",
        (player, *roster),
    ).fetchone()["n"]
    dismissals = conn.execute(
        f"SELECT COUNT(*) AS n FROM wickets JOIN deliveries "
        f"ON wickets.delivery_pk = deliveries.delivery_pk "
        f"WHERE wickets.player_out = ? AND wickets.kind NOT IN ({NON_DISMISSAL_KINDS_SQL}) AND deliveries.bowler IN ({placeholders})",
        (player, *roster),
    ).fetchone()["n"]

    bowling_rows = conn.execute(
        f"SELECT * FROM deliveries WHERE bowler = ? AND batter IN ({placeholders})",
        (player, *roster),
    ).fetchall()
    bowling_wickets = conn.execute(
        f"SELECT COUNT(*) AS n FROM wickets JOIN deliveries "
        f"ON wickets.delivery_pk = deliveries.delivery_pk "
        f"WHERE deliveries.bowler = ? AND wickets.kind IN ({BOWLER_WICKET_KINDS_SQL}) "
        f"AND deliveries.batter IN ({placeholders})",
        (player, *roster),
    ).fetchone()["n"]

    if not batting_rows and not bowling_rows:
        return {
            "player": player,
            "opponent_team": opponent_team,
            "available": False,
            "note": (
                f"{player} has no recorded deliveries against {opponent_team}'s "
                "known roster in this dataset."
            ),
        }

    batting = (
        compute_batting_stats(batting_rows, dismissals, innings_count).to_dict()
        if batting_rows
        else None
    )
    bowling = (
        compute_bowling_stats(bowling_rows, bowling_wickets).to_dict()
        if bowling_rows
        else None
    )
    sample = (batting["balls_faced"] if batting else 0) + (
        bowling["legal_deliveries"] if bowling else 0
    )
    confidence = matchup_confidence(balls=sample, dismissals=dismissals)

    return {
        "player": player,
        "opponent_team": opponent_team,
        "available": True,
        "batting": batting,
        "bowling": bowling,
        "confidence": confidence,
        "note": None if confidence["level"] != "LOW" else confidence["reason"],
    }


def frequent_opponents(
    conn: sqlite3.Connection, batter: str, match_ids: list[str], limit: int = 5
) -> list[dict[str, Any]]:
    if not match_ids:
        return []
    placeholders = ",".join("?" for _ in match_ids)
    rows = conn.execute(
        f"""
        SELECT bowler, COUNT(*) AS n
        FROM deliveries
        WHERE batter = ? AND match_id IN ({placeholders})
        GROUP BY bowler
        ORDER BY n DESC
        LIMIT ?
        """,
        (batter, *match_ids, limit),
    ).fetchall()
    return [batter_vs_bowler(conn, batter, r["bowler"], match_ids) for r in rows]

def pace_vs_spin_matchup(conn: sqlite3.Connection, player: str, match_ids: list[str] | None = None) -> dict[str, Any]:
    '''Aggregate a batter's record against pace vs spin.

    Cricsheet does not record bowling style, so this depends on
    players.bowling_style being filled from another source. Deliveries from
    bowlers with no known style are counted as 'Unknown' and reported as
    coverage; no verdict is given unless most balls have a known style.
    match_ids=None means the whole career; an empty list means an empty
    window and returns no data (it must not widen to all history).
    '''
    result: dict[str, Any] = {"Pace": None, "Spin": None, "Unknown": None}
    if match_ids is not None and not match_ids:
        return {"player": player, "matchup_stats": result, "coverage": None,
                "weakness_analysis": "Not enough data"}

    window = ""
    if match_ids is not None:
        window = f" AND d.match_id IN ({','.join('?' for _ in match_ids)})"
    sql = f'''
        SELECT
            CASE
                WHEN p.bowling_style LIKE '%fast%' OR p.bowling_style LIKE '%medium%' OR p.bowling_style LIKE '%pace%' THEN 'Pace'
                WHEN p.bowling_style LIKE '%spin%' OR p.bowling_style LIKE '%break%' OR p.bowling_style LIKE '%orthodox%' OR p.bowling_style LIKE '%chinaman%' THEN 'Spin'
                ELSE 'Unknown'
            END as style,
            SUM(CASE WHEN d.runs_wides = 0 THEN d.runs_batter ELSE 0 END) AS runs,
            SUM(CASE WHEN d.runs_wides = 0 THEN 1 ELSE 0 END) AS balls_faced,
            SUM(CASE WHEN w.wicket_pk IS NOT NULL AND w.player_out = ? THEN 1 ELSE 0 END) AS dismissals
        FROM deliveries d
        LEFT JOIN players p ON d.bowler = p.player_name
        LEFT JOIN wickets w ON d.delivery_pk = w.delivery_pk
            AND w.kind IN ({BOWLER_WICKET_KINDS_SQL})
        WHERE d.batter = ? {window}
        GROUP BY style
    '''
    # Only bowler-credited dismissals say anything about pace vs spin (a
    # run-out does not), and they count off every delivery: a stumping off a
    # wide is still the bowler's wicket. Balls faced exclude wides.
    # player twice: once for w.player_out = ?, once for d.batter = ?
    rows = conn.execute(sql, [player, player, *(match_ids or [])]).fetchall()

    for r in rows:
        balls, runs, dismissals = r["balls_faced"], r["runs"], r["dismissals"]
        result[r["style"]] = {
            "runs": runs,
            "balls_faced": balls,
            "dismissals": dismissals,
            "strike_rate": (runs / balls * 100) if balls > 0 else None,
            # No dismissals -> no average (a not-out total is not an average).
            "average": (runs / dismissals) if dismissals > 0 else None,
        }

    total = sum(v["balls_faced"] for v in result.values() if v)
    known = sum(result[k]["balls_faced"] for k in ("Pace", "Spin") if result[k])
    coverage = known / total if total else None

    weakness = "Not enough data"
    pace, spin = result["Pace"], result["Spin"]
    if (
        coverage is not None and coverage >= 0.6
        and pace and spin
        and pace["balls_faced"] > 30 and spin["balls_faced"] > 30
        and pace["average"] is not None and spin["average"] is not None
    ):
        if pace["average"] < spin["average"] * 0.75:
            weakness = "Weak against Pace"
        elif spin["average"] < pace["average"] * 0.75:
            weakness = "Weak against Spin"
        else:
            weakness = "Balanced"

    return {
        "player": player,
        "matchup_stats": result,
        "coverage": coverage,
        "weakness_analysis": weakness,
    }
