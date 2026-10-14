import sqlite3
from cricket_agent.config import DATABASE_PATH

conn = sqlite3.connect(DATABASE_PATH)
c = conn.cursor()

def fetch(q):
    return c.execute(q).fetchone()[0]

players = fetch("SELECT COUNT(DISTINCT player_name) FROM player_participation")
teams = fetch("SELECT COUNT(DISTINCT team_home) FROM matches")
matches = fetch("SELECT COUNT(*) FROM matches")
deliveries = fetch("SELECT COUNT(*) FROM deliveries")
wickets = fetch("SELECT COUNT(*) FROM wickets")
date_min = fetch("SELECT MIN(match_date) FROM matches")
date_max = fetch("SELECT MAX(match_date) FROM matches")
formats = [row[0] for row in c.execute("SELECT DISTINCT match_type FROM matches").fetchall()]

print(f"Players: {players}")
print(f"Teams: {teams}")
print(f"Matches: {matches}")
print(f"Deliveries: {deliveries}")
print(f"Wickets: {wickets}")
print(f"Date Range: {date_min} to {date_max}")
print(f"Formats: {formats}")

conn.close()
