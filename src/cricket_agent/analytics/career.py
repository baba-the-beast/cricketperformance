"""Lifetime (whole-career) analytics for one player.

Everything is aggregated per innings in SQL (a handful of grouped queries
on the indexed batter/bowler columns), then rolled up in Python. The
formulas match analytics/statistics.py: balls faced exclude wides, runs
conceded exclude byes/leg-byes/penalties, bowler wickets exclude run-outs
and other non-bowler dismissals, and 'retired hurt' / 'retired not out' are not dismissals.

Unlike compute_stats, a dismissal without facing a ball (run out at the
non-striker's end) counts as an innings even when the player never faced or
bowled in that match, so dismissals can exceed compute_stats by a few.

This deliberately ignores any match window: it is the career view a coach
opens from the Player Explorer. Windowed analysis stays in
build_player_profile.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Iterable

from cricket_agent.dismissals import BOWLER_WICKET_KINDS, NON_DISMISSAL_KINDS
from cricket_agent.exceptions import PlayerNotFoundError


FORMAT_LABELS = {
    "Test": "Test",
    "ODI": "ODI",
    "T20": "T20",
    "IT20": "T20I (other)",
    "ODM": "One-day (other)",
    "MDM": "Multi-day (other)",
}
FORMAT_ORDER = ["Test", "ODI", "T20", "IT20", "ODM", "MDM"]


def _in_clause(values: Iterable[Any]) -> str:
    return ",".join("?" for _ in values)


def _batting_totals(innings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not innings:
        return None
    runs = sum(i["runs"] for i in innings)
    balls = sum(i["balls"] for i in innings)
    outs = sum(1 for i in innings if i["out"])
    top = max(innings, key=lambda i: (i["runs"], not i["out"]))
    return {
        "innings": len(innings),
        "runs": runs,
        "balls_faced": balls,
        "dismissals": outs,
        "not_outs": len(innings) - outs,
        "average": runs / outs if outs else None,
        "strike_rate": runs / balls * 100 if balls else None,
        "fours": sum(i["fours"] for i in innings),
        "sixes": sum(i["sixes"] for i in innings),
        "fifties": sum(1 for i in innings if 50 <= i["runs"] < 100),
        "hundreds": sum(1 for i in innings if i["runs"] >= 100),
        "ducks": sum(1 for i in innings if i["runs"] == 0 and i["out"]),
        "highest": {"runs": top["runs"], "not_out": not top["out"]},
    }


def _bowling_totals(innings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not innings:
        return None
    legal = sum(i["legal"] for i in innings)
    conceded = sum(i["conceded"] for i in innings)
    wickets = sum(i["wickets"] for i in innings)
    best = max(innings, key=lambda i: (i["wickets"], -i["conceded"]))
    return {
        "innings": len(innings),
        "legal_deliveries": legal,
        "overs": f"{legal // 6}.{legal % 6}",
        "runs_conceded": conceded,
        "wickets": wickets,
        "dot_balls": sum(i["dots"] for i in innings),
        "economy": conceded / (legal / 6) if legal else None,
        "average": conceded / wickets if wickets else None,
        "strike_rate": legal / wickets if wickets else None,
        "three_wicket_hauls": sum(1 for i in innings if 3 <= i["wickets"] < 5),
        "five_wicket_hauls": sum(1 for i in innings if i["wickets"] >= 5),
        "best": {"wickets": best["wickets"], "runs": best["conceded"]},
    }


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[r[key] or "Unknown"].append(r)
    return out


def career_summary(conn: sqlite3.Connection, player: str) -> dict[str, Any]:
    """Return the player's whole-career analytics from every stored delivery."""
    bat_rows = conn.execute(
        """
        SELECT match_id, innings_index,
               SUM(CASE WHEN runs_wides = 0 THEN runs_batter ELSE 0 END) AS runs,
               SUM(CASE WHEN runs_wides = 0 THEN 1 ELSE 0 END) AS balls,
               SUM(CASE WHEN runs_wides = 0 AND runs_batter = 4 THEN 1 ELSE 0 END) AS fours,
               SUM(CASE WHEN runs_wides = 0 AND runs_batter = 6 THEN 1 ELSE 0 END) AS sixes
        FROM deliveries
        WHERE batter = ?
        GROUP BY match_id, innings_index
        """,
        (player,),
    ).fetchall()
    outs = {
        (r["match_id"], r["innings_index"])
        for r in conn.execute(
            f"""
            SELECT d.match_id, d.innings_index
            FROM wickets w
            JOIN deliveries d ON d.delivery_pk = w.delivery_pk
            WHERE w.player_out = ? AND w.kind NOT IN ({_in_clause(NON_DISMISSAL_KINDS)})
            """,
            (player, *NON_DISMISSAL_KINDS),
        )
    }
    bowl_rows = conn.execute(
        """
        SELECT match_id, innings_index,
               SUM(CASE WHEN runs_wides = 0 AND runs_noballs = 0 THEN 1 ELSE 0 END) AS legal,
               SUM(runs_total - runs_byes - runs_legbyes - runs_penalty) AS conceded,
               SUM(CASE WHEN runs_wides = 0 AND runs_noballs = 0 AND runs_total = 0 THEN 1 ELSE 0 END) AS dots
        FROM deliveries
        WHERE bowler = ?
        GROUP BY match_id, innings_index
        """,
        (player,),
    ).fetchall()
    bowl_wkts: dict[tuple[str, int], int] = {
        (r["match_id"], r["innings_index"]): r["n"]
        for r in conn.execute(
            f"""
            SELECT d.match_id, d.innings_index, COUNT(*) AS n
            FROM wickets w
            JOIN deliveries d ON d.delivery_pk = w.delivery_pk
            WHERE d.bowler = ? AND w.kind IN ({_in_clause(BOWLER_WICKET_KINDS)})
            GROUP BY d.match_id, d.innings_index
            """,
            (player, *BOWLER_WICKET_KINDS),
        )
    }

    if not bat_rows and not bowl_rows:
        raise PlayerNotFoundError(f"No deliveries found for player {player!r}.")

    match_ids = sorted(
        {r["match_id"] for r in bat_rows} | {r["match_id"] for r in bowl_rows} | {m for m, _ in outs}
    )
    meta: dict[str, dict[str, Any]] = {}
    batting_team: dict[tuple[str, int], str] = {}
    # Chunk to stay under SQLite's bound-parameter limit on older builds.
    for start in range(0, len(match_ids), 900):
        chunk = match_ids[start:start + 900]
        for r in conn.execute(
            f"""
            SELECT match_id, match_type, match_date, season, event_name, venue,
                   team_home, team_away
            FROM matches WHERE match_id IN ({_in_clause(chunk)})
            """,
            chunk,
        ):
            meta[r["match_id"]] = dict(r)
        for r in conn.execute(
            f"SELECT match_id, innings_index, batting_team FROM innings WHERE match_id IN ({_in_clause(chunk)})",
            chunk,
        ):
            batting_team[(r["match_id"], r["innings_index"])] = r["batting_team"]

    def _context(match_id: str, innings_index: int, batting: bool) -> dict[str, Any]:
        m = meta.get(match_id, {})
        bat_side = batting_team.get((match_id, innings_index))
        teams = [m.get("team_home"), m.get("team_away")]
        other = next((t for t in teams if t and t != bat_side), None)
        return {
            "match_id": match_id,
            "date": m.get("match_date"),
            "year": (m.get("match_date") or "")[:4] or "Unknown",
            "format": m.get("match_type") or "Unknown",
            "competition": m.get("event_name") or "Unknown",
            "venue": m.get("venue"),
            "team": bat_side if batting else other,
            "opponent": other if batting else bat_side,
        }

    bat_innings = [
        {
            **_context(r["match_id"], r["innings_index"], True),
            "runs": r["runs"] or 0,
            "balls": r["balls"] or 0,
            "fours": r["fours"] or 0,
            "sixes": r["sixes"] or 0,
            "out": (r["match_id"], r["innings_index"]) in outs,
        }
        for r in bat_rows
    ]
    # Dismissed without facing a legal ball (e.g. run out at the non-striker's
    # end): still an innings and a dismissal, so averages match compute_stats.
    faced = {(r["match_id"], r["innings_index"]) for r in bat_rows}
    bat_innings += [
        {**_context(mid, idx, True), "runs": 0, "balls": 0, "fours": 0, "sixes": 0, "out": True}
        for (mid, idx) in sorted(outs - faced)
    ]
    bowl_innings = [
        {
            **_context(r["match_id"], r["innings_index"], False),
            "legal": r["legal"] or 0,
            "conceded": r["conceded"] or 0,
            "dots": r["dots"] or 0,
            "wickets": bowl_wkts.get((r["match_id"], r["innings_index"]), 0),
        }
        for r in bowl_rows
    ]

    match_formats = {mid: (meta.get(mid, {}).get("match_type") or "Unknown") for mid in match_ids}
    match_years = {mid: ((meta.get(mid, {}).get("match_date") or "")[:4] or "Unknown") for mid in match_ids}
    dates = sorted(d for d in (meta.get(mid, {}).get("match_date") for mid in match_ids) if d)

    bat_by_fmt, bowl_by_fmt = _group(bat_innings, "format"), _group(bowl_innings, "format")
    formats = sorted(
        set(match_formats.values()),
        key=lambda f: FORMAT_ORDER.index(f) if f in FORMAT_ORDER else len(FORMAT_ORDER),
    )
    by_format = [
        {
            "format": f,
            "label": FORMAT_LABELS.get(f, f),
            "matches": sum(1 for v in match_formats.values() if v == f),
            "batting": _batting_totals(bat_by_fmt.get(f, [])),
            "bowling": _bowling_totals(bowl_by_fmt.get(f, [])),
        }
        for f in formats
    ]

    bat_by_year, bowl_by_year = _group(bat_innings, "year"), _group(bowl_innings, "year")
    timeline = []
    cum_runs = cum_wkts = 0
    for year in sorted(set(match_years.values())):
        runs = sum(i["runs"] for i in bat_by_year.get(year, []))
        wkts = sum(i["wickets"] for i in bowl_by_year.get(year, []))
        cum_runs += runs
        cum_wkts += wkts
        bat = _batting_totals(bat_by_year.get(year, []))
        bowl = _bowling_totals(bowl_by_year.get(year, []))
        timeline.append({
            "year": year,
            "matches": sum(1 for v in match_years.values() if v == year),
            "runs": runs,
            "wickets": wkts,
            "cumulative_runs": cum_runs,
            "cumulative_wickets": cum_wkts,
            "batting_strike_rate": bat["strike_rate"] if bat else None,
            "batting_average": bat["average"] if bat else None,
            "economy": bowl["economy"] if bowl else None,
        })

    comp_matches: dict[str, set[str]] = defaultdict(set)
    for mid in match_ids:
        comp_matches[meta.get(mid, {}).get("event_name") or "Unknown"].add(mid)
    bat_by_comp, bowl_by_comp = _group(bat_innings, "competition"), _group(bowl_innings, "competition")
    competitions = sorted(
        (
            {
                "competition": c,
                "matches": len(mids),
                "runs": sum(i["runs"] for i in bat_by_comp.get(c, [])),
                "wickets": sum(i["wickets"] for i in bowl_by_comp.get(c, [])),
            }
            for c, mids in comp_matches.items()
        ),
        key=lambda c: (c["matches"], c["runs"], c["wickets"]),
        reverse=True,
    )[:8]

    teams = sorted(
        {i["team"] for i in bat_innings if i["team"]} | {i["team"] for i in bowl_innings if i["team"]}
    )

    def _clean(i: dict[str, Any], keys: list[str]) -> dict[str, Any]:
        base = ["match_id", "date", "format", "competition", "venue", "opponent"]
        return {k: i[k] for k in base + keys}

    best_innings = [
        _clean(i, ["runs", "balls", "fours", "sixes"]) | {"not_out": not i["out"]}
        for i in sorted(bat_innings, key=lambda i: (i["runs"], -i["balls"]), reverse=True)[:5]
    ]
    best_spells = [
        _clean(i, ["wickets", "conceded", "legal"])
        for i in sorted(bowl_innings, key=lambda i: (i["wickets"], -i["conceded"]), reverse=True)[:5]
        if i["wickets"] > 0
    ]

    return {
        "player": player,
        "scope": "lifetime",
        "span": {
            "first_match": dates[0] if dates else None,
            "last_match": dates[-1] if dates else None,
            "matches": len(match_ids),
            "seasons": len({y for y in match_years.values() if y != "Unknown"}),
        },
        "teams": teams,
        "batting": _batting_totals(bat_innings),
        "bowling": _bowling_totals(bowl_innings),
        "by_format": by_format,
        "timeline": timeline,
        "competitions": competitions,
        "best_innings": best_innings,
        "best_bowling": best_spells,
        "note": (
            "Lifetime figures cover every match stored for this player in the local "
            "Cricsheet database; matches outside the archive are not counted. "
            "Dismissals without facing a ball (e.g. run out at the non-striker's end) "
            "count as an innings, so career totals can be one or two innings higher "
            "than the windowed form analysis, which only sees matches where the "
            "player faced or bowled a delivery."
        ),
    }
