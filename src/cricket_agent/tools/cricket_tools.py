"""
Deterministic tool functions: get_match_data, compute_stats, get_next_ball.

Term: Tool layer
    Plain, synchronous Python functions that do one well-defined job each,
    with no knowledge of MCP, agents, or LLMs at all. They can be called
    directly from a test, a script, or -- later -- an MCP wrapper.

Why we separate this from the MCP layer:
    Section 14 of the project brief requires this split explicitly. If the
    actual logic lived inside an `@mcp.tool()` decorator, we could not unit
    test it without spinning up an MCP server, and we could not reuse it
    from, say, a future agent that talks to these functions directly in
    Python rather than over MCP. Keeping this layer's only dependency on
    the database (via `database.py`) and the statistics engine (via
    `analytics/statistics.py`) makes it trivially testable (see
    tests/test_tools.py) and trivially reusable.

In our project:
    `mcp_server.py` imports this module and wraps each function with
    `@mcp.tool()`, adding almost no additional logic of its own.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cricket_agent.analytics.statistics import compute_batting_stats, compute_bowling_stats
from cricket_agent.exceptions import (
    InvalidRangeError,
    MatchNotFoundError,
    PlayerNotFoundError,
    ReplayAtStartError,
    ReplayEndedError,
    ReplaySessionNotFoundError,
)
from cricket_agent.dismissals import BOWLER_WICKET_KINDS_SQL, NON_DISMISSAL_KINDS_SQL


# ---------------------------------------------------------------------------
# Tool 1: get_match_data
# ---------------------------------------------------------------------------


def get_match_data(conn: sqlite3.Connection, match_id: str) -> dict[str, Any]:
    """Retrieve structured, cleaned-up information about one match.

    Input:
        match_id: The Cricsheet match identifier (the file name, without
            ".json", that the match was ingested from -- e.g. "1001").

    Output (dict):
        {
            "match_id": str,
            "teams": [str, str],
            "date": str | None,           # first date of the match
            "venue": str | None,
            "city": str | None,
            "competition": str | None,    # event name, if any
            "match_type": str,            # "T20", "ODI", "Test", ...
            "season": str | None,
            "winner": str | None,
            "outcome_summary": str | None,
            "player_of_match": str | None,
            "innings": [
                {
                    "innings_index": int,
                    "batting_team": str,
                    "total_runs": int,
                    "wickets_lost": int,
                    "legal_deliveries_faced": int,
                },
                ...
            ],
            "players": {team_name: [player_name, ...], ...},
        }

    Error cases:
        Raises MatchNotFoundError if match_id is not in the database. We
        do not return an empty/partial dict for an unknown match -- an
        empty dict could be silently misread as "a match with no data",
        which is a different (and misleading) thing.

    Why this is a tool:
        It answers a single, well-scoped question ("what do we know about
        this match?") using only data already ingested -- it makes no
        external calls and needs no reasoning.

    Why it is deterministic:
        The same match_id, against the same database, always returns
        identical output. There is no LLM call, no randomness, and no
        reliance on wall-clock time.
    """
    match_row = conn.execute(
        "SELECT * FROM matches WHERE match_id = ?", (match_id,)
    ).fetchone()
    if match_row is None:
        raise MatchNotFoundError(match_id)

    innings_rows = conn.execute(
        "SELECT innings_index, batting_team FROM innings WHERE match_id = ? "
        "ORDER BY innings_index",
        (match_id,),
    ).fetchall()

    innings_summaries = []
    players_by_team: dict[str, set[str]] = {
        match_row["team_home"]: set(),
        match_row["team_away"]: set(),
    }

    for inn in innings_rows:
        idx = inn["innings_index"]
        team = inn["batting_team"]

        totals = conn.execute(
            """
            SELECT
                COALESCE(SUM(runs_total), 0) AS total_runs,
                COALESCE(SUM(CASE WHEN runs_wides = 0 AND runs_noballs = 0
                                  THEN 1 ELSE 0 END), 0) AS legal_deliveries
            FROM deliveries
            WHERE match_id = ? AND innings_index = ?
            """,
            (match_id, idx),
        ).fetchone()

        wickets_lost = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM wickets
            JOIN deliveries ON wickets.delivery_pk = deliveries.delivery_pk
            WHERE deliveries.match_id = ? AND deliveries.innings_index = ?
              AND wickets.kind NOT IN ({NON_DISMISSAL_KINDS_SQL})
            """,
            (match_id, idx),
        ).fetchone()["n"]

        innings_summaries.append(
            {
                "innings_index": idx,
                "batting_team": team,
                "total_runs": totals["total_runs"],
                "wickets_lost": wickets_lost,
                "legal_deliveries_faced": totals["legal_deliveries"],
            }
        )

        for row in conn.execute(
            "SELECT DISTINCT batter FROM deliveries "
            "WHERE match_id = ? AND innings_index = ? AND batter IS NOT NULL",
            (match_id, idx),
        ):
            players_by_team.setdefault(team, set()).add(row["batter"])

    return {
        "match_id": match_row["match_id"],
        "teams": [match_row["team_home"], match_row["team_away"]],
        "date": match_row["match_date"],
        "venue": match_row["venue"],
        "city": match_row["city"],
        "competition": match_row["event_name"],
        "match_type": match_row["match_type"],
        "season": match_row["season"],
        "winner": match_row["winner"],
        "outcome_summary": match_row["outcome_summary"],
        "player_of_match": match_row["player_of_match"],
        "innings": innings_summaries,
        "players": {team: sorted(names) for team, names in players_by_team.items()},
    }


