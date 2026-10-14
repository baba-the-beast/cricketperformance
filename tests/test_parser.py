"""Tests for cricket_agent.ingestion.parser."""

from __future__ import annotations

import pytest

from cricket_agent.exceptions import DataValidationError
from cricket_agent.ingestion.parser import parse_match_dict, parse_match_file


def test_parses_valid_match_file(fixtures_dir):
    match = parse_match_file(fixtures_dir / "match_1001.json")

    assert match.match_id == "match_1001"
    assert match.info.teams == ("Riverside Kings", "Hillcrest Tigers")
    assert match.info.match_type == "T20"
    assert match.info.venue == "College Ground No. 1"
    assert len(match.innings) == 2

    first_innings = match.innings[0]
    assert first_innings.batting_team == "Riverside Kings"
    # Over 0 has 7 delivery entries in the fixture (one extra ball for a wide).
    assert len(first_innings.deliveries) == 7 + 6


def test_wide_delivery_parsed_with_extras(fixtures_dir):
    match = parse_match_file(fixtures_dir / "match_1001.json")
    wide_delivery = match.innings[0].deliveries[3]

    assert wide_delivery.is_wide is True
    assert wide_delivery.runs_wides == 1
    assert wide_delivery.is_faced_by_batter is False
    assert wide_delivery.is_legal_delivery is False


def test_wicket_parsed_with_fielder(fixtures_dir):
    match = parse_match_file(fixtures_dir / "match_1001.json")
    caught_delivery = match.innings[0].deliveries[-1]

    assert len(caught_delivery.wickets) == 1
    wicket = caught_delivery.wickets[0]
    assert wicket.kind == "caught"
    assert wicket.player_out == "C Rao"
    assert wicket.fielders == ("H Joshi",)


def test_missing_optional_venue_does_not_crash(fixtures_dir):
    match = parse_match_file(fixtures_dir / "match_1003_no_venue.json")
    assert match.info.venue is None
    assert match.info.city is None
    # Missing player_of_match / officials must not raise either.
    assert match.info.player_of_match == ()


def test_missing_required_info_section_raises(fixtures_dir):
    with pytest.raises(DataValidationError, match="'info'"):
        parse_match_file(fixtures_dir / "match_invalid_missing_info.json")


def test_missing_required_field_in_info_raises():
    raw = {
        "info": {
            "balls_per_over": 6,
            "dates": ["2025-01-01"],
            "team_type": "club",
            "toss": {"decision": "bat", "winner": "A"},
            "gender": "male",
            "dates": ["2025-01-01"],
            "team_type": "club",
            "toss": {"decision": "bat", "winner": "A"},
            "match_type": "T20",
            "balls_per_over": 6,
            "team_type": "club",
            "teams": ["A", "B"],
            "toss": {"decision": "bat", "winner": "A"},
            # "players" key deliberately omitted
        },
        "innings": [],
    }
    with pytest.raises(DataValidationError, match="players"):
        parse_match_dict(raw, match_id="broken")


def test_wrong_number_of_teams_raises():
    raw = {
        "info": {
            "balls_per_over": 6,
            "dates": ["2025-01-01"],
            "team_type": "club",
            "toss": {"decision": "bat", "winner": "A"},
            "gender": "male",
            "dates": ["2025-01-01"],
            "team_type": "club",
            "toss": {"decision": "bat", "winner": "A"},
            "match_type": "T20",
            "balls_per_over": 6,
            "team_type": "club",
            "teams": ["A", "B", "C"],
            "toss": {"decision": "bat", "winner": "A"},
            "players": {"A": [], "B": [], "C": []},
        },
        "innings": [],
    }
    with pytest.raises(DataValidationError, match="exactly two"):
        parse_match_dict(raw, match_id="broken")


def test_forfeited_innings_with_no_overs_key_is_handled():
    raw = {
        "info": {
            "balls_per_over": 6,
            "dates": ["2025-01-01"],
            "team_type": "club",
            "toss": {"decision": "bat", "winner": "A"},
            "gender": "male",
            "dates": ["2025-01-01"],
            "team_type": "club",
            "toss": {"decision": "bat", "winner": "A"},
            "match_type": "T20",
            "balls_per_over": 6,
            "team_type": "club",
            "teams": ["A", "B"],
            "toss": {"decision": "bat", "winner": "A"},
            "players": {"A": ["p1"], "B": ["p2"]},
        },
        "innings": [
            {"team": "A", "forfeited": True},
        ],
    }
    match = parse_match_dict(raw, match_id="forfeit_case")
    assert len(match.innings) == 1
    assert match.innings[0].deliveries == ()


