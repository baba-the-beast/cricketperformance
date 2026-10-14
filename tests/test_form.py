"""Form series and trend language from fixture matches."""

from cricket_agent.analytics.form import batting_form_by_match, summarize_batting_form


def test_batting_form_has_one_row_per_match_with_innings(populated_db):
    series = batting_form_by_match(populated_db, "A Sharma")
    assert [row["match_id"] for row in series] == ["match_1001", "match_1002"]
    assert series[0]["runs"] + series[1]["runs"] == 29


def test_form_trend_needs_two_matches(populated_db):
    series = batting_form_by_match(populated_db, "A Sharma")
    summary = summarize_batting_form(series)
    assert summary["available"] is True
    assert summary["rolling_average_runs"] == 29 / 2
    assert summary["trend"]["direction"] in {"improving", "declining", "stable"}
    assert "selected sample" in summary["trend"]["statement"]


def test_form_insufficient_for_single_point():
    summary = summarize_batting_form([{"runs": 10, "strike_rate": 100, "match_id": "x"}])
    assert summary["trend"]["direction"] == "insufficient"
