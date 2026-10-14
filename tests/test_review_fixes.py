"""Regressions for issues found in code review (dismissal kinds, cache, memory, providers)."""

from __future__ import annotations

import pytest

from cricket_agent.analytics.cache import _dataset_signature, cached
from cricket_agent.analytics.fielding import fielding_credits
from cricket_agent.database import clear_database, create_schema, insert_match
from cricket_agent.external_matches import CricketDataMatchProvider
from cricket_agent.ingestion.parser import parse_match_file
from cricket_agent.memory import save_player_snapshot
from cricket_agent.tools.cricket_tools import compute_stats


def _undismissed_delivery(conn, batter):
    return conn.execute(
        """
        SELECT d.delivery_pk FROM deliveries d
        LEFT JOIN wickets w ON w.delivery_pk = d.delivery_pk
        WHERE d.batter = ? AND w.wicket_pk IS NULL
        LIMIT 1
        """,
        (batter,),
    ).fetchone()["delivery_pk"]


def test_retired_not_out_is_not_a_dismissal(populated_db):
    before = compute_stats(populated_db, "A Sharma")["batting"]["dismissals"]
    populated_db.execute(
        "INSERT INTO wickets (delivery_pk, kind, player_out, fielders) VALUES (?, 'retired not out', 'A Sharma', NULL)",
        (_undismissed_delivery(populated_db, "A Sharma"),),
    )
    assert compute_stats(populated_db, "A Sharma")["batting"]["dismissals"] == before


def test_retired_out_is_still_a_dismissal(populated_db):
    before = compute_stats(populated_db, "A Sharma")["batting"]["dismissals"]
    populated_db.execute(
        "INSERT INTO wickets (delivery_pk, kind, player_out, fielders) VALUES (?, 'retired out', 'A Sharma', NULL)",
        (_undismissed_delivery(populated_db, "A Sharma"),),
    )
    assert compute_stats(populated_db, "A Sharma")["batting"]["dismissals"] == before + 1


def test_fielding_empty_window_does_not_widen_to_career(populated_db):
    fielder = populated_db.execute(
        "SELECT fielders FROM wickets WHERE fielders IS NOT NULL LIMIT 1"
    ).fetchone()["fielders"].split(",")[0].strip()
    assert fielding_credits(populated_db, fielder)["total_credits"] > 0
    assert fielding_credits(populated_db, fielder, [])["total_credits"] == 0


def test_cache_signature_changes_when_a_match_is_revised(populated_db, fixtures_dir):
    before = _dataset_signature(populated_db)
    # Re-ingesting the same file (a Cricsheet revision) keeps the match count.
    insert_match(populated_db, parse_match_file(fixtures_dir / "match_1001.json"))
    assert _dataset_signature(populated_db) != before


def test_cache_recomputes_only_after_data_changes(populated_db, fixtures_dir):
    calls = []

    def compute():
        calls.append(1)
        return len(calls)

    assert cached(populated_db, "probe", compute) == 1
    assert cached(populated_db, "probe", compute) == 1
    insert_match(populated_db, parse_match_file(fixtures_dir / "match_1001.json"))
    assert cached(populated_db, "probe", compute) == 2


def test_clear_database_drops_old_dataset_memory(populated_db):
    save_player_snapshot(populated_db, "A Sharma", {"note": "old dataset"})
    clear_database(populated_db)
    create_schema(populated_db)
    assert populated_db.execute("SELECT COUNT(*) AS n FROM player_memory").fetchone()["n"] == 0


def test_provider_skips_finished_current_matches(monkeypatch):
    provider = CricketDataMatchProvider("test-key")

    def fake_get_rows(endpoint):
        if endpoint == "matches":
            return []
        return [
            {"id": "done", "teams": ["India", "England"], "matchEnded": True, "status": "India won"},
            {"id": "live", "teams": ["Australia", "England"], "matchEnded": False},
        ]

    monkeypatch.setattr(provider, "_get_rows", fake_get_rows)
    assert [m.match_id for m in provider.upcoming()] == ["live"]


def test_provider_backs_off_after_a_failure(monkeypatch):
    provider = CricketDataMatchProvider("test-key")
    calls = []

    def failing_get_rows(endpoint):
        calls.append(endpoint)
        raise OSError("network down")

    monkeypatch.setattr(provider, "_get_rows", failing_get_rows)
    with pytest.raises(OSError):
        provider.upcoming()
    with pytest.raises(OSError):
        provider.upcoming()
    assert calls == ["matches"]
