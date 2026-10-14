import sqlite3
from cricket_agent.config import DATABASE_PATH

conn = sqlite3.connect(DATABASE_PATH)
c = conn.cursor()

def fetch(q):
    return c.execute(q).fetchone()[0]

teams = fetch("SELECT COUNT(*) FROM (SELECT DISTINCT team_home FROM matches UNION SELECT DISTINCT team_away FROM matches)")
matches = fetch("SELECT COUNT(*) FROM matches")
deliveries = fetch("SELECT COUNT(*) FROM deliveries")
wickets = fetch("SELECT COUNT(*) FROM wickets")
date_min = fetch("SELECT MIN(match_date) FROM matches")
date_max = fetch("SELECT MAX(match_date) FROM matches")
formats = [row[0] for row in c.execute("SELECT DISTINCT match_type FROM matches").fetchall()]
try:
    players = fetch("SELECT COUNT(DISTINCT registry_id) FROM player_metadata")
except sqlite3.OperationalError:
    players = fetch("SELECT COUNT(DISTINCT player_name) FROM player_participation")
try:
    competitions = [row[0] for row in c.execute("SELECT DISTINCT event_name FROM matches WHERE event_name IS NOT NULL").fetchall()]
except sqlite3.OperationalError:
    competitions = []

print(f"Players: {players}")
print(f"Teams: {teams}")
print(f"Matches: {matches}")
print(f"Deliveries: {deliveries}")
print(f"Wickets: {wickets}")
print(f"Date Range: {date_min} to {date_max}")
print(f"Formats: {formats}")
print(f"Competitions: {competitions}")
