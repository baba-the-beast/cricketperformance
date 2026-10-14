from cricket_agent.memory import create_analysis_session, load_analysis_session, save_coordinator


def test_analysis_session_round_trip(populated_db):
    create_analysis_session(
        populated_db,
        "sess-1",
        "Who to pick?",
        selected_players=["A Sharma"],
        selected_match="match_1001",
        last_n_matches=2,
        opponent="Hillcrest Tigers",
    )
    save_coordinator(populated_db, "sess-1", {"selection_summary": "LOW CONFIDENCE sample"})
    loaded = load_analysis_session(populated_db, "sess-1")
    assert loaded["question"] == "Who to pick?"
    assert loaded["selected_players"] == ["A Sharma"]
    assert loaded["coordinator"]["selection_summary"] == "LOW CONFIDENCE sample"
