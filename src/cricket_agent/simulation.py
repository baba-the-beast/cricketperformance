"""Historical-statistics-based limited-overs scenario. Not a prediction model."""

from __future__ import annotations

import math
import random
import sqlite3
from typing import Any

from cricket_agent.dismissals import NON_DISMISSAL_KINDS
from cricket_agent.exceptions import PlayerNotFoundError
from cricket_agent.observability import record_call
from cricket_agent.tools.cricket_tools import compute_stats

LABEL = "Historical-statistics-based simulation"

DEFAULT_SR = 120.0
DEFAULT_ECONOMY = 8.0
DEFAULT_WICKET_PER_OVER = 0.25

_BASELINE_OUTCOMES = [
    {"runs_batter": 0, "runs_total": 0, "extras": {}, "wicket_kind": None},
    {"runs_batter": 1, "runs_total": 1, "extras": {}, "wicket_kind": None},
    {"runs_batter": 2, "runs_total": 2, "extras": {}, "wicket_kind": None},
    {"runs_batter": 3, "runs_total": 3, "extras": {}, "wicket_kind": None},
    {"runs_batter": 4, "runs_total": 4, "extras": {}, "wicket_kind": None},
    {"runs_batter": 6, "runs_total": 6, "extras": {}, "wicket_kind": None},
    {"runs_batter": 0, "runs_total": 1, "extras": {"wides": 1}, "wicket_kind": None},
    {"runs_batter": 0, "runs_total": 1, "extras": {"noballs": 1}, "wicket_kind": None},
    {"runs_batter": 0, "runs_total": 0, "extras": {}, "wicket_kind": "bowled"},
]
_BASELINE_WEIGHTS = {
    "ODI": (47, 34, 8, 1, 6, 1, 1, 1, 3),
    "T20": (33, 31, 10, 1, 14, 4, 2, 1, 3),
}


def _safe_stats(conn: sqlite3.Connection, player: str) -> dict[str, Any] | None:
    try:
        return compute_stats(conn, player)
    except PlayerNotFoundError:
        return None


def _delivery_history(
    conn: sqlite3.Connection,
    player: str,
    *,
    batter: bool,
    match_type: str,
    date_limit: str | None = None,
) -> list[dict[str, Any]]:
    role_column = "batter" if batter else "bowler"
    query = f"""
        SELECT d.runs_batter, d.runs_total, d.runs_wides, d.runs_noballs,
               d.runs_byes, d.runs_legbyes, d.runs_penalty,
               (SELECT w.kind FROM wickets w WHERE w.delivery_pk = d.delivery_pk
                ORDER BY w.wicket_pk LIMIT 1) AS wicket_kind
        FROM deliveries d
        JOIN matches m ON m.match_id = d.match_id
        WHERE d.{role_column} = ? AND UPPER(m.match_type) = ?
    """
    params = [player, match_type.upper()]
    if date_limit:
        query += " AND m.match_date < ?"
        params.append(date_limit)
        
    query += """
        ORDER BY m.match_date DESC, d.delivery_pk DESC
        LIMIT 600
    """
    rows = conn.execute(query, tuple(params)).fetchall()
    return [
        {
            "runs_batter": int(row["runs_batter"]),
            "runs_total": int(row["runs_total"]),
            "extras": {
                name: int(row[column])
                for name, column in (
                    ("wides", "runs_wides"),
                    ("noballs", "runs_noballs"),
                    ("byes", "runs_byes"),
                    ("legbyes", "runs_legbyes"),
                    ("penalty", "runs_penalty"),
                )
                if row[column]
            },
            "wicket_kind": row["wicket_kind"],
        }
        for row in rows
    ]


def _outcome_key(outcome: dict[str, Any]) -> str:
    extras = outcome.get("extras", {})
    if extras.get("wides"):
        return "WD"
    if extras.get("noballs"):
        return "NB"
    
    wk = outcome.get("wicket_kind")
    if wk:
        if wk.lower() == "run out":
            return "run_out"
        return "W"
        
    if extras.get("byes") or extras.get("legbyes") or extras.get("penalty"):
        return "B_LB"
        
    return str(outcome.get("runs_batter", 0))

