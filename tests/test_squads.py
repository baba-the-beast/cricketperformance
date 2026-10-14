"""Squad lookup from fixture-feed team names (shared by the simulator and tournament)."""

from cricket_agent.squads import current_squad, feed_formats, feed_team, team_gender


def test_feed_team_names_map_onto_cricsheet():
    assert feed_team("Australia Women") == ("Australia", "female", None)
    assert feed_team("South Africa") == ("South Africa", None, None)
    assert "under-19" in feed_team("India U19")[2]
    assert "not been announced" in feed_team("Tbc")[2]
    assert feed_formats("t20") == ("T20", "IT20") and feed_formats("odi") == ("ODI",) and feed_formats("") == ()


def test_current_squad_is_gender_and_window_aware(populated_db):
    team = populated_db.execute("SELECT team_home FROM matches LIMIT 1").fetchone()[0]
    gender = team_gender(populated_db, team)
    squad = current_squad(populated_db, team, gender=gender)
    assert squad and len(squad) == len(set(squad))
    other = "female" if gender == "male" else "male"
    assert current_squad(populated_db, team, gender=other) == []
    assert current_squad(populated_db, team, gender=gender, year="1999") == []
