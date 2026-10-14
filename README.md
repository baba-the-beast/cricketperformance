# Cricket Team Performance Agent

An agentic cricket analytics and selection-support system for a college
project demonstration. It combines **historical Cricsheet data**,
**deterministic statistics**, **MCP tools**, **specialist analyst agents**,
and **confidence-aware recommendations** so a coach can inspect evidence
rather than a black-box score.

The Phase 1 pipeline (ingestion, SQLite, `compute_stats`, MCP replay tools)
is preserved. Later layers sit on top of those same functions — they do not
replace them.

> **Data honesty:** the repo ships with schema-accurate **College Trial
> League fixtures** under `tests/fixtures/`. The dashboard labels this as
> sample/demo data. Ingest a real Cricsheet archive (see below) if you want
> larger samples; the code path is identical.

Read `docs/architecture.md`, `docs/data_model.md`, and `docs/tools.md`
for design reasoning.

## Implemented

- Cricsheet JSON ingestion → SQLite (`matches`, `innings`, `deliveries`, `wickets`, plus additive `players` / session / memory tables)
- Deterministic batting and bowling formulas (`analytics/statistics.py`)
- MCP tools: `get_match_data`, `compute_stats`, `start_replay`, `get_next_ball`, plus `get_previous_ball`, `analyze_player`, `compare_players`
- Player form, matchups, T20 phases, explainable roles, HIGH/MEDIUM/LOW confidence
- Fielding **credits** from `wickets.fielders` only (no dropped-catch rates)
- Batting / Bowling / Fielding analysts and a Coordinator (deterministic: they compute every figure and decide the order)
- **LLM explanation step**: Claude (`claude-opus-5-5`, Anthropic API) writes the coordinator's explanation from the analysts' evidence; a grounding check rejects any figure not in that evidence, and the app falls back to a rule-based summary when no API key is set (`agents/narrator.py`)
- Long-term player snapshots and analysis-session memory
- Structured selection report and historical-statistics-based simulation
- **Match Predictor**: evidence-ranked best-XI recommendation combining recent form, ground/venue scoring history, and opponent-roster matchups, with optional manual pitch/weather notes (`analytics/venue.py`, `analytics/conditions.py`, `agents/selector.py`)
- Dynamic Player Explorer backed by the active SQLite player universe
- Dataset aggregates, venue history API, and optional upcoming-match provider boundary
- Flask command-center UI: overview, players, upcoming matches, compare, replay (extras labelled), 3D simplified pitch, simulation, agent view, Match Predictor, architecture

## Not implemented / future work

- A live weather/forecast API (Match Predictor accepts a manual pitch/weather note, or a pluggable `ConditionsProvider`, same boundary pattern as `external_matches.py`; none is configured out of the box)
- Pace vs spin roles (Cricsheet deliveries do not record bowling style) -- this also means the Match Predictor cannot reason about pace-friendly vs spin-friendly conditions, only overall scoring patterns at a venue
- Dropped catches, fielding positions, expected runs
- A win-probability match engine (simulation is an expected-value walk-through; the Match Predictor ranks evidence, it does not output a win percentage)
- Physically accurate ball trajectories (3D replay is explicitly simplified)
- Upcoming fixtures when no permitted external provider is configured

## Problem

Coaches need to combine recent form, matchup context, and sample-size
warnings when picking a side. This project keeps every number traceable to
stored deliveries and refuses to invent statistics when the sample is thin.

## Architecture

```text
Cricsheet JSON
    → Ingestion / parser
    → SQLite
    → Deterministic analytics (same engine as Phase 1)
    → MCP server  (protocol wrappers)
    → Specialist agents  (in-process Python; same functions MCP wraps)
    → Coordinator
    → Selection report + player memory
    → Flask coach dashboard
```

The Architecture page in the UI distinguishes **MCP protocol calls**
(`channel=mcp`) from **in-process function calls** used by the dashboard.

## Dataset

Fixtures: `tests/fixtures/match_1001.json`, `match_1002.json` (fictional
College Trial League, Cricsheet JSON 1.2.0).

Real data: use the source-based downloader below. The IPL URL is resolved
from the current official Cricsheet downloads page at runtime.

## Confidence governance

Confidence is **not** a model probability. It is a category derived from
counts (matches, innings, balls, matchup sample). Fixture-sized samples
correctly come back **LOW**.

## Frontend

`python webapp/app.py` then open http://127.0.0.1:5000

Install Flask from `webapp/requirements.txt` if needed. The dashboard uses
the players and matches currently present in SQLite.

