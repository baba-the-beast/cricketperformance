"""Who is in a team's current squad, given a name from a fixture feed.

Fixture feeds (CricketData.org) name sides "Australia Women" and formats "odi"/"t20".
Cricsheet stores the women's side as "Australia" with gender = 'female', splits T20
internationals across the T20 and IT20 match types, and publishes no under-19 cricket.
"""

from __future__ import annotations

import re
import sqlite3

FEED_FORMATS = {"T20": ("T20", "IT20"), "T20I": ("T20", "IT20"), "IT20": ("T20", "IT20"),
                "ODI": ("ODI",), "TEST": ("Test",)}


def feed_formats(match_type: str | None) -> tuple[str, ...]:
    match_type = (match_type or "").strip().upper()
    return FEED_FORMATS.get(match_type, (match_type,) if match_type else ())


def feed_team(name: str | None) -> tuple[str, str | None, str | None]:
    """(Cricsheet team name, gender or None, reason no squad can exist or None)."""
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name or name.lower() in {"tbc", "tba", "tbd"}:
        return name, None, "The teams for this fixture have not been announced yet."
    if re.search(r"\b(u-?19|under[- ]?19s?)\b", name, re.I):
        return name, None, "Cricsheet does not publish under-19 matches, so there is no squad history for this side."
    women = re.match(r"(.+?)\s+women(?:'s)?$", name, re.I)
    if women:
        return women.group(1), "female", None
    return name, None, None


def team_gender(conn: sqlite3.Connection, team: str) -> str | None:
    """The gender a team name has played most; IPL and WPL sides share names."""
    row = conn.execute(
        """
        SELECT gender, COUNT(*) AS n FROM matches
        WHERE (team_home = ? OR team_away = ?) AND gender IS NOT NULL
        GROUP BY gender ORDER BY n DESC LIMIT 1
        """,
        (team, team),
    ).fetchone()
    return row[0] if row else None


def current_squad(
    conn: sqlite3.Connection,
    team: str,
    *,
    gender: str | None,
    formats: tuple[str, ...] = (),
    year: str = "",
) -> list[str]:
    """Players who batted or bowled for the team, most appearances first.

    With a year, that calendar year; otherwise the 12 months up to the team's
    latest recorded match, so retired players drop out of the squad.
    """
    fmt_sql = f"AND pp.format IN ({','.join('?' * len(formats))})" if formats else ""
    if year:
        window, window_args = "AND strftime('%Y', m.match_date) = ?", [year]
    else:
        window = f"""AND m.match_date >= (SELECT date(MAX(m2.match_date), '-12 months')
                FROM player_participation p2 JOIN matches m2 ON m2.match_id = p2.match_id
                WHERE p2.team = ? AND (? IS NULL OR m2.gender = ?) {fmt_sql.replace('pp.', 'p2.')})"""
        window_args = [team, gender, gender, *formats]
    rows = conn.execute(
        f"""
        SELECT pp.player_name, COUNT(*) AS appearances, MAX(m.match_date) AS latest_match
        FROM player_participation pp
        JOIN matches m ON m.match_id = pp.match_id
        WHERE pp.team = ? AND (? IS NULL OR m.gender = ?) {fmt_sql} {window}
          AND (EXISTS (SELECT 1 FROM deliveries d WHERE d.batter = pp.player_name)
               OR EXISTS (SELECT 1 FROM deliveries d WHERE d.bowler = pp.player_name))
        GROUP BY pp.player_name
        ORDER BY appearances DESC, latest_match DESC, pp.player_name
        """,
        (team, gender, gender, *formats, *window_args),
    ).fetchall()
    return [row[0] for row in rows]