# ---------------------------------------------------------------------------
# Tool 2: compute_stats
# ---------------------------------------------------------------------------


def _resolve_match_range(
    conn: sqlite3.Connection,
    player_id: str,
    start_match: str | None,
    end_match: str | None,
    last_n_matches: int | None,
) -> list[str]:
    """Turn the caller's range parameters into a concrete list of match_ids.

    Exactly one of (start_match/end_match) or last_n_matches should be
    used; this project treats "no range given" as "every match on record
    for this player", which is a deliberate, documented default (see
    docs/tools.md) rather than an error.
    """
    if last_n_matches is not None and last_n_matches <= 0:
        raise InvalidRangeError(
            f"last_n_matches must be a positive integer, got {last_n_matches}."
        )

    # Every match_id this player appears in at all (batted or bowled),
    # ordered chronologically using the match's own recorded date.
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
        (player_id, player_id),
    ).fetchall()
    all_match_ids = [r["match_id"] for r in rows]

    if not all_match_ids:
        raise PlayerNotFoundError(player_id)

    if last_n_matches is not None:
        return all_match_ids[-last_n_matches:]

    if start_match is None and end_match is None:
        return all_match_ids

    start_idx = all_match_ids.index(start_match) if start_match else 0
    end_idx = (
        all_match_ids.index(end_match) + 1 if end_match else len(all_match_ids)
    )
    if start_idx >= end_idx:
        raise InvalidRangeError(
            f"start_match={start_match!r} does not come before "
            f"end_match={end_match!r} for player {player_id!r}."
        )
    return all_match_ids[start_idx:end_idx]


