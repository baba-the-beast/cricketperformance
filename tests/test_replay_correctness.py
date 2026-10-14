import sqlite3
import pytest
from cricket_agent.tools.cricket_tools import start_replay, get_next_ball
from cricket_agent.exceptions import ReplayEndedError

def get_real_matches(conn, limit=10):
    rows = conn.execute("SELECT match_id FROM matches ORDER BY RANDOM() LIMIT ?", (limit,)).fetchall()
    return [r["match_id"] for r in rows]

def get_match_totals(conn, match_id):
    innings_totals = conn.execute("""
        SELECT innings_index, SUM(runs_total) as total_runs, 
               SUM(CASE WHEN w.wicket_pk IS NOT NULL THEN 1 ELSE 0 END) as total_wickets
        FROM deliveries d
        LEFT JOIN wickets w ON d.delivery_pk = w.delivery_pk
        WHERE d.match_id = ?
        GROUP BY innings_index
    """, (match_id,)).fetchall()
    
    return {r["innings_index"]: {"runs": r["total_runs"], "wickets": r["total_wickets"]} for r in innings_totals}

def test_replay_correctness(real_db):
    conn = real_db
    matches = get_real_matches(conn, 20)
    
    for match_id in matches:
        expected = get_match_totals(conn, match_id)
        
        replay_start = start_replay(conn, match_id)
        session_id = replay_start["session_id"]
        
        innings_state = {}
        
        while True:
            try:
                ball = get_next_ball(conn, session_id)
            except ReplayEndedError:
                break
                
            idx = ball["innings_index"]
            if idx not in innings_state:
                innings_state[idx] = {"runs": 0, "wickets": 0}
                
            innings_state[idx]["runs"] += ball["runs_total"]
            if ball["wickets"]:
                innings_state[idx]["wickets"] += len(ball["wickets"])
                
        for idx, exp in expected.items():
            assert innings_state[idx]["runs"] == exp["runs"]
            assert innings_state[idx]["wickets"] == exp["wickets"]
            
    conn.close()
