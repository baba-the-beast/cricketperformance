import sqlite3
import pytest
from cricket_agent.simulation import simulate_match

def test_historical_backtest_with_date_limit(real_db):
    conn = real_db
    
    # Time-based separation
    all_matches = conn.execute("SELECT match_id, match_date, team_home, team_away, match_type FROM matches ORDER BY match_date ASC").fetchall()
    
    for test_match in all_matches[-10:]:
        match_id = test_match["match_id"]
        date_limit = test_match["match_date"]
        team_a = test_match["team_home"]
        team_b = test_match["team_away"]
        match_type = test_match["match_type"] or "T20"
        if match_type.upper() not in ["ODI", "T20"]:
            match_type = "T20"
    
        players_a = list(dict.fromkeys([r["player_name"] for r in conn.execute("SELECT player_name FROM player_participation WHERE match_id = ? AND team = ?", (match_id, team_a)).fetchall()]))
        players_b = list(dict.fromkeys([r["player_name"] for r in conn.execute("SELECT player_name FROM player_participation WHERE match_id = ? AND team = ?", (match_id, team_b)).fetchall()]))
        
        if len(players_a) >= 11 and len(players_b) >= 11:
            batters_a = players_a[:11]
            batters_b = players_b[:11]
            bowlers_a = players_a[5:11]
            bowlers_b = players_b[5:11]
            
            sim_result = simulate_match(
                conn,
                team_a=team_a,
                team_b=team_b,
                batting_lineup_a=batters_a,
                batting_lineup_b=batters_b,
                bowling_lineup_a=bowlers_a,
                bowling_lineup_b=bowlers_b,
                overs=20 if match_type=="T20" else 50,
                match_type=match_type,
                date_limit=date_limit
            )
            
            assert sim_result["available"] is True
            assert "innings" in sim_result
            break

