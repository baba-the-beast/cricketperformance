"""Player analytics facade: reuses compute_stats rather than a second engine."""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from cricket_agent.analytics.cache import cached
from cricket_agent.analytics.confidence import (
    batting_confidence,
    bowling_confidence,
    overall_from_parts,
)
from cricket_agent.analytics.fielding import fielding_credits
from cricket_agent.analytics.form import (
    batting_form_by_match,
    bowling_form_by_match,
    summarize_batting_form,
    summarize_bowling_form,
)
from cricket_agent.analytics.matchup import frequent_opponents, pace_vs_spin_matchup
from cricket_agent.analytics.phases import phase_breakdown
from cricket_agent.analytics.roles import classify_roles
from cricket_agent.analytics.venue import best_venues_for_player
from cricket_agent.tools.cricket_tools import compute_stats
from cricket_agent.dismissals import BOWLER_WICKET_KINDS_SQL

FORM_WINDOW = 5
"""Recent matches summarised in the batting/bowling form panels."""


def _enrich_batting(batting: dict[str, Any] | None) -> dict[str, Any] | None:
    if not batting:
        return None
    balls = batting["balls_faced"]
    innings = batting["innings"]
    fours = batting["fours"]
    sixes = batting["sixes"]
    batting["boundary_percentage"] = (
        (fours + sixes) / balls * 100 if balls else None
    )
    batting["runs_per_innings"] = (
        batting["runs"] / innings if innings else None
    )
    return batting


def _enrich_bowling(bowling: dict[str, Any] | None) -> dict[str, Any] | None:
    if not bowling:
        return None
    legal = bowling["legal_deliveries"]
    bowling["dot_ball_percentage"] = (
        bowling["dot_balls"] / legal * 100 if legal else None
    )
    return bowling


def build_player_profile(
    conn: sqlite3.Connection,
    player: str,
    *,
    last_n_matches: int | None = None,
    start_match: str | None = None,
    end_match: str | None = None,
) -> dict[str, Any]:
    stats = compute_stats(
        conn,
        player,
        start_match=start_match,
        end_match=end_match,
        last_n_matches=last_n_matches,
    )
    match_ids: list[str] = stats["matches_considered"]
    batting = _enrich_batting(stats["batting"])
    bowling = _enrich_bowling(stats["bowling"])

    bat_conf = (
        batting_confidence(
            matches=len(match_ids),
            innings=batting["innings"],
            balls_faced=batting["balls_faced"],
        )
        if batting
        else None
    )
    bowl_conf = (
        bowling_confidence(
            matches=len(match_ids),
            legal_deliveries=bowling["legal_deliveries"],
            wickets=bowling["wickets"],
        )
        if bowling
        else None
    )
    confidence = overall_from_parts(bat_conf, bowl_conf)

    # The summaries only look at the last FORM_WINDOW matches; building the
    # series for the whole window would cost several queries per match.
    bat_series = batting_form_by_match(conn, player, match_ids, last_n=FORM_WINDOW)
    bowl_series = bowling_form_by_match(conn, player, match_ids, last_n=FORM_WINDOW)
    batting_form = summarize_batting_form(bat_series, last_n=FORM_WINDOW)
    bowling_form = summarize_bowling_form(bowl_series, last_n=FORM_WINDOW)
    fielding = fielding_credits(conn, player, match_ids)
    phases = phase_breakdown(conn, player, match_ids)
    roles = classify_roles(conn, player, match_ids, batting, bowling)
    matchups = frequent_opponents(conn, player, match_ids)
    # Same window as every other figure on the profile (no future leakage).
    pace_vs_spin = pace_vs_spin_matchup(conn, player, match_ids)
    best_venues = best_venues_for_player(conn, player, match_ids)

    team_row = conn.execute(
        "SELECT team, date_of_birth, country, batting_style, bowling_style, role, registry_id FROM players WHERE player_name = ?", (player,)
    ).fetchone()
    
    metadata = {
        "team": "Not available",
        "date_of_birth": "Not available",
        "age": "Not available",
        "country": "Not available",
        "batting_style": "Not available",
        "bowling_style": "Not available",
        "role": "Not available",
        "registry_id": "Not available"
    }

    if team_row:
        for k in ["team", "date_of_birth", "country", "batting_style", "bowling_style", "role", "registry_id"]:
            if team_row[k]:
                metadata[k] = team_row[k]
                metadata["provenance"] = "Cricsheet Database"

    from cricket_agent.metadata_provider import get_metadata_provider
    provider = get_metadata_provider()
    enriched = provider.enrich_player(player)
    if enriched:
        for k, v in enriched.items():
            if k in metadata and metadata[k] == "Not available" and v:
                metadata[k] = v
                metadata["provenance"] = "External Provider"

    # Calculate age
    dob = metadata.get("date_of_birth")
    if dob and dob != "Not available":
        try:
            from datetime import datetime
            # Age must be computed from verified date of birth and the relevant match or simulation date.
            # If we have match_ids, we can use the date of the most recent match considered.
            # Otherwise, we use current date as fallback.
            ref_date = datetime.now()
            if match_ids:
                latest_match_date = conn.execute(
                    f"SELECT MAX(match_date) as max_date FROM matches WHERE match_id IN ({','.join('?' for _ in match_ids)})", 
                    match_ids
                ).fetchone()["max_date"]
                if latest_match_date:
                    ref_date = datetime.strptime(latest_match_date, "%Y-%m-%d")
                    
            birth_date = datetime.strptime(dob, "%Y-%m-%d")
            age = ref_date.year - birth_date.year - ((ref_date.month, ref_date.day) < (birth_date.month, birth_date.day))
            metadata["age"] = age
        except Exception:
            metadata["age"] = "Age unavailable in current dataset."
    else:
        metadata["age"] = "Age unavailable in current dataset."

    if "provenance" not in metadata:
        metadata["provenance"] = "No Data"
    # "role" in the profile is the evidence-based classification (roles.py);
    # keep the role recorded in the players table under its own key.
    metadata["registered_role"] = metadata.pop("role")

    return {
        "player": player,
        **metadata,
        "matches_considered": match_ids,
        "sample_size_warning": stats["sample_size_warning"],
        "batting": batting,
        "bowling": bowling,
        "confidence": confidence,
        "batting_confidence": bat_conf,
        "bowling_confidence": bowl_conf,
        "batting_form": batting_form,
        "bowling_form": bowling_form,
        "phases": phases,
        "fielding": fielding,
        "role": roles,
        "matchups": matchups,
        "pace_vs_spin": pace_vs_spin,
        "best_venues": best_venues,
        "data_note": (
            "All figures are computed from ingested Cricsheet deliveries. "
            "Nothing here is a projection of future performance."
        ),
    }


