"""
SQLite database layer.

Term: SQLite
    A relational database engine that stores an entire database as one
    ordinary file on disk (no separate server process to install or run).

Why we use it:
    Phase 1 explicitly avoids infrastructure that would distract from the
    core pipeline (see project brief section 3). SQLite ships inside
    Python's standard library (the `sqlite3` module), needs no
    installation on Windows or macOS, and is still a real relational
    database with real SQL -- so a professor can open cricket.db directly
    with any SQLite browser and see exactly what we stored.

In our project:
    `create_schema()` builds six small tables (see docs/data_model.md for
    the full entity-relationship description). `insert_match()` writes one
    parsed Match into those tables. Every query the statistics engine and
    the tools need is a plain SQL SELECT, kept in this module so SQL lives
    in exactly one place.

Design choice: no ORM.
    An ORM (e.g. SQLAlchemy) adds a translation layer between Python
    objects and SQL that is not needed for six tables and a handful of
    queries, and it would make it harder to show a professor the exact SQL
    being run.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from cricket_agent.models import Match

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS matches (
    match_id            TEXT PRIMARY KEY,
    team_home           TEXT NOT NULL,
    team_away           TEXT NOT NULL,
    match_type          TEXT NOT NULL,
    gender              TEXT NOT NULL,
    venue               TEXT,
    city                TEXT,
    season              TEXT,
    event_name          TEXT,
    match_date          TEXT,
    winner              TEXT,
    outcome_summary     TEXT,
    player_of_match     TEXT
);

CREATE TABLE IF NOT EXISTS innings (
    match_id            TEXT NOT NULL,
    innings_index       INTEGER NOT NULL,
    batting_team        TEXT NOT NULL,
    PRIMARY KEY (match_id, innings_index),
    FOREIGN KEY (match_id) REFERENCES matches(match_id)
);

CREATE TABLE IF NOT EXISTS deliveries (
    delivery_pk         INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id            TEXT NOT NULL,
    innings_index       INTEGER NOT NULL,
    over_number         INTEGER NOT NULL,
    ball_in_over        INTEGER NOT NULL,
    actual_delivery     TEXT NOT NULL,
    batter              TEXT NOT NULL,
    bowler              TEXT NOT NULL,
    non_striker         TEXT NOT NULL,
    runs_batter         INTEGER NOT NULL,
    runs_total          INTEGER NOT NULL,
    runs_wides          INTEGER NOT NULL DEFAULT 0,
    runs_noballs        INTEGER NOT NULL DEFAULT 0,
    runs_byes           INTEGER NOT NULL DEFAULT 0,
    runs_legbyes        INTEGER NOT NULL DEFAULT 0,
    runs_penalty        INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (match_id, innings_index)
        REFERENCES innings(match_id, innings_index)
);

CREATE TABLE IF NOT EXISTS wickets (
    wicket_pk           INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_pk         INTEGER NOT NULL,
    kind                TEXT NOT NULL,
    player_out          TEXT NOT NULL,
    fielders            TEXT,
    FOREIGN KEY (delivery_pk) REFERENCES deliveries(delivery_pk)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_batter ON deliveries(batter);
CREATE INDEX IF NOT EXISTS idx_deliveries_bowler ON deliveries(bowler);
CREATE INDEX IF NOT EXISTS idx_deliveries_match ON deliveries(match_id);
-- Composite/covering indexes: on the full Cricsheet archive (11M+ deliveries)
-- per-player and per-innings queries must be answerable from an index alone.
CREATE INDEX IF NOT EXISTS idx_deliveries_batter_bowler ON deliveries(batter, bowler);
CREATE INDEX IF NOT EXISTS idx_deliveries_batter_match ON deliveries(batter, match_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_bowler_match ON deliveries(bowler, match_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_innings_runs
    ON deliveries(match_id, innings_index, runs_total, runs_batter);

CREATE INDEX IF NOT EXISTS idx_wickets_delivery ON wickets(delivery_pk);
CREATE INDEX IF NOT EXISTS idx_wickets_player_out ON wickets(player_out);

CREATE TABLE IF NOT EXISTS players (
    player_name         TEXT PRIMARY KEY,
    team                TEXT,
    registry_id         TEXT,
    country             TEXT,
    role                TEXT,
    batting_style       TEXT,
    bowling_style       TEXT,
    date_of_birth       TEXT
);

CREATE TABLE IF NOT EXISTS player_participation (
    player_name         TEXT NOT NULL,
    match_id            TEXT NOT NULL,
    team                TEXT,
    format              TEXT,
    dataset_name        TEXT,
    PRIMARY KEY (player_name, match_id),
    FOREIGN KEY (player_name) REFERENCES players(player_name),
    FOREIGN KEY (match_id) REFERENCES matches(match_id)
);

CREATE INDEX IF NOT EXISTS idx_participation_team ON player_participation(team);
CREATE INDEX IF NOT EXISTS idx_participation_cover
    ON player_participation(player_name, match_id, format, team);
CREATE INDEX IF NOT EXISTS idx_matches_venue ON matches(venue);

CREATE TABLE IF NOT EXISTS teams (
    team_name           TEXT PRIMARY KEY,
    country             TEXT
);

CREATE TABLE IF NOT EXISTS venues (
    venue_name          TEXT PRIMARY KEY,
    city                TEXT,
    country             TEXT
);

CREATE TABLE IF NOT EXISTS external_matches (
    match_id            TEXT PRIMARY KEY,
    team_a              TEXT NOT NULL,
    team_b              TEXT NOT NULL,
    match_date          TEXT,
    match_time          TEXT,
    venue               TEXT,
    format              TEXT,
    competition         TEXT,
    source              TEXT NOT NULL,
    status               TEXT
);

CREATE TABLE IF NOT EXISTS analysis_sessions (
    session_id          TEXT PRIMARY KEY,
    question            TEXT NOT NULL,
    selected_players    TEXT,
    selected_match      TEXT,
    last_n_matches      INTEGER,
    opponent            TEXT,
    coordinator_json    TEXT,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_pk              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL,
    agent_name          TEXT NOT NULL,
    status              TEXT NOT NULL,
    payload_json        TEXT,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES analysis_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_session ON agent_runs(session_id);

CREATE TABLE IF NOT EXISTS player_memory (
    player_name         TEXT PRIMARY KEY,
    snapshot_json       TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_metadata (
    metadata_key        TEXT PRIMARY KEY,
    dataset_name        TEXT NOT NULL,
    source              TEXT NOT NULL,
    is_demo             INTEGER NOT NULL DEFAULT 0,
    match_count         INTEGER NOT NULL DEFAULT 0,
    player_count        INTEGER NOT NULL DEFAULT 0,
    delivery_count      INTEGER NOT NULL DEFAULT 0,
    date_start          TEXT,
    date_end            TEXT,
    teams_json          TEXT,
    formats_json        TEXT,
    competitions_json   TEXT,
    imported_at         TEXT NOT NULL
);

-- Whole-database aggregates (dashboard KPIs, leaderboards) that need a full
-- scan of deliveries. Keyed by a dataset signature so a re-ingest
-- invalidates them; purely derived, safe to delete at any time.
CREATE TABLE IF NOT EXISTS aggregate_cache (
    cache_key           TEXT PRIMARY KEY,
    signature           TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- A counter bumped on any change to players, so cache signatures notice
-- in-place metadata edits that do not change match or delivery counts.
CREATE TABLE IF NOT EXISTS data_revision (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    revision            INTEGER NOT NULL
);
INSERT OR IGNORE INTO data_revision (id, revision) VALUES (1, 0);
CREATE TRIGGER IF NOT EXISTS players_revision_insert AFTER INSERT ON players
BEGIN UPDATE data_revision SET revision = revision + 1 WHERE id = 1; END;
CREATE TRIGGER IF NOT EXISTS players_revision_update AFTER UPDATE ON players
BEGIN UPDATE data_revision SET revision = revision + 1 WHERE id = 1; END;
CREATE TRIGGER IF NOT EXISTS players_revision_delete AFTER DELETE ON players
BEGIN UPDATE data_revision SET revision = revision + 1 WHERE id = 1; END;
"""
# Additive tables (players, analysis_sessions, agent_runs, player_memory)
# are created with IF NOT EXISTS so an existing Phase 1 cricket.db remains
# readable. Fielding still lives on wickets.fielders -- there is no
# invented fielding_events table.


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with sane defaults for this project.

    `row_factory = sqlite3.Row` lets callers access columns by name
    (row["runs_total"]) instead of a fragile positional index.
    Foreign keys are OFF by default in SQLite; we turn them on so that a
    bug that tries to insert a delivery for a non-existent match fails
    loudly during development instead of silently corrupting the data.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn



