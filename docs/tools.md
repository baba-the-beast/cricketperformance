# Tools — Phase 1

## 1. Tool layer vs MCP layer

This is the most important architectural boundary in Phase 1, so it is
worth stating plainly:

```text
MCP Tool  (mcp_server.py)
    │   thin wrapper: open a connection, call the function below,
    │   translate our exceptions into a clean MCP error
    ▼
Python Tool Function  (tools/cricket_tools.py)
    │   the actual logic: SQL queries, calling the statistics engine,
    │   managing replay state
    ▼
Analytics / Database Layer  (analytics/statistics.py, database.py)
    │
    ▼
Cricsheet Data  (SQLite, populated by ingestion/)
```

**Why we keep the MCP layer thin:** `mcp_server.py`'s three tool
functions each do exactly three things: open a connection, call the
matching function in `tools/cricket_tools.py`, and let our own exception
types propagate (FastMCP turns them into a clean tool error for the
client automatically — see Section 4 below). No SQL, no statistics
formula, and no replay-state logic lives in `mcp_server.py`.

**Why this separation matters in practice, not just in theory:**
- `tests/test_tools.py` calls `cricket_tools.get_match_data()`,
  `compute_stats()`, and the replay functions **directly**, with a real
  in-memory SQLite database, with no MCP server involved at all. This is
  what makes those 20+ tests run in well under a second.
- A future phase's agent code (a Batting Analyst, say) could import and
  call `tools/cricket_tools.py` directly in Python, without going through
  MCP at all, if that ever turns out to be simpler than an MCP round
  trip — the option stays open because the logic was never welded to MCP
  in the first place.

## 2. Tool 1: `get_match_data`

**Input:** `match_id: str` — the Cricsheet file name (without `.json`)
the match was ingested from.

**Output:** a dictionary with `match_id`, `teams`, `date`, `venue`,
`city`, `competition`, `match_type`, `season`, `winner`,
`outcome_summary`, `player_of_match`, a list of per-innings summaries
(`innings_index`, `batting_team`, `total_runs`, `wickets_lost`,
`legal_deliveries_faced`), and the players who batted for each team.

**Error cases:** raises `MatchNotFoundError` if `match_id` is not in the
database. This is a deliberate design choice: an unknown match returns a
clear error, not an empty or partially-filled dictionary that a caller
could misread as "a real match with no data".

**Why this is a tool (not a resource):** see Section 5 below.

**Why it is deterministic:** the same `match_id`, against the same
database, always returns identical output — every value comes from a SQL
`SELECT` against data ingested earlier, with no external call and no
randomness.

**Example call:**
```python
get_match_data(conn, "1001")
# -> {"match_id": "1001", "teams": ["Team A", "Team B"], ...}
```

## 3. Tool 2: `compute_stats`

**Input:**
```python
compute_stats(
    conn,
    player_id: str,
    *,
    start_match: str | None = None,
    end_match: str | None = None,
    last_n_matches: int | None = None,
)
```

We chose this parameter shape (over the brief's original sketch of
`compute_stats(player, range)`) because "a range" is ambiguous on its
own — a range of what? Match IDs? Dates? Recent-N? Making the three valid
interpretations explicit, named parameters avoids that ambiguity, and
`last_n_matches` in particular is the shape a coach actually asks for
("how has this player looked over their last 5 games?").

If none of the three range parameters are given, the range defaults to
every match currently ingested for that player — a documented default,
not a hidden behavior.

**Output:** `player_id`, `matches_considered` (the resolved list of
match_ids the statistics were computed over — so the caller can always
see exactly what was included), `batting` (or `null` if the player never
batted in the range), `bowling` (or `null` if they never bowled), and
`sample_size_warning` (a string, or `null`), which is set whenever fewer
than 3 matches are considered.

See `analytics/statistics.py`'s module docstring for the exact formula
and cricket-law justification for every individual statistic (strike
rate, average, economy, etc.) — that reasoning is intentionally kept in
one place rather than duplicated here.

