# Architecture

Phase 1 built the deterministic foundation. The current system keeps that
layer and adds analytics, agents, memory, simulation, and a coach dashboard
on top of the **same** `compute_stats` / replay functions.

```text
Cricsheet → Ingestion → SQLite → Analytics engine
    → MCP server (protocol)
    → Specialist agents + Coordinator (in-process Python)
    → Selection report / memory
    → Flask dashboard
```

Phase 1 research notes remain in `docs/phase_1.md`.

## 1. What Phase 1 is (and is not)

Phase 1 builds the **deterministic foundation** the rest of the Cricket
Team Performance Agent will eventually sit on top of. It takes real
Cricsheet ball-by-ball data, turns it into a small local database, and
exposes three well-documented tools over MCP. It intentionally contains
**no LLM, no agents, and no memory beyond a temporary replay pointer.**

> "Before introducing autonomous reasoning, we first created reliable
> tools that expose verified cricket information. The later agents will
> use these tools rather than directly manipulating raw data."

## 2. Architecture diagram

```text
                CRICSHEET (cricsheet.org)
                    │  (.zip of JSON files, one per match)
                    ▼
             Data Ingestion            <- ingestion/downloader.py
                    │
                    ▼
              JSON Parser              <- ingestion/parser.py
                    │  (validates + normalizes into Match/Innings/Delivery)
                    ▼
                SQLite                 <- database.py
           (matches, innings, deliveries, wickets)
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
     Match Data   Stats     Replay
       Logic      Engine     Logic
    (tools.py)  (analytics/  (tools.py,
                statistics.py)  in-memory
                              session state)
          │         │         │
          └─────────┼─────────┘
                    ▼
               Tool Layer               <- tools/cricket_tools.py
          (plain, testable Python functions)
                    │
                    ▼
                MCP Server              <- mcp_server.py
          (thin @mcp.tool() wrappers)
                    │
   ═══════════════════════════════════════════  <- Phase 1 boundary
                    ▼
          Future AI Agent Layer
   (Batting / Bowling / Fielding Analysts,
    Coordinator, long-term memory,
    confidence engine, selection reports)
```

Everything above the double line was Phase 1. Specialist agents, memory,
confidence, selection reports, simulation, and the dashboard now exist
**above** that line's tool layer; they still call the same Python functions.

## 3. Design decisions

### Why Cricsheet JSON (not CSV)?

Cricsheet publishes each match as both a JSON file and a set of CSV
files. We chose JSON because:
- It preserves the natural nested structure of a match (innings → overs →
  deliveries → wickets), which maps directly onto our data model.
- It includes structured fields (e.g. `wickets[].fielders[].name`) that
  the flattened CSV format represents less directly.
- The official format is documented and versioned
  (`meta.data_version`, currently `1.2.0` at the time this project was
  built), which gave us something concrete to verify our parser against.

### Why SQLite (not PostgreSQL, not a vector database)?

- Ships in Python's standard library; nothing to install on either
  Windows or macOS.
- The entire database is one file, easy to inspect directly (e.g. with
  DB Browser for SQLite) when explaining the project to a professor.
- Six small tables and a handful of SELECT queries do not need a
  client-server database or a schema migration tool.
- A vector database has no role in Phase 1: nothing here needs semantic
  similarity search. That may become relevant once long-term player
  memory is introduced in a later phase — a deliberate future decision,
  not something Phase 1 needs to anticipate today.

### Why Python 3.11+?

`pyproject.toml` requires Python 3.11 or newer. 3.11 is a conservative,
widely-available choice: it is old enough to be preinstalled or trivially
installable on both current Windows and macOS systems, and new enough to
support every standard-library feature this project uses (`pathlib`,
`dataclasses`, `sqlite3`, modern type-hint syntax such as `str | None`).
There was no feature in this project that required a newer version.

### Why the official MCP Python SDK, and why pin to the v1.x line?

We use the `mcp` package's high-level `FastMCP` API
(`from mcp.server.fastmcp import FastMCP`), which turns a plain Python
function into an MCP tool with one decorator (`@mcp.tool()`) by reading
its type hints and docstring.

At the time this project was built, the `mcp` package on PyPI had two
active lines: a `1.x` line (versions up to `1.29.1`) and a newer `2.x`
line (from `2.0.0` onward). We deliberately pinned `requirements.txt` to
`mcp>=1.28,<2` rather than the newest `2.x` release, for a college-project
setting specifically:
- The `1.x` `FastMCP` API is the version almost all existing MCP
  tutorials, course materials, and community examples are written
  against, which matters when the whole team is learning MCP for the
  first time.
