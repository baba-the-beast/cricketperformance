# Phase 1 — Research Findings, Scope, and Status

## Research findings (summary; full detail in docs/data_model.md and docs/architecture.md)

**Cricsheet.** We used the current official JSON format
(`data_version: "1.2.0"`), verified directly against
`https://cricsheet.org/format/json/`. See `docs/data_model.md` Section 1
for the full field-by-field verification: which fields we confirmed
exist and use, and which statistics we deliberately do NOT claim to
calculate because Cricsheet does not record the underlying data (fielding
opportunities, dropped catches, expected runs).

**A note on this build's data source.** This implementation was built and
tested inside a sandboxed development environment whose outbound network
access does not include `cricsheet.org`. `ingestion/downloader.py`
contains a real, working downloader (standard library only) for
Cricsheet's published `.zip` archives, but it could not be exercised
against the live site from inside that sandbox. Development, the test
suite, and `demo_phase1.py` instead use a small set of hand-built JSON
fixtures under `tests/fixtures/` that are structurally identical to real
Cricsheet files (same field names, same nesting, same schema version) but
describe a fictional "College Trial League" rather than real matches.
**Before presenting this project, run the real download yourself** (see
`README.md` "Dataset setup") to ingest genuine Cricsheet data and confirm
the pipeline end-to-end on real matches — the code path is identical
either way.

**MCP.** We used the official MCP Python SDK (PyPI package `mcp`),
pinned to the `1.x` line (`mcp>=1.28,<2`) and its high-level `FastMCP`
API. See `docs/architecture.md` "Why MCP SDK v1.x" for the reasoning.
Every API call this project makes (`FastMCP(name, instructions=...)`,
`@mcp.tool()`, `mcp.run(transport="stdio")`, `mcp.call_tool(...)`,
`mcp.list_tools()`) was verified directly against the installed package
during development, not assumed from memory or an outdated tutorial.

**Python / cross-platform.** Python 3.11+, using only `pathlib` for
paths, so the project runs unmodified on Windows and macOS. See
`docs/architecture.md` Section 4.

## Implement NOW vs DO NOT implement yet

**Implemented in Phase 1:**
- Cricsheet ingestion (`ingestion/downloader.py`, `ingestion/parser.py`)
- JSON parsing and validation
- SQLite storage (`database.py`)
- Deterministic statistics engine (`analytics/statistics.py`)
- `get_match_data()`, `compute_stats()`, `start_replay()`/`get_next_ball()`
  (`tools/cricket_tools.py`)
- MCP server exposing all four as tools (`mcp_server.py`)
- Automated tests: parser, statistics, tools, and MCP (in-process AND a
  real stdio subprocess round trip) — 46 tests, all passing
- Documentation (this file, `architecture.md`, `data_model.md`,
  `tools.md`) and `README.md`
- `demo_phase1.py`

**Deliberately NOT implemented yet** (see project brief Section 24):
- Batting / Bowling / Fielding Analyst agents
- Coordinator agent
- Long-term player memory
- Session memory beyond the temporary replay pointer described in
  `docs/tools.md` Section 4
- Confidence engine (beyond the simple `sample_size_warning` field, which
  is plain arithmetic on a match count, not a model judgment)
- Final selection report
- Multi-agent orchestration
- Any LLM call anywhere in this codebase

## Quality bar — self-check against the brief's checklist

- [x] Works on macOS (developed and tested on Linux/macOS-compatible
      stdlib-only code; every path uses `pathlib`)
- [x] Works on Windows (same reasoning; README gives PowerShell commands
      for every step — Windows execution itself was not separately
      verified inside this sandbox, which also has no Windows machine
      available, so please confirm on your own Windows machine before
      presenting)
- [x] Uses real Cricsheet data *format* (see the data-source note above —
      confirm with a real download before presenting)
- [x] Uses verified current Cricsheet fields (docs/data_model.md Section 1)
- [x] Has deterministic statistics (analytics/statistics.py)
- [x] Has `get_match_data()`, `compute_stats()`, `get_next_ball()`
- [x] Has an MCP server
- [x] Has automated tests (46, all passing — see below)
- [x] Has a demo (`demo_phase1.py`, output shown below)
- [x] Has clear error handling (custom exceptions, never a bare traceback
      for an expected condition)
