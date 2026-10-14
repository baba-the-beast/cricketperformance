"""
Cricsheet JSON parser.

Term: Parsing
    Converting Cricsheet's raw JSON structure (a nested dictionary, as
    Python's `json` module reads it) into this project's own typed
    objects (Match, Innings, Delivery -- see models.py).

Why we use it:
    Every other part of the system (the database, the statistics engine,
    the tools) should never have to know the exact shape of Cricsheet's
    JSON, or care about Cricsheet's quirks (for example: a wide that is
    then followed by a normal delivery both use the SAME `actual_delivery`
    number). Centralizing that knowledge in one module means those quirks
    are handled once, correctly, and documented once.

In our project:
    `parse_match_file()` reads one Cricsheet JSON file and returns a single
    Match object, or raises DataValidationError with a clear message if
    the file does not contain the minimum data this project requires.

Fields verified against the official Cricsheet JSON format documentation
(https://cricsheet.org/format/json/, schema version 1.2.0) during Phase 1
research. See docs/data_model.md "Research Findings" for the field-by-
field verification notes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from cricket_agent.exceptions import DataValidationError
from cricket_agent.models import Delivery, Innings, Match, MatchInfo, Wicket

logger = logging.getLogger(__name__)

# Required top-level sections per the Cricsheet JSON 1.2.0 format spec.
_REQUIRED_INFO_FIELDS = (
    "balls_per_over",
    "dates",
    "gender",
    "match_type",
    "team_type",
    "teams",
    "toss",
    "players",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DataValidationError(message)


def parse_match_file(path: Path) -> Match:
    """Parse one Cricsheet JSON file into a normalized Match object.

    The match_id is taken from the file name (Cricsheet's own downloads
    name each file `<match_id>.json`, matching the id used in their
    website URLs), which keeps match_id stable and human-checkable.

    Raises:
        DataValidationError: If the file is not valid JSON, or is missing
            information this project requires (see _REQUIRED_INFO_FIELDS
            and the innings/deliveries checks below).
    """
    match_id = path.stem
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DataValidationError(f"Could not read {path}: {exc}") from exc

    try:
        raw: dict[str, Any] = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise DataValidationError(f"{path.name} is not valid JSON: {exc}") from exc

    return parse_match_dict(raw, match_id=match_id)


def parse_match_dict(raw: dict[str, Any], *, match_id: str) -> Match:
    """Parse an already-loaded Cricsheet JSON dict into a Match object.

    Split out from parse_match_file() so that tests, and any future code
    that already has the dict in memory (e.g. from an API response),
    do not need to write it to disk first.
    """
    _require("info" in raw, "Missing required top-level 'info' section.")
    info_raw = raw["info"]

    for required_field in _REQUIRED_INFO_FIELDS:
        _require(
            required_field in info_raw,
            f"'info' section is missing required field '{required_field}'.",
        )

    _require(
        isinstance(info_raw["teams"], list) and len(info_raw["teams"]) == 2,
        "'info.teams' must be a list of exactly two team names.",
    )

    match_info = _parse_info(info_raw, match_id=match_id)

    innings_raw = raw.get("innings", [])
    _require(
        isinstance(innings_raw, list),
        "'innings' must be a list if present.",
    )

    innings = tuple(
        _parse_innings(inn, match_id=match_id, innings_index=idx)
        for idx, inn in enumerate(innings_raw)
    )

    return Match(info=match_info, innings=innings)


def _parse_info(info_raw: dict[str, Any], *, match_id: str) -> MatchInfo:
    outcome_raw = info_raw.get("outcome", {})
    winner = outcome_raw.get("winner")
    outcome_summary = _summarize_outcome(outcome_raw)

    event_raw = info_raw.get("event")
    event_name = event_raw.get("name") if isinstance(event_raw, dict) else None

    player_of_match = info_raw.get("player_of_match") or []
    _require(
        isinstance(player_of_match, list),
        "'info.player_of_match' must be a list if present.",
    )

    raw_players = info_raw.get("players") or {}
    players_by_team: dict[str, tuple[str, ...]] = {}
    if isinstance(raw_players, dict):
        for team, names in raw_players.items():
            if isinstance(names, list):
                players_by_team[str(team)] = tuple(str(n) for n in names)

    registry_raw = info_raw.get("registry", {})
    registry_people = registry_raw.get("people", {}) if isinstance(registry_raw, dict) else {}
    player_registry = (
        {str(name): str(registry_id) for name, registry_id in registry_people.items()}
        if isinstance(registry_people, dict)
        else {}
    )

    return MatchInfo(
        match_id=match_id,
        teams=tuple(info_raw["teams"]),
        dates=tuple(info_raw.get("dates", [])),
        match_type=info_raw["match_type"],
        gender=info_raw["gender"],
        venue=info_raw.get("venue"),
        city=info_raw.get("city"),
        season=info_raw.get("season"),
        event_name=event_name,
        winner=winner,
        outcome_summary=outcome_summary,
        player_of_match=tuple(player_of_match),
        players_by_team=players_by_team,
        player_registry=player_registry,
    )


def _summarize_outcome(outcome_raw: dict[str, Any]) -> str | None:
    """Turn Cricsheet's `outcome` object into one short human-readable line.

    We do not invent anything here -- every value used already exists in
    the `outcome` object as documented at
    https://cricsheet.org/format/json/#outcome.
    """
    if not outcome_raw:
        return None
    if "result" in outcome_raw:
        # "draw", "no result", or "tie"
        result = str(outcome_raw["result"])
        eliminator = outcome_raw.get("eliminator")
        bowl_out = outcome_raw.get("bowl_out")
        if eliminator:
            return f"Match tied; {eliminator} won the super over."
        if bowl_out:
            return f"Match tied; {bowl_out} won the bowl-out."
        return result.capitalize()
    winner = outcome_raw.get("winner")
    by = outcome_raw.get("by", {})
    if winner and by:
        if "runs" in by and by.get("innings"):
            return f"{winner} won by an innings and {by['runs']} runs."
        if "runs" in by:
            return f"{winner} won by {by['runs']} runs."
        if "wickets" in by:
            return f"{winner} won by {by['wickets']} wickets."
    if winner:
        return f"{winner} won."
    return None


def _parse_innings(
    inn_raw: dict[str, Any], *, match_id: str, innings_index: int
) -> Innings:
    _require(
        "team" in inn_raw,
        f"Innings {innings_index} is missing required field 'team'.",
    )
    batting_team = inn_raw["team"]

    overs_raw = inn_raw.get("overs", [])
    _require(
        isinstance(overs_raw, list),
        f"Innings {innings_index} 'overs' must be a list if present.",
    )
    # A forfeited innings legitimately has no 'overs' key at all. We do not
    # invent deliveries for it -- an empty tuple correctly represents "no
    # play took place", per docs/data_model.md.

    deliveries: list[Delivery] = []
    for over_raw in overs_raw:
        _require(
            "over" in over_raw and "deliveries" in over_raw,
            f"Innings {innings_index} has an over missing 'over' or "
            "'deliveries'.",
        )
        over_number = int(over_raw["over"])
        for ball_in_over, delivery_raw in enumerate(over_raw["deliveries"], start=1):
            deliveries.append(
                _parse_delivery(
                    delivery_raw,
                    match_id=match_id,
                    innings_index=innings_index,
                    over_number=over_number,
                    ball_in_over=ball_in_over,
                )
            )

    return Innings(
        match_id=match_id,
        innings_index=innings_index,
        batting_team=batting_team,
        deliveries=tuple(deliveries),
    )


def _parse_delivery(
    delivery_raw: dict[str, Any],
    *,
    match_id: str,
    innings_index: int,
    over_number: int,
    ball_in_over: int,
) -> Delivery:
    for required_field in ("batter", "bowler", "non_striker", "runs"):
        _require(
            required_field in delivery_raw,
            f"Delivery over={over_number} in innings {innings_index} is "
            f"missing required field '{required_field}'.",
        )

    runs_raw = delivery_raw["runs"]
    for required_runs_field in ("batter", "extras", "total"):
        _require(
            required_runs_field in runs_raw,
            f"Delivery over={over_number} in innings {innings_index} has "
            f"'runs' missing required field '{required_runs_field}'.",
        )
    _require(
        all(isinstance(runs_raw[f], int) for f in ("batter", "extras", "total")),
        f"Delivery over={over_number} in innings {innings_index} has "
        "non-numeric run values.",
    )

    extras_raw = delivery_raw.get("extras", {})
    _require(
        isinstance(extras_raw, dict),
        f"Delivery over={over_number} in innings {innings_index} has a "
        "non-object 'extras' field.",
    )

    declared_extras_total = runs_raw["extras"]
    actual_extras_total = sum(
        int(extras_raw.get(k, 0))
        for k in ("wides", "noballs", "byes", "legbyes", "penalty")
    )
    if declared_extras_total != actual_extras_total:
        logger.warning(
            "Delivery over=%s in innings %s: runs.extras (%s) does not "
            "match the sum of the 'extras' breakdown (%s). Using the "
            "breakdown sum for bowler-charged-runs calculations.",
            over_number,
            innings_index,
            declared_extras_total,
            actual_extras_total,
        )

    wickets_raw = delivery_raw.get("wickets", [])
    _require(
        isinstance(wickets_raw, list),
        f"Delivery over={over_number} in innings {innings_index} has a "
        "non-list 'wickets' field.",
    )
    wickets = tuple(
        Wicket(
            kind=w["kind"],
            player_out=w["player_out"],
            fielders=tuple(
                f.get("name", "unknown") for f in w.get("fielders", [])
            ),
        )
        for w in wickets_raw
    )

    # actual_delivery is required by the current (1.2.0) format, but Phase 1
    # must still handle older-format files gracefully rather than crashing
    # -- so we fall back to "<over>.<ball_in_over>" if it is absent, and
    # log that we did so, rather than silently pretending we had it.
    actual_delivery = delivery_raw.get("actual_delivery")
    if actual_delivery is None:
        actual_delivery = f"{over_number}.{ball_in_over}"
        logger.warning(
            "Delivery in over %s (innings %s) is missing 'actual_delivery'; "
            "derived '%s' instead. This match may use an older Cricsheet "
            "schema version.",
            over_number,
            innings_index,
            actual_delivery,
        )

    return Delivery(
        match_id=match_id,
        innings_index=innings_index,
        over_number=over_number,
        ball_in_over=ball_in_over,
        actual_delivery=str(actual_delivery),
        batter=delivery_raw["batter"],
        bowler=delivery_raw["bowler"],
        non_striker=delivery_raw["non_striker"],
        runs_batter=runs_raw["batter"],
        runs_total=runs_raw["total"],
        runs_wides=int(extras_raw.get("wides", 0)),
        runs_noballs=int(extras_raw.get("noballs", 0)),
        runs_byes=int(extras_raw.get("byes", 0)),
        runs_legbyes=int(extras_raw.get("legbyes", 0)),
        runs_penalty=int(extras_raw.get("penalty", 0)),
        wickets=wickets,
    )
