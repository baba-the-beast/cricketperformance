"""
Demo web app backend.

Thin Flask wrapper around the same cricket_agent functions the MCP server
uses. New analytics/agent endpoints call Python in-process; they do not
claim to be MCP round-trips.
"""

from __future__ import annotations

import os
import sys
import gzip
import json
import logging
import time
import threading
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlencode

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from flask import Flask, Response, g, jsonify, request, send_from_directory  # noqa: E402
from werkzeug.exceptions import HTTPException  # noqa: E402

from cricket_agent.agents.runner import run_analysis  # noqa: E402
from cricket_agent.agents.selector import recommend_lineup  # noqa: E402
from cricket_agent.squads import current_squad, feed_formats, feed_team, team_gender  # noqa: E402
from cricket_agent.analytics.cache import cached, dataset_signature, lock_for  # noqa: E402
from cricket_agent.analytics.career import career_summary  # noqa: E402
from cricket_agent.analytics.matchup import batter_vs_bowler  # noqa: E402
from cricket_agent.analytics.player_profile import (  # noqa: E402
    build_player_profile,
    compare_players,
    overview_kpis,
    top_performers,
)
from cricket_agent.analytics.venue import venue_profile  # noqa: E402
from cricket_agent.config import DATABASE_PATH  # noqa: E402
from cricket_agent.database import (  # noqa: E402
    connect,
    create_schema,
    replace_dataset_metadata,
)
from cricket_agent.dismissals import NON_DISMISSAL_KINDS_SQL  # noqa: E402
from cricket_agent.exceptions import CricketAgentError  # noqa: E402
from cricket_agent.external_matches import get_upcoming_provider  # noqa: E402
from cricket_agent.memory import load_analysis_session, load_player_snapshot, recent_sessions  # noqa: E402
from cricket_agent.observability import recent_calls, record_call  # noqa: E402
from cricket_agent.simulation import simulate_innings, simulate_match  # noqa: E402
from cricket_agent.tools import cricket_tools  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="")
log = logging.getLogger("cricket_agent.web")


def _bootstrap_real_dataset_if_present() -> None:
    """Prefer a local Cricsheet archive when a real archive is present and the DB is still demo-only."""
    if not DATABASE_PATH.exists():
        return

    conn = connect(DATABASE_PATH)
    try:
        # Older or half-created databases may lack dataset_metadata; bring the
        # schema up to date here, at startup, rather than inside a request.
        create_schema(conn)
        row = conn.execute(
            "SELECT is_demo FROM dataset_metadata WHERE metadata_key = 'current'"
        ).fetchone()
        match_count = conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"]

        # A populated temporary fixture database used by tests may not have dataset metadata yet.
        # In that case, do not auto-load real archives into the test DB.
        if match_count > 0 and row is None:
            return
        if row is not None and row["is_demo"] == 0:
            return
        if row is not None and row["is_demo"] == 1 and match_count > 0:
            # Keep the demo DB as a starting point, but allow a real archive to be layered in.
            pass
    finally:
        conn.close()

    real_sources = [
        PROJECT_ROOT / "data" / "raw" / "ipl",
        PROJECT_ROOT / "data" / "raw" / "ipl_male",
        PROJECT_ROOT / "data" / "raw" / "cricsheet",
    ]
    for source_dir in real_sources:
        if source_dir.exists() and any(source_dir.rglob("*.json")):
            _ingest_and_swap(source_dir)
            return


def _remove_db_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"), Path(f"{path}-journal")):
        candidate.unlink(missing_ok=True)


def _ingest_and_swap(source_dir: Path) -> None:
    """Ingest source_dir into a staging file and replace the demo DB only on success.

    Ingesting in place would wipe the demo data first; a failed or interrupted
    run would then leave an empty or partial database behind.
    """
    from cricket_agent.ingest import ingest_directory

    staging = DATABASE_PATH.with_name(f"{DATABASE_PATH.stem}.staging{DATABASE_PATH.suffix}")
    try:
        _remove_db_files(staging)
        succeeded, failed = ingest_directory(source_dir, staging)
        if not succeeded:
            log.warning("No matches ingested from %s (%d failed); keeping the demo dataset.", source_dir, failed)
            return
        # Same bookkeeping as `python -m cricket_agent.ingest`, so the
        # dashboard labels this as the real archive, not demo data.
        conn = connect(staging)
        try:
            create_schema(conn)
            replace_dataset_metadata(conn, dataset_name="Cricsheet", source=str(source_dir), is_demo=False)
            # Fold the WAL into the main file so the swap moves a complete database.
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            conn.close()
        # Retire the old database's WAL too; a leftover -wal file would be
        # replayed onto the new database.
        conn = connect(DATABASE_PATH)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        Path(f"{DATABASE_PATH}-wal").unlink(missing_ok=True)
        Path(f"{DATABASE_PATH}-shm").unlink(missing_ok=True)
        os.replace(staging, DATABASE_PATH)
    except Exception:
        log.exception("Could not load the archive in %s; keeping the current dataset.", source_dir)
    finally:
        try:
            _remove_db_files(staging)
        except OSError:
            log.warning("Could not remove staging database %s", staging)


