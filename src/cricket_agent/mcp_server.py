"""
MCP server for the Cricket Team Performance Agent (Phase 1).

Term: MCP (Model Context Protocol)
    A standardized protocol that lets an AI application (an "MCP client",
    for example an LLM-based agent) discover and call a set of named
    "tools" exposed by an "MCP server", without the client needing to know
    anything about how those tools are implemented.

Why we use it:
    Every future phase of this project (Batting/Bowling/Fielding Analyst
    agents, the Coordinator) will need a standard way to call
    get_match_data, compute_stats, and get_next_ball. MCP is the protocol
    this course, and the wider agentic-AI ecosystem, has standardized on
    for exactly that purpose.

In our project:
    This module is a *thin* wrapper (see docs/tools.md "Tool layer vs MCP
    layer"). It contains almost no logic of its own: it opens a database
    connection, calls a function from tools/cricket_tools.py, and turns
    any of our custom exceptions into a clean MCP error instead of letting
    a raw Python traceback reach the client. It also owns start_replay,
    since a replay must be started before get_next_ball can be called from
    an MCP client (MCP tool arguments are plain JSON values -- a client
    cannot hand us a live database connection or a Python object).

    We use the official MCP Python SDK (package name "mcp"), pinned to the
    v1.x line (see requirements.txt and docs/architecture.md "Why MCP SDK
    v1.x" for the reasoning), and its FastMCP-style high level API,
    imported here as `FastMCP`.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from typing import Any, Iterator

from mcp.server.fastmcp import FastMCP

from cricket_agent.config import DATABASE_PATH
from cricket_agent.database import connect
from cricket_agent.exceptions import CricketAgentError
from cricket_agent.observability import record_call
from cricket_agent.tools import cricket_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "Cricket Team Performance Agent (Phase 1)",
    instructions=(
        "Deterministic cricket data tools backed by locally-ingested "
        "Cricsheet ball-by-ball data. No statistical claim is inferred or "
        "guessed: every number returned comes directly from stored "
        "delivery-by-delivery records. Use get_match_data to look up a "
        "specific match, compute_stats to get a player's batting/bowling "
        "figures over a range of matches, and start_replay + get_next_ball "
        "to step through a match's deliveries one at a time in order."
    ),
)


@contextlib.contextmanager
def _get_connection() -> Iterator[sqlite3.Connection]:
    """Open a fresh connection per call and guarantee it is closed.

    Phase 1 intentionally keeps this simple: SQLite handles concurrent
    reads well, the dataset is small, and a short-lived per-call
    connection avoids any question of connection lifetime management
    inside an MCP server. A future phase may introduce a connection pool
    if profiling shows it is actually needed.

    Implemented as a context manager (rather than returning the bare
    connection) specifically so `conn.close()` always runs, including on
    an exception -- a plain `with connect(...) as conn:` would NOT close
    the connection, since sqlite3.Connection's own context-manager
    protocol only commits or rolls back the transaction, it does not
    close the underlying connection.
    """
    conn = connect(DATABASE_PATH)
    try:
        yield conn
    finally:
        conn.close()


@mcp.tool()
def get_match_data(match_id: str) -> dict[str, Any]:
    """Retrieve structured information about a single cricket match.

    Args:
        match_id: The Cricsheet match identifier, e.g. "1001". This is the
            file name (without ".json") the match was ingested from.

    Returns a dictionary with teams, date, venue, competition, per-innings
    totals, and the players involved. Raises a clear error if the
    match_id does not exist in the local database -- it never fabricates
    match details.
    """
    with _get_connection() as conn:
        try:
            record_call("get_match_data", channel="mcp", arguments={"match_id": match_id})
            return cricket_tools.get_match_data(conn, match_id)
        except CricketAgentError as exc:
            logger.info("get_match_data(%r) failed: %s", match_id, exc)
            raise


@mcp.tool()
def compute_stats(
    player_id: str,
    start_match: str | None = None,
    end_match: str | None = None,
    last_n_matches: int | None = None,
) -> dict[str, Any]:
    """Compute deterministic batting and bowling statistics for a player.

    Args:
        player_id: The player's exact Cricsheet name, e.g. "MS Dhoni".
        start_match: Optional match_id to start the range at (inclusive).
        end_match: Optional match_id to end the range at (inclusive).
        last_n_matches: Optional convenience filter for "most recent N
            matches this player appeared in". Takes precedence over
            start_match/end_match if both are given.

    If no range is given, statistics cover every match currently ingested
    for this player. The response includes a sample_size_warning when
    fewer than 3 matches are considered, so small samples are never
    presented with false confidence -- this is intentionally NOT hidden
    from the caller.
    """
    with _get_connection() as conn:
        try:
            record_call(
                "compute_stats",
                channel="mcp",
                arguments={"player_id": player_id, "last_n_matches": last_n_matches},
            )
            return cricket_tools.compute_stats(
                conn,
                player_id,
                start_match=start_match,
                end_match=end_match,
                last_n_matches=last_n_matches,
            )
        except CricketAgentError as exc:
            logger.info("compute_stats(%r) failed: %s", player_id, exc)
            raise


@mcp.tool()
def start_replay(match_id: str) -> dict[str, Any]:
    """Start a deterministic, ball-by-ball replay session for a match.

    Args:
        match_id: The Cricsheet match identifier to replay.

    Returns a session_id to pass into get_next_ball(). This is a replay
    of already-recorded deliveries in their original order -- it is NOT a
    live match feed. See docs/tools.md for the distinction between this
    temporary replay state and the long-term player memory planned for a
    later phase.
    """
    with _get_connection() as conn:
        try:
            record_call("start_replay", channel="mcp", arguments={"match_id": match_id})
            return cricket_tools.start_replay(conn, match_id)
        except CricketAgentError as exc:
            logger.info("start_replay(%r) failed: %s", match_id, exc)
            raise


@mcp.tool()
def get_next_ball(session_id: str) -> dict[str, Any]:
    """Return the next delivery in an active replay session.

    Args:
        session_id: A session id returned by start_replay().

    Each call advances the session by exactly one delivery. Raises a
    clear error once every delivery in the match has already been
    returned, rather than silently repeating or fabricating a delivery.
    """
    with _get_connection() as conn:
        try:
            record_call("get_next_ball", channel="mcp", arguments={"session_id": session_id})
            return cricket_tools.get_next_ball(conn, session_id)
        except CricketAgentError as exc:
            logger.info("get_next_ball(%r) failed: %s", session_id, exc)
            raise


@mcp.tool()
def get_previous_ball(session_id: str) -> dict[str, Any]:
    """Rewind a replay session by one already-returned delivery."""
    with _get_connection() as conn:
        try:
            record_call("get_previous_ball", channel="mcp", arguments={"session_id": session_id})
            return cricket_tools.get_previous_ball(conn, session_id)
        except CricketAgentError as exc:
            logger.info("get_previous_ball(%r) failed: %s", session_id, exc)
            raise


@mcp.tool()
def analyze_player(player_id: str, last_n_matches: int | None = None) -> dict[str, Any]:
    """Full deterministic player profile (form, role, confidence, fielding credits)."""
    from cricket_agent.analytics.player_profile import build_player_profile

    with _get_connection() as conn:
        try:
            record_call("analyze_player", channel="mcp", arguments={"player_id": player_id})
            return build_player_profile(conn, player_id, last_n_matches=last_n_matches)
        except CricketAgentError as exc:
            logger.info("analyze_player(%r) failed: %s", player_id, exc)
            raise


@mcp.tool()
def compare_players(player_a: str, player_b: str, last_n_matches: int | None = None) -> dict[str, Any]:
    """Side-by-side comparison of two players using the same analytics facade."""
    from cricket_agent.analytics.player_profile import compare_players as _compare

    with _get_connection() as conn:
        try:
            record_call(
                "compare_players",
                channel="mcp",
                arguments={"player_a": player_a, "player_b": player_b},
            )
            return _compare(conn, [player_a, player_b], last_n_matches=last_n_matches)
        except CricketAgentError as exc:
            logger.info("compare_players failed: %s", exc)
            raise


def main() -> None:
    """Entry point: run the MCP server over stdio transport.

    stdio transport (standard input/output) is what the official MCP
    Inspector and Claude Desktop expect for a locally-run server, and it
    needs no network port or extra configuration on either Windows or
    macOS.
    """
    logger.info("Starting Cricket Team Performance Agent MCP server (stdio)...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