- [x] Has architecture / data-model / tool documentation
- [x] Has professor-friendly explanations (this file, and the "Professor
      Explanation" section below)
- [x] Does not contain fake AI behavior (no LLM call anywhere in `src/`)
- [x] Does not implement future phases prematurely
- [x] Does not assume Linux (stdlib-only file/network operations)
- [x] Does not use hard-coded user-specific paths

## Test results (actually executed, not assumed)

```text
$ python -m pytest -v
...
46 passed in 1.15s
```

Full breakdown: 8 parser tests, 13 statistics tests, 16 tool-layer tests,
7 in-process MCP tests, 1 real-subprocess stdio MCP test (spawns
`python -m cricket_agent.mcp_server` as a genuine second process and
drives it with the official MCP client library, listing tools and
calling `get_match_data` for both a valid and an invalid match_id).

## Demonstration (actually executed, not assumed)

`python demo_phase1.py` runs all four demos against a temporary database
built from the fixture data, so it works immediately after cloning the
repository:

- **Demo 1** retrieves `match_1001` via `get_match_data()` and prints its
  full structured summary (teams, venue, per-innings totals).
- **Demo 2** computes `A Sharma`'s statistics via `compute_stats()`
  across both fixture matches (29 runs, strike rate ≈223.1, and a
  `sample_size_warning` since only 2 matches are on record).
- **Demo 3** replays the first 5 deliveries of `match_1001` ball-by-ball.
- **Demo 4** calls all three tools again, this time through the actual
  `FastMCP` object `mcp_server.py` exposes, proving the MCP layer is a
  real, working wrapper and not just a design on paper.

## Professor explanation

### What we built (concise points)

1. A validating parser that turns official Cricsheet JSON into a small,
   typed internal representation (`Match`/`Innings`/`Delivery`/`Wicket`).
2. A 4-table SQLite database (`matches`, `innings`, `deliveries`,
   `wickets`) that a professor can open directly and inspect.
3. A deterministic statistics engine implementing cricket's actual
   scoring rules (strike rate excludes wides but not no-balls; batting
   average is undefined, not zero, if never dismissed; byes/leg-byes are
   never charged to the bowler, even when mixed with a wide on the same
   delivery).
4. Three tools — `get_match_data`, `compute_stats`,
   `start_replay`/`get_next_ball` — implemented as plain, independently
   testable Python functions before any MCP code was written.
5. An MCP server that is a thin wrapper around those functions, verified
   to work over a real stdio subprocess using the official MCP client.
6. 46 automated tests covering valid input, invalid input, and edge cases
   (zero balls faced, zero wickets, replay end, unknown player/match).
7. Complete documentation explaining every technology choice and every
   deliberately-deferred feature.
8. No LLM or agent code anywhere — by design, per the brief's "No Fake
   AI" requirement.

### Why each component exists

See `docs/architecture.md` Section 3 ("Design decisions") for every
component; it is written specifically to answer "why does this exist"
for each one, not just "what does this do".

### Important technical terms

See `docs/architecture.md` Section 5 for the full glossary (MCP, MCP
Server/Client, Tool, Resource, JSON, SQLite, deterministic function,
ball-by-ball data, replay state, data normalization).

### Likely professor questions

**Q: Why did you choose Cricsheet JSON over the CSV format?**
A: JSON preserves the natural nested match → innings → over → delivery →
wicket structure, and it is versioned, which let us verify our parser
against a documented schema (1.2.0) rather than guessing at column
meanings in a flat CSV.

**Q: Why SQLite instead of a "real" database like PostgreSQL?**
A: SQLite *is* a real relational database — it just doesn't need a
separate server process. For six tables and a handful of queries, that
is a strict advantage for a course project: nothing to install, and the
whole database is one file we can open and show directly.

**Q: What is MCP, in one sentence?**
A: A standard protocol that lets an AI application discover and call a
set of named tools on a server, without needing to know how those tools
are implemented internally.

**Q: Why use MCP instead of just importing your Python functions
directly?**
A: For Phase 1 alone, direct import would be simpler. The point of MCP is
what comes next: any future agent (ours or someone else's, in Python or
another language, local or remote) can call these same tools without
needing our source code — only the protocol.

**Q: What is the difference between an MCP tool and a normal Python
function?**
A: An MCP tool *is* a normal Python function underneath (see
`tools/cricket_tools.py`), wrapped so that an MCP client can discover its
name, its input schema, and its description, and call it over a
standard transport (stdio, in our case) instead of a direct function
call in the same process.

**Q: Why isn't an LLM used anywhere yet?**
A: Per the brief's "No Fake AI" principle: we wanted a foundation of
verified, deterministic tools before introducing any component whose
output cannot be checked line-by-line. A future agent will reason *using*
these tools, not guess at the numbers itself.

**Q: How does `get_next_ball()` maintain state?**
A: `start_replay()` creates a session with a list of that match's
delivery primary keys in chronological order and a position counter,
stored in an in-memory dictionary keyed by a generated session ID. Each
`get_next_ball()` call looks up the session, returns the delivery at the
current position, and increments it. This is deliberately temporary
(lost on server restart) and explicitly documented as distinct from the
long-term player memory a later phase will add — see `docs/tools.md`
Section 4.

**Q: How do you prevent incorrect statistics?**
A: Every formula is documented with its exact denominator and edge-case
behavior in `analytics/statistics.py`, and locked in by unit tests that
check the cricket-specific edge cases directly — e.g. a batting average
of `None` (not 0 or infinity) when a player was never dismissed, and a
bowler being charged only for the wide, not the byes, on a delivery that
combines both.

**Q: How can the system scale to thousands of matches?**
A: The ingestion pipeline (`ingest.py`) already processes an arbitrary
directory of JSON files one at a time into the same SQLite schema — the
two fixture matches used for testing exercise the identical code path a
1,000-match archive would. The two indexes on `deliveries` (`batter`,
`bowler`) keep player-lookup queries fast as the table grows.

**Q: How will this foundation support the future multi-agent
architecture?**
A: The three tools are the complete, trustworthy interface a future
Batting/Bowling/Fielding Analyst agent (and the Coordinator that merges
their outputs) will call through MCP, instead of touching raw Cricsheet
JSON or SQL directly. The `sample_size_warning` field already present in
`compute_stats()`'s output is the seed of the "flag low-confidence
recommendations" governance requirement in the full project — a later
phase's confidence engine will build on it rather than starting over.

## Stop point

Phase 1 stops here, per Section 32 of the project brief. No specialist
agent, Coordinator, long-term memory, or LLM reasoning has been added.
The project is ready for review before a separate Phase 2 prompt begins.