_bootstrap_real_dataset_if_present()


_schema_ready: set[Path] = set()
_schema_lock = threading.Lock()


def _connect():
    if not DATABASE_PATH.exists():
        raise FileNotFoundError(
            f"No database found at {DATABASE_PATH}. Run ingestion first, e.g.:\n"
            f"  python -m cricket_agent.ingest --source ipl"
        )
    conn = connect(DATABASE_PATH)
    # create_schema commits and may build indexes over millions of rows, so
    # run it once per database file, not on every request.
    if DATABASE_PATH not in _schema_ready:
        with _schema_lock:
            if DATABASE_PATH not in _schema_ready:
                create_schema(conn)
                _schema_ready.add(DATABASE_PATH)
    return conn


# ---------- Response memo and compression ----------
# Everything below the dataset only changes on ingestion, so responses are
# memoised in process, keyed by database file and dataset signature. Player
# lists (5 MB) and match lists (7 MB) are served pre-serialised and
# pre-gzipped; profiles, rosters and comparisons skip seconds of SQL on repeat.
_MEMO_MAX = 96
_memo_lock = threading.Lock()
_memo: "OrderedDict[tuple, tuple[str, object]]" = OrderedDict()
_GZIP_MIN_BYTES = 2048


def _memo_get(conn, key, compute):
    """Return compute() for this dataset version, reusing a previous result."""
    signature = dataset_signature(conn)
    full_key = (str(DATABASE_PATH), *key)

    def lookup():
        with _memo_lock:
            hit = _memo.get(full_key)
            if hit is not None and hit[0] == signature:
                _memo.move_to_end(full_key)
                return True, hit[1]
        return False, None

    found, value = lookup()
    if found:
        return value
    # Concurrent cold requests (e.g. the warm-up thread and the first page
    # load) wait for one computation instead of each running it.
    with lock_for(repr(full_key)):
        found, value = lookup()
        if found:
            return value
        value = compute()
        with _memo_lock:
            _memo[full_key] = (signature, value)
            _memo.move_to_end(full_key)
            while len(_memo) > _MEMO_MAX:
                _memo.popitem(last=False)
    return value


def _accepts_gzip() -> bool:
    return "gzip" in request.headers.get("Accept-Encoding", "")


def _memo_json(conn, key, compute):
    """A JSON response whose serialised (and gzipped) body is memoised."""
    def build():
        body = json.dumps(compute(), separators=(",", ":")).encode("utf-8")
        return body, (gzip.compress(body, 6) if len(body) >= _GZIP_MIN_BYTES else None)

    body, gz = _memo_get(conn, ("json", *key), build)
    if gz is not None and _accepts_gzip():
        response = Response(gz, mimetype="application/json")
        response.headers["Content-Encoding"] = "gzip"
    else:
        response = Response(body, mimetype="application/json")
    response.headers["Vary"] = "Accept-Encoding"
    return response


def _profile(conn, player, last_n_matches=None):
    """build_player_profile, memoised; callers must not mutate the result."""
    return _memo_get(
        conn, ("profile", player, last_n_matches),
        lambda: build_player_profile(conn, player, last_n_matches=last_n_matches),
    )


_SECRET_PARAMS = {"apikey", "api_key", "key", "token"}


def _loggable_path() -> str:
    """request.full_path with credential query parameters masked."""
    if not request.args:
        return request.path
    query = urlencode(
        [(k, "***" if k.lower() in _SECRET_PARAMS else v) for k, v in request.args.items(multi=True)]
    )
    return f"{request.path}?{query}"


def _cricketdata_key() -> str:
    # Server environment only: the key never passes through the browser.
    return os.environ.get("CRICKETDATA_API_KEY", "").strip()


@app.before_request
def _start_timer():
    g.request_started = time.perf_counter()


@app.after_request
def _log_and_harden(response):
    if request.path.startswith("/api/"):
        elapsed_ms = (time.perf_counter() - g.get("request_started", time.perf_counter())) * 1000
        log.info("%s %s %s %.0fms", request.method, _loggable_path(), response.status_code, elapsed_ms)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


@app.after_request
def _compress_json(response):
    if (
        response.status_code == 200
        and response.mimetype == "application/json"
        and not response.direct_passthrough
        and "Content-Encoding" not in response.headers
        and _accepts_gzip()
    ):
        data = response.get_data()
        if len(data) >= _GZIP_MIN_BYTES:
            response.set_data(gzip.compress(data, 5))
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Vary"] = "Accept-Encoding"
    return response


@app.errorhandler(CricketAgentError)
def handle_cricket_error(exc: CricketAgentError):
    return jsonify({"error": str(exc), "code": type(exc).__name__}), 404


@app.errorhandler(FileNotFoundError)
def handle_missing_db(exc: FileNotFoundError):
    return jsonify({"error": str(exc), "code": "DatabaseUnavailable"}), 503


