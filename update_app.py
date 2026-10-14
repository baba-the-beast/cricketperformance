import re

with open('webapp/app.py', 'r') as f:
    code = f.read()

code = code.replace("from cricket_agent.database import connect, create_schema", "from cricket_agent.database import connect, create_schema, clear_database")

old_snippet = """
    for source_dir in real_sources:
        if source_dir.exists() and any(source_dir.rglob("*.json")):
            from cricket_agent.ingest import ingest_directory
            ingest_directory(source_dir, DATABASE_PATH)
            return
"""

new_snippet = """
    for source_dir in real_sources:
        if source_dir.exists() and any(source_dir.rglob("*.json")):
            from cricket_agent.ingest import ingest_directory
            conn = connect(DATABASE_PATH)
            clear_database(conn)
            conn.close()
            ingest_directory(source_dir, DATABASE_PATH)
            return
"""
code = code.replace(old_snippet, new_snippet)

with open('webapp/app.py', 'w') as f:
    f.write(code)
print("Updated webapp/app.py")
