"""
Phase 1 demonstration script.

Run with:
    python demo_phase1.py

What this shows, in order:
    Demo 1 - Retrieve a known match with get_match_data().
    Demo 2 - Calculate a player's recent statistics with compute_stats().
    Demo 3 - Replay several deliveries using start_replay()/get_next_ball().
    Demo 4 - Call the same three capabilities through the MCP server
             object directly (the same code path a real MCP client uses),
             to prove the MCP layer is a genuine, working wrapper and not
             just a plan.

This script ingests the small fixture dataset under tests/fixtures/ into
a temporary SQLite database (NOT the project's real data/processed/cricket.db)
so it can be run immediately after cloning the repository, with no
download step required. See README.md "Dataset setup" for how to point
this same pipeline at real, downloaded Cricsheet data instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

logging.basicConfig(level=logging.WARNING)  # keep demo output focused

sys.path.insert(0, str(Path(__file__).parent / "src"))

from cricket_agent.database import connect, create_schema, insert_match  # noqa: E402
from cricket_agent.ingestion.parser import parse_match_file  # noqa: E402
from cricket_agent.tools import cricket_tools  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"


def _print_header(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def _print_json(label: str, data) -> None:
    print(f"\n{label}:")
    print(json.dumps(data, indent=2, default=str))


def build_demo_database(db_path: Path) -> None:
    conn = connect(db_path)
    create_schema(conn)
    for filename in ("match_1001.json", "match_1002.json"):
        match = parse_match_file(FIXTURES_DIR / filename)
        insert_match(conn, match)
    conn.close()


def demo_1_get_match_data(conn) -> None:
    _print_header("DEMO 1: get_match_data('match_1002')")
    result = cricket_tools.get_match_data(conn, "match_1002")
    _print_json("Result", result)


def demo_2_compute_stats(conn) -> None:
    _print_header("DEMO 2: compute_stats('B Verma')")
    result = cricket_tools.compute_stats(conn, "B Verma")
    _print_json("Result", result)
    if result["sample_size_warning"]:
        print(f"\n>>> NOTE: {result['sample_size_warning']}")


def demo_3_replay(conn) -> None:
    _print_header("DEMO 3: start_replay + get_next_ball('match_1001')")
    session = cricket_tools.start_replay(conn, "match_1001")
    print(f"\nStarted replay session {session['session_id']} "
          f"({session['total_deliveries']} deliveries total).")

    for _ in range(5):
        ball = cricket_tools.get_next_ball(conn, session["session_id"])
        wicket_note = ""
        if ball["wickets"]:
            w = ball["wickets"][0]
            wicket_note = f"  WICKET! {w['player_out']} ({w['kind']})"
        print(
            f"  Ball {ball['position']:>2}/{ball['total_deliveries']} "
            f"[{ball['actual_delivery']}]  {ball['bowler']} to {ball['batter']}: "
            f"{ball['runs_total']} run(s){wicket_note}"
        )


async def demo_4_mcp_layer(db_path: Path) -> None:
    _print_header("DEMO 4: The same capabilities, called through MCP")

    import cricket_agent.mcp_server as mcp_server

    # Point the MCP server's connection helper at our demo database.
    mcp_server.DATABASE_PATH = db_path

    _, match_result = await mcp_server.mcp.call_tool(
        "get_match_data", {"match_id": "match_1001"}
    )
    print("\nMCP tool call: get_match_data('match_1001') -> "
          f"teams={match_result['teams']}, venue={match_result['venue']!r}")

    _, stats_result = await mcp_server.mcp.call_tool(
        "compute_stats", {"player_id": "B Verma"}
    )
    print("MCP tool call: compute_stats('B Verma') -> "
          f"runs={stats_result['batting']['runs']}, "
          f"strike_rate={stats_result['batting']['strike_rate']:.1f}")

    _, session_result = await mcp_server.mcp.call_tool(
        "start_replay", {"match_id": "match_1001"}
    )
    _, ball_result = await mcp_server.mcp.call_tool(
        "get_next_ball", {"session_id": session_result["session_id"]}
    )
    print("MCP tool call: start_replay + get_next_ball -> "
          f"first ball {ball_result['actual_delivery']}: "
          f"{ball_result['bowler']} to {ball_result['batter']}")

    print("\n>>> All three tools were called through the exact FastMCP "
          "object that mcp_server.py exposes over stdio -- the MCP layer "
          "is real, not a stub.")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "demo_cricket.db"
        build_demo_database(db_path)

        conn = connect(db_path)
        try:
            demo_1_get_match_data(conn)
            demo_2_compute_stats(conn)
            demo_3_replay(conn)
        finally:
            conn.close()

        asyncio.run(demo_4_mcp_layer(db_path))

    print("\n" + "=" * 70)
    print("Phase 1 demonstration complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