@app.errorhandler(Exception)
def handle_unexpected(exc: Exception):
    # Let Flask's own HTTP errors (404 for a missing page/asset, 405, ...)
    # keep their status instead of becoming a generic 500.
    if isinstance(exc, HTTPException):
        return jsonify({"error": exc.description, "code": exc.name}), exc.code
    app.logger.exception("Unhandled error")
    return jsonify({"error": "Something went wrong. Check the server log.", "code": "InternalError"}), 500


@app.route("/healthz")
def healthz():
    """Liveness and database readiness, for monitors and process managers."""
    try:
        conn = _connect()
        try:
            matches = conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"]
        finally:
            conn.close()
    except Exception as exc:  # report, never raise, from a health check
        return jsonify({"status": "error", "database": str(exc)}), 503
    return jsonify({"status": "ok", "matches": matches})


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/matches")
def api_list_matches():
    conn = _connect()
    try:
        return _memo_json(conn, ("matches",), lambda: _match_list(conn))
    finally:
        conn.close()


def _match_list(conn):
    rows = conn.execute(
        """
             SELECT match_id, team_home, team_away, match_date, venue, match_type,
                 season, event_name, winner
        FROM matches
        ORDER BY match_date ASC, match_id ASC
        """
    ).fetchall()
    return (
        [
            {
                "match_id": r["match_id"],
                "team_home": r["team_home"],
                "team_away": r["team_away"],
                "match_date": r["match_date"],
                "venue": r["venue"],
                "match_type": r["match_type"],
                "season": r["season"],
                "competition": r["event_name"],
                "winner": r["winner"],
            }
            for r in rows
        ]
    )


@app.route("/api/matches/<match_id>")
def api_get_match(match_id: str):
    conn = _connect()
    try:
        record_call("get_match_data", channel="in-process", arguments={"match_id": match_id})
        return jsonify(cricket_tools.get_match_data(conn, match_id))
    finally:
        conn.close()


@app.route("/api/matches/<match_id>/deliveries")
def api_match_deliveries(match_id: str):
    conn = _connect()
    try:
        return jsonify(cricket_tools.list_match_deliveries(conn, match_id))
    finally:
        conn.close()


_PLAYER_FILTER_ARGS = (
    "search", "q", "registry_id", "team", "country", "format", "dataset",
    "competition", "season", "year", "match", "start_date", "end_date",
)


@app.route("/api/players")
def api_list_players():
    conn = _connect()
    try:
        # Only the unfiltered lists are memoised: every search keystroke is a
        # new variant and would push the multi-MB full lists out of the LRU.
        if not any(request.args.get(name) for name in _PLAYER_FILTER_ARGS):
            key = (request.args.get("details") == "1", request.args.get("sort", "name"))
            return _memo_json(conn, ("players", key), lambda: _player_list(conn))
        return jsonify(_player_list(conn))
    finally:
        conn.close()


def _player_list(conn):
    details = request.args.get("details") == "1"
    search = (request.args.get("search") or request.args.get("q") or "").strip()
    registry_id = request.args.get("registry_id")
    team = request.args.get("team")
    country = request.args.get("country")
    match_format = request.args.get("format")
    dataset = request.args.get("dataset")
    competition = request.args.get("competition")
    season = request.args.get("season") or request.args.get("year")
    match_id = request.args.get("match")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    sort = request.args.get("sort", "name")
    order = "p.player_name ASC" if sort != "matches" else "matches_count DESC, p.player_name ASC"
    clauses = ["1 = 1"]
    params: list[str] = []
    if search:
        clauses.append("p.player_name LIKE ?")
        params.append(f"%{search}%")
    if registry_id:
        clauses.append("p.registry_id = ?")
        params.append(registry_id)
    if team:
        # Historical team participation
        clauses.append("EXISTS (SELECT 1 FROM player_participation pt WHERE pt.player_name = p.player_name AND pt.team = ?)")
        params.append(team)
    if country:
        clauses.append("p.country = ?")
        params.append(country)
    if match_format:
        clauses.append("EXISTS (SELECT 1 FROM player_participation pf WHERE pf.player_name = p.player_name AND pf.format = ?)")
        params.append(match_format)

    # Additional historical participation filters
    historical_filters = []
    if competition:
        historical_filters.append("m.event_name = ?")
        params.append(competition)
    if season:
        historical_filters.append("m.season = ?")
        params.append(season)
    if match_id:
        historical_filters.append("m.match_id = ?")
        params.append(match_id)
    if start_date:
        historical_filters.append("m.match_date >= ?")
        params.append(start_date)
    if end_date:
        historical_filters.append("m.match_date <= ?")
        params.append(end_date)

    if historical_filters:
        hist_where = " AND ".join(historical_filters)
        clauses.append(f"""
            EXISTS (
                SELECT 1 FROM player_participation hpp
                JOIN matches m ON hpp.match_id = m.match_id
                WHERE hpp.player_name = p.player_name AND {hist_where}
            )
        """)

    if dataset:
        current_dataset = conn.execute(
            "SELECT dataset_name FROM dataset_metadata WHERE metadata_key = 'current'"
        ).fetchone()
        if not current_dataset or dataset != current_dataset["dataset_name"]:
            clauses.append("1 = 0")

    rows = conn.execute(
        f"""
        SELECT p.player_name, p.team, p.country, p.registry_id, p.role,
               COALESCE(p.batting_style, 'Not available') as batting_style,
               COALESCE(p.bowling_style, 'Not available') as bowling_style,
               COALESCE(p.date_of_birth, 'Not available') as date_of_birth,
               COUNT(DISTINCT pp.match_id) AS matches_count,
               GROUP_CONCAT(DISTINCT pp.format) AS formats,
               GROUP_CONCAT(DISTINCT pp.team) AS all_teams
        FROM players p
        LEFT JOIN player_participation pp ON pp.player_name = p.player_name
        WHERE {' AND '.join(clauses)}
        GROUP BY p.player_name
        ORDER BY {order}
        """,
        params,
    ).fetchall()
    if not details:
        return [r["player_name"] for r in rows]

    result = []
    for r in rows:
        d = dict(r)
        d["formats"] = [f for f in (d["formats"] or "").split(",") if f]
        d["all_teams"] = [t for t in (d["all_teams"] or "").split(",") if t]
        # Replace missing metadata with "Not available" per Phase 6 rules
        for k in ["country", "role", "registry_id"]:
            if not d.get(k):
                d[k] = "Not available"
        result.append(d)
    return result