- `1.x` is still an actively maintained line, not an abandoned one.
- The exact API surface we use (`FastMCP(name, instructions=...)`,
  `@mcp.tool()`, `mcp.run(transport="stdio")`) was directly verified
  against the installed `1.x` package while building this project (see
  `docs/phase_1.md` "Research Findings").

This is a pragmatic, explicitly-reasoned choice for Phase 1, not a claim
that `2.x` is somehow wrong — a later phase could re-evaluate this once
the `2.x` line's tooling/tutorial ecosystem has matured further.

### Why no ORM?

An ORM (e.g. SQLAlchemy) exists to hide SQL behind Python objects. With
six tables and a dozen queries, hiding the SQL makes the project *harder*
to explain to a professor, not easier — every query in this project is
plain, readable SQL living in `database.py` and `tools/cricket_tools.py`.

### Why a `tools/` layer separate from `mcp_server.py`?

See `docs/tools.md` "Tool layer vs MCP layer" for the full explanation.
In short: the tool functions have zero MCP-specific code in them, so they
can be unit tested directly (see `tests/test_tools.py`) without starting
an MCP server, and can be reused by any future code path (a script, a
notebook, a future agent) that wants to call them directly in Python.

### Why the project structure differs slightly from the original sketch

The original brief's sketch included a top-level `players` table and a
`fielding_events` table. Phase 1 does **not** create either:
- `players`: Phase 1 has no separate player-identity concept beyond the
  name string Cricsheet already uses consistently as `batter`/`bowler`.
  Introducing a numeric player ID table now, before any use case needs
  one, would be unused schema. `docs/data_model.md` explains this in more
  detail, including what Cricsheet's own `registry.people` section
  provides for a future phase that does need it.
- `fielding_events`: no fielding statistic is computed in Phase 1 (see
  `docs/data_model.md` "Which statistics we should NOT claim to
  calculate yet"), so there is nothing yet to store in such a table.

## 4. Cross-platform compatibility

- All paths are built with `pathlib.Path`, relative to `config.BASE_DIR`
  (itself derived from `__file__`, never hard-coded).
- Every shell command in `README.md` is given for both macOS/Linux and
  Windows PowerShell.
- `ingestion/downloader.py` uses only `urllib.request` and `zipfile` from
  the standard library — no platform-specific download tool.
- No file paths use OS-specific separators; `Path` objects handle that
  automatically on whichever OS the project runs on.

## 5. Term glossary

**MCP (Model Context Protocol):**
A standardized protocol for connecting AI applications to external tools
and resources.
*Why we use it:* our future AI agents need a standard way to access
cricket-data operations.
*In our project:* the MCP server exposes `get_match_data`,
`compute_stats`, `start_replay`, and `get_next_ball`.

**MCP Server:**
The process that holds a set of tools and answers requests from an MCP
client to list or call them.
*Why we use it:* it is the piece that will let a future LLM-based agent
call our cricket tools without needing to import our Python code
directly.
*In our project:* `mcp_server.py`, run via `python -m cricket_agent.mcp_server`.

**MCP Client:**
Any application that connects to an MCP server to discover and call its
tools — for example, the MCP Inspector, Claude Desktop, or a future
agent framework.
*Why it matters here:* Phase 1 has no client of its own yet; our
automated tests (`tests/test_mcp_server.py`) act as a client for testing
purposes, and `docs/tools.md` explains how to connect a real one.

**Tool (MCP sense):**
A named function an MCP server exposes, with a described input schema
and output, that a client can call.
*In our project:* `get_match_data`, `compute_stats`, `start_replay`,
`get_next_ball`.

**Resource (MCP sense):**
See `docs/tools.md` "MCP Tool vs MCP Resource" for the full discussion of
why Phase 1 uses tools rather than a resource for the Cricsheet archive.

**Deterministic function:**
A function that always produces the same output for the same input — no
randomness, no external service call, no LLM involved.
*Why it matters here:* every statistic in Phase 1 is deterministic, which
is what makes it trustworthy input for a future reasoning agent.

**Data normalization:**
Converting a data source's own particular structure (Cricsheet's nested
JSON) into a project's own consistent internal representation (our
`Match`/`Innings`/`Delivery` dataclasses and SQLite tables), so the rest
of the system never has to know Cricsheet's specific quirks.
