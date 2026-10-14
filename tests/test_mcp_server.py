"""
Tests for cricket_agent.mcp_server.

Term: MCP Inspector
    A standalone GUI tool (`mcp dev`) for interactively poking at an MCP
    server: it lists tools, lets you fill in a form for each tool's
    arguments, and shows you the raw response. It is excellent for manual,
    human exploration but not something a `pytest` suite can drive
    automatically.

Why this file exists:
    Section 17 of the project brief requires automated proof the MCP
    server actually works, that does not depend on Claude Desktop or a
    human clicking through the Inspector. We use the MCP Python SDK's own
    FastMCP object directly (`mcp_server.mcp`), calling `.list_tools()` and
    `.call_tool()` exactly as an in-process MCP client library would. This
    exercises the real schema generation and dispatch code path -- just
    without needing a second OS process or a real stdio pipe.

Note on error handling in this test file:
    FastMCP's own `call_tool()` (the object method, as opposed to what
    happens over an actual wire protocol) re-raises tool failures as
    `mcp.server.fastmcp.exceptions.ToolError` rather than returning an
    error content block. A real MCP client talking over stdio/SSE would
    instead receive a normal tool-call response with `isError=True` --
    the server layer catches ToolError for us in that case. We verify the
    underlying exception message here, which is the part our own code is
    responsible for.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from cricket_agent import mcp_server
from cricket_agent.database import connect, create_schema, insert_match
from cricket_agent.ingestion.parser import parse_match_file

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _use_temporary_database(tmp_path, monkeypatch):
    """Point the MCP server's database connection at a fresh temp DB.

    mcp_server._get_connection() reads cricket_agent.config.DATABASE_PATH
    at call time, so patching that name is enough to redirect every tool
    call in this test module without touching the real project database.
    """
    db_path = tmp_path / "test_cricket.db"
    conn = connect(db_path)
    create_schema(conn)
    for filename in ("match_1001.json", "match_1002.json"):
        match = parse_match_file(FIXTURES_DIR / filename)
        insert_match(conn, match)
    conn.close()

    monkeypatch.setattr(mcp_server, "DATABASE_PATH", db_path)
    yield


async def _call_tool(name: str, arguments: dict) -> dict:
    """Call one registered MCP tool by name and return its structured result.

    Because our tools are annotated to return `dict[str, Any]`, FastMCP
    returns a 2-tuple: a list of human-readable content blocks (what a
    text-only MCP client would render), and the structured-content dict
    itself (what a client that understands JSON output schemas -- like a
    future agent -- would use directly). We use the structured form here
    since it needs no re-parsing and is the more precise contract.
    """
    content_blocks, structured_result = await mcp_server.mcp.call_tool(name, arguments)
    return structured_result


@pytest.mark.anyio
async def test_tools_are_registered():
    tools = await mcp_server.mcp.list_tools()
    tool_names = {tool.name for tool in tools}
    assert {"get_match_data", "compute_stats", "start_replay", "get_next_ball"} <= tool_names


@pytest.mark.anyio
async def test_get_match_data_tool_call_returns_expected_structure():
    result = await _call_tool("get_match_data", {"match_id": "match_1001"})
    assert result["match_id"] == "match_1001"
    assert set(result["teams"]) == {"Riverside Kings", "Hillcrest Tigers"}


@pytest.mark.anyio
async def test_compute_stats_tool_call_returns_expected_structure():
    result = await _call_tool("compute_stats", {"player_id": "A Sharma"})
    assert result["batting"]["runs"] == 29


@pytest.mark.anyio
async def test_get_match_data_unknown_match_raises_clean_error():
    with pytest.raises(ToolError, match="No match found"):
        await mcp_server.mcp.call_tool("get_match_data", {"match_id": "nope"})


@pytest.mark.anyio
async def test_compute_stats_unknown_player_raises_clean_error():
    with pytest.raises(ToolError, match="No data found for player"):
        await mcp_server.mcp.call_tool("compute_stats", {"player_id": "Z Nobody"})


@pytest.mark.anyio
async def test_replay_tool_roundtrip():
    session = await _call_tool("start_replay", {"match_id": "match_1001"})
    ball = await _call_tool("get_next_ball", {"session_id": session["session_id"]})
    assert ball["actual_delivery"] == "0.1"


@pytest.mark.anyio
async def test_replay_unknown_session_raises_clean_error():
    with pytest.raises(ToolError, match="No active replay session"):
        await mcp_server.mcp.call_tool("get_next_ball", {"session_id": "bogus"})


@pytest.fixture
def anyio_backend():
    return "asyncio"

