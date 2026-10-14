import sqlite3
from cricket_agent.config import DATABASE_PATH

def audit():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    print("--- PHASE 2: DATABASE AUDIT ---")
    
    # 1. Foreign keys
    print("1. Foreign Keys:", list(conn.execute("PRAGMA foreign_key_check").fetchall()))
    
    # 3. Duplicate matches
    print("3. Duplicate matches:", conn.execute("SELECT match_id, COUNT(*) FROM matches GROUP BY match_id HAVING COUNT(*) > 1").fetchall())
    
    # 4. Duplicate deliveries
    print("4. Duplicate deliveries:", conn.execute("SELECT match_id, innings_index, over_number, ball_in_over, COUNT(*) FROM deliveries GROUP BY match_id, innings_index, over_number, ball_in_over HAVING COUNT(*) > 1").fetchall())
    
    # 5. Duplicate wickets
    print("5. Duplicate wickets:", conn.execute("SELECT delivery_pk, player_out, COUNT(*) FROM wickets GROUP BY delivery_pk, player_out HAVING COUNT(*) > 1").fetchall())
    
    # 6. Orphaned wickets
    print("6. Orphaned wickets:", conn.execute("SELECT COUNT(*) FROM wickets WHERE delivery_pk NOT IN (SELECT delivery_pk FROM deliveries)").fetchone()[0])
    
    # 7. Orphaned deliveries
    print("7. Orphaned deliveries:", conn.execute("SELECT COUNT(*) FROM deliveries WHERE match_id NOT IN (SELECT match_id FROM matches)").fetchone()[0])
    
    # 8. Orphaned player participation
    print("8. Orphaned player_participation:", conn.execute("SELECT COUNT(*) FROM player_participation WHERE match_id NOT IN (SELECT match_id FROM matches)").fetchone()[0])
    
    # 9. Invalid innings references
    print("9. Orphaned innings:", conn.execute("SELECT COUNT(*) FROM innings WHERE match_id NOT IN (SELECT match_id FROM matches)").fetchone()[0])
    print("9. Deliveries with invalid innings:", conn.execute("SELECT COUNT(*) FROM deliveries d LEFT JOIN innings i ON d.match_id = i.match_id AND d.innings_index = i.innings_index WHERE i.match_id IS NULL").fetchone()[0])
    
    # 10. Null critical fields
    print("10. Null match dates:", conn.execute("SELECT COUNT(*) FROM matches WHERE match_date IS NULL").fetchone()[0])
    print("10. Null match format:", conn.execute("SELECT COUNT(*) FROM matches WHERE match_type IS NULL").fetchone()[0])
    
    # 11. Invalid run totals (runs_total != runs_batter + extras)
    print("11. Invalid run totals:", conn.execute("SELECT COUNT(*) FROM deliveries WHERE runs_total != (runs_batter + runs_wides + runs_noballs + runs_byes + runs_legbyes + runs_penalty)").fetchone()[0])
    
    # 12. Invalid extra totals
    print("12. Invalid extras:", conn.execute("SELECT COUNT(*) FROM deliveries WHERE runs_wides < 0 OR runs_noballs < 0").fetchone()[0])
    
    # 14. Invalid dates
    # Assuming match_date should be YYYY-MM-DD
    print("14. Invalid dates:", conn.execute("SELECT match_id, match_date FROM matches WHERE match_date NOT LIKE '____-__-__'").fetchall())
    
    # 15. Duplicate player identities (players with same name)
    print("15. Duplicate player names:", conn.execute("SELECT player_name, COUNT(*) FROM players GROUP BY player_name HAVING COUNT(*) > 1").fetchall())
    
    # Dataset counts
    print("\nDataset counts:")
    print("Matches:", conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0])
    print("Deliveries:", conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0])
    print("Players:", conn.execute("SELECT COUNT(*) FROM players").fetchone()[0])
    print("Wickets:", conn.execute("SELECT COUNT(*) FROM wickets").fetchone()[0])

if __name__ == "__main__":
    audit()