def clear_database(conn: sqlite3.Connection) -> None:
    """Drop all tables to prepare for a clean ingestion of a different dataset."""
    tables = [
        "aggregate_cache", "dataset_metadata", "venues", "teams", "player_participation",
        "players", "wickets", "deliveries", "innings", "matches", "external_matches",
        # Snapshots and agent runs describe the old dataset; keeping them would
        # show stale memory against the new one.
        "player_memory", "agent_runs", "analysis_sessions", "data_revision",
    ]
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not already exist."""
    conn.executescript(SCHEMA_SQL)
    # Additive migrations for databases created before the registry fields.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(players)")}
    for name, sql_type in (
        ("registry_id", "TEXT"), ("country", "TEXT"), ("role", "TEXT"),
        ("batting_style", "TEXT"), ("bowling_style", "TEXT"), ("date_of_birth", "TEXT"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE players ADD COLUMN {name} {sql_type}")
    metadata_columns = {row["name"] for row in conn.execute("PRAGMA table_info(dataset_metadata)")}
    for name in ("teams_json", "formats_json", "competitions_json"):
        if name not in metadata_columns:
            conn.execute(f"ALTER TABLE dataset_metadata ADD COLUMN {name} TEXT")
    conn.commit()


def replace_dataset_metadata(
    conn: sqlite3.Connection,
    *,
    dataset_name: str,
    source: str,
    is_demo: bool,
) -> None:
    """Store identity and counts for the dataset currently in this database."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS match_count,
               (SELECT COUNT(*) FROM players) AS player_count,
               (SELECT COUNT(*) FROM deliveries) AS delivery_count,
               MIN(match_date) AS date_start,
               MAX(match_date) AS date_end,
               (SELECT json_group_array(team_name) FROM teams) AS teams_json,
               (SELECT json_group_array(DISTINCT match_type) FROM matches) AS formats_json,
               (SELECT json_group_array(DISTINCT event_name) FROM matches WHERE event_name IS NOT NULL) AS competitions_json
        FROM matches
        """
    ).fetchone()
    conn.execute("DELETE FROM dataset_metadata")
    conn.execute(
        """
        INSERT INTO dataset_metadata (
            metadata_key, dataset_name, source, is_demo, match_count,
            player_count, delivery_count, date_start, date_end,
            teams_json, formats_json, competitions_json, imported_at
        ) VALUES ('current', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            dataset_name,
            source,
            int(is_demo),
            row["match_count"],
            row["player_count"],
            row["delivery_count"],
            row["date_start"],
            row["date_end"],
            row["teams_json"] or "[]",
            row["formats_json"] or "[]",
            row["competitions_json"] or "[]",
        ),
    )
    conn.commit()