def _build_distribution(outcomes: list[dict[str, Any]]) -> dict[str, float]:
    counts: dict[str, int] = {}
    total = len(outcomes)
    if total == 0:
        return {}
    for o in outcomes:
        k = _outcome_key(o)
        counts[k] = counts.get(k, 0) + 1
    return {k: v / total for k, v in counts.items()}

def _sample_delivery(
    rng: random.Random,
    batter: str,
    bowler: str,
    batter_history: dict[str, list[dict[str, Any]]],
    bowler_history: dict[str, list[dict[str, Any]]],
    match_type: str,
) -> dict[str, Any]:
    batter_outcomes = batter_history.get(batter, [])
    bowler_outcomes = bowler_history.get(bowler, [])
    
    bat_dist = _build_distribution(batter_outcomes)
    bowl_dist = _build_distribution(bowler_outcomes)
    
    # We create a prior distribution from the global baseline
    keys = ["0", "1", "2", "3", "4", "5", "6", "W", "run_out", "WD", "NB", "B_LB"]
    pw = _BASELINE_WEIGHTS[match_type]
    
    # pw matches: 0, 1, 2, 3, 4, 6, WD, NB, W
    prior_map = {
        "0": pw[0], "1": pw[1], "2": pw[2], "3": pw[3], "4": pw[4], "6": pw[5],
        "WD": pw[6], "NB": pw[7], "W": pw[8],
        "5": 0, "run_out": 0, "B_LB": 0
    }
    prior_total = sum(prior_map.values())
    prior_dist = {k: prior_map[k] / prior_total for k in keys}
    
    # Explicit Shrinkage Model (Phase 8)
    # ----------------------------------
    # We blend the batter's empirical distribution and the bowler's empirical distribution
    # with a global league prior. We use the number of historical deliveries as the "weight".
    # - A rookie with 10 balls is dominated by the prior.
    # - A veteran with 2000 balls heavily influences the distribution.
    # We cap player weights at 2000 so extremely long careers don't become totally inflexible.
    # We give the prior an effective sample size of 500 deliveries.
    
    BATTER_MAX_W = 2000
    BOWLER_MAX_W = 2000
    PRIOR_W = 500
    
    bat_w = min(len(batter_outcomes), BATTER_MAX_W)
    bowl_w = min(len(bowler_outcomes), BOWLER_MAX_W)
    total_w = bat_w + bowl_w + PRIOR_W
    
    probs = []
    for k in keys:
        p_bat = bat_dist.get(k, 0)
        p_bowl = bowl_dist.get(k, 0)
        p_prior = prior_dist.get(k, 0)
        
        # We allow bowler control to slightly overweight for wickets and extras
        if k in ["W", "WD", "NB"]:
            adjusted_bowl_w = bowl_w * 1.5
            adjusted_total = bat_w + adjusted_bowl_w + PRIOR_W
            p = (bat_w * p_bat + adjusted_bowl_w * p_bowl + PRIOR_W * p_prior) / adjusted_total
        else:
            p = (bat_w * p_bat + bowl_w * p_bowl + PRIOR_W * p_prior) / total_w
            
        probs.append(p)

    total_prob = sum(probs)
    if total_prob == 0:
        return rng.choices(_BASELINE_OUTCOMES, weights=_BASELINE_WEIGHTS[match_type], k=1)[0]
        
    sampled_key = rng.choices(keys, weights=probs, k=1)[0]
    
    if sampled_key in ["0", "1", "2", "3", "4", "5", "6"]:
        r = int(sampled_key)
        return {"runs_batter": r, "runs_total": r, "extras": {}, "wicket_kind": None}
    elif sampled_key == "W":
        return {"runs_batter": 0, "runs_total": 0, "extras": {}, "wicket_kind": rng.choice(["bowled", "caught", "lbw", "stumped"])}
    elif sampled_key == "run_out":
        return {"runs_batter": 0, "runs_total": 0, "extras": {}, "wicket_kind": "run out"}
    elif sampled_key == "WD":
        return {"runs_batter": 0, "runs_total": 1, "extras": {"wides": 1}, "wicket_kind": None}
    elif sampled_key == "NB":
        return {"runs_batter": 0, "runs_total": 1, "extras": {"noballs": 1}, "wicket_kind": None}
    else: # B_LB
        return {"runs_batter": 0, "runs_total": 1, "extras": {"legbyes": 1}, "wicket_kind": None}