@app.route("/api/dataset")
def api_dataset():
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT dataset_name, source, is_demo, match_count, player_count, "
            "delivery_count, date_start, date_end, teams_json, formats_json, "
            "competitions_json, imported_at "
            "FROM dataset_metadata WHERE metadata_key = 'current'"
        ).fetchone()
        if row:
            payload = dict(row)
            for key in ("teams_json", "formats_json", "competitions_json"):
                payload[key.removesuffix("_json")] = json.loads(payload.pop(key) or "[]")
            payload["team_count"] = len(payload["teams"])
            return jsonify(payload)
        fallback = conn.execute(
            "SELECT COUNT(*) AS match_count, COUNT(DISTINCT player_name) AS player_count "
            "FROM players"
        ).fetchone()
        deliveries = conn.execute("SELECT COUNT(*) AS n FROM deliveries").fetchone()["n"]
        teams = [r["team"] for r in conn.execute(
            "SELECT team_home AS team FROM matches UNION SELECT team_away FROM matches ORDER BY team"
        ).fetchall()]
        formats = [r["match_type"] for r in conn.execute(
            "SELECT DISTINCT match_type FROM matches ORDER BY match_type"
        ).fetchall()]
        return jsonify({
            "dataset_name": "College Demo Dataset",
            "source": "tests/fixtures",
            "is_demo": True,
            "match_count": conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"],
            "player_count": fallback["player_count"],
            "delivery_count": deliveries,
            "date_start": None,
            "date_end": None,
            "teams": teams, "team_count": len(teams), "formats": formats, "competitions": [],
        })
    finally:
        conn.close()


@app.route("/api/venues")
def api_venues():
    conn = _connect()

    def compute() -> list[dict]:
        rows = conn.execute(
            f"""
            WITH innings_totals AS (
                SELECT m.venue, m.city, m.match_type AS format, d.match_id,
                       d.innings_index, SUM(d.runs_total) AS score,
                       COUNT(*) AS deliveries,
                       SUM(CASE WHEN d.runs_batter IN (4, 6) THEN 1 ELSE 0 END) AS boundaries,
                       COUNT(CASE WHEN w.kind NOT IN ({NON_DISMISSAL_KINDS_SQL}) THEN 1 END) AS wickets
                FROM matches m
                JOIN deliveries d ON d.match_id = m.match_id
                LEFT JOIN wickets w ON w.delivery_pk = d.delivery_pk
                WHERE m.venue IS NOT NULL
                GROUP BY m.venue, m.city, m.match_type, d.match_id, d.innings_index
            )
            SELECT venue, city, COUNT(DISTINCT match_id) AS match_count,
                   GROUP_CONCAT(DISTINCT format) AS formats,
                   AVG(CASE WHEN innings_index = 0 THEN score END) AS average_first_innings_score,
                   AVG(CASE WHEN innings_index = 1 THEN score END) AS average_second_innings_score,
                   CAST(SUM(boundaries) AS REAL) / NULLIF(SUM(deliveries), 0) AS boundary_rate,
                   CAST(SUM(wickets) AS REAL) / NULLIF(SUM(deliveries), 0) AS wicket_rate
            FROM innings_totals
            GROUP BY venue, city ORDER BY venue
            """
        ).fetchall()
        return [dict(r) for r in rows]

    try:
        # Aggregates every delivery in the dataset; only changes on ingestion.
        return _memo_json(conn, ("venues",), lambda: cached(conn, "venues", compute))
    finally:
        conn.close()


@app.route("/api/upcoming")
def api_upcoming():
    provider = get_upcoming_provider()
    try:
        matches = provider.upcoming()
    except (OSError, RuntimeError, ValueError) as exc:
        return jsonify({
            "available": False,
            "source": provider.name,
            "message": f"Fixture provider request failed: {exc}",
            "matches": [],
        }), 502
    return jsonify({
        "available": bool(matches),
        "source": provider.name,
        "message": "Upcoming match data unavailable" if not matches else None,
        "matches": [match.__dict__ for match in matches],
    })


