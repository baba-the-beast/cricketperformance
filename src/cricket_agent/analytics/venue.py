"""Venue ("ground") analytics: historical scoring patterns and player-at-venue form.

Term: Pitch profile
    Cricsheet does not record pitch composition (soil, grass cover, etc.),
    so this project never invents "green top" / "turner" labels. Instead
    it characterises a ground the only honest way available: from the
    runs and wickets actually recorded there in the ingested dataset.
    This mirrors the existing `/api/venues` dashboard query so the two
    surfaces cannot silently disagree.

Term: Player-at-venue form
    A player's ordinary batting/bowling statistics (analytics/statistics.py),
    computed only from deliveries at matches played at one named venue.
    It reuses the same deterministic engine as compute_stats() -- there is
    no second formula for "venue form".
"""

from __future__ import annotations

import sqlite3
from typing import Any

from cricket_agent.analytics.cache import cached
from cricket_agent.analytics.statistics import compute_batting_stats, compute_bowling_stats
from cricket_agent.dismissals import BOWLER_WICKET_KINDS_SQL, NON_DISMISSAL_KINDS_SQL

LOW_MATCH_THRESHOLD = 3
"""Below this many matches at a venue, characterisation is LOW confidence."""


def venue_profile(conn: sqlite3.Connection, venue: str) -> dict[str, Any]:
    """Aggregate scoring history for one venue from ingested matches only.

    Output includes average first/second innings score, boundary rate,
    wicket rate, and a plain-language characterisation. Returns
    available=False (not an error) if the venue string does not match
    any ingested match -- an unknown/mistyped venue is a normal input,
    not an exceptional one.
    """
    match_count = conn.execute(
        "SELECT COUNT(*) AS n FROM matches WHERE venue = ?", (venue,)
    ).fetchone()["n"]
    if not match_count:
        return {
            "venue": venue,
            "available": False,
            "match_count": 0,
            "note": (
                f"No ingested matches recorded at '{venue}'. Pitch history "
                "cannot be characterised for this ground yet."
            ),
        }

    row = conn.execute(
        f"""
        WITH innings_totals AS (
            SELECT m.venue, d.match_id, d.innings_index,
                   SUM(d.runs_total) AS score,
                   COUNT(*) AS deliveries,
                   SUM(CASE WHEN d.runs_batter IN (4, 6) THEN 1 ELSE 0 END) AS boundaries,
                   COUNT(CASE WHEN w.kind NOT IN ({NON_DISMISSAL_KINDS_SQL}) THEN 1 END) AS wickets
            FROM matches m
            JOIN deliveries d ON d.match_id = m.match_id
            LEFT JOIN wickets w ON w.delivery_pk = d.delivery_pk
            WHERE m.venue = ?
            GROUP BY m.venue, d.match_id, d.innings_index
        )
        SELECT COUNT(DISTINCT match_id) AS match_count,
               AVG(CASE WHEN innings_index = 0 THEN score END) AS avg_first_innings,
               AVG(CASE WHEN innings_index = 1 THEN score END) AS avg_second_innings,
               CAST(SUM(boundaries) AS REAL) / NULLIF(SUM(deliveries), 0) AS boundary_rate,
               CAST(SUM(wickets) AS REAL) / NULLIF(SUM(deliveries), 0) AS wicket_rate
        FROM innings_totals
        """,
        (venue,),
    ).fetchone()

    # Compare first innings with first innings, format by format: a T20
    # ground must not be judged against Test or super-over innings. The
    # dataset baseline is a full scan of deliveries, identical for every
    # venue, so it is cached.
    baselines: dict[str, float] = cached(
        conn,
        "first_innings_avg_by_format",
        lambda: {
            # JSON keys are strings; "" stands for a missing match_type.
            r["match_type"] or "": r["avg_score"]
            for r in conn.execute(
                """
                SELECT m.match_type, AVG(t.score) AS avg_score
                FROM (
                    SELECT d.match_id, SUM(d.runs_total) AS score
                    FROM deliveries d
                    WHERE d.innings_index = 0
                    GROUP BY d.match_id
                ) t
                JOIN matches m ON m.match_id = t.match_id
                GROUP BY m.match_type
                """
            )
        },
    )
    venue_formats = conn.execute(
        """
        SELECT m.match_type, COUNT(DISTINCT d.match_id) AS n
        FROM matches m
        JOIN deliveries d ON d.match_id = m.match_id AND d.innings_index = 0
        WHERE m.venue = ?
        GROUP BY m.match_type
        """,
        (venue,),
    ).fetchall()
    # Expected first-innings score here if the ground were average for the
    # mix of formats actually played on it.
    weighted = [(r["n"], baselines.get(r["match_type"] or "")) for r in venue_formats]
    weighted = [(n, b) for n, b in weighted if b is not None]
    total = sum(n for n, _ in weighted)
    expected_first = sum(n * b for n, b in weighted) / total if total else None

    avg_first = row["avg_first_innings"]
    characterisation = "Unclear from the current sample."
    if avg_first is not None and expected_first:
        if avg_first >= expected_first * 1.1:
            characterisation = "Historically high-scoring relative to the rest of the dataset."
        elif avg_first <= expected_first * 0.9:
            characterisation = "Historically low-scoring relative to the rest of the dataset."
        else:
            characterisation = "Scoring here is close to the dataset-wide average."

    confidence = "LOW" if match_count < LOW_MATCH_THRESHOLD else (
        "MEDIUM" if match_count < LOW_MATCH_THRESHOLD * 2 else "HIGH"
    )

    return {
        "venue": venue,
        "available": True,
        "match_count": match_count,
        "average_first_innings_score": avg_first,
        "average_second_innings_score": row["avg_second_innings"],
        "boundary_rate": row["boundary_rate"],
        "wicket_rate": row["wicket_rate"],
        "expected_first_innings_score": expected_first,
        "characterisation": characterisation,
        "confidence": confidence,
        "note": (
            f"Based on {match_count} ingested match(es) at this venue. "
            + (
                "Treat this characterisation as low-confidence; the sample is small."
                if confidence == "LOW"
                else "Sample size supports this characterisation within this dataset."
            )
        ),
        "data_note": (
            "No pitch composition (soil/grass) is recorded by Cricsheet. This is a "
            "scoring-pattern summary of matches actually played here, not a groundsman's report."
        ),
    }


