"""Tests for the optional upcoming-match provider boundary."""

from datetime import date, timedelta

from cricket_agent.external_matches import (
    CricketDataMatchProvider,
    UnavailableMatchProvider,
    get_upcoming_provider,
)


def test_default_provider_is_honestly_unavailable():
    provider = get_upcoming_provider()
    assert isinstance(provider, UnavailableMatchProvider)
    assert provider.upcoming() == []


def test_cricketdata_provider_normalizes_future_and_live_matches(monkeypatch):
    provider = CricketDataMatchProvider("test-key")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    calls = []

    def fake_get_rows(endpoint):
        calls.append(endpoint)
        if endpoint == "matches":
            return [{
                "id": "future-1",
                "name": "India vs West Indies",
                "teams": ["India", "West Indies"],
                "date": tomorrow,
                "dateTimeGMT": f"{tomorrow}T08:30:00Z",
                "matchType": "odi",
                "venue": "Mumbai",
                "seriesName": "Tour of India",
            }]
        return [{
            "id": "live-1",
            "teams": ["Australia", "England"],
            "date": date.today().isoformat(),
            "status": "Australia 120/2",
        }]

    monkeypatch.setattr(provider, "_get_rows", fake_get_rows)
    matches = provider.upcoming()

    assert [match.match_id for match in matches] == ["live-1", "future-1"]
    future = next(match for match in matches if match.match_id == "future-1")
    assert future.time == "02:00 PM IST"
    assert future.competition == "Tour of India"
    assert next(match for match in matches if match.match_id == "live-1").status == "Australia 120/2"
    provider.upcoming()
    assert calls == ["matches", "currentMatches"]


def test_cricketdata_provider_uses_api_key_from_environment(monkeypatch):
    monkeypatch.setenv("CRICKETDATA_API_KEY", "configured-key")
    provider = get_upcoming_provider()
    assert isinstance(provider, CricketDataMatchProvider)
    assert provider.api_key == "configured-key"