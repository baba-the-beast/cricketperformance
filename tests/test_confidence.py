from cricket_agent.analytics.confidence import (
    HIGH,
    LOW,
    MEDIUM,
    batting_confidence,
    matchup_confidence,
)


def test_low_confidence_for_fixture_sized_sample():
    result = batting_confidence(matches=2, innings=2, balls_faced=12)
    assert result["level"] == LOW
    assert "2 match" in result["reason"]


def test_medium_confidence_from_match_count():
    result = batting_confidence(matches=4, innings=4, balls_faced=40)
    assert result["level"] == MEDIUM


def test_high_confidence_requires_volume():
    result = batting_confidence(matches=10, innings=10, balls_faced=80)
    assert result["level"] == HIGH


def test_matchup_small_sample_is_low():
    result = matchup_confidence(balls=3, dismissals=0)
    assert result["level"] == LOW
    assert "Only 3" in result["reason"]