@app.route("/api/players/<player_id>/stats")
def api_player_stats(player_id: str):
    last_n = request.args.get("last_n", type=int)
    start_match = request.args.get("start_match") or None
    end_match = request.args.get("end_match") or None
    conn = _connect()
    try:
        record_call("compute_stats", channel="in-process", arguments={"player_id": player_id})
        return _memo_json(
            conn, ("stats", player_id, last_n, start_match, end_match),
            lambda: cricket_tools.compute_stats(
                conn,
                player_id,
                start_match=start_match,
                end_match=end_match,
                last_n_matches=last_n,
            ),
        )
    finally:
        conn.close()


@app.route("/api/players/<player_id>/profile")
def api_player_profile(player_id: str):
    last_n = request.args.get("last_n", type=int)
    conn = _connect()
    try:
        record_call("analyze_player", channel="in-process", arguments={"player_id": player_id})
        # Memory snapshots change when agents run, so they are read fresh.
        profile = dict(_profile(conn, player_id, last_n))
        profile["historical_memory"] = load_player_snapshot(conn, player_id)
        return jsonify(profile)
    finally:
        conn.close()


@app.route("/api/players/<player_id>/career")
def api_player_career(player_id: str):
    conn = _connect()
    try:
        record_call("career_summary", channel="in-process", arguments={"player_id": player_id})
        return _memo_json(conn, ("career", player_id), lambda: career_summary(conn, player_id))
    finally:
        conn.close()


@app.route("/api/compare")
def api_compare():
    names = request.args.get("players", "")
    players = [n.strip() for n in names.split(",") if n.strip()]
    if len(players) < 2:
        return jsonify({"error": "Provide at least two player names.", "code": "InvalidRangeError"}), 400
    last_n = request.args.get("last_n", type=int)
    conn = _connect()
    try:
        record_call("compare_players", channel="in-process", arguments={"players": players})
        return jsonify(compare_players(
            conn, players, last_n_matches=last_n,
            profile_builder=lambda c, name, last_n_matches=None: _profile(c, name, last_n_matches),
        ))
    finally:
        conn.close()


@app.route("/api/matchup")
def api_matchup():
    batter = request.args.get("batter")
    bowler = request.args.get("bowler")
    if not batter or not bowler:
        return jsonify({"error": "batter and bowler are required", "code": "InvalidRangeError"}), 400
    conn = _connect()
    try:
        return jsonify(batter_vs_bowler(conn, batter, bowler))
    finally:
        conn.close()


@app.route("/api/overview")
def api_overview():
    conn = _connect()
    try:
        kpis = overview_kpis(conn)
        kpis["top_performers"] = top_performers(conn)
        kpis["recent_sessions"] = recent_sessions(conn)
        kpis["recent_tool_calls"] = recent_calls()
        return jsonify(kpis)
    finally:
        conn.close()


@app.route("/api/replay/start", methods=["POST"])
def api_replay_start():
    match_id = (request.get_json(silent=True) or {}).get("match_id")
    if not match_id:
        return jsonify({"error": "match_id is required", "code": "InvalidRangeError"}), 400
    conn = _connect()
    try:
        record_call("start_replay", channel="in-process", arguments={"match_id": match_id})
        return jsonify(cricket_tools.start_replay(conn, match_id))
    finally:
        conn.close()


@app.route("/api/replay/next", methods=["POST"])
def api_replay_next():
    session_id = (request.get_json(silent=True) or {}).get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required", "code": "InvalidRangeError"}), 400
    conn = _connect()
    try:
        record_call("get_next_ball", channel="in-process", arguments={"session_id": session_id})
        return jsonify(cricket_tools.get_next_ball(conn, session_id))
    finally:
        conn.close()


@app.route("/api/replay/previous", methods=["POST"])
def api_replay_previous():
    session_id = (request.get_json(silent=True) or {}).get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required", "code": "InvalidRangeError"}), 400
    conn = _connect()
    try:
        return jsonify(cricket_tools.get_previous_ball(conn, session_id))
    finally:
        conn.close()


@app.route("/api/analysis/start", methods=["POST"])
def api_analysis_start():
    body = request.get_json(silent=True) or {}
    players = body.get("players") or []
    question = body.get("question") or "Which player should be considered for the next selection?"
    if not players:
        return jsonify({"error": "players is required", "code": "InvalidRangeError"}), 400
    conn = _connect()
    try:
        result = run_analysis(
            conn,
            players,
            question,
            selected_match=body.get("match_id"),
            last_n_matches=body.get("last_n_matches"),
            opponent=body.get("opponent"),
        )
        return jsonify(result)
    finally:
        conn.close()


@app.route("/api/analysis/<session_id>")
def api_analysis_get(session_id: str):
    conn = _connect()
    try:
        stored = load_analysis_session(conn, session_id)
        if not stored:
            return jsonify({"error": "Analysis session not found.", "code": "NotFound"}), 404
        return jsonify(stored)
    finally:
        conn.close()


