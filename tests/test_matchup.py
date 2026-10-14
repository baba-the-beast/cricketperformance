from cricket_agent.analytics.matchup import batter_vs_bowler
from cricket_agent.analytics.player_profile import compare_players
from cricket_agent.exceptions import PlayerNotFoundError
import pytest


def test_matchup_uses_real_deliveries(populated_db):
    result = batter_vs_bowler(populated_db, "A Sharma", "F Patel")
    assert result["available"] is True
    assert result["balls_faced"] >= 1
    assert result["runs"] == 5  # 4 then 1 in match_1001 over 0
    assert result["confidence"]["level"] == "LOW"


def test_matchup_unknown_player_raises(populated_db):
    with pytest.raises(PlayerNotFoundError):
        batter_vs_bowler(populated_db, "Z Nobody", "F Patel")


def test_compare_two_players_side_by_side(populated_db):
    result = compare_players(populated_db, ["A Sharma", "D Singh"])
    assert result["players"] == ["A Sharma", "D Singh"]
    assert result["comparison"]["batting"]["runs"][0] == 29
    assert "No composite rank" in result["comparison"]["note"]
