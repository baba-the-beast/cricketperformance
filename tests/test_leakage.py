import sqlite3
import pytest
from pathlib import Path
from cricket_agent.database import connect, create_schema, insert_match
from cricket_agent.ingestion.parser import parse_match_file
from cricket_agent.simulation import _delivery_history

def test_temporal_leakage_protection():
    fixtures_dir = Path(__file__).parent / "fixtures"
    conn = connect(Path(":memory:"))
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    
    # Insert 1001 (date T) and 1002 (date T+1)
    match1 = parse_match_file(fixtures_dir / "match_1001.json")
    match2 = parse_match_file(fixtures_dir / "match_1002.json")
    
    insert_match(conn, match1)
    
    # We want to get the history of a player before 1002
    target_player = match2.innings[0].deliveries[0].batter
    date_limit = match2.info.dates[0]
    
    # Get history for this player before 1002
    history_before = _delivery_history(conn, target_player, batter=True, match_type=match2.info.match_type, date_limit=date_limit)
    
    # Now insert match 1002 (which is AT the date_limit, so it should be excluded)
    insert_match(conn, match2)
    
    # Get history again
    history_after = _delivery_history(conn, target_player, batter=True, match_type=match2.info.match_type, date_limit=date_limit)
    
    # The history MUST not change, meaning match_1002 didn't leak into the date_limit
    assert history_before == history_after, "Temporal leakage detected! Future match influenced historical features."
    
    conn.close()