def insert_match(conn: sqlite3.Connection, match: Match) -> None:
    """Insert one parsed Match (and all its innings/deliveries/wickets).

    Uses `INSERT OR REPLACE` for the match row so re-running ingestion on
    the same file is safe (idempotent) rather than raising a duplicate-key
    error, which matters because Cricsheet occasionally issues a
    "revision" of a match file that fixes an error in the original data.
    """
    info = match.info
    conn.execute(
        """
        INSERT OR REPLACE INTO matches (
            match_id, team_home, team_away, match_type, gender,
            venue, city, season, event_name, match_date, winner,
            outcome_summary, player_of_match
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            info.match_id,
            info.teams[0],
            info.teams[1],
            info.match_type,
            info.gender,
            info.venue,
            info.city,
            info.season,
            info.event_name,
            info.dates[0] if info.dates else None,
            info.winner,
            info.outcome_summary,
            ", ".join(info.player_of_match) or None,
        ),
    )

    # Clear out any previously-inserted innings/deliveries/wickets for this
    # match_id before re-inserting, so re-running ingestion never leaves
    # stale duplicate deliveries behind.
    delivery_pks = [
        row["delivery_pk"]
        for row in conn.execute(
            "SELECT delivery_pk FROM deliveries WHERE match_id = ?",
            (info.match_id,),
        )
    ]
    if delivery_pks:
        conn.executemany(
            "DELETE FROM wickets WHERE delivery_pk = ?",
            [(pk,) for pk in delivery_pks],
        )
    conn.execute("DELETE FROM deliveries WHERE match_id = ?", (info.match_id,))
    conn.execute("DELETE FROM innings WHERE match_id = ?", (info.match_id,))

    for innings in match.innings:
        conn.execute(
            """
            INSERT INTO innings (match_id, innings_index, batting_team)
            VALUES (?, ?, ?)
            """,
            (innings.match_id, innings.innings_index, innings.batting_team),
        )

        for delivery in innings.deliveries:
            cursor = conn.execute(
                """
                INSERT INTO deliveries (
                    match_id, innings_index, over_number, ball_in_over,
                    actual_delivery, batter, bowler, non_striker,
                    runs_batter, runs_total,
                    runs_wides, runs_noballs, runs_byes, runs_legbyes, runs_penalty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery.match_id,
                    delivery.innings_index,
                    delivery.over_number,
                    delivery.ball_in_over,
                    delivery.actual_delivery,
                    delivery.batter,
                    delivery.bowler,
                    delivery.non_striker,
                    delivery.runs_batter,
                    delivery.runs_total,
                    delivery.runs_wides,
                    delivery.runs_noballs,
                    delivery.runs_byes,
                    delivery.runs_legbyes,
                    delivery.runs_penalty,
                ),
            )
            delivery_pk = cursor.lastrowid
            for wicket in delivery.wickets:
                conn.execute(
                    """
                    INSERT INTO wickets (delivery_pk, kind, player_out, fielders)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        delivery_pk,
                        wicket.kind,
                        wicket.player_out,
                        ", ".join(wicket.fielders) or None,
                    ),
                )

    _upsert_players_from_match(conn, match)

    conn.commit()
    logger.info(
        "Inserted match %s (%d innings, %d deliveries).",
        info.match_id,
        len(match.innings),
        sum(len(i.deliveries) for i in match.innings),
    )


def _upsert_players_from_match(conn: sqlite3.Connection, match: Match) -> None:
    """Record roster names without changing delivery identity (still name strings)."""
    team_by_player: dict[str, str | None] = {}
    for team, names in match.info.players_by_team.items():
        for name in names:
            team_by_player[name] = team
    for innings in match.innings:
        for delivery in innings.deliveries:
            team_by_player.setdefault(delivery.batter, innings.batting_team)
            team_by_player.setdefault(delivery.non_striker, innings.batting_team)
            team_by_player.setdefault(delivery.bowler, None)

    for name, team in team_by_player.items():
        conn.execute(
            """
            INSERT INTO players (player_name, team, registry_id) VALUES (?, ?, ?)
            ON CONFLICT(player_name) DO UPDATE SET
                team = COALESCE(excluded.team, players.team),
                registry_id = COALESCE(excluded.registry_id, players.registry_id)
            """,
            (name, team, match.info.player_registry.get(name)),
        )

    conn.executemany(
        """
        INSERT OR REPLACE INTO player_participation
            (player_name, match_id, team, format, dataset_name)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (name, match.match_id, team, match.info.match_type, match.info.event_name)
            for name, team in team_by_player.items()
        ],
    )

    for team in match.info.teams:
        conn.execute("INSERT OR IGNORE INTO teams (team_name) VALUES (?)", (team,))
    if match.info.venue:
        conn.execute(
            "INSERT INTO venues (venue_name, city) VALUES (?, ?) "
            "ON CONFLICT(venue_name) DO UPDATE SET city = COALESCE(excluded.city, venues.city)",
            (match.info.venue, match.info.city),
        )