@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    body = request.get_json(silent=True) or {}
    batting = body.get("batting_lineup") or []
    bowling = body.get("bowling_lineup") or []
    overs = int(body.get("overs") or 20)
    target = body.get("target")
    conn = _connect()
    try:
        return jsonify(
            simulate_innings(
                conn,
                batting,
                bowling,
                overs=overs,
                target=int(target) if target not in (None, "") else None,
            )
        )
    finally:
        conn.close()


@app.route("/api/historical-teams")
def api_historical_teams():
    conn = _connect()
    try:
        return _memo_json(conn, ("historical-teams",), lambda: _historical_teams(conn))
    finally:
        conn.close()


def _historical_teams(conn):
    rows = conn.execute(
        '''
        SELECT DISTINCT event_name AS competition, strftime('%Y', match_date) AS year, team_home AS team
        FROM matches
        WHERE event_name IS NOT NULL
        UNION
        SELECT DISTINCT event_name AS competition, strftime('%Y', match_date) AS year, team_away AS team
        FROM matches
        WHERE event_name IS NOT NULL
        ORDER BY competition, year DESC, team
        '''
    ).fetchall()

    hierarchy = {}
    for row in rows:
        comp = row["competition"]
        year = row["year"]
        team = row["team"]
        if not comp or not year or not team: continue

        if comp == "Indian Premier League": comp = "IPL"

        if comp not in hierarchy:
            hierarchy[comp] = {}
        if year not in hierarchy[comp]:
            hierarchy[comp][year] = []
        if team not in hierarchy[comp][year]:
            hierarchy[comp][year].append(team)

    return hierarchy

_team_gender = team_gender


@app.route("/api/team-squad")
def api_team_squad():
    """Players from the team's most recent season, for prefilling a squad."""
    team = (request.args.get("team") or "").strip()
    if not team:
        return jsonify({"error": "team is required"}), 400
    conn = _connect()
    try:
        gender = _team_gender(conn, team)
        latest = conn.execute(
            """
            SELECT MAX(match_date) AS d FROM matches
            WHERE (team_home = ? OR team_away = ?) AND (? IS NULL OR gender = ?)
            """,
            (team, team, gender, gender),
        ).fetchone()["d"]
        if not latest:
            return jsonify({"team": team, "season": None, "squad": []})
        season = latest[:4]
        rows = conn.execute(
            """
            SELECT pp.player_name, COUNT(*) AS n
            FROM player_participation pp
            JOIN matches m ON m.match_id = pp.match_id
            WHERE pp.team = ? AND strftime('%Y', m.match_date) = ?
              AND (? IS NULL OR m.gender = ?)
            GROUP BY pp.player_name
            ORDER BY n DESC, pp.player_name
            """,
            (team, season, gender, gender),
        ).fetchall()
        return jsonify({
            "team": team,
            "season": season,
            "gender": gender,
            "squad": [r["player_name"] for r in rows],
        })
    finally:
        conn.close()


@app.route("/api/team-roster")
def api_team_roster():
    requested = (request.args.get("team") or "").strip()
    match_type = (request.args.get("format") or "").strip().upper()
    year = (request.args.get("year") or "").strip()
    if not requested:
        return jsonify({"available": False, "note": "team is required", "recommended_xi": []}), 400
    team, gender, impossible = feed_team(requested)
    unavailable = lambda note: jsonify({"available": False, "team": requested, "note": note,
                                        "recommended_xi": [], "bowling_suggestions": []})
    if impossible:
        return unavailable(impossible)
    conn = _connect()
    try:
        gender = gender or team_gender(conn, team)
        formats = feed_formats(match_type)
        squad = current_squad(conn, team, gender=gender, formats=formats, year=year)
        format_note = None
        if not squad and formats:
            squad = current_squad(conn, team, gender=gender, year=year)
            if squad:
                format_note = f"No {match_type} matches for {requested} in the dataset; squad taken from all formats."
        if not squad:
            return unavailable(f"No matches for {requested} exist in the active dataset. Enter a verified squad before simulating this fixture.")

        opponent_name = request.args.get("opponent") or None
        opponent = feed_team(opponent_name)[0] if opponent_name else None
        # Feeds give "Ground, City"; Cricsheet usually stores the ground alone.
        venue = (request.args.get("venue") or "").split(",")[0].strip() or None
        recommendation = _memo_get(
            conn, ("roster-lineup", tuple(squad), opponent, venue),
            lambda: recommend_lineup(
                conn,
                squad=squad,
                opponent=opponent,
                venue=venue,
                last_n_matches=10,
                xi_size=min(11, len(squad)),
            ),
        )
        ranked_cases = recommendation["recommended_xi"] + recommendation["bench"]
        bowlers = sorted(
            (player for player in ranked_cases if player.get("bowling")),
            key=lambda player: (
                player["bowling"].get("wickets") or 0,
                -(99 if player["bowling"].get("economy") is None else player["bowling"].get("economy")),
            ),
            reverse=True,
        )
        return jsonify({
            "available": True,
            "team": requested,
            "dataset_team": team,
            "gender": gender,
            "squad_size": len(squad),
            "squad": squad,
            "squad_note": format_note or (
                f"Squad: {len(squad)} players who played for {requested} in {year}." if year else
                f"Squad: {len(squad)} players who played for {requested} in the 12 months to their latest recorded match."),
            "recommended_xi": [player["player"] for player in recommendation["recommended_xi"]],
            "bowling_suggestions": [player["player"] for player in bowlers[:5]],
            "flags": recommendation["flags"],
            "ranking_method": recommendation["ranking_method"],
        })
    finally:
        conn.close()


