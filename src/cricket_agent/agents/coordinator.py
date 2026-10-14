"""Coordinator: reason over specialist reports rather than concatenating them."""

from __future__ import annotations

from typing import Any

from cricket_agent.analytics.confidence import LOW, more_conservative
from cricket_agent.analytics.team_balance import summarize_balance
from cricket_agent.observability import record_call

_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _conf(report: dict[str, Any]) -> str:
    return str(report.get("confidence") or LOW)


def synthesize(
    *,
    question: str,
    players: list[str],
    batting_reports: list[dict[str, Any]],
    bowling_reports: list[dict[str, Any]],
    fielding_reports: list[dict[str, Any]],
    role_reports: list[dict[str, Any]],
    opponent: str | None = None,
    last_n_matches: int | None = None,
) -> dict[str, Any]:
    record_call("coordinator", channel="in-process", arguments={"players": players})

    by_player: dict[str, dict[str, Any]] = {p: {} for p in players}
    for r in batting_reports:
        by_player.setdefault(r["player"], {})["batting"] = r
    for r in bowling_reports:
        by_player.setdefault(r["player"], {})["bowling"] = r
    for r in fielding_reports:
        by_player.setdefault(r["player"], {})["fielding"] = r
    for r in role_reports:
        by_player.setdefault(r["player"], {})["role"] = r

    recommendations: list[dict[str, Any]] = []
    flags: list[str] = []
    conflicts: list[str] = []

    for player in players:
        bundle = by_player.get(player, {})
        bat = bundle.get("batting") or {}
        bowl = bundle.get("bowling") or {}
        field = bundle.get("fielding") or {}
        role = (bundle.get("role") or {}).get("primary")

        bat_c = _conf(bat)
        bowl_c = _conf(bowl)
        overall = more_conservative(bat_c, bowl_c) if bat.get("metrics") and bowl.get("metrics") else (
            bat_c if bat.get("metrics") else bowl_c if bowl.get("metrics") else LOW
        )

        bat_useful = bool(bat.get("metrics"))
        bowl_useful = bool(bowl.get("metrics"))
        if bat_useful and bowl_useful and _RANK[bat_c] != _RANK[bowl_c]:
            stronger = "batting" if _RANK[bat_c] > _RANK[bowl_c] else "bowling"
            weaker = "bowling" if stronger == "batting" else "batting"
            conflicts.append(
                f"{player}: {stronger} evidence is {_conf(bat if stronger == 'batting' else bowl)} "
                f"while {weaker} evidence is weaker. The coordinator weights the higher-confidence domain more, "
                "and still flags the weaker sample."
            )

        reasons = []
        if bat_useful:
            reasons.append(bat["recommendation"])
        if bowl_useful:
            reasons.append(bowl["recommendation"])
        if not bat_useful and not bowl_useful:
            reasons.append("No batting or bowling sample.")

        if overall == LOW:
            flags.append(
                f"{player}: LOW CONFIDENCE — specialist reports are based on a small sample."
            )

        recommendations.append(
            {
                "player": player,
                "role": (role or {}).get("role") if role else None,
                "role_reason": (role or {}).get("reason") if role else None,
                "evidence": {
                    "batting": bat.get("observations"),
                    "bowling": bowl.get("observations"),
                    "fielding": field.get("observations"),
                },
                "confidence": overall,
                "notes": reasons,
            }
        )

    # Prefer players with any MEDIUM/HIGH domain over all-LOW, without a fake score.
    ordered = sorted(
        recommendations,
        key=lambda r: (_RANK.get(r["confidence"], 0), 1 if r["role"] else 0),
        reverse=True,
    )
    if ordered:
        lead = ordered[0]
        if lead["confidence"] == LOW:
            flags.append(
                "Every recommendation in this set is LOW CONFIDENCE because "
                "the ingested sample is small. Do not treat the ordering as a ranking."
            )
            selection_summary = (
                f"Among the named players, {lead['player']} has the most complete "
                "recorded evidence in this dataset, but confidence is LOW for all names. "
                "This is not a predicted match-winning selection."
            )
        else:
            selection_summary = (
                f"{lead['player']} currently has the stronger evidence-backed case "
                f"({lead['confidence']} confidence) among the selected players, "
                "based on specialist reports — not a composite score."
            )
    else:
        selection_summary = "No players were supplied."

    balance = summarize_balance(role_reports)

    return {
        "question": question,
        "context": {
            "opponent": opponent,
            "last_n_matches": last_n_matches,
            "available_players": players,
        },
        "batting_analysis": batting_reports,
        "bowling_analysis": bowling_reports,
        "fielding_analysis": fielding_reports,
        "team_balance": balance,
        "conflicts_resolved": conflicts,
        "recommendations": ordered,
        "risks_flags": flags,
        "selection_summary": selection_summary,
        "note": (
            "The coordinator compares specialist confidence and completeness. "
            "It does not produce an unexplained numeric selection score."
        ),
    }
