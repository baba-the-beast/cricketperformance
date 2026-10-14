import sqlite3
from cricket_agent.config import DATABASE_PATH
from cricket_agent.tools.cricket_tools import compute_stats

conn = sqlite3.connect(DATABASE_PATH)
conn.row_factory = sqlite3.Row

# Get 5 players with substantial deliveries
players = [row["player_name"] for row in conn.execute(
    "SELECT batter AS player_name FROM deliveries GROUP BY batter HAVING COUNT(*) > 100 ORDER BY RANDOM() LIMIT 5"
).fetchall()]

for player in players:
    print(f"Validating {player}...")
    stats = compute_stats(conn, player)
    
    # Independent Batting Calculation
    batting_sql = conn.execute("""
        SELECT 
            COUNT(DISTINCT match_id || '-' || innings_index) as innings,
            SUM(runs_batter) as runs,
            SUM(CASE WHEN runs_wides = 0 THEN 1 ELSE 0 END) as balls_faced,
            SUM(CASE WHEN runs_batter = 4 THEN 1 ELSE 0 END) as fours,
            SUM(CASE WHEN runs_batter = 6 THEN 1 ELSE 0 END) as sixes
        FROM deliveries
        WHERE batter = ?
    """, (player,)).fetchone()
    
    dismissals = conn.execute("""
        SELECT COUNT(*) as n FROM wickets w
        JOIN deliveries d ON w.delivery_pk = d.delivery_pk
        WHERE w.player_out = ? AND w.kind != 'retired hurt'
    """, (player,)).fetchone()["n"]
    
    avg = batting_sql["runs"] / dismissals if dismissals > 0 else None
    sr = (batting_sql["runs"] / batting_sql["balls_faced"] * 100) if batting_sql["balls_faced"] > 0 else None
    
    b_stats = stats["batting"]
    assert b_stats["innings"] == batting_sql["innings"]
    assert b_stats["runs"] == batting_sql["runs"]
    assert b_stats["balls_faced"] == batting_sql["balls_faced"]
    assert b_stats["fours"] == batting_sql["fours"]
    assert b_stats["sixes"] == batting_sql["sixes"]
    assert b_stats["dismissals"] == dismissals
    
    if avg is not None:
        assert abs(b_stats["average"] - avg) < 0.001
    else:
        assert b_stats["average"] is None
        
    if sr is not None:
        assert abs(b_stats["strike_rate"] - sr) < 0.001
    else:
        assert b_stats["strike_rate"] is None

    # Independent Bowling Calculation
    bowling_sql = conn.execute("""
        SELECT 
            SUM(CASE WHEN runs_wides = 0 AND runs_noballs = 0 THEN 1 ELSE 0 END) as legal_deliveries,
            SUM(runs_total - runs_byes - runs_legbyes - runs_penalty) as runs_conceded,
            SUM(CASE WHEN runs_wides = 0 AND runs_noballs = 0 AND runs_total = 0 THEN 1 ELSE 0 END) as dot_balls
        FROM deliveries
        WHERE bowler = ?
    """, (player,)).fetchone()
    
    if bowling_sql and bowling_sql["legal_deliveries"] is not None:
        wickets = conn.execute("""
            SELECT COUNT(*) as n FROM wickets w
            JOIN deliveries d ON w.delivery_pk = d.delivery_pk
            WHERE d.bowler = ? AND w.kind IN ('bowled', 'caught', 'lbw', 'stumped', 'hit wicket', 'caught and bowled')
        """, (player,)).fetchone()["n"]
        
        legal = bowling_sql["legal_deliveries"]
        runs_c = bowling_sql["runs_conceded"]
        dots = bowling_sql["dot_balls"]
        
        overs_decimal = legal / 6.0
        econ = runs_c / overs_decimal if overs_decimal > 0 else None
        b_avg = runs_c / wickets if wickets > 0 else None
        b_sr = legal / wickets if wickets > 0 else None
        
        bw_stats = stats["bowling"]
        assert bw_stats is not None
        assert bw_stats["legal_deliveries"] == legal
        assert bw_stats["runs_conceded"] == runs_c
        assert bw_stats["wickets"] == wickets
        assert bw_stats["dot_balls"] == dots
        
        if econ is not None: assert abs(bw_stats["economy"] - econ) < 0.001
        else: assert bw_stats["economy"] is None
        
        if b_avg is not None: assert abs(bw_stats["average"] - b_avg) < 0.001
        else: assert bw_stats["average"] is None
        
        if b_sr is not None: assert abs(bw_stats["strike_rate"] - b_sr) < 0.001
        else: assert bw_stats["strike_rate"] is None

print("Validation Successful!")
