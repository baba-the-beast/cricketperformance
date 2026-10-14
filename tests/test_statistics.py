"""Tests for cricket_agent.analytics.statistics.

These tests use plain dictionaries (not a real database) because the
statistics engine takes Mapping objects, not SQL -- see the module
docstring in analytics/statistics.py for why that separation exists.
"""

from __future__ import annotations

from cricket_agent.analytics.statistics import compute_batting_stats, compute_bowling_stats


def _delivery(
    runs_batter=0,
    runs_total=None,
    runs_wides=0,
    runs_noballs=0,
    runs_byes=0,
    runs_legbyes=0,
    runs_penalty=0,
):
    if runs_total is None:
        runs_total = runs_batter + runs_wides + runs_noballs + runs_byes + runs_legbyes + runs_penalty
    return {
        "runs_batter": runs_batter,
        "runs_total": runs_total,
        "runs_wides": runs_wides,
        "runs_noballs": runs_noballs,
        "runs_byes": runs_byes,
        "runs_legbyes": runs_legbyes,
        "runs_penalty": runs_penalty,
    }


# --- Batting -----------------------------------------------------------


def test_batting_strike_rate_and_average_known_player():
    deliveries = [
        _delivery(runs_batter=4),
        _delivery(runs_batter=0),
        _delivery(runs_batter=6),
        _delivery(runs_batter=1),
    ]
    stats = compute_batting_stats(deliveries, dismissals_of_player=1, innings_count=1)

    assert stats.runs == 11
    assert stats.balls_faced == 4
    assert stats.strike_rate == 275.0
    assert stats.average == 11.0
    assert stats.fours == 1
    assert stats.sixes == 1


def test_batting_average_is_none_when_never_dismissed():
    deliveries = [_delivery(runs_batter=10), _delivery(runs_batter=20)]
    stats = compute_batting_stats(deliveries, dismissals_of_player=0, innings_count=1)

    # Not out every innings -- average is undefined by cricket convention,
    # and must NOT silently become 0 or infinity.
    assert stats.average is None
    assert stats.runs == 30


def test_batting_strike_rate_is_none_with_zero_balls_faced():
    stats = compute_batting_stats([], dismissals_of_player=0, innings_count=1)
    assert stats.strike_rate is None
    assert stats.average is None
    assert stats.runs == 0
    assert stats.balls_faced == 0


def test_batting_not_outs_derived_correctly():
    deliveries = [_delivery(runs_batter=1)] * 10
    stats = compute_batting_stats(deliveries, dismissals_of_player=2, innings_count=5)
    result = stats.to_dict()
    assert result["not_outs"] == 3


# --- Bowling -------------------------------------------------------------


def test_bowling_economy_and_average_known_bowler():
    # One full over (6 legal deliveries), 8 runs conceded, 1 wicket.
    deliveries = [
        _delivery(runs_batter=4),
        _delivery(runs_batter=0),
        _delivery(runs_batter=1),
        _delivery(runs_batter=1),
        _delivery(runs_batter=2),
        _delivery(runs_batter=0),
    ]
    stats = compute_bowling_stats(deliveries, wickets_taken=1)

    assert stats.legal_deliveries == 6
    assert stats.runs_conceded == 8
    assert stats.overs_display == "1.0"
    assert stats.economy == 8.0
    assert stats.average == 8.0
    assert stats.strike_rate == 6.0
    assert stats.dot_balls == 2


def test_bowling_average_is_none_with_zero_wickets():
    deliveries = [_delivery(runs_batter=4) for _ in range(6)]
    stats = compute_bowling_stats(deliveries, wickets_taken=0)

    assert stats.average is None
    assert stats.strike_rate is None
    # Economy is still defined -- it does not depend on wickets.
    assert stats.economy == 24.0


def test_bowling_economy_is_none_with_zero_legal_deliveries():
    # A bowler who only ever bowled wides (0 legal deliveries).
    deliveries = [_delivery(runs_wides=1) for _ in range(3)]
    stats = compute_bowling_stats(deliveries, wickets_taken=0)

    assert stats.legal_deliveries == 0
    assert stats.economy is None


def test_wides_and_noballs_are_charged_to_bowler():
    deliveries = [_delivery(runs_wides=1), _delivery(runs_noballs=1)]
    stats = compute_bowling_stats(deliveries, wickets_taken=0)

    assert stats.runs_conceded == 2
    assert stats.legal_deliveries == 0


def test_byes_and_legbyes_are_not_charged_to_bowler():
    deliveries = [_delivery(runs_byes=4), _delivery(runs_legbyes=2)]
    stats = compute_bowling_stats(deliveries, wickets_taken=0)

    assert stats.runs_conceded == 0
    assert stats.legal_deliveries == 2


def test_byes_taken_off_a_wide_are_split_correctly():
    """A rare but legal delivery: umpire calls a wide, then extra runs are
    run as byes on the same ball. The bowler is charged for the wide (1
    run, by convention) but NOT for the byes."""
    delivery = _delivery(runs_wides=1, runs_byes=4, runs_total=5)
    stats = compute_bowling_stats([delivery], wickets_taken=0)

    assert stats.runs_conceded == 1
    assert stats.legal_deliveries == 0


def test_overs_display_formats_partial_over():
    deliveries = [_delivery(runs_batter=0) for _ in range(8)]  # 1.2 overs
    stats = compute_bowling_stats(deliveries, wickets_taken=0)
    assert stats.overs_display == "1.2"


def test_dot_balls_exclude_wides_and_noballs():
    deliveries = [_delivery(), _delivery(runs_wides=1), _delivery(runs_noballs=1)]
    stats = compute_bowling_stats(deliveries, wickets_taken=0)
    # Only the first delivery is a legal, run-less ball.
    assert stats.dot_balls == 1
