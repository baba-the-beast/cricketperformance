# Data Model — Phase 1

## 1. Research findings: what Cricsheet actually provides

This section records what was verified directly against Cricsheet's own
documentation (`https://cricsheet.org/format/json/` and
`https://cricsheet.org/downloads/`) before any code was written, per the
project brief's instruction not to assume a field exists merely because
it would be useful.

**Format and version:** Cricsheet's current JSON format is versioned; the
files this project was developed and tested against declare
`"meta": {"data_version": "1.2.0"}`. Every field this project reads is
one documented at that schema version.

**Fields confirmed to exist** (and used by this project):
- `info.teams` (exactly two team names), `info.dates`, `info.match_type`,
  `info.gender`, `info.team_type`, `info.venue`, `info.city`,
  `info.season`, `info.event.name`, `info.toss`, `info.outcome`
  (`winner`, `by.runs`, `by.wickets`, or `result` for a tie/draw/no
  result), `info.player_of_match`, `info.players` (team → player-name
  list), `info.registry.people` (player name → a stable Cricsheet person
  ID — see "Player identity" below).
- Per innings: `team`, `overs` (a list of `{over, deliveries}`), and,
  when a side did not bat, `forfeited: true` with no `overs` key at all
  (Phase 1 handles this by recording zero deliveries for that innings,
  not by crashing or inventing overs).
- Per delivery: `batter`, `bowler`, `non_striker`, `runs.batter`,
  `runs.extras`, `runs.total`, `actual_delivery` (the true chronological
  ball label, which can repeat across a wide/no-ball and the retry that
  follows it), `extras` (an object whose keys — `wides`, `noballs`,
  `byes`, `legbyes`, `penalty` — each hold the *number of runs* of that
  extra type on that delivery, not just a flag), and `wickets` (a list,
  because Cricsheet allows more than one dismissal on a single delivery
  in rare cases), each with `kind`, `player_out`, and `fielders`
  (`fielders[].name`).

**Fields verified to exist but deliberately NOT used in Phase 1:**
- `info.registry.people` (a stable ID) — see "Player identity" below.
- Detailed fielding attribution beyond "who took the catch/run-out" (the
  `fielders` list on a wicket). Cricsheet does not record dropped
  catches, fielding position, or expected-runs-saved. **We do not invent
  these.**

**Statistics we can safely calculate today** (backed directly by the
fields above): batting innings/runs/balls faced/average/strike
rate/fours/sixes; bowling overs/runs conceded/wickets/economy/average/
strike rate/dot balls; per-match team totals and wickets lost; the
identity of who took a specific catch/stumping/run-out on a given ball.

**Statistics we should NOT claim to calculate yet** (because the
underlying data does not exist in Cricsheet, or Phase 1 does not yet
build the aggregation for it): fielding "opportunities" or dropped-catch
rates (not recorded at all), true batter-vs-bowler matchup histories
across a whole career (would require aggregating across the entire
archive, not just an ingested subset — a later-phase feature),
partnership statistics (would require additional aggregation not yet
built), and anything phrased as "expected runs" or a similar
model-derived statistic (Cricsheet contains only what actually happened,
never a projection).

## 2. Why Phase 1's schema differs from the original 6-table sketch

The original project brief sketched: `matches`, `players`, `innings`,
`deliveries`, `wickets`, `fielding_events`. Phase 1 implements **four**
of these (`matches`, `innings`, `deliveries`, `wickets`) and deliberately
defers the other two:

