"""Lifetime career analytics must agree with compute_stats and never leak windows."""

from __future__ import annotations

import pytest

from cricket_agent.analytics.career import career_summary
from cricket_agent.analytics.matchup import pace_vs_spin_matchup
from cricket_agent.analytics.player_profile import build_player_profile
from cricket_agent.analytics.venue import best_venues_for_player
from cricket_agent.exceptions import PlayerNotFoundError
from cricket_agent.tools.cricket_tools import compute_stats


def _players(conn):
    return [r[0] for r in conn.execute(
        "SELECT batter FROM deliveries UNION SELECT bowler FROM deliveries"
    )]


def test_career_totals_match_compute_stats(populated_db):
    for player in _players(populated_db):
        career = career_summary(populated_db, player)
        stats = compute_stats(populated_db, player)
        if stats["batting"]:
            assert career["batting"]["runs"] == stats["batting"]["runs"]
            assert career["batting"]["balls_faced"] == stats["batting"]["balls_faced"]
            # career_summary also counts run-outs at the non-striker's end in
            # matches where the player never faced a ball (see career.py), so
            # it can exceed compute_stats but never fall below it.
            assert career["batting"]["dismissals"] >= stats["batting"]["dismissals"]
        if stats["bowling"]:
            assert career["bowling"]["wickets"] == stats["bowling"]["wickets"]
            assert career["bowling"]["runs_conceded"] == stats["bowling"]["runs_conceded"]
            assert career["bowling"]["legal_deliveries"] == stats["bowling"]["legal_deliveries"]
        assert career["span"]["matches"] == len(stats["matches_considered"])


def test_career_breakdowns_add_up(populated_db):
    player = _players(populated_db)[0]
    career = career_summary(populated_db, player)
    assert sum(f["matches"] for f in career["by_format"]) == career["span"]["matches"]
    assert sum(y["matches"] for y in career["timeline"]) == career["span"]["matches"]
    if career["batting"]:
        assert career["timeline"][-1]["cumulative_runs"] == career["batting"]["runs"]


def test_career_unknown_player_raises(populated_db):
    with pytest.raises(PlayerNotFoundError):
        career_summary(populated_db, "Nobody At All")


def test_empty_window_does_not_widen_to_all_history(populated_db):
    player = _players(populated_db)[0]
    ps = pace_vs_spin_matchup(populated_db, player, [])
    assert all(v is None for v in ps["matchup_stats"].values())
    venues = best_venues_for_player(populated_db, player, [])
    assert venues["top_batting_venues"] == [] and venues["top_bowling_venues"] == []


def test_profile_includes_windowed_pace_spin_and_venues(populated_db):
    player = _players(populated_db)[0]
    profile = build_player_profile(populated_db, player, last_n_matches=1)
    assert "pace_vs_spin" in profile and "best_venues" in profile
    window = set(profile["matches_considered"])
    for v in profile["best_venues"]["top_batting_venues"]:
        assert v["matches"] <= len(window)


def test_overview_cache_refreshes_after_ingest(populated_db, fixtures_dir):
    from cricket_agent.analytics.player_profile import overview_kpis
    from cricket_agent.database import insert_match
    from cricket_agent.ingestion.parser import parse_match_file

    before = overview_kpis(populated_db)
    assert overview_kpis(populated_db)["total_deliveries"] == before["total_deliveries"]
    insert_match(populated_db, parse_match_file(fixtures_dir / "match_1003_no_venue.json"))
    after = overview_kpis(populated_db)
    assert after["total_matches"] == before["total_matches"] + 1
    assert after["total_deliveries"] > before["total_deliveries"]
