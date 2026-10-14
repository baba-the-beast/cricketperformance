"""Team Selector: an evidence-ranked XI recommendation.

This is the piece that combines recent form, ground history, and
opponent-specific matchups into one "who should play" answer. It is
deliberately built the same way the rest of this project is built:

    - Every number traces back to a stored delivery (compute_stats,
      venue.py, matchup.py) -- nothing here is a machine-learned
      probability or a black-box rating.
    - Ranking uses the same HIGH/MEDIUM/LOW confidence tiers the
      coordinator already uses, then breaks ties on plain, visible
      counts (does venue-specific evidence exist? does opponent-specific
      evidence exist? how many runs/wickets are on record?) -- never a
      single combined "score".
    - Anything the project genuinely cannot know honestly (pace vs spin,
      a live weather forecast, fielding positions) is named as a gap,
      not silently guessed.

Term: "Prediction" in this module
    Coaches asked for a prediction of the best XI for a specific match.
    What this function actually returns is an *evidence-ranked
    recommendation*: given the historical record, here is the strongest
    defensible XI and why. It is not a simulated match outcome or a win
    probability -- see simulation.py for the (separately labelled)
    historical-statistics-based simulation.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from cricket_agent.analytics.conditions import resolve_conditions
from cricket_agent.analytics.matchup import player_vs_team
from cricket_agent.analytics.player_profile import build_player_profile
from cricket_agent.analytics.team_balance import summarize_balance
from cricket_agent.analytics.venue import player_form_at_venue, venue_profile
from cricket_agent.exceptions import InvalidRangeError
from cricket_agent.observability import record_call

_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

DEFAULT_XI_SIZE = 11


def _fmt(value: float | None, digits: int = 1) -> str | None:
    return f"{value:.{digits}f}" if value is not None else None


def _build_player_case(
    conn: sqlite3.Connection,
    player: str,
    *,
    opponent: str | None,
    venue: str | None,
    last_n_matches: int | None,
) -> dict[str, Any]:
    profile = build_player_profile(conn, player, last_n_matches=last_n_matches)
    role_report = profile.get("role") or {}
    primary_role = (role_report.get("primary") or {}).get("role")

    evidence: list[str] = []
    batting = profile.get("batting")
    bowling = profile.get("bowling")

    if batting:
        sr = _fmt(batting.get("strike_rate"))
        evidence.append(
            f"Overall: {batting['runs']} runs from {batting['balls_faced']} balls"
            + (f" (SR {sr})" if sr else "")
            + f", confidence {profile['batting_confidence']['level']}."
        )
    if bowling:
        econ = _fmt(bowling.get("economy"), 2)
        evidence.append(
            f"Overall: {bowling['wickets']} wicket(s) from {bowling['legal_deliveries']} "
            f"legal deliveries" + (f" (economy {econ})" if econ else "")
            + f", confidence {profile['bowling_confidence']['level']}."
        )
    if not batting and not bowling:
        evidence.append("No batting or bowling deliveries on record for this player.")

    bat_form = profile.get("batting_form") or {}
    if bat_form.get("available"):
        evidence.append("Recent batting form: " + bat_form["trend"]["statement"])
    bowl_form = profile.get("bowling_form") or {}
    if bowl_form.get("available"):
        evidence.append("Recent bowling form: " + bowl_form["trend"]["statement"])

    venue_form = None
    if venue:
        venue_form = player_form_at_venue(conn, player, venue)
        if venue_form.get("available"):
            bits = []
            if venue_form.get("batting"):
                bits.append(f"{venue_form['batting']['runs']} runs")
            if venue_form.get("bowling"):
                bits.append(f"{venue_form['bowling']['wickets']} wickets")
            evidence.append(
                f"At {venue}: " + ", ".join(bits) + f" across {venue_form['matches_considered']} "
                f"match(es) ({venue_form['confidence']} confidence)."
            )
        else:
            evidence.append(venue_form.get("note") or f"No recorded appearances at {venue}.")

    matchup = None
    if opponent:
        matchup = player_vs_team(conn, player, opponent)
        if matchup.get("available"):
            bits = []
            if matchup.get("batting"):
                bits.append(f"{matchup['batting']['runs']} runs")
            if matchup.get("bowling"):
                bits.append(f"{matchup['bowling']['wickets']} wickets")
            evidence.append(f"Vs {opponent}: " + ", ".join(bits) + ".")
        else:
            evidence.append(matchup.get("note") or f"No recorded matchup data vs {opponent}.")

    return {
        "player": player,
        "team": profile.get("team"),
        "role": primary_role,
        "role_report": role_report,
        "confidence": profile["confidence"]["level"],
        "batting": batting,
        "bowling": bowling,
        "venue_form": venue_form,
        "matchup_vs_opponent": matchup,
        "evidence": evidence,
        "sort_key": (
            _RANK.get(profile["confidence"]["level"], 0),
            1 if (venue_form or {}).get("available") else 0,
            1 if (matchup or {}).get("available") else 0,
            batting["runs"] if batting else 0,
            bowling["wickets"] if bowling else 0,
        ),
    }


def recommend_lineup(
    conn: sqlite3.Connection,
    *,
    squad: list[str],
    opponent: str | None = None,
    venue: str | None = None,
    last_n_matches: int | None = None,
    pitch_note: str | None = None,
    weather_note: str | None = None,
    xi_size: int = DEFAULT_XI_SIZE,
) -> dict[str, Any]:
    """Recommend a best-available XI from `squad`, given match context.

    Raises InvalidRangeError if `squad` is empty -- an empty candidate
    pool is a caller error, not a "no recommendation" result.
    """
    record_call(
        "team_selector",
        channel="in-process",
        arguments={"squad": squad, "opponent": opponent, "venue": venue},
    )
    if not squad:
        raise InvalidRangeError("squad must contain at least one player name.")

    cases = [
        _build_player_case(
            conn, player, opponent=opponent, venue=venue, last_n_matches=last_n_matches
        )
        for player in squad
    ]
    ranked = sorted(cases, key=lambda c: c["sort_key"], reverse=True)
    selected = ranked[:xi_size]
    bench = ranked[xi_size:]

    def batting_order_key(player_case):
        # Extract the average first over from the primary role metrics
        role_report = player_case.get("role_report") or {}
        primary = role_report.get("primary") or {}
        metrics = primary.get("supporting_metrics") or {}
        
        # If they have a batting position, use it
        avg_over = metrics.get("average_first_over_0based")
        
        # If they are a pure bowler who hasn't batted, push them to the bottom
        if avg_over is None:
            # Bowlers usually bat 8-11. We can sort them by wickets (descending) at the bottom.
            wickets = player_case.get("bowling", {}).get("wickets") if player_case.get("bowling") else 0
            return (100, -wickets)
            
        return (avg_over, 0)

    selected = sorted(selected, key=batting_order_key)

    flags: list[str] = []

    if len(squad) < xi_size:
        flags.append(
            f"Squad has only {len(squad)} named player(s); a full XI of {xi_size} "
            "cannot be filled. Every named player is recommended."
        )
    if all(c["confidence"] == "LOW" for c in ranked):
        flags.append(
            "Every player in this squad has LOW overall confidence. Treat the "
            "ordering below as the best available evidence, not a reliable ranking."
        )

    v_profile = venue_profile(conn, venue) if venue else None
    if v_profile and not v_profile.get("available"):
        flags.append(v_profile["note"])

    conditions = resolve_conditions(venue=venue, pitch_note=pitch_note, weather_note=weather_note)
    if not conditions.get("available"):
        flags.append(conditions["note"])

    balance = summarize_balance([c["role_report"] for c in selected])

    for c in selected:
        c.pop("sort_key", None)
    for c in bench:
        c.pop("sort_key", None)

    return {
        "squad": squad,
        "opponent": opponent,
        "venue": venue,
        "venue_profile": v_profile,
        "conditions": conditions,
        "recommended_xi": selected,
        "bench": bench,
        "team_balance": balance,
        "flags": flags,
        "ranking_method": (
            "Players are grouped by HIGH/MEDIUM/LOW confidence (from sample size, "
            "as elsewhere in this project). Within a tier, ties are broken first by "
            "whether venue-specific evidence exists, then opponent-specific evidence, "
            "then by plain recorded runs and wickets -- never a single combined score."
        ),
        "prediction_type": "evidence-ranked recommendation",
        "disclaimer": (
            "This ranks the named squad by the strength and relevance of their "
            "recorded evidence (recent form, ground history, opponent matchups). "
            "It is not a machine-learned win probability, and it does not know "
            "today's pace/spin conditions -- Cricsheet does not record bowling style."
        ),
    }