def player_form_at_venue(conn: sqlite3.Connection, player: str, venue: str) -> dict[str, Any]:
    """A player's batting/bowling record restricted to matches at one venue."""
    match_ids = [
        r["match_id"]
        for r in conn.execute(
            "SELECT match_id FROM matches WHERE venue = ?", (venue,)
        ).fetchall()
    ]
    if not match_ids:
        return {
            "player": player,
            "venue": venue,
            "available": False,
            "matches_considered": 0,
            "note": f"No ingested matches at '{venue}' to draw a venue-specific record from.",
        }

    placeholders = ",".join("?" for _ in match_ids)

    batting_rows = conn.execute(
        f"SELECT * FROM deliveries WHERE batter = ? AND runs_wides = 0 "
        f"AND match_id IN ({placeholders})",
        (player, *match_ids),
    ).fetchall()
    innings_count = conn.execute(
        f"SELECT COUNT(DISTINCT match_id || '-' || innings_index) AS n "
        f"FROM deliveries WHERE batter = ? AND match_id IN ({placeholders})",
        (player, *match_ids),
    ).fetchone()["n"]
    dismissals = conn.execute(
        f"SELECT COUNT(*) AS n FROM wickets JOIN deliveries "
        f"ON wickets.delivery_pk = deliveries.delivery_pk "
        f"WHERE wickets.player_out = ? AND wickets.kind NOT IN ({NON_DISMISSAL_KINDS_SQL}) AND deliveries.match_id IN ({placeholders})",
        (player, *match_ids),
    ).fetchone()["n"]

    bowling_rows = conn.execute(
        f"SELECT * FROM deliveries WHERE bowler = ? AND match_id IN ({placeholders})",
        (player, *match_ids),
    ).fetchall()
    bowling_wickets = conn.execute(
        f"SELECT COUNT(*) AS n FROM wickets JOIN deliveries "
        f"ON wickets.delivery_pk = deliveries.delivery_pk "
        f"WHERE deliveries.bowler = ? AND wickets.kind IN ({BOWLER_WICKET_KINDS_SQL}) "
        f"AND deliveries.match_id IN ({placeholders})",
        (player, *match_ids),
    ).fetchone()["n"]

    matches_present = conn.execute(
        f"SELECT DISTINCT match_id FROM deliveries "
        f"WHERE (batter = ? OR bowler = ?) AND match_id IN ({placeholders})",
        (player, player, *match_ids),
    ).fetchall()

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

    if not matches_present:
        return {
            "player": player,
            "venue": venue,
            "available": False,
            "matches_considered": 0,
            "note": f"{player} has no recorded appearances at '{venue}' in this dataset.",
        }

    n = len(matches_present)
    return {
        "player": player,
        "venue": venue,
        "available": True,
        "matches_considered": n,
        "batting": batting,
        "bowling": bowling,
        "confidence": "LOW" if n < LOW_MATCH_THRESHOLD else ("MEDIUM" if n < LOW_MATCH_THRESHOLD * 2 else "HIGH"),
        "note": (
            f"{n} match(es) for {player} at '{venue}'. "
            + ("Treat as low-confidence; the sample is small." if n < LOW_MATCH_THRESHOLD else "")
        ),
    }

