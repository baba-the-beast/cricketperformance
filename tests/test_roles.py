from cricket_agent.analytics.player_profile import build_player_profile
from cricket_agent.analytics.roles import SPIN_PACE_NOTE, classify_roles


def test_role_includes_unassignable_spin_pace(populated_db):
    profile = build_player_profile(populated_db, "A Sharma")
    names = [r["role"] for r in profile["role"]["not_assigned"]]
    assert "Spin specialist" in names
    assert "Pace specialist" in names
    assert all(not r["assigned"] for r in profile["role"]["not_assigned"] if r["role"].endswith("specialist"))


def test_spin_note_is_explicit():
    assert "do not record bowling style" in SPIN_PACE_NOTE


def test_allrounder_candidate_for_d_singh(populated_db):
    profile = build_player_profile(populated_db, "D Singh")
    roles = [c["role"] for c in profile["role"]["candidates"]]
    assert "Bowling all-rounder" in roles
    assert profile["bowling"]["legal_deliveries"] == 12


def test_classify_without_matches_notes_insufficient(populated_db):
    result = classify_roles(populated_db, "A Sharma", [], None, None)
    assert result["primary"] is None
    assert "Insufficient data" in result["note"]
