from cricket_agent.exceptions import ReplayAtStartError
from cricket_agent.simulation import LABEL, simulate_innings, simulate_match
from cricket_agent.tools import cricket_tools
import pytest


def test_simulation_labelled_and_over_count(populated_db):
    result = simulate_innings(
        populated_db,
        ["A Sharma", "B Verma"],
        ["F Patel", "G Nair"],
        overs=4,
        target=40,
    )
    assert result["available"] is True
    assert result["label"] == LABEL
    assert len(result["progression"]) == 4
    assert "not a match result" in " ".join(result["assumptions"]).lower() or "expected-value" in result["assumptions"][0].lower()


def test_simulation_unavailable_without_lineups(populated_db):
    result = simulate_innings(populated_db, [], ["F Patel"], overs=2)
    assert result["available"] is False


def test_simulation_has_scorecard_and_player_breakdown(populated_db):
    result = simulate_innings(
        populated_db,
        ["A Sharma", "B Verma"],
        ["F Patel", "G Nair"],
        overs=3,
        target=40,
    )
    assert result["available"] is True
    assert "scorecard" in result
    assert "player_statistics" in result
    assert result["scorecard"]["projected_score"] >= 0
    assert result["scorecard"]["runs"] >= 0
    assert result["player_statistics"]["batting"]["A Sharma"]["runs"] >= 0


def test_previous_ball_rewinds(populated_db):
    session = cricket_tools.start_replay(populated_db, "match_1001")
    sid = session["session_id"]
    first = cricket_tools.get_next_ball(populated_db, sid)
    second = cricket_tools.get_next_ball(populated_db, sid)
    assert second["position"] == 2
    prev = cricket_tools.get_previous_ball(populated_db, sid)
    assert prev["actual_delivery"] == first["actual_delivery"]
    again = cricket_tools.get_next_ball(populated_db, sid)
    assert again["actual_delivery"] == second["actual_delivery"]


def test_previous_at_start_raises(populated_db):
    session = cricket_tools.start_replay(populated_db, "match_1001")
    with pytest.raises(ReplayAtStartError):
        cricket_tools.get_previous_ball(populated_db, session["session_id"])


def test_full_match_simulation_has_two_innings_and_reproducible_deliveries(populated_db):
    lineups = {
        "team_a": "Hillcrest Tigers",
        "team_b": "Riverside Hawks",
        "batting_lineup_a": ["A Sharma", "B Verma", "C Rao", "D Singh"],
        "batting_lineup_b": ["E Khan", "F Patel", "G Nair", "H Shah"],
        "bowling_lineup_a": ["A Sharma", "B Verma"],
        "bowling_lineup_b": ["F Patel", "G Nair"],
        "overs": 4,
        "seed": 37,
    }
    first = simulate_match(populated_db, **lineups)
    second = simulate_match(populated_db, **lineups)

    assert first["available"] is True
    assert len(first["innings"]) == 2
    assert first["target"] == first["innings"][0]["total_runs"] + 1
    assert first["player_of_match"] is None
    assert first["deliveries"] == second["deliveries"]
    assert {ball["innings_index"] for ball in first["deliveries"]} <= {0, 1}
    assert all(0 <= innings["legal_deliveries_faced"] <= 24 for innings in first["innings"])
    if first["innings"][1]["total_runs"] >= first["target"]:
        assert first["winner"] == "Riverside Hawks"


def test_full_odi_simulation_runs_up_to_50_overs_per_innings(populated_db):
    team_a = [f"A batter {index}" for index in range(1, 12)]
    team_b = [f"B batter {index}" for index in range(1, 12)]
    bowlers_a = [f"A bowler {index}" for index in range(1, 6)]
    bowlers_b = [f"B bowler {index}" for index in range(1, 6)]
    result = simulate_match(
        populated_db,
        team_a="Team A",
        team_b="Team B",
        batting_lineup_a=team_a,
        batting_lineup_b=team_b,
        bowling_lineup_a=bowlers_a,
        bowling_lineup_b=bowlers_b,
        overs=50,
        match_type="ODI",
        seed=81,
    )

    assert result["available"] is True
    assert result["match_type"] == "ODI · 50-over simulation"
    assert result["players"] == {"Team A": team_a, "Team B": team_b}
    assert len(result["innings"]) == 2
    assert all(innings["legal_deliveries_faced"] <= 300 for innings in result["innings"])