def best_venues_for_player(
    conn: sqlite3.Connection, player: str, match_ids: list[str] | None = None, limit: int = 5
) -> dict[str, Any]:
    '''Top venues by runs scored and by wickets taken.

    match_ids=None covers the whole career; a list restricts to that window
    (an empty list returns nothing rather than widening to all history).
    '''
    if match_ids is not None and not match_ids:
        return {"player": player, "top_batting_venues": [], "top_bowling_venues": []}
    window, params = "", []
    if match_ids is not None:
        window = f" AND d.match_id IN ({','.join('?' for _ in match_ids)})"
        params = list(match_ids)

    bat_venues = conn.execute(
        f'''
        SELECT m.venue,
               SUM(d.runs_batter) AS runs,
               COUNT(*) AS balls_faced,
               COUNT(DISTINCT d.match_id) AS matches
        FROM deliveries d
        JOIN matches m ON m.match_id = d.match_id
        WHERE d.batter = ? AND d.runs_wides = 0 {window}
        GROUP BY m.venue
        ORDER BY runs DESC
        LIMIT ?
        ''', (player, *params, limit)
    ).fetchall()

    # Wickets count over every delivery (a stumping off a wide is the
    # bowler's wicket); only the ball count excludes wides and no-balls.
    bowl_venues = conn.execute(
        f'''
        SELECT m.venue,
               SUM(CASE WHEN w.kind IN ({BOWLER_WICKET_KINDS_SQL}) THEN 1 ELSE 0 END) AS wickets,
               SUM(CASE WHEN d.runs_wides = 0 AND d.runs_noballs = 0 THEN 1 ELSE 0 END) AS legal_deliveries,
               COUNT(DISTINCT d.match_id) AS matches
        FROM deliveries d
        JOIN matches m ON m.match_id = d.match_id
        LEFT JOIN wickets w ON w.delivery_pk = d.delivery_pk
        WHERE d.bowler = ? {window}
        GROUP BY m.venue
        ORDER BY wickets DESC
        LIMIT ?
        ''', (player, *params, limit)
    ).fetchall()

    return {
        "player": player,
        "top_batting_venues": [dict(r) for r in bat_venues],
        "top_bowling_venues": [dict(r) for r in bowl_venues],
    }