**Error cases:** `PlayerNotFoundError` if the player has no deliveries at
all; `InvalidRangeError` if `last_n_matches <= 0`, or if `start_match`
does not come before `end_match` in the player's own chronological match
order.

**Example call:**
```python
compute_stats(conn, "MS Dhoni", last_n_matches=5)
```

## 4. Tool 3: `start_replay` / `get_next_ball`

**Replay state vs long-term memory — the distinction that matters most
for the project's future architecture:**

| | Replay state | Long-term memory (a later phase) |
|---|---|---|
| **What it holds** | "Which delivery of match X comes next" | A player's performance history across many matches/seasons |
| **Lifespan** | One replay session; gone when it ends or the server restarts | Persists indefinitely, deliberately |
| **Storage (Phase 1)** | An in-memory Python dictionary (`_REPLAY_SESSIONS`) | Not built yet |
| **Purpose** | Let a caller step through one match ball-by-ball, in order | Let a future agent reason about trends over time |

Phase 1 implements only the left column. Building the right column before
the tools above exist and are trustworthy would be building on a
foundation that is not there yet — see Section 25 ("No Fake AI") of the
project brief.

**`start_replay(match_id) -> {session_id, match_id, total_deliveries}`**
Raises `MatchNotFoundError` if the match does not exist.

**`get_next_ball(session_id) -> {...one delivery's full detail...}`**
Each call advances the session by exactly one delivery (in Cricsheet's
own recorded order, across all innings). Raises
`ReplaySessionNotFoundError` for an unknown session, or `ReplayEndedError`
once every delivery has already been returned. It never repeats or
fabricates a delivery to avoid raising an error.

**This is NOT a live-data API.** It replays deliveries that already
happened, from data already sitting in our own database, strictly in the
order Cricsheet recorded them. A future agent describing this tool to
itself (or to a user) should say "replay" or "step through", never
"live" or "real-time".

## 5. MCP Tool vs MCP Resource

**Tool:** a named, callable function with typed inputs and an output —
the client asks the server to *do* something (e.g. "compute these
statistics") and gets a result back.

**Resource:** a named, addressable piece of data the client can *read*,
similar in spirit to a URL — the server exposes it, but doing something
with it beyond fetching it is the client's job.

**Which are we using in Phase 1, and why:** all three of our capabilities
are exposed as **tools**, not resources. `get_match_data` and
`compute_stats` both require server-side computation (SQL joins,
statistics formulas) before returning an answer — that is exactly what a
tool is for. `get_next_ball` additionally has to manage session state
across calls, which a resource (a static, addressable piece of data) is
not designed to do at all.

**We deliberately did not force a "resource" into the design just
because the original problem statement uses that word.** The Cricsheet
*archive* (the raw collection of match JSON files) is closer in spirit
to what MCP resources are for — a body of data a client could browse or
fetch directly, similar to a set of files. But Phase 1 does not expose
the raw archive to a client at all; it only exposes the three computed
tools above. Exposing raw ingested match JSON as an MCP resource (e.g.
one resource per `match_id`) is a reasonable feature for a **later
phase**, once there is an actual client use case that needs the raw data
rather than our tools' cleaned-up summaries — and this document flags
that as a deliberate, deferred design decision rather than an omission.

## 6. MCP testing workflow

Two ways to see this project's MCP server work, both documented in the
main `README.md` "Running the MCP server" section:

1. **The MCP Inspector** (`mcp dev src/cricket_agent/mcp_server.py`) — a
   browser-based GUI for interactively listing tools and calling them by
   filling in a form. Best for human exploration.
2. **`tests/test_mcp_server.py`** — an automated `pytest` suite that
   calls the exact same `FastMCP` object `mcp_server.py` exposes, the
   same way any MCP client library would (`list_tools()`, `call_tool()`),
   plus a real subprocess round-trip test over stdio using the official
   `mcp` Python client (`ClientSession` + `stdio_client`), which spawns
   `python -m cricket_agent.mcp_server` as a genuine second process and
   talks to it — this is the same transport the Inspector and Claude
   Desktop use. Neither of these requires Claude Desktop to run.
