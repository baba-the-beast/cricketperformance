"""Smoke tests for the Flask API using the fixture database."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"
sys.path.insert(0, str(WEBAPP.parent / "src"))
sys.path.insert(0, str(WEBAPP.parent))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    flask = pytest.importorskip("flask")
    del flask
    db_path = tmp_path / "web.db"
    # Copy populated in-memory schema by re-ingesting fixtures into a file DB.
    from cricket_agent.database import connect, create_schema, insert_match
    from cricket_agent.ingestion.parser import parse_match_file

    conn = connect(db_path)
    create_schema(conn)
    fixtures = Path(__file__).parent / "fixtures"
    for name in ("match_1001.json", "match_1002.json"):
        insert_match(conn, parse_match_file(fixtures / name))
    conn.close()

    import cricket_agent.config as config
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    import webapp.app as web
    monkeypatch.setattr(web, "DATABASE_PATH", db_path)
    web.app.config["TESTING"] = True
    return web.app.test_client()


def test_overview_and_player_profile(client):
    overview = client.get("/api/overview").get_json()
    assert overview["total_matches"] == 2
    assert overview["sample_data"] is True
    stats = client.get("/api/players/A%20Sharma/stats").get_json()
    assert stats["batting"]["runs"] == 29
    profile = client.get("/api/players/A%20Sharma/profile").get_json()
    assert profile["confidence"]["level"] == "LOW"
    sim = client.post(
        "/api/simulate",
        json={"batting_lineup": ["A Sharma"], "bowling_lineup": ["F Patel"], "overs": 2},
    ).get_json()
    assert sim["available"] is True
    assert "Historical-statistics-based" in sim["label"]


def test_match_list_includes_replay_identifiers(client):
    matches = client.get("/api/matches").get_json()
    assert matches
    match = matches[0]
    assert match["match_id"] == "match_1001"
    assert match["match_date"]
    assert "season" in match
    assert "competition" in match


def test_team_roster_returns_only_players_from_requested_team(client):
    match = client.get("/api/matches").get_json()[0]
    response = client.get(f"/api/team-roster?team={match['team_home']}")
    body = response.get_json()
    assert response.status_code == 200
    assert body["available"] is True
    assert body["recommended_xi"]


def test_team_roster_understands_fixture_feed_names(client):
    match = client.get("/api/matches").get_json()[0]
    team = match["team_home"]
    # Feed format names ("t20") map onto Cricsheet's, and the squad says where it came from.
    body = client.get(f"/api/team-roster?team={team}&format=t20").get_json()
    assert body["available"] is True and body["squad"] and body["squad_note"]
    assert set(body["recommended_xi"]) <= set(body["squad"])
    # "<Team> Women" is the same team name with gender = female; the fixtures are men's matches.
    women = client.get(f"/api/team-roster?team={team} Women").get_json()
    assert women["available"] is False and women["recommended_xi"] == []
    # Sides Cricsheet never publishes get a reason instead of empty boxes.
    u19 = client.get("/api/team-roster?team=India U19").get_json()
    assert u19["available"] is False and "under-19" in u19["note"]
    assert "not been announced" in client.get("/api/team-roster?team=Tbc").get_json()["note"]


def test_simulate_match_returns_two_seeded_innings(client):
    response = client.post(
        "/api/simulate-match",
        json={
            "team_a": "Hillcrest Tigers",
            "team_b": "Riverside Hawks",
            "batting_lineup_a": ["A Sharma", "B Verma", "C Rao", "D Singh"],
            "batting_lineup_b": ["E Khan", "F Patel", "G Nair", "H Shah"],
            "bowling_lineup_a": ["A Sharma", "B Verma"],
            "bowling_lineup_b": ["F Patel", "G Nair"],
            "overs": 2,
            "seed": 19,
        },
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["available"] is True
    assert len(body["innings"]) == 2
    assert body["seed"] == 19


def test_player_explorer_and_unavailable_upcoming(client):
    players = client.get("/api/players?details=1&search=A%20Sharma").get_json()
    assert players[0]["player_name"] == "A Sharma"
    assert players[0]["matches_count"] == 2

    dataset = client.get("/api/dataset").get_json()
    assert dataset["dataset_name"] == "College Demo Dataset"
    assert dataset["match_count"] == 2
    assert dataset["team_count"] == 2

    upcoming = client.get("/api/upcoming").get_json()
    assert upcoming["available"] is False
    assert upcoming["message"] == "Upcoming match data unavailable"

    venues = client.get("/api/venues").get_json()
    venue = next(row for row in venues if row["venue"] == "College Ground No. 1")
    assert venue["match_count"] == 1
    assert venue["average_first_innings_score"] is not None


def test_recommend_lineup_endpoint(client):
    resp = client.post(
        "/api/recommend_lineup",
        json={
            "squad": ["A Sharma", "B Verma", "D Singh", "E Khan"],
            "opponent": "Hillcrest Tigers",
            "venue": "College Ground No. 1",
        },
    )
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["prediction_type"] == "evidence-ranked recommendation"
    assert len(body["recommended_xi"]) == 4
    assert body["venue_profile"]["available"] is True


def test_recommend_lineup_requires_squad(client):
    resp = client.post("/api/recommend_lineup", json={"squad": []})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "InvalidRangeError"


def test_venue_profile_endpoint(client):
    resp = client.get("/api/venue_profile?venue=College%20Ground%20No.%201")
    body = resp.get_json()
    assert body["available"] is True
    assert body["match_count"] == 1


def test_player_career_endpoint(client):
    players = client.get("/api/players").get_json()
    career = client.get(f"/api/players/{players[0]}/career").get_json()
    assert career["scope"] == "lifetime"
    assert career["span"]["matches"] >= 1
    assert client.get("/api/players/Nobody%20At%20All/career").status_code == 404