The Player Explorer supports database-backed name, team, country, format,
active-dataset, and sort filters. Upcoming/live fixtures can be retrieved
from CricketData.org (formerly CricAPI) when `CRICKETDATA_API_KEY` is set in
the server environment. The provider is optional, server-side, and cached for
one hour. Its free service is near-live (the provider says data may be a few
minutes behind), not an official ICC/BCCI feed. No fixtures are fabricated
when the key is absent or the provider is unavailable.

Set the key in the same shell that starts the app; do not commit it or place
it in browser JavaScript:

```bash
export CRICKETDATA_API_KEY="your-key"
python webapp/app.py
```

## Replay

Ball-by-ball replay uses stored deliveries in Cricsheet order. A wide and
the following legal ball can share `actual_delivery` (e.g. `0.4`). The UI
labels WIDE / NO BALL / BYE / LEG BYE. 3D animation is a simplified arc.

## Simulation

`/api/simulate-match` simulates both innings ball by ball, with an ODI (50
overs) or T20 (20 overs) default, strike rotation, bowler rotation, all-out
and chase completion, and a reproducible seed. It samples the selected
players' most recent 600 recorded deliveries in the requested format. If a
player has no matching history, a generic format-specific distribution is
used and disclosed. Full-length matches require 11 batters and at least five
bowling options per side.

The local Cricsheet directory currently contains IPL T20 data. Therefore an
ODI simulation has no local ODI outcome history and uses generic fallback
outcomes; it is not an accurate forecast. Likewise, the public fixture feed
does not guarantee playing XIs. Local team-roster suggestions are evidence-
ranked only when that team and format exist in the active database; otherwise
enter a verified XI manually. A scorecard and charts are generated from every
simulated delivery, but a simulated player of the match is not invented.

## Match Predictor

`recommend_lineup()` (`src/cricket_agent/agents/selector.py`) is the
"who should we pick for this match" tool: give it a squad, and optionally
an opponent, a venue, and a manual pitch/weather note, and it returns a
ranked XI plus bench, each with a plain evidence list.

What it actually does, precisely:

- **Recent form** -- reuses `analytics/form.py` (already part of every
  player profile).
- **Ground / pitch** -- `analytics/venue.py` characterises a venue from
  its own historical scoring (average first/second innings score,
  boundary rate) and gives each candidate their own record at that venue.
  There is no soil/grass/pitch-report data in Cricsheet, so this is a
  *scoring-pattern* summary, not a groundsman's report -- and it says so.
- **Opponent** -- `analytics/matchup.py:player_vs_team()` aggregates a
  player's deliveries against the opponent's known roster specifically,
  not just "everyone they've ever played."