def compare_players(
    conn: sqlite3.Connection,
    players: list[str],
    last_n_matches: int | None = None,
    *,
    profile_builder: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Side-by-side profiles. profile_builder lets a caller reuse cached profiles."""
    build = profile_builder or build_player_profile
    profiles = [build(conn, name, last_n_matches=last_n_matches) for name in players]
    keys_bat = [
        "runs",
        "average",
        "strike_rate",
        "balls_faced",
        "fours",
        "sixes",
        "boundary_percentage",
    ]
    keys_bowl = [
        "wickets",
        "economy",
        "average",
        "dot_ball_percentage",
        "legal_deliveries",
    ]
    return {
        "players": players,
        "profiles": profiles,
        "comparison": {
            "batting": {
                k: [p["batting"].get(k) if p["batting"] else None for p in profiles]
                for k in keys_bat
            },
            "bowling": {
                k: [p["bowling"].get(k) if p["bowling"] else None for p in profiles]
                for k in keys_bowl
            },
            "confidence": [p["confidence"]["level"] for p in profiles],
            "recent_form_batting": [
                p["batting_form"]["trend"]["statement"] for p in profiles
            ],
            "note": (
                "Players are shown side by side. No composite rank is computed; "
                "weights would need an explicit methodology which this project "
                "does not impose."
            ),
        },
    }


def _dataset_counts(conn: sqlite3.Connection) -> dict[str, int]:
    def compute() -> dict[str, int]:
        return {
            "matches": conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"],
            "deliveries": conn.execute("SELECT COUNT(*) AS n FROM deliveries").fetchone()["n"],
            "players": conn.execute(
                """
                SELECT COUNT(*) AS n FROM (
                    SELECT DISTINCT batter AS name FROM deliveries
                    UNION
                    SELECT DISTINCT bowler FROM deliveries
                )
                """
            ).fetchone()["n"],
        }
    return cached(conn, "dataset_counts", compute)


def overview_kpis(conn: sqlite3.Connection) -> dict[str, Any]:
    counts = _dataset_counts(conn)
    matches, deliveries, players = counts["matches"], counts["deliveries"], counts["players"]
    memory_n = 0
    try:
        memory_n = conn.execute("SELECT COUNT(*) AS n FROM player_memory").fetchone()["n"]
    except sqlite3.OperationalError:
        memory_n = 0
    recent = conn.execute(
        """
        SELECT match_id, team_home, team_away, match_date, winner
        FROM matches
        ORDER BY match_date DESC, match_id DESC
        LIMIT 8
        """
    ).fetchall()
    return {
        "total_matches": matches,
        "total_players": players,
        "total_deliveries": deliveries,
        "players_in_memory": memory_n,
        "sample_data": matches <= 5,
        "recent_matches": [dict(r) for r in recent],
        "note": (
            "These KPIs count rows in the local SQLite database. "
            "If the current dataset is College Demo Dataset, treat this as "
            "sample/demo data."
            if matches <= 5
            else "Figures reflect the currently ingested Cricsheet archive."
        ),
    }


def top_performers(conn: sqlite3.Connection, limit: int = 5) -> dict[str, Any]:
    return cached(conn, f"top_performers:{limit}", lambda: _top_performers(conn, limit))


def _top_performers(conn: sqlite3.Connection, limit: int) -> dict[str, Any]:
    batters = conn.execute(
        """
        SELECT batter AS player,
               SUM(runs_batter) AS runs,
               COUNT(*) AS balls
        FROM deliveries
        WHERE runs_wides = 0
        GROUP BY batter
        ORDER BY runs DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    bowlers = conn.execute(
        f"""
        SELECT d.bowler AS player,
               SUM(CASE WHEN w.kind IN ({BOWLER_WICKET_KINDS_SQL}) THEN 1 ELSE 0 END)
                 AS wickets
        FROM deliveries d
        LEFT JOIN wickets w ON w.delivery_pk = d.delivery_pk
        GROUP BY d.bowler
        ORDER BY wickets DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return {
        "by_runs": [dict(r) for r in batters],
        "by_wickets": [dict(r) for r in bowlers],
        "note": "Ranked by totals in the ingested database only.",
    }
