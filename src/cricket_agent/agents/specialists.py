"""Deterministic specialist analysts. They interpret stored metrics; they do not invent them."""

from __future__ import annotations

import sqlite3
from typing import Any

from cricket_agent.analytics.player_profile import build_player_profile
from cricket_agent.observability import record_call


def _obs(text: str, evidence: Any) -> dict[str, Any]:
    return {"text": text, "evidence": evidence}


def batting_analyst(conn: sqlite3.Connection, player: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    record_call("batting_analyst", channel="in-process", arguments={"player": player})
    profile = profile or build_player_profile(conn, player)
    batting = profile.get("batting")
    form = profile.get("batting_form") or {}
    observations: list[dict[str, Any]] = []
    evidence: list[str] = []

    if not batting:
        return {
            "agent": "Batting Analyst",
            "player": player,
            "metrics": {},
            "observations": [
                _obs(
                    "Insufficient data: this player has no batting deliveries in the selected sample.",
                    None,
                )
            ],
            "recommendation": "Do not infer a batting role from this sample.",
            "confidence": "LOW",
            "evidence": ["No batting rows in compute_stats for this player/range."],
        }

    sr = batting.get("strike_rate")
    avg = batting.get("average")
    observations.append(
        _obs(
            f"{player} scored {batting['runs']} runs from {batting['balls_faced']} balls "
            f"in {batting['innings']} innings"
            + (f" at a strike rate of {sr:.1f}." if sr is not None else "."),
            {"runs": batting["runs"], "balls_faced": batting["balls_faced"], "strike_rate": sr},
        )
    )
    evidence.append("compute_stats batting block")
    if avg is None:
        observations.append(
            _obs(
                "Batting average is undefined because the player was not dismissed in this sample.",
                {"average": None, "dismissals": batting["dismissals"]},
            )
        )
    if form.get("available"):
        observations.append(_obs(form["trend"]["statement"], form["trend"]))
        evidence.append("per-match batting series")
    conf = (profile.get("batting_confidence") or {}).get("level", "LOW")
    rec = (
        "Batting numbers are usable as supporting evidence, with the stated sample-size limits."
        if conf != "LOW"
        else "Treat batting figures as low-confidence; the sample is small."
    )
    return {
        "agent": "Batting Analyst",
        "player": player,
        "metrics": batting,
        "observations": observations,
        "recommendation": rec,
        "confidence": conf,
        "evidence": evidence,
        "form": form,
        "role": (profile.get("role") or {}).get("primary"),
    }


def bowling_analyst(conn: sqlite3.Connection, player: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    record_call("bowling_analyst", channel="in-process", arguments={"player": player})
    profile = profile or build_player_profile(conn, player)
    bowling = profile.get("bowling")
    form = profile.get("bowling_form") or {}
    if not bowling:
        return {
            "agent": "Bowling Analyst",
            "player": player,
            "metrics": {},
            "observations": [
                _obs(
                    "Insufficient data: this player has no bowling deliveries in the selected sample.",
                    None,
                )
            ],
            "recommendation": "Do not infer a bowling role from this sample.",
            "confidence": "LOW",
            "evidence": ["No bowling rows in compute_stats for this player/range."],
        }
    observations = [
        _obs(
            f"{player} bowled {bowling['overs_display']} overs, conceded "
            f"{bowling['runs_conceded']} runs, and took {bowling['wickets']} wicket(s).",
            {
                "wickets": bowling["wickets"],
                "economy": bowling["economy"],
                "legal_deliveries": bowling["legal_deliveries"],
            },
        )
    ]
    if form.get("available"):
        observations.append(_obs(form["trend"]["statement"], form["trend"]))
    conf = (profile.get("bowling_confidence") or {}).get("level", "LOW")
    return {
        "agent": "Bowling Analyst",
        "player": player,
        "metrics": bowling,
        "observations": observations,
        "recommendation": (
            "Bowling figures can support selection discussion within the stated confidence."
            if conf != "LOW"
            else "Bowling sample is small; economy and strike rate may not generalise."
        ),
        "confidence": conf,
        "evidence": ["compute_stats bowling block", "per-match bowling series"],
        "form": form,
    }


def fielding_analyst(conn: sqlite3.Connection, player: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    record_call("fielding_analyst", channel="in-process", arguments={"player": player})
    profile = profile or build_player_profile(conn, player)
    fielding = profile.get("fielding") or {}
    if not fielding.get("available"):
        return {
            "agent": "Fielding Analyst",
            "player": player,
            "metrics": fielding,
            "observations": [
                _obs(
                    fielding.get("note")
                    or "Not available in the current dataset.",
                    fielding,
                )
            ],
            "recommendation": (
                "Do not rank this player on fielding. Cricsheet only names "
                "fielders on recorded dismissals."
            ),
            "confidence": "LOW",
            "evidence": ["wickets.fielders column"],
        }
    observations = [
        _obs(
            f"{player} is credited on {fielding['total_credits']} dismissal(s) "
            f"({fielding['catches']} catch(es), {fielding['run_outs']} run-out(s), "
            f"{fielding['stumpings']} stumping(s)).",
            fielding,
        )
    ]
    return {
        "agent": "Fielding Analyst",
        "player": player,
        "metrics": fielding,
        "observations": observations,
        "recommendation": (
            "Fielding credits are dismissal attributions only, not a complete fielding record."
        ),
        "confidence": "LOW" if fielding["total_credits"] < 3 else "MEDIUM",
        "evidence": ["wickets.fielders"],
    }