- **Forecast / conditions** -- `analytics/conditions.py` accepts a manual
  pitch/weather note (a coach's own report), or a pluggable
  `ConditionsProvider` for a future live API. With neither configured, it
  says so honestly rather than guessing today's weather.
- **Ranking** -- candidates are grouped into the same HIGH/MEDIUM/LOW
  confidence tiers used everywhere else in this project (from sample
  size). Ties within a tier are broken by whether venue/opponent-specific
  evidence exists, then by plain recorded runs/wickets -- there is no
  hidden combined "score."

This is an **evidence-ranked recommendation**, not a machine-learned win
probability. It is exposed at `POST /api/recommend_lineup` and in the
"Match Predictor" dashboard tab.

## Installation

### macOS/Linux
```bash
git clone <your-repo-url>
cd cricket-team-performance-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r webapp/requirements.txt
```

### Windows PowerShell
```powershell
git clone <your-repo-url>
cd cricket-team-performance-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r webapp/requirements.txt
```

A virtual environment only works on the operating system that created it. If
the project folder already contains a `venv` or `.venv` made on another OS,
create a fresh one under a new name (for example `.venv-windows`).

The MCP server needs the MCP SDK **1.x** (`mcp>=1.28,<2`, pinned in
`requirements.txt`); a globally installed `mcp` 2.x will not work.

### Enabling the LLM step (optional)

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # macOS/Linux: export ANTHROPIC_API_KEY=...
```

Without a key everything still runs; the selection report shows the
rule-based summary and says why.

## Dataset setup

Phase 1 ships with fixture data under `tests/fixtures/` so tests and the
demo work immediately with **no download step**. To ingest real
Cricsheet data instead:

1. Explicitly request a dataset. Nothing is downloaded automatically. The
    first real-data demonstration uses IPL:

### macOS/Linux
```bash
PYTHONPATH=src python3 -m cricket_agent.ingest --source ipl
```

### Windows PowerShell
```powershell
$env:PYTHONPATH = "src"
python -m cricket_agent.ingest --source ipl
```

For a custom ZIP or local JSON directory:

```bash
PYTHONPATH=src python3 -m cricket_agent.ingest --source custom --path /path/to/cricsheet.zip
PYTHONPATH=src python3 -m cricket_agent.ingest --source custom --path data/raw/my-json-directory
```

## Database setup

### macOS/Linux
```bash
PYTHONPATH=src python3 -m cricket_agent.ingest tests/fixtures
# or, once you've downloaded real data:
PYTHONPATH=src python3 -m cricket_agent.ingest data/raw/ipl_male
```

### Windows PowerShell
```powershell
$env:PYTHONPATH = "src"
python -m cricket_agent.ingest tests/fixtures
```

This creates `data/processed/cricket.db`. Re-running ingestion is safe.

## Running tests

```bash
python3 -m pytest -v
```

(Windows: `python -m pytest -v`)

`pyproject.toml` configures pytest to find `cricket_agent` under `src/`.

## Running the Phase 1 CLI demo

```bash
python3 demo_phase1.py
```

## Running the web dashboard

```bash
PYTHONPATH=src python3 -m cricket_agent.ingest tests/fixtures
python3 webapp/app.py
```

Open http://127.0.0.1:5000

`webapp/app.py` serves with **waitress** (production WSGI server, debugger off)
and warms its response caches in the background on startup. Configure it with
environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `CRICKET_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` to share on a LAN) |
| `CRICKET_PORT` | `5000` | Port |
| `CRICKET_THREADS` | `8` | Worker threads |
| `CRICKET_DEBUG` | unset | `1` = Flask dev server with reloader and debugger (local development only; the debugger can run code for anyone who reaches the port) |
| `CRICKETDATA_API_KEY` | unset | Enables the Upcoming Matches feed |
| `ANTHROPIC_API_KEY` | unset | Enables the LLM explanation step (Claude) |
| `CRICKET_LLM` | `on` | `off` skips the LLM step and always uses the rule-based summary |
| `CRICKET_LLM_MODEL` | `claude-opus-5-5` | Claude model used for the explanation |

`GET /healthz` reports liveness and database readiness. API requests are
logged with status and duration.

### Demo flow (viva)

1. Overview — match/player/delivery counts (sample badge if fixtures)
2. Players → A Sharma — batting, bowling, form, confidence, role
3. Compare A Sharma vs D Singh
4. Agent Analysis — question “Which player should be considered for the next selection?”
5. Selection Report — evidence + LOW confidence flags on fixture data
6. Match Predictor — squad + opponent + venue (+ optional manual pitch/weather note) → ranked XI
7. Match Replay — extras labelled; optional 3D pitch
8. Simulation — labelled historical-statistics scenario
9. Architecture — pipeline + MCP tool list

## Running the MCP server

```bash
PYTHONPATH=src python3 -m cricket_agent.mcp_server
```

Optional Inspector: `npx @modelcontextprotocol/inspector python3 -m cricket_agent.mcp_server`

## Project structure

```text
cricket-team-performance-agent/
├── README.md
├── requirements.txt
├── pyproject.toml
├── demo_phase1.py
├── data/raw  data/processed
├── src/cricket_agent/
│   ├── analytics/   statistics, form, matchup, venue, conditions, roles, confidence, profile
│   ├── agents/      specialists, coordinator, selector, runner
│   ├── memory.py    player snapshots + analysis sessions
│   ├── simulation.py
│   ├── external_matches.py   optional upcoming-match provider boundary
│   ├── tools/cricket_tools.py
│   ├── mcp_server.py
│   └── ...
├── webapp/          Flask dashboard
├── tests/
└── docs/
```

## Limitations

- Small fixture samples yield LOW confidence by design.
- No bowling-style field in Cricsheet JSON used here.
- Dashboard analysis is in-process; it does not pretend every click is MCP.
- Replay state is process-local (lost on server restart).

## Troubleshooting

**`ModuleNotFoundError: No module named 'cricket_agent'`**
Put `src/` on `PYTHONPATH` when running scripts. pytest already does this.

**`sqlite3.OperationalError: unable to open database file`**
Run from the project root so `data/processed/` can be created.

**Database unavailable in the UI**
Ingest first: `PYTHONPATH=src python3 -m cricket_agent.ingest tests/fixtures`
