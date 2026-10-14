import sqlite3
from cricket_agent.config import DATABASE_PATH
from cricket_agent.database import replace_dataset_metadata

conn = sqlite3.connect(DATABASE_PATH)
conn.row_factory = sqlite3.Row
replace_dataset_metadata(conn, dataset_name="Cricsheet IPL", source="ipl", is_demo=False)
conn.close()