**`players` (deferred):** Cricsheet's `registry.people` maps each name to
a stable ID (e.g. `"MS Dhoni": "3caa4...`"), which *would* be the right
foundation for a `players` table once we need to, for example, merge two
Cricsheet name spellings for the same person, or attach long-term memory
to a player (a later phase's job). Phase 1 has no such need yet: every
query Phase 1 runs (`compute_stats`, `get_match_data`) works correctly
using the name string directly, because a single ingestion run keeps
names consistent. Adding a `players` table today, with nothing yet
populating or using the extra column it would add, would be schema we
cannot justify. This is a **deliberate deferral**, documented here so a
later phase does not have to rediscover why it is missing.

**`fielding_events` (deferred):** No fielding statistic is computed in
Phase 1 (see Section 1 above). A table with no consumer is not
"forward-thinking", it is dead schema a professor would rightly ask us
to explain. The `wickets` table already stores everything Cricsheet
provides about fielding involvement (the `fielders` column, a
comma-joined list of names credited with a catch/run-out/stumping),
which is sufficient for Phase 1 and a reasonable starting point for a
future `fielding_events` table.

## 3. Entity-relationship description

```text
matches (1) ──< (many) innings (1) ──< (many) deliveries (1) ──< (many) wickets
```

- One **match** has exactly two **innings** in a completed limited-overs
  match (more in a multi-innings format like a Test, which Phase 1's
  schema already supports since `innings_index` is not constrained to
  0/1).
- One **innings** has many **deliveries**, in strict order
  (`over_number`, `ball_in_over`).
- One **delivery** has zero, one, or (rarely) more than one **wicket**.

### `matches`

| Column            | Type | Notes                                             |
|-------------------|------|----------------------------------------------------|
| `match_id`        | TEXT | **Primary key.** The Cricsheet file name (no `.json`). |
| `team_home`       | TEXT | `info.teams[0]`.                                   |
| `team_away`       | TEXT | `info.teams[1]`.                                   |
| `match_type`      | TEXT | e.g. `"T20"`, `"ODI"`, `"Test"`.                   |
| `gender`          | TEXT | `info.gender`.                                     |
| `venue`           | TEXT | Nullable — not every match record includes it.     |
| `city`            | TEXT | Nullable.                                          |
| `season`          | TEXT | Nullable.                                          |
| `event_name`      | TEXT | Nullable — the competition name, if any.           |
| `match_date`      | TEXT | The first date in `info.dates`.                    |
| `winner`          | TEXT | Nullable — absent for a tie/draw/no-result.        |
| `outcome_summary` | TEXT | A short, human-readable one-line summary we derive; see `ingestion/parser.py::_summarize_outcome`. |
| `player_of_match` | TEXT | Nullable; comma-joined if more than one name.      |

### `innings`

| Column          | Type    | Notes                                          |
|-----------------|---------|-------------------------------------------------|
| `match_id`      | TEXT    | **Part of composite primary key.** FK → `matches`. |
| `innings_index` | INTEGER | **Part of composite primary key.** 0-based, in the order Cricsheet lists innings. |
| `batting_team`  | TEXT    | Which team batted this innings.                |

### `deliveries`

| Column            | Type    | Notes                                                  |
|-------------------|---------|---------------------------------------------------------|
| `delivery_pk`     | INTEGER | **Primary key** (auto-increment; our own surrogate key, not a Cricsheet field). |
| `match_id`, `innings_index` | TEXT, INTEGER | FK → `innings`.                              |
| `over_number`     | INTEGER | 0-based over number within the innings.                |
| `ball_in_over`    | INTEGER | 1-based position of this delivery within its JSON `overs[].deliveries` list (NOT the same as the legal-ball count — a wide followed by a retry both get their own `ball_in_over`). |
| `actual_delivery` | TEXT    | Cricsheet's own true chronological ball label (e.g. `"0.4"`), which can repeat. |
| `batter`, `bowler`, `non_striker` | TEXT | Player names exactly as Cricsheet records them. |
| `runs_batter`     | INTEGER | Runs credited to the batter's individual score.        |
| `runs_total`      | INTEGER | Total runs from this delivery, including all extras.   |
| `runs_wides`, `runs_noballs`, `runs_byes`, `runs_legbyes`, `runs_penalty` | INTEGER | The *exact amount* of each extra type on this delivery (usually 0). Stored separately, rather than as a single "is this a wide?" flag, specifically so a rare delivery combining two extra types (e.g. byes run after a wide is called) can still be split correctly between "charged to the bowler" and "not charged to the bowler" — see `analytics/statistics.py`. |

### `wickets`

| Column        | Type    | Notes                                                    |
|---------------|---------|------------------------------------------------------------|
| `wicket_pk`   | INTEGER | **Primary key** (auto-increment; our own surrogate key).  |
| `delivery_pk` | INTEGER | FK → `deliveries`. Which ball this dismissal happened on. |
| `kind`        | TEXT    | e.g. `"bowled"`, `"caught"`, `"lbw"`, `"run out"`.        |
| `player_out`  | TEXT    | The dismissed player's name.                              |
| `fielders`    | TEXT    | Nullable; comma-joined fielder name(s) credited, if any.  |

### Indexes

`idx_deliveries_batter`, `idx_deliveries_bowler`, and
`idx_deliveries_match` speed up the exact three query patterns
`compute_stats` and `get_match_data` use (filter by player name, filter
by match). No other index was added, since no other query pattern exists
yet in Phase 1.

## 4. Player identity (a note for a later phase)

Phase 1 uses a player's exact Cricsheet name string as their identifier
everywhere (`compute_stats(player_id="A Sharma")`). This is simple and
correct as long as one ingestion run's data is internally consistent,
which it is. It is **not** robust to the same real person appearing under
two different name spellings across different Cricsheet files (a known,
documented quirk of the real archive). Cricsheet's own
`info.registry.people` section exists specifically to solve that problem
with a stable ID, and a later phase that ingests the full archive across
many competitions should introduce a `players` table keyed on that
registry ID rather than the name string. We flag this now, deliberately,
rather than silently building something that would need to be reworked
without warning.