def test_invalid_json_syntax_raises(tmp_path):
    bad_file = tmp_path / "broken.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(DataValidationError, match="not valid JSON"):
        parse_match_file(bad_file)


def test_actual_delivery_fallback_for_older_schema(caplog):
    raw = {
        "info": {
            "balls_per_over": 6,
            "dates": ["2025-01-01"],
            "team_type": "club",
            "toss": {"decision": "bat", "winner": "A"},
            "gender": "male",
            "dates": ["2025-01-01"],
            "team_type": "club",
            "toss": {"decision": "bat", "winner": "A"},
            "match_type": "T20",
            "balls_per_over": 6,
            "team_type": "club",
            "teams": ["A", "B"],
            "toss": {"decision": "bat", "winner": "A"},
            "players": {"A": ["p1"], "B": ["p2"]},
        },
        "innings": [
            {
                "team": "A",
                "overs": [
                    {
                        "over": 0,
                        "deliveries": [
                            {
                                # No 'actual_delivery' key -- simulates an
                                # older Cricsheet schema version.
                                "batter": "p1",
                                "bowler": "p2",
                                "non_striker": "p1b",
                                "runs": {"batter": 1, "extras": 0, "total": 1},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    match = parse_match_dict(raw, match_id="old_schema")
    assert match.innings[0].deliveries[0].actual_delivery == "0.1"


def test_optional_cricsheet_registry_ids_are_preserved():
    raw = {
        "info": {
            "balls_per_over": 6,
            "dates": ["2025-01-01"],
            "team_type": "club",
            "toss": {"decision": "bat", "winner": "A"},
            "gender": "male",
            "dates": ["2025-01-01"],
            "team_type": "club",
            "toss": {"decision": "bat", "winner": "A"},
            "match_type": "T20",
            "balls_per_over": 6,
            "team_type": "club",
            "teams": ["A", "B"],
            "toss": {"decision": "bat", "winner": "A"},
            "players": {"A": ["Player One"], "B": ["Player Two"]},
            "registry": {"people": {"Player One": "registry-1"}},
        },
        "innings": [],
    }
    match = parse_match_dict(raw, match_id="registry_case")
    assert match.info.player_registry == {"Player One": "registry-1"}

from hypothesis import HealthCheck, given, settings, strategies as st
from cricket_agent.models import Match

# This test checks correctness, not speed: on a busy or low-memory machine the
# default "input generation is slow" check would fail it for no real reason.
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(
    batter=st.text(min_size=1),
    bowler=st.text(min_size=1),
    non_striker=st.text(min_size=1),
    runs_batter=st.integers(min_value=0, max_value=6),
    wides=st.integers(min_value=0, max_value=5),
    noballs=st.integers(min_value=0, max_value=2),
    byes=st.integers(min_value=0, max_value=4),
    legbyes=st.integers(min_value=0, max_value=4),
    penalty=st.integers(min_value=0, max_value=5),
)
def test_delivery_fuzzing(batter, bowler, non_striker, runs_batter, wides, noballs, byes, legbyes, penalty):
    extras_total = wides + noballs + byes + legbyes + penalty
    total = runs_batter + extras_total

    extras_dict = {}
    if wides > 0: extras_dict["wides"] = wides
    if noballs > 0: extras_dict["noballs"] = noballs
    if byes > 0: extras_dict["byes"] = byes
    if legbyes > 0: extras_dict["legbyes"] = legbyes
    if penalty > 0: extras_dict["penalty"] = penalty

    delivery_raw = {
        "batter": batter,
        "bowler": bowler,
        "non_striker": non_striker,
        "runs": {
            "batter": runs_batter,
            "extras": extras_total,
            "total": total
        },
        "actual_delivery": "0.1"
    }
    if extras_dict:
        delivery_raw["extras"] = extras_dict

    raw = {
        "info": {
            "match_type": "T20",
            "balls_per_over": 6,
            "gender": "male",
            "dates": ["2025-01-01"],
            "team_type": "club",
            "toss": {"decision": "bat", "winner": "A"},
            "teams": ["A", "B"],
            "players": {"A": [batter, non_striker], "B": [bowler]}
        },
        "innings": [
            {
                "team": "A",
                "overs": [
                    {
                        "over": 0,
                        "deliveries": [delivery_raw]
                    }
                ]
            }
        ]
    }
    match = parse_match_dict(raw, match_id="fuzz")
    assert len(match.innings) == 1
    d = match.innings[0].deliveries[0]
    assert d.runs_batter == runs_batter
    assert d.runs_wides == wides
    assert d.runs_noballs == noballs
    assert d.runs_byes == byes
    assert d.runs_legbyes == legbyes
    assert d.runs_penalty == penalty
