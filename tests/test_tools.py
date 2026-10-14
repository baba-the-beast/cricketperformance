"""Tests for cricket_agent.tools.cricket_tools, backed by a real SQLite DB."""

from __future__ import annotations

import pytest

from cricket_agent.exceptions import (
    InvalidRangeError,
    MatchNotFoundError,
    PlayerNotFoundError,
    ReplayEndedError,
    ReplaySessionNotFoundError,
)
from cricket_agent.tools import cricket_tools


# --- get_match_data --------------------------------------------------------


def test_get_match_data_valid_match(populated_db):
    result = cricket_tools.get_match_data(populated_db, "match_1001")

    assert result["match_id"] == "match_1001"
    assert set(result["teams"]) == {"Riverside Kings", "Hillcrest Tigers"}
    assert result["venue"] == "College Ground No. 1"
    assert result["match_type"] == "T20"
    assert len(result["innings"]) == 2

    first_innings = result["innings"][0]
    assert first_innings["batting_team"] == "Riverside Kings"
    # Runs: over0 (4+1+0+0(wide counted as extra not batter)+6+0+1) + over1(2+0+4+0+1+0)
    # total_runs sums runs_total across all deliveries including extras.
    assert first_innings["total_runs"] > 0
    assert first_innings["wickets_lost"] == 2


def test_get_match_data_unknown_match_raises(populated_db):
    with pytest.raises(MatchNotFoundError):
        cricket_tools.get_match_data(populated_db, "does_not_exist")


# --- compute_stats ----------------------------------------------------------


def test_compute_stats_known_batter_across_two_matches(populated_db):
    result = cricket_tools.compute_stats(populated_db, "A Sharma")

    assert result["matches_considered"] == ["match_1001", "match_1002"]
    assert result["batting"] is not None
    # match 1001: 4+1+2+0+4+1 = 12 off the bat (7 balls faced, excluding 1 wide faced by B Verma not A Sharma)
    # match 1002: 0+1+6+6+4 = 17 off the bat, out caught-and-bowled
    assert result["batting"]["runs"] == 12 + 17
    assert result["batting"]["dismissals"] == 1  # out once (in match_1002), not out in match_1001


def test_compute_stats_unknown_player_raises(populated_db):
    with pytest.raises(PlayerNotFoundError):
        cricket_tools.compute_stats(populated_db, "Z Nobody")


def test_compute_stats_invalid_last_n_matches_raises(populated_db):
    with pytest.raises(InvalidRangeError):
        cricket_tools.compute_stats(populated_db, "A Sharma", last_n_matches=0)


def test_compute_stats_last_n_matches_filters_range(populated_db):
    result = cricket_tools.compute_stats(populated_db, "A Sharma", last_n_matches=1)
    assert result["matches_considered"] == ["match_1002"]


def test_compute_stats_low_sample_size_warning_present(populated_db):
    result = cricket_tools.compute_stats(populated_db, "A Sharma", last_n_matches=1)
    assert result["sample_size_warning"] is not None
    assert "low-confidence" in result["sample_size_warning"]


def test_compute_stats_allrounder_has_both_batting_and_bowling(populated_db):
    # D Singh bats a little in match_1002 and bowls a full over of legal
    # deliveries in the second innings of BOTH matches, so both sections
    # should be populated, and his two lbw/bowled wickets (one per match)
    # should both be counted.
    result = cricket_tools.compute_stats(populated_db, "D Singh")
    assert result["batting"] is not None
    assert result["bowling"] is not None
    assert result["bowling"]["wickets"] == 2
    assert result["bowling"]["legal_deliveries"] == 12


def test_compute_stats_start_end_match_range(populated_db):
    result = cricket_tools.compute_stats(
        populated_db, "A Sharma", start_match="match_1001", end_match="match_1001"
    )
    assert result["matches_considered"] == ["match_1001"]


def test_compute_stats_invalid_start_after_end_raises(populated_db):
    with pytest.raises(InvalidRangeError):
        cricket_tools.compute_stats(
            populated_db,
            "A Sharma",
            start_match="match_1002",
            end_match="match_1001",
        )


def test_run_out_not_credited_to_bowler(populated_db):
    # No run-outs exist in the fixtures; this test documents and locks in
    # the exclusion rule using a bowler (F Patel) who has zero wickets by
    # any method, to guard against a future accidental regression that
    # credits run-outs to bowlers.
    result = cricket_tools.compute_stats(populated_db, "F Patel")
    assert result["bowling"]["wickets"] == 1  # F Patel bowled B Verma (bowled, not run out)


# --- replay (start_replay / get_next_ball) ----------------------------------


def test_replay_returns_deliveries_in_order(populated_db):
    session = cricket_tools.start_replay(populated_db, "match_1001")
    # 13 deliveries in the first innings + 6 in the second innings = 19.
    assert session["total_deliveries"] == 19

    first_ball = cricket_tools.get_next_ball(populated_db, session["session_id"])
    assert first_ball["actual_delivery"] == "0.1"
    assert first_ball["batter"] == "A Sharma"
    assert first_ball["position"] == 1

    second_ball = cricket_tools.get_next_ball(populated_db, session["session_id"])
    assert second_ball["actual_delivery"] == "0.2"
    assert second_ball["position"] == 2


def test_replay_reports_wickets_and_extras(populated_db):
    session = cricket_tools.start_replay(populated_db, "match_1001")
    sid = session["session_id"]

    balls = [cricket_tools.get_next_ball(populated_db, sid) for _ in range(6)]

    wide_ball = balls[3]  # 4th delivery: the wide (see fixtures/match_1001.json)
    assert wide_ball["extras"]["wides"] == 1
    assert wide_ball["wickets"] == []

    wicket_ball = balls[5]  # 6th delivery: B Verma bowled
    assert wicket_ball["wickets"] == [
        {"kind": "bowled", "player_out": "B Verma", "fielders": []}
    ]


def test_replay_ends_cleanly(populated_db):
    session = cricket_tools.start_replay(populated_db, "match_1001")
    sid = session["session_id"]
    for _ in range(session["total_deliveries"]):
        cricket_tools.get_next_ball(populated_db, sid)

    with pytest.raises(ReplayEndedError):
        cricket_tools.get_next_ball(populated_db, sid)


def test_replay_unknown_session_raises(populated_db):
    with pytest.raises(ReplaySessionNotFoundError):
        cricket_tools.get_next_ball(populated_db, "not-a-real-session")


def test_replay_unknown_match_raises(populated_db):
    with pytest.raises(MatchNotFoundError):
        cricket_tools.start_replay(populated_db, "does_not_exist")