def _simulate_ball_innings(
    conn: sqlite3.Connection,
    *,
    batting_team: str,
    batting_lineup: list[str],
    bowling_lineup: list[str],
    innings_index: int,
    overs: int,
    match_type: str,
    rng: random.Random,
    target: int | None = None,
    date_limit: str | None = None,
    precomputed_batter_history: dict[str, list[dict[str, Any]]] | None = None,
    precomputed_bowler_history: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if precomputed_batter_history is not None:
        batter_history = {p: precomputed_batter_history.get(p, []) for p in batting_lineup}
    else:
        batter_history = {
            player: _delivery_history(conn, player, batter=True, match_type=match_type, date_limit=date_limit)
            for player in batting_lineup
        }

    if precomputed_bowler_history is not None:
        bowler_history = {p: precomputed_bowler_history.get(p, []) for p in bowling_lineup}
    else:
        bowler_history = {
            player: _delivery_history(conn, player, batter=False, match_type=match_type, date_limit=date_limit)
            for player in bowling_lineup
        }
    score = 0
    wickets_lost = 0
    striker_index = 0
    non_striker_index = 1
    next_batter_index = 2
    legal_deliveries = 0
    deliveries: list[dict[str, Any]] = []
    over_summaries: list[dict[str, Any]] = []
    bowler_over_counts = {player: 0 for player in bowling_lineup}
    previous_bowler = None
    dismissed: set[int] = set()
    max_bowler_overs = max(math.ceil(overs / 5), math.ceil(overs / max(1, len(bowling_lineup))))
    innings_complete = False

    for over_number in range(overs):
        if innings_complete or wickets_lost >= min(10, len(batting_lineup) - 1):
            break
        available_bowlers = [
            player for player in bowling_lineup
            if bowler_over_counts[player] < max_bowler_overs and player != previous_bowler
        ]
        if not available_bowlers:
            available_bowlers = [
                player for player in bowling_lineup
                if bowler_over_counts[player] < max_bowler_overs
            ]
        if not available_bowlers:
            available_bowlers = list(bowling_lineup)
        bowler = available_bowlers[over_number % len(available_bowlers)]
        previous_bowler = bowler
        bowler_over_counts[bowler] += 1
        legal_balls_in_over = 0
        deliveries_in_over = 0
        over_runs = 0

        while legal_balls_in_over < 6 and deliveries_in_over < 18:
            if wickets_lost >= min(10, len(batting_lineup) - 1):
                innings_complete = True
                break
            if target is not None and score >= target:
                innings_complete = True
                break
            striker = batting_lineup[striker_index]
            non_striker = batting_lineup[non_striker_index]
            outcome = _sample_delivery(
                rng,
                striker,
                bowler,
                batter_history,
                bowler_history,
                match_type,
            )
            extras = dict(outcome["extras"])
            runs_batter = int(outcome["runs_batter"])
            runs_total = int(outcome["runs_total"])
            is_legal = not extras.get("wides") and not extras.get("noballs")
            deliveries_in_over += 1
            if is_legal:
                legal_balls_in_over += 1
                legal_deliveries += 1

            score += runs_total
            over_runs += runs_total
            wicket_kind = outcome.get("wicket_kind")
            if wicket_kind and wicket_kind.lower() not in NON_DISMISSAL_KINDS:
                player_out_index = non_striker_index if wicket_kind.lower() == "run out" and rng.random() < 0.2 else striker_index
                player_out_was_striker = player_out_index == striker_index
                player_out = batting_lineup[player_out_index]
                wickets_lost += 1
                dismissed.add(player_out_index)
                wicket_payload = [{"kind": wicket_kind, "player_out": player_out, "fielders": []}]
            else:
                player_out_index = None
                player_out_was_striker = False
                wicket_payload = []

            ball_in_over = legal_balls_in_over + 1
            deliveries.append({
                "innings_index": innings_index,
                "over_number": over_number,
                "ball_in_over": ball_in_over,
                "actual_delivery": f"{over_number}.{ball_in_over}",
                "batter": striker,
                "bowler": bowler,
                "non_striker": non_striker,
                "runs_batter": runs_batter,
                "runs_total": runs_total,
                "extras": {name: extras.get(name, 0) for name in ("wides", "noballs", "byes", "legbyes", "penalty")},
                "wickets": wicket_payload,
            })

            if runs_total % 2:
                striker_index, non_striker_index = non_striker_index, striker_index
            if player_out_index is not None:
                if next_batter_index < len(batting_lineup):
                    replacement_index = next_batter_index
                    next_batter_index += 1
                    if player_out_was_striker:
                        striker_index = replacement_index
                    else:
                        non_striker_index = replacement_index
                else:
                    innings_complete = True

        over_summaries.append({
            "over": over_number + 1,
            "bowler": bowler,
            "runs": over_runs,
            "wickets": sum(len(ball["wickets"]) for ball in deliveries if ball["innings_index"] == innings_index and ball["over_number"] == over_number),
        })
        if legal_balls_in_over == 6 and not innings_complete:
            striker_index, non_striker_index = non_striker_index, striker_index
        if target is not None and score >= target:
            innings_complete = True

    return {
        "innings": {
            "innings_index": innings_index,
            "batting_team": batting_team,
            "total_runs": score,
            "wickets_lost": wickets_lost,
            "legal_deliveries_faced": legal_deliveries,
        },
        "deliveries": deliveries,
        "overs": over_summaries,
        "complete": innings_complete or legal_deliveries >= overs * 6 or wickets_lost >= min(10, len(batting_lineup) - 1),
    }


def simulate_match(
    conn: sqlite3.Connection,
    *,
    team_a: str,
    team_b: str,
    batting_lineup_a: list[str],
    batting_lineup_b: list[str],
    bowling_lineup_a: list[str],
    bowling_lineup_b: list[str],
    overs: int = 50,
    match_type: str = "ODI",
    seed: int | None = None,
    date_limit: str | None = None,
    precomputed_batter_history: dict[str, list[dict[str, Any]]] | None = None,
    precomputed_bowler_history: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Simulate both limited-overs innings from player-specific ball histories."""
    if not team_a or not team_b or team_a == team_b:
        return {"available": False, "note": "Select two different teams."}
    if not 1 <= overs <= 50:
        return {"available": False, "note": "Overs must be between 1 and 50."}
    normalized_format = match_type.strip().upper()
    if normalized_format not in {"ODI", "T20"}:
        return {"available": False, "note": "Only ODI and T20 limited-overs simulations are supported."}
    lineups = (batting_lineup_a, batting_lineup_b, bowling_lineup_a, bowling_lineup_b)
    if any(len(lineup) < 2 or len(set(lineup)) != len(lineup) for lineup in lineups):
        return {"available": False, "note": "Each batting and bowling lineup needs at least two unique players."}
    if overs >= 20 and (
        len(batting_lineup_a) != 11
        or len(batting_lineup_b) != 11
        or len(bowling_lineup_a) < 5
        or len(bowling_lineup_b) < 5
    ):
        return {
            "available": False,
            "note": "Full-length simulations require 11 batters per team and at least five bowling options per team.",
        }

    actual_seed = seed if seed is not None else random.SystemRandom().randrange(0, 2**32)
    rng = random.Random(actual_seed)
    record_call(
        "simulate_match",
        channel="in-process",
        arguments={"team_a": team_a, "team_b": team_b, "overs": overs, "seed": actual_seed},
    )
    first_innings = _simulate_ball_innings(
        conn,
        batting_team=team_a,
        batting_lineup=batting_lineup_a,
        bowling_lineup=bowling_lineup_b,
        innings_index=0,
        overs=overs,
        match_type=normalized_format,
        rng=rng,
        date_limit=date_limit,
        precomputed_batter_history=precomputed_batter_history,
        precomputed_bowler_history=precomputed_bowler_history,
    )
    target = first_innings["innings"]["total_runs"] + 1
    second_innings = _simulate_ball_innings(
        conn,
        batting_team=team_b,
        batting_lineup=batting_lineup_b,
        bowling_lineup=bowling_lineup_a,
        innings_index=1,
        overs=overs,
        match_type=normalized_format,
        rng=rng,
        target=target,
        date_limit=date_limit,
        precomputed_batter_history=precomputed_batter_history,
        precomputed_bowler_history=precomputed_bowler_history,
    )
    first_score = first_innings["innings"]["total_runs"]
    second_score = second_innings["innings"]["total_runs"]
    if second_score >= target:
        winner = team_b
        wickets_remaining = max(0, len(batting_lineup_b) - 1 - second_innings["innings"]["wickets_lost"])
        outcome = f"{team_b} chased {target} with {wickets_remaining} wicket(s) remaining."
    elif second_score == first_score:
        winner = None
        outcome = "Match tied in this simulation."
    else:
        winner = team_a
        outcome = f"{team_a} defended {first_score}; {team_b} finished on {second_score}."

    return {
        "available": True,
        "label": "Historical-delivery-based randomized simulation",
        "teams": [team_a, team_b],
        "players": {team_a: batting_lineup_a, team_b: batting_lineup_b},
        "winner": winner,
        "outcome_summary": outcome,
        "target": target,
        "seed": actual_seed,
        "match_type": f"{normalized_format} · {overs}-over simulation",
        "player_of_match": None,
        "innings": [first_innings["innings"], second_innings["innings"]],
        "deliveries": first_innings["deliveries"] + second_innings["deliveries"],
        "overs": [first_innings["overs"], second_innings["overs"]],
        "lineups": {
            team_a: {"batting": batting_lineup_a, "bowling": bowling_lineup_a},
            team_b: {"batting": batting_lineup_b, "bowling": bowling_lineup_b},
        },
        "assumptions": [
            f"Each ball is sampled from the selected batter's and bowler's most recent 600 recorded Cricsheet {normalized_format} deliveries, blended equally when both histories exist.",
            f"Players without local {normalized_format} ball history use a generic limited-overs fallback distribution.",
            "The model does not know today's pitch, weather, verified playing XI, injuries, toss, or tactical matchups.",
            "This is a stochastic scenario, not an accurate forecast or official scorecard. Use the returned seed to reproduce it.",
        ],
    }


def simulate_innings(
    conn: sqlite3.Connection,
    batting_lineup: list[str],
    bowling_lineup: list[str],
    *,
    overs: int = 20,
    target: int | None = None,
) -> dict[str, Any]:
    record_call(
        "simulate_innings",
        channel="in-process",
        arguments={"overs": overs, "batters": batting_lineup, "bowlers": bowling_lineup},
    )
    if overs <= 0:
        return {
            "available": False,
            "label": LABEL,
            "note": "Simulation unavailable: overs must be positive.",
        }
    if not batting_lineup or not bowling_lineup:
        return {
            "available": False,
            "label": LABEL,
            "note": "Simulation unavailable: batting and bowling lineups are required.",
        }

    bat_stats = {p: _safe_stats(conn, p) for p in batting_lineup}
    bowl_stats = {p: _safe_stats(conn, p) for p in bowling_lineup}

    assumptions = [
        LABEL + ": each over uses expected runs from the current striker's historical "
        "strike rate and the bowler's historical economy, averaged together.",
        f"Players with no batting record are assigned a placeholder strike rate of {DEFAULT_SR}.",
        f"Players with no bowling record are assigned a placeholder economy of {DEFAULT_ECONOMY}.",
        "Wicket expectation per over uses 6 / bowling strike rate when defined, "
        f"otherwise {DEFAULT_WICKET_PER_OVER}.",
        "This is an expected-value scenario, not a match result.",
    ]

    striker_idx = 0
    non_idx = 1 if len(batting_lineup) > 1 else 0
    wickets = 0.0
    total = 0.0
    progression: list[dict[str, Any]] = []
    used_placeholders: list[str] = []
    batting_breakdown: dict[str, dict[str, Any]] = {}
    bowling_breakdown: dict[str, dict[str, Any]] = {}

    for player in batting_lineup:
        stats = bat_stats.get(player) or {}
        batting = (stats.get("batting") or {})
        batting_breakdown[player] = {
            "runs": batting.get("runs", 0),
            "balls": batting.get("balls_faced", 0),
            "average": batting.get("average"),
            "strike_rate": batting.get("strike_rate"),
            "fours": batting.get("fours", 0),
            "sixes": batting.get("sixes", 0),
        }

    for player in bowling_lineup:
        stats = bowl_stats.get(player) or {}
        bowling = (stats.get("bowling") or {})
        bowling_breakdown[player] = {
            "wickets": bowling.get("wickets", 0),
            "overs": bowling.get("overs", 0),
            "economy": bowling.get("economy"),
            "strike_rate": bowling.get("strike_rate"),
            "average": bowling.get("average"),
            "dot_ball_percentage": bowling.get("dot_ball_percentage"),
        }

    required_run_rate = None
    for over in range(overs):
        if wickets >= 10 or striker_idx >= len(batting_lineup):
            break
        bowler = bowling_lineup[over % len(bowling_lineup)]
        striker = batting_lineup[min(striker_idx, len(batting_lineup) - 1)]
        bstat = bat_stats.get(striker) or {}
        bowl = bowl_stats.get(bowler) or {}
        batting = (bstat or {}).get("batting")
        bowling = (bowl or {}).get("bowling")

        sr = batting["strike_rate"] if batting and batting.get("strike_rate") else None
        if sr is None:
            sr = DEFAULT_SR
            used_placeholders.append(f"{striker} batting SR")
        economy = bowling["economy"] if bowling and bowling.get("economy") else None
        if economy is None:
            economy = DEFAULT_ECONOMY
            used_placeholders.append(f"{bowler} economy")

        expected_from_sr = 6 * (sr / 100.0)
        expected_from_econ = economy
        expected_runs = (expected_from_sr + expected_from_econ) / 2.0

        bowl_sr = bowling["strike_rate"] if bowling and bowling.get("strike_rate") else None
        expected_wickets = (6.0 / bowl_sr) if bowl_sr else DEFAULT_WICKET_PER_OVER

        total += expected_runs
        wickets = min(10.0, wickets + expected_wickets)
        rr = total / (over + 1)
        remaining_overs = max(0, overs - (over + 1))
        if target is not None and remaining_overs > 0:
            required_run_rate = max(0.0, (target - total) / remaining_overs)
        projected = total + rr * remaining_overs

        if wickets >= 10:
            striker_idx = len(batting_lineup)
        elif expected_wickets >= 0.5:
            striker_idx = min(striker_idx + 1, len(batting_lineup) - 1)

        progression.append(
            {
                "over": over + 1,
                "striker": striker,
                "bowler": bowler,
                "runs_this_over": round(expected_runs, 2),
                "score": round(total, 2),
                "wickets": round(wickets, 2),
                "run_rate": round(rr, 2),
                "required_run_rate": round(required_run_rate, 2) if required_run_rate is not None else None,
                "projected_score": round(projected, 2),
            }
        )
        striker_idx, non_idx = non_idx, striker_idx

    unique_placeholders = sorted(set(used_placeholders))
    if unique_placeholders:
        assumptions.append(
            "Placeholder rates were used for: " + ", ".join(unique_placeholders) + "."
        )

    final_score = progression[-1]["score"] if progression else 0
    final_wickets = progression[-1]["wickets"] if progression else 0
    scorecard = {
        "runs": round(final_score, 2),
        "wickets": round(final_wickets, 2),
        "overs": overs,
        "target": target,
        "run_rate": round(final_score / max(1, len(progression)), 2) if progression else 0,
        "required_run_rate": round(required_run_rate, 2) if required_run_rate is not None else None,
        "projected_score": round((final_score + (final_score / max(1, len(progression))) * max(0, overs - len(progression))), 2) if progression else 0,
        "batting": [
            {
                "player": player,
                "expected_runs": round((bat_stats.get(player) or {}).get("batting", {}).get("runs", 0) / max(1, (bat_stats.get(player) or {}).get("batting", {}).get("innings", 1)), 2),
                "average": (bat_stats.get(player) or {}).get("batting", {}).get("average"),
                "strike_rate": (bat_stats.get(player) or {}).get("batting", {}).get("strike_rate"),
                "balls": (bat_stats.get(player) or {}).get("batting", {}).get("balls_faced", 0),
            }
            for player in batting_lineup
        ],
        "bowling": [
            {
                "player": player,
                "wickets": (bowl_stats.get(player) or {}).get("bowling", {}).get("wickets", 0),
                "economy": (bowl_stats.get(player) or {}).get("bowling", {}).get("economy"),
                "average": (bowl_stats.get(player) or {}).get("bowling", {}).get("average"),
                "expected_wickets": round((6.0 / ((bowl_stats.get(player) or {}).get("bowling", {}).get("strike_rate") or DEFAULT_SR)) if (bowl_stats.get(player) or {}).get("bowling", {}).get("strike_rate") else DEFAULT_WICKET_PER_OVER, 2),
            }
            for player in bowling_lineup
        ],
    }

    return {
        "available": True,
        "label": LABEL,
        "overs_requested": overs,
        "target": target,
        "batting_lineup": batting_lineup,
        "bowling_lineup": bowling_lineup,
        "progression": progression,
        "final_score": final_score,
        "final_wickets": final_wickets,
        "scorecard": scorecard,
        "player_statistics": {
            "batting": {
                name: {
                    "runs": stats.get("batting", {}).get("runs", 0),
                    "average": stats.get("batting", {}).get("average"),
                    "strike_rate": stats.get("batting", {}).get("strike_rate"),
                    "balls_faced": stats.get("batting", {}).get("balls_faced", 0),
                    "fours": stats.get("batting", {}).get("fours", 0),
                    "sixes": stats.get("batting", {}).get("sixes", 0),
                    "matches_seen": stats.get("matches_considered", []),
                }
                for name, stats in bat_stats.items()
            },
            "bowling": {
                name: {
                    "wickets": stats.get("bowling", {}).get("wickets", 0),
                    "economy": stats.get("bowling", {}).get("economy"),
                    "average": stats.get("bowling", {}).get("average"),
                    "strike_rate": stats.get("bowling", {}).get("strike_rate"),
                    "dot_ball_percentage": stats.get("bowling", {}).get("dot_ball_percentage"),
                }
                for name, stats in bowl_stats.items()
            },
        },
        "assumptions": assumptions,
        "note": (
            "Do not present these figures as an actual match outcome. "
            "They are a transparent expected-value walk-through of historical rates."
        ),
    }

def simulate_monte_carlo(
    conn: sqlite3.Connection,
    *,
    team_a: str,
    team_b: str,
    batting_lineup_a: list[str],
    batting_lineup_b: list[str],
    bowling_lineup_a: list[str],
    bowling_lineup_b: list[str],
    overs: int = 20,
    match_type: str = "T20",
    simulations: int = 1000,
    date_limit: str | None = None,
) -> dict[str, Any]:
    """Run a Monte Carlo simulation (Phase 24 & 26), reusing feature distributions."""
    all_batters = set(batting_lineup_a + batting_lineup_b)
    all_bowlers = set(bowling_lineup_a + bowling_lineup_b)
    
    precomputed_batter = {}
    for p in all_batters:
        precomputed_batter[p] = _delivery_history(conn, p, batter=True, match_type=match_type, date_limit=date_limit)
        
    precomputed_bowler = {}
    for p in all_bowlers:
        precomputed_bowler[p] = _delivery_history(conn, p, batter=False, match_type=match_type, date_limit=date_limit)
        
    results = []
    team_a_wins = 0
    team_b_wins = 0
    ties = 0
    
    for i in range(simulations):
        # Pass seed to prevent them all being identical
        res = simulate_match(
            conn,
            team_a=team_a,
            team_b=team_b,
            batting_lineup_a=batting_lineup_a,
            batting_lineup_b=batting_lineup_b,
            bowling_lineup_a=bowling_lineup_a,
            bowling_lineup_b=bowling_lineup_b,
            overs=overs,
            match_type=match_type,
            seed=None,
            date_limit=date_limit,
            precomputed_batter_history=precomputed_batter,
            precomputed_bowler_history=precomputed_bowler,
        )
        if not res.get("available"):
            return res
        
        winner = res.get("winner")
        if winner == team_a:
            team_a_wins += 1
        elif winner == team_b:
            team_b_wins += 1
        else:
            ties += 1
            
        # We don't save the full balls to avoid OOM
        results.append({
            "score_a": res["innings"][0]["total_runs"],
            "score_b": res["innings"][1]["total_runs"],
            "wickets_a": res["innings"][0]["wickets_lost"],
            "wickets_b": res["innings"][1]["wickets_lost"],
            "winner": winner
        })
        
    return {
        "available": True,
        "label": f"Monte Carlo Simulation ({simulations} iterations)",
        "simulations": simulations,
        "team_a_win_probability": team_a_wins / simulations,
        "team_b_win_probability": team_b_wins / simulations,
        "tie_probability": ties / simulations,
        "results": results,
    }
