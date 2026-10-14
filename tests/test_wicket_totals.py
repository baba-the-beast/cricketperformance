import sqlite3
import pytest
from pathlib import Path
from cricket_agent.database import connect, create_schema, insert_match
from cricket_agent.ingestion.parser import parse_match_file
from cricket_agent.tools.cricket_tools import get_match_data

def test_wicket_totals_integration():
    # 1. Raw Cricsheet
    fixtures_dir = Path(__file__).parent / "fixtures"
    match = parse_match_file(fixtures_dir / "match_1001.json")
    
    # 2. Database
    conn = connect(Path(":memory:"))
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    insert_match(conn, match)
    
    # 3. Scorecard API (get_match_data)
    match_data = get_match_data(conn, "match_1001")
    
    # Verify innings 0
    in0 = match_data["innings"][0]
    # Let's count actual dismissals in the match object
    raw_wickets_0 = 0
    for d in match.innings[0].deliveries:
        for w in d.wickets:
            if w.kind not in ("retired hurt", "retired not out"):
                raw_wickets_0 += 1
                
    assert in0["wickets_lost"] == raw_wickets_0, f"Expected {raw_wickets_0}, got {in0['wickets_lost']}"
    
    conn.close()
