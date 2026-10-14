"""
Real subprocess MCP round-trip test.

Unlike tests/test_mcp_server.py (which calls the FastMCP object's own
Python methods in-process), this test actually spawns
`python -m cricket_agent.mcp_server` as a second OS process and talks to
it over stdio using the official `mcp` Python *client* library -- the
same transport the MCP Inspector and Claude Desktop use. This is the
closest thing to an automated version of "open the Inspector and click
around" that a pytest suite can do on its own, on both Windows and
macOS/Linux, with no manual steps.

The subprocess is pointed at a temporary test database via the
CRICKET_DB_PATH environment variable (see config.py), so this test never
touches the real data/processed/cricket.db.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"


@pytest.fixture()
def demo_database(tmp_path):
    """Build a small temp database the subprocess server can read from."""
    from cricket_agent.database import connect, create_schema, insert_match  # noqa
    from cricket_agent.ingestion.parser import parse_match_file  # noqa

    db_path = tmp_path / "stdio_test.db"
    conn = connect(db_path)
    create_schema(conn)
    for filename in ("match_1001.json", "match_1002.json"):
        match = parse_match_file(FIXTURES_DIR / filename)
        insert_match(conn, match)
    conn.close()
    return db_path


@pytest.mark.anyio
async def test_stdio_server_lists_tools_and_answers_calls(demo_database):
    subprocess_env = dict(os.environ)
    subprocess_env["PYTHONPATH"] = str(SRC_DIR)
    subprocess_env["CRICKET_DB_PATH"] = str(demo_database)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cricket_agent.mcp_server"],
        env=subprocess_env,
        cwd=str(PROJECT_ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {t.name for t in tools.tools}
            assert {
                "get_match_data",
                "compute_stats",
                "start_replay",
                "get_next_ball",
            } <= tool_names

            result = await session.call_tool(
                "get_match_data", {"match_id": "match_1001"}
            )
            assert result.isError is False
            assert result.structuredContent["match_id"] == "match_1001"

            error_result = await session.call_tool(
                "get_match_data", {"match_id": "no_such_match"}
            )
            assert error_result.isError is True


@pytest.fixture
def anyio_backend():
    return "asyncio"