@app.route("/api/simulate-match", methods=["POST"])
def api_simulate_match():
    body = request.get_json(silent=True) or {}
    conn = _connect()
    try:
        result = simulate_match(
            conn,
            team_a=str(body.get("team_a") or "").strip(),
            team_b=str(body.get("team_b") or "").strip(),
            batting_lineup_a=body.get("batting_lineup_a") or [],
            batting_lineup_b=body.get("batting_lineup_b") or [],
            bowling_lineup_a=body.get("bowling_lineup_a") or [],
            bowling_lineup_b=body.get("bowling_lineup_b") or [],
            overs=int(body.get("overs") or 50),
            match_type=str(body.get("match_type") or "ODI"),
            seed=int(body["seed"]) if body.get("seed") not in (None, "") else None,
        )
        if not result["available"]:
            return jsonify(result), 400
        return jsonify(result)
    except (TypeError, ValueError) as exc:
        return jsonify({"available": False, "note": f"Invalid simulation request: {exc}"}), 400
    finally:
        conn.close()


@app.route("/api/recommend_lineup", methods=["POST"])
def api_recommend_lineup():
    body = request.get_json(silent=True) or {}
    squad = body.get("squad") or []
    if not squad:
        return jsonify({"error": "squad is required", "code": "InvalidRangeError"}), 400
    conn = _connect()
    try:
        result = recommend_lineup(
            conn,
            squad=squad,
            opponent=body.get("opponent") or None,
            venue=body.get("venue") or None,
            last_n_matches=body.get("last_n_matches"),
            pitch_note=body.get("pitch_note") or None,
            weather_note=body.get("weather_note") or None,
            xi_size=int(body.get("xi_size") or 11),
        )
        return jsonify(result)
    finally:
        conn.close()


@app.route("/api/venue_profile")
def api_venue_profile():
    venue = request.args.get("venue")
    if not venue:
        return jsonify({"error": "venue query param is required", "code": "InvalidRangeError"}), 400
    conn = _connect()
    try:
        return _memo_json(conn, ("venue", venue), lambda: venue_profile(conn, venue))
    finally:
        conn.close()


@app.route("/api/architecture")
def api_architecture():
    return jsonify(
        {
            "pipeline": [
                "Cricsheet",
                "Data Ingestion",
                "SQLite",
                "Analytics Engine",
                "MCP Server",
                "Specialist Agents",
                "Coordinator",
                "Claude (LLM explanation)",
                "Selection Report",
                "Coach Dashboard",
            ],
            "mcp_tools": [
                {
                    "tool": "get_match_data",
                    "purpose": "Structured match summary from SQLite",
                    "status": "available",
                },
                {
                    "tool": "compute_stats",
                    "purpose": "Deterministic batting/bowling figures",
                    "status": "available",
                },
                {
                    "tool": "start_replay",
                    "purpose": "Open a ball-by-ball replay session",
                    "status": "available",
                },
                {
                    "tool": "get_next_ball",
                    "purpose": "Return the next recorded delivery",
                    "status": "available",
                },
                {
                    "tool": "get_previous_ball",
                    "purpose": "Rewind one delivery in a replay session",
                    "status": "available",
                },
                {
                    "tool": "analyze_player",
                    "purpose": "Profile with form, role, and confidence",
                    "status": "available",
                },
                {
                    "tool": "compare_players",
                    "purpose": "Side-by-side player comparison",
                    "status": "available",
                },
            ],
            "recent_calls": recent_calls(),
            "honesty": (
                "Dashboard analysis uses in-process Python calls to the same "
                "functions MCP wraps. A row with channel=mcp means an MCP "
                "client actually invoked the tool."
            ),
        }
    )




