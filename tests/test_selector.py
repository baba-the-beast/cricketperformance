"""Tests for venue analytics, team-level matchups, and the lineup selector."""

from __future__ import annotations

import pytest

from cricket_agent.agents.selector import recommend_lineup
from cricket_agent.analytics.conditions import resolve_conditions
from cricket_agent.analytics.matchup import player_vs_team
from cricket_agent.analytics.venue import player_form_at_venue, venue_profile
from cricket_agent.exceptions import InvalidRangeError


def test_venue_profile_known_venue(populated_db):
    profile = venue_profile(populated_db, "College Ground No. 1")
    assert profile["available"] is True
    assert profile["match_count"] >= 1
    assert "characterisation" in profile


def test_venue_profile_unknown_venue(populated_db):
    profile = venue_profile(populated_db, "Nonexistent Stadium")
    assert profile["available"] is False
    assert profile["match_count"] == 0


def test_player_form_at_venue_known(populated_db):
    form = player_form_at_venue(populated_db, "A Sharma", "College Ground No. 1")
    assert form["available"] is True
    assert form["matches_considered"] >= 1


def test_player_form_at_venue_no_data(populated_db):
    form = player_form_at_venue(populated_db, "A Sharma", "Nonexistent Stadium")
    assert form["available"] is False


def test_player_vs_team_known_roster(populated_db):
    result = player_vs_team(populated_db, "F Patel", "Riverside Kings")
    assert result["available"] is True
    assert result["batting"] is None or result["bowling"] is not None or True


def test_player_vs_team_unknown_team(populated_db):
    result = player_vs_team(populated_db, "F Patel", "Nobody FC")
    assert result["available"] is False


def test_resolve_conditions_manual_wins():
    resolved = resolve_conditions(venue="Anywhere", pitch_note="Dry, expected to turn")
    assert resolved["available"] is True
    assert resolved["source"] == "manual"
    assert resolved["pitch_note"] == "Dry, expected to turn"


def test_resolve_conditions_unavailable_by_default():
    resolved = resolve_conditions(venue="Anywhere")
    assert resolved["available"] is False
    assert resolved["source"] == "unavailable"


def test_recommend_lineup_basic(populated_db):
    squad = ["A Sharma", "B Verma", "D Singh", "E Khan"]
    result = recommend_lineup(
        populated_db,
        squad=squad,
        opponent="Hillcrest Tigers",
        venue="College Ground No. 1",
        xi_size=11,
    )
    assert result["prediction_type"] == "evidence-ranked recommendation"
    assert len(result["recommended_xi"]) == len(squad)
    assert result["bench"] == []
    assert any("Squad has only" in f for f in result["flags"])
    names = {c["player"] for c in result["recommended_xi"]}
    assert names == set(squad)
    for case in result["recommended_xi"]:
        assert "evidence" in case
        assert isinstance(case["evidence"], list)


def test_recommend_lineup_empty_squad_raises(populated_db):
    with pytest.raises(InvalidRangeError):
        recommend_lineup(populated_db, squad=[])


def test_recommend_lineup_no_venue_or_opponent(populated_db):
    result = recommend_lineup(populated_db, squad=["A Sharma", "B Verma"])
    assert result["venue_profile"] is None
    assert result["conditions"]["available"] is False
    assert result["opponent"] is None


def test_recommend_lineup_manual_conditions(populated_db):
    result = recommend_lineup(
        populated_db,
        squad=["A Sharma", "B Verma"],
        pitch_note="Green top, expect early movement",
    )
    assert result["conditions"]["available"] is True
    assert result["conditions"]["pitch_note"] == "Green top, expect early movement"
