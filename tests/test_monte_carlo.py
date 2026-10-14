import sqlite3
import pytest
from cricket_agent.simulation import simulate_monte_carlo

def test_monte_carlo_performance(real_db):
    conn = real_db
    
    # We will pick a match in the middle of our DB.
    all_matches = conn.execute("SELECT match_id, match_date, team_home, team_away, match_type FROM matches ORDER BY match_date ASC").fetchall()
    if len(all_matches) < 2:
        pytest.skip("Not enough matches")
        
    test_match = all_matches[-1]
    
    match_id = test_match["match_id"]
    team_a = test_match["team_home"]
    team_b = test_match["team_away"]
    
    players_a = list(dict.fromkeys([r["player_name"] for r in conn.execute("SELECT player_name FROM player_participation WHERE match_id = ? AND team = ?", (match_id, team_a)).fetchall()]))
    players_b = list(dict.fromkeys([r["player_name"] for r in conn.execute("SELECT player_name FROM player_participation WHERE match_id = ? AND team = ?", (match_id, team_b)).fetchall()]))
    
    if len(players_a) >= 11 and len(players_b) >= 11:
        res = simulate_monte_carlo(
            conn,
            team_a=team_a,
            team_b=team_b,
            batting_lineup_a=players_a[:11],
            batting_lineup_b=players_b[:11],
            bowling_lineup_a=players_a[5:11],
            bowling_lineup_b=players_b[5:11],
            overs=20,
            match_type="T20",
            simulations=100  # We run 100 for a quick unit test
        )
        assert res["available"] is True
        assert len(res["results"]) == 100
        
    conn.close()