@app.route("/api/upcoming-tournaments")
def api_upcoming_tournaments():
    apikey = _cricketdata_key()
    if not apikey:
        # Return mock upcoming tournaments if no key provided
        return jsonify([
            {
                "id": "ipl_2026",
                "name": "Indian Premier League 2026 (Mock)",
                "format": "T20",
                "teams": ["Chennai Super Kings", "Mumbai Indians", "Royal Challengers Bangalore", "Kolkata Knight Riders", "Sunrisers Hyderabad", "Delhi Capitals"],
                "year": "2024"
            },
            {
                "id": "t20_wc_2026",
                "name": "ICC T20 World Cup 2026 (Mock)",
                "format": "T20",
                "teams": ["India", "Australia", "England", "South Africa", "New Zealand", "Pakistan"],
                "year": "2024"
            }
        ])

    import urllib.request, json
    try:
        url = "https://api.cricapi.com/v1/series?" + urlencode({"apikey": apikey})
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get("status") == "failure":
                return jsonify({"error": data.get("reason", "API Error")}), 400

            series_list = []
            for s in data.get("data", []):
                # Filter for upcoming/ongoing or just return all recent
                if s.get("odi", 0) > 0 or s.get("t20", 0) > 0:
                    series_list.append({
                        "id": s.get("id"),
                        "name": s.get("name"),
                        "format": "T20" if s.get("t20", 0) > s.get("odi", 0) else "ODI",
                        "teams": [], # Will fetch on demand
                        "year": str(s.get("startDate", "").split("-")[0] if s.get("startDate") else "2024")
                    })
            return jsonify(series_list[:30]) # limit to 30
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/live-tournament-info")
def api_live_tournament_info():
    apikey = _cricketdata_key()
    series_id = request.args.get("id")
    if not apikey or not series_id:
        return jsonify({"error": "Missing key or id"}), 400

    import urllib.request, json
    try:
        url = "https://api.cricapi.com/v1/series_info?" + urlencode({"apikey": apikey, "id": series_id})
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get("status") == "failure":
                return jsonify({"error": data.get("reason", "API Error")}), 400

            info = data.get("data", {})
            match_list = info.get("matchList", [])
            teams = set()
            for m in match_list:
                for t in m.get("teamInfo", []):
                    teams.add(t.get("name") or t.get("shortname"))

            # Placeholder sides ("Tbc") are not real teams.
            teams = sorted(t for t in teams if t and not feed_team(t)[2])
            return jsonify({"teams": list(teams)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/simulate-tournament", methods=["POST"])

def api_simulate_tournament():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object body"}), 400
    teams = data.get("teams", [])
    year = data.get("year", "2024")
    format_type = data.get("format", "T20")

    if not isinstance(teams, list) or len(teams) < 2:
        return jsonify({"error": "Need at least 2 teams"}), 400

    from cricket_agent.tournament import simulate_tournament
    conn = _connect()
    try:
        result = simulate_tournament(conn, teams, year, format=format_type)
    except CricketAgentError:
        raise
    except Exception:
        log.exception("Tournament simulation failed")
        return jsonify({"error": "Tournament simulation failed"}), 500
    finally:
        conn.close()

    return jsonify(result)

def _warm_caches() -> None:
    """Fill the response memo in the background so first page visits are fast.

    Goes through the real routes (same keys the browser will request); any
    failure just means that page computes on first visit instead.
    """
    client = app.test_client()
    urls = ["/api/overview", "/api/dataset", "/api/matches", "/api/players",
            "/api/players?details=1", "/api/historical-teams", "/api/venues"]
    for url in urls:
        client.get(url)
    leaders = (client.get("/api/overview").get_json(silent=True) or {}).get("top_performers", {}).get("by_runs", [])
    names = [p["player"] for p in leaders[:2]]
    for name in names:
        client.get(f"/api/players/{name}/career")
        client.get(f"/api/players/{name}/profile")
    if len(names) == 2:
        client.get("/api/compare?" + urlencode({"players": ",".join(names)}))
    app.logger.info("Response caches warmed.")


def main() -> None:
    """Serve the dashboard.

    Production by default: waitress (a multi-threaded WSGI server) with the
    Flask debugger off -- the Werkzeug debugger executes arbitrary Python for
    anyone who can reach the port, so it is opt-in via CRICKET_DEBUG=1.

    Environment:
        CRICKET_HOST   bind address (default 127.0.0.1; use 0.0.0.0 to share on a LAN)
        CRICKET_PORT   port (default 5000)
        CRICKET_DEBUG  1 = Flask development server with reloader and debugger
        CRICKET_THREADS  waitress worker threads (default 8)
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("CRICKET_HOST", "127.0.0.1")
    port = int(os.environ.get("CRICKET_PORT", "5000"))
    debug = os.environ.get("CRICKET_DEBUG") == "1"

    log.info("Using database: %s", DATABASE_PATH)
    if not DATABASE_PATH.exists():
        log.warning("Database not found yet. Run: python -m cricket_agent.ingest --source ipl")

    def warm():
        threading.Thread(target=_warm_caches, name="warm-caches", daemon=True).start()

    if debug:
        # With the reloader, only the child process serves requests.
        if DATABASE_PATH.exists() and os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            warm()
        app.run(host=host, port=port, debug=True)
        return

    if DATABASE_PATH.exists():
        warm()
    try:
        from waitress import serve
    except ImportError:
        log.warning("waitress is not installed (pip install waitress); using Flask's threaded server.")
        app.run(host=host, port=port, debug=False, threaded=True)
        return
    log.info("Serving on http://%s:%d (waitress, %s threads)", host, port, os.environ.get("CRICKET_THREADS", "8"))
    serve(app, host=host, port=port, threads=int(os.environ.get("CRICKET_THREADS", "8")), ident="cricket-agent")


if __name__ == "__main__":
    main()