def compute_stats(
    conn: sqlite3.Connection,
    player_id: str,
    *,
    start_match: str | None = None,
    end_match: str | None = None,
    last_n_matches: int | None = None,
) -> dict[str, Any]:
    """Compute batting and bowling statistics for a player over a range.

    Input:
        player_id: The player's Cricsheet name exactly as it appears in
            the data (e.g. "MS Dhoni"). Phase 1 uses the name string
            itself as the identifier; see docs/data_model.md for why a
            numeric player id is deferred to a later phase.
        start_match / end_match: Optional match_id bounds (inclusive),
            interpreted in the player's own chronological match order.
        last_n_matches: Optional convenience parameter: "most recent N
            matches this player appeared in". Cannot be combined
            meaningfully with start_match/end_match; if given, it takes
            precedence.

        If none of the three range parameters are given, statistics are
        computed over every match currently in the database for this
        player. This is a deliberate default, not a hidden behavior --
        it is documented here and in docs/tools.md.

    Output (dict):
        {
            "player_id": str,
            "matches_considered": [match_id, ...],
            "batting": { ... see analytics.statistics.BattingStats ... } | None,
            "bowling": { ... see analytics.statistics.BowlingStats ... } | None,
            "sample_size_warning": str | None,
        }
        `batting` is None if the player never batted in the range;
        `bowling` is None if the player never bowled in the range. Both
        being None cannot happen -- that case raises PlayerNotFoundError.

    Error cases:
        PlayerNotFoundError: player_id has no deliveries at all.
        InvalidRangeError: last_n_matches <= 0, or start_match does not
            precede end_match.

    Why this is a tool:
        Turns raw ball-by-ball rows into the handful of numbers a human
        (or a future analyst agent) actually needs, using one fixed,
        documented formula per statistic -- see analytics/statistics.py
        for exactly which formula and why.
    """
    match_ids = _resolve_match_range(
        conn, player_id, start_match, end_match, last_n_matches
    )
    placeholders = ",".join("?" for _ in match_ids)

    batting_rows = conn.execute(
        f"""
        SELECT * FROM deliveries
        WHERE batter = ? AND runs_wides = 0 AND match_id IN ({placeholders})
        """,
        (player_id, *match_ids),
    ).fetchall()

    batting_innings_count = conn.execute(
        f"""
        SELECT COUNT(DISTINCT match_id || '-' || innings_index) AS n
        FROM deliveries
        WHERE batter = ? AND match_id IN ({placeholders})
        """,
        (player_id, *match_ids),
    ).fetchone()["n"]

    batting_dismissals = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM wickets
        JOIN deliveries ON wickets.delivery_pk = deliveries.delivery_pk
        WHERE wickets.player_out = ? 
          AND wickets.kind NOT IN ({NON_DISMISSAL_KINDS_SQL})
          AND deliveries.match_id IN ({placeholders})
        """,
        (player_id, *match_ids),
    ).fetchone()["n"]

    bowling_rows = conn.execute(
        f"""
        SELECT * FROM deliveries
        WHERE bowler = ? AND match_id IN ({placeholders})
        """,
        (player_id, *match_ids),
    ).fetchall()

    bowling_wickets = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM wickets
        JOIN deliveries ON wickets.delivery_pk = deliveries.delivery_pk
        WHERE deliveries.bowler = ?
          AND wickets.kind IN ({BOWLER_WICKET_KINDS_SQL})
          AND deliveries.match_id IN ({placeholders})
        """,
        (player_id, *match_ids),
    ).fetchone()["n"]

    batting = (
        compute_batting_stats(batting_rows, batting_dismissals, batting_innings_count).to_dict()
        if batting_rows
        else None
    )
    bowling = (
        compute_bowling_stats(bowling_rows, bowling_wickets).to_dict()
        if bowling_rows
        else None
    )

    sample_size_warning = None
    if len(match_ids) < 3:
        sample_size_warning = (
            f"Only {len(match_ids)} match(es) in this range. Treat these "
            "statistics as low-confidence; small samples can be misleading."
        )

    return {
        "player_id": player_id,
        "matches_considered": match_ids,
        "batting": batting,
        "bowling": bowling,
        "sample_size_warning": sample_size_warning,
    }


# ---------------------------------------------------------------------------
# Tool 3: get_next_ball (deterministic replay)
# ---------------------------------------------------------------------------
#
# Term: Replay state
#     The small amount of information needed to know "which delivery comes
#     next" for one match being stepped through -- nothing more. This is
#     NOT the same thing as long-term player memory (a future phase); it
#     is thrown away as soon as the replay session ends or the process
#     restarts. See docs/tools.md for the full distinction.


@dataclass
class ReplaySession:
    match_id: str
    delivery_ids: list[int]  # ordered delivery_pk values for this match
    position: int = 0  # index into delivery_ids of the NEXT ball to return


_REPLAY_SESSIONS: dict[str, ReplaySession] = {}
"""In-memory replay session store.

Phase 1 keeps this as a simple process-local dictionary. This is
explicitly a Phase 1 simplification: it is lost if the MCP server
restarts, and it does not work across multiple server processes. A later
phase may move this into SQLite or another store if a persistent or
multi-process replay is required -- but that is a deliberate future
decision, not an accidental limitation we are hiding.
"""


def start_replay(conn: sqlite3.Connection, match_id: str) -> dict[str, Any]:
    """Begin a new replay session for a match, returning a session_id.

    Raises:
        MatchNotFoundError: if match_id is not in the database.
    """
    match_row = conn.execute(
        "SELECT match_id FROM matches WHERE match_id = ?", (match_id,)
    ).fetchone()
    if match_row is None:
        raise MatchNotFoundError(match_id)

    delivery_ids = [
        row["delivery_pk"]
        for row in conn.execute(
            """
            SELECT delivery_pk FROM deliveries
            WHERE match_id = ?
            ORDER BY innings_index ASC, over_number ASC, ball_in_over ASC
            """,
            (match_id,),
        )
    ]

    session_id = str(uuid.uuid4())
    _REPLAY_SESSIONS[session_id] = ReplaySession(
        match_id=match_id, delivery_ids=delivery_ids
    )
    return {
        "session_id": session_id,
        "match_id": match_id,
        "total_deliveries": len(delivery_ids),
    }


