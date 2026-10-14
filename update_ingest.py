import re

with open('src/cricket_agent/ingest.py', 'r') as f:
    code = f.read()

code = code.replace("from cricket_agent.database import connect, create_schema, insert_match, replace_dataset_metadata", "from cricket_agent.database import connect, create_schema, insert_match, replace_dataset_metadata, clear_database")

# Inside ingest_directory, we won't clear. 
# We will clear inside main() before ingest_directory is called.

old_main_snippet = """
    succeeded, failed = ingest_directory(source_dir, args.db_path)
"""

new_main_snippet = """
    conn = connect(args.db_path)
    try:
        row = conn.execute("SELECT is_demo FROM dataset_metadata WHERE metadata_key = 'current'").fetchone()
        is_currently_demo = bool(row["is_demo"]) if row else False
        is_target_demo = (args.source == "fixtures" or source_dir.name == "fixtures")
        if row and is_currently_demo != is_target_demo:
            clear_database(conn)
    except sqlite3.OperationalError:
        pass # Table doesn't exist yet
    finally:
        conn.close()

    succeeded, failed = ingest_directory(source_dir, args.db_path)
"""
code = code.replace(old_main_snippet, new_main_snippet)

# Also need to import sqlite3 in ingest.py if not imported. Wait, ingest.py doesn't have sqlite3?
if "import sqlite3" not in code:
    code = code.replace("import sys", "import sys\nimport sqlite3")

with open('src/cricket_agent/ingest.py', 'w') as f:
    f.write(code)
print("Updated ingest.py")
