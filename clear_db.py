import re

with open('src/cricket_agent/database.py', 'r') as f:
    code = f.read()

new_func = """
def clear_database(conn: sqlite3.Connection) -> None:
    \"\"\"Drop all tables to prepare for a clean ingestion of a different dataset.\"\"\"
    tables = [
        "matches", "innings", "deliveries", "wickets", "players",
        "player_participation", "teams", "venues", "dataset_metadata"
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
"""

# Insert it before create_schema
code = code.replace("def create_schema(conn: sqlite3.Connection) -> None:", new_func + "\ndef create_schema(conn: sqlite3.Connection) -> None:")

with open('src/cricket_agent/database.py', 'w') as f:
    f.write(code)
print("Added clear_database")