def get_next_ball(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    """Return the next delivery in a replay session and advance its state.

    Input:
        session_id: A session id returned by start_replay().

    Output (dict): one delivery's full detail, plus replay progress:
        {
            "session_id": str,
            "position": int,          # 1-based index of THIS delivery
            "total_deliveries": int,
            "innings_index": int,
            "over_number": int,
            "ball_in_over": int,
            "actual_delivery": str,
            "batter": str, "bowler": str, "non_striker": str,
            "runs_batter": int, "runs_total": int,
            "extras": {"wides": int, "noballs": int, "byes": int,
                       "legbyes": int, "penalty": int},
            "wickets": [{"kind": str, "player_out": str, "fielders": [str]}],
        }

    Error cases:
        ReplaySessionNotFoundError: session_id is unknown (never started,
            or the server has since restarted).
        ReplayEndedError: every delivery in the match has already been
            returned by previous calls.

    This is NOT a live-data API: it replays deliveries that already
    happened, strictly in the order Cricsheet recorded them, from data
    already sitting in our own database. See docs/tools.md for why this
    distinction matters for how a future agent should describe this tool.
    """
    session = _REPLAY_SESSIONS.get(session_id)
    if session is None:
        raise ReplaySessionNotFoundError(session_id)

    if session.position >= len(session.delivery_ids):
        raise ReplayEndedError(session.match_id)

    delivery_pk = session.delivery_ids[session.position]
    session.position += 1
    return _delivery_payload(conn, session, delivery_pk, session_id=session_id)


def get_previous_ball(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    """Rewind one delivery without changing get_next_ball() behaviour.

    After two successful get_next_ball calls the session position is 2.
    get_previous_ball then returns the first delivery again and leaves
    position at 1, so the next get_next_ball returns the second delivery.
    """
    session = _REPLAY_SESSIONS.get(session_id)
    if session is None:
        raise ReplaySessionNotFoundError(session_id)
    if session.position <= 1:
        raise ReplayAtStartError(session.match_id)

    session.position -= 1
    delivery_pk = session.delivery_ids[session.position - 1]
    return _delivery_payload(conn, session, delivery_pk, session_id=session_id)


def list_match_deliveries(conn: sqlite3.Connection, match_id: str) -> list[dict[str, Any]]:
    """All deliveries for score-progression charts (not a live feed)."""
    match_row = conn.execute(
        "SELECT match_id FROM matches WHERE match_id = ?", (match_id,)
    ).fetchone()
    if match_row is None:
        raise MatchNotFoundError(match_id)
    rows = conn.execute(
        """
        SELECT * FROM deliveries
        WHERE match_id = ?
        ORDER BY innings_index ASC, over_number ASC, ball_in_over ASC
        """,
        (match_id,),
    ).fetchall()
    result = []
    cum = 0
    for row in rows:
        cum += int(row["runs_total"])
        wickets = conn.execute(
            "SELECT kind, player_out, fielders FROM wickets WHERE delivery_pk = ?",
            (row["delivery_pk"],),
        ).fetchall()
        result.append(_row_to_ball(row, wickets, extra={"cumulative_runs": cum}))
    return result


def _row_to_ball(row: sqlite3.Row, wickets: list, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "innings_index": row["innings_index"],
        "over_number": row["over_number"],
        "ball_in_over": row["ball_in_over"],
        "actual_delivery": row["actual_delivery"],
        "batter": row["batter"],
        "bowler": row["bowler"],
        "non_striker": row["non_striker"],
        "runs_batter": row["runs_batter"],
        "runs_total": row["runs_total"],
        "extras": {
            "wides": row["runs_wides"],
            "noballs": row["runs_noballs"],
            "byes": row["runs_byes"],
            "legbyes": row["runs_legbyes"],
            "penalty": row["runs_penalty"],
        },
        "wickets": [
            {
                "kind": w["kind"],
                "player_out": w["player_out"],
                "fielders": (w["fielders"] or "").split(", ") if w["fielders"] else [],
            }
            for w in wickets
        ],
    }
    if extra:
        payload.update(extra)
    return payload


def _delivery_payload(
    conn: sqlite3.Connection,
    session: ReplaySession,
    delivery_pk: int,
    *,
    session_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM deliveries WHERE delivery_pk = ?", (delivery_pk,)
    ).fetchone()
    wickets = conn.execute(
        "SELECT kind, player_out, fielders FROM wickets WHERE delivery_pk = ?",
        (delivery_pk,),
    ).fetchall()
    payload = _row_to_ball(row, wickets)
    payload.update(
        {
            "session_id": session_id,
            "position": session.position,
            "total_deliveries": len(session.delivery_ids),
            "match_id": session.match_id,
        }
    )
    return payload
