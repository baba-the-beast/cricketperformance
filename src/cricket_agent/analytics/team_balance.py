"""Team-balance notes from assigned roles — no invented XI scores."""

from __future__ import annotations

from collections import Counter
from typing import Any


def summarize_balance(role_reports: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    low_confidence: list[str] = []
    for report in role_reports:
        player = report.get("player")
        primary = report.get("primary")
        if primary and primary.get("assigned"):
            counts[primary["role"]] += 1
            if primary.get("confidence") == "LOW":
                low_confidence.append(player)
        else:
            counts["Unclassified"] += 1

    observations: list[str] = []
    if counts["Opener"] < 1:
        observations.append(
            "No player in this set is classified as an opener from first-innings position."
        )
    if counts["Bowling all-rounder"] + sum(
        1 for k, n in counts.items() if "bowler" in k.lower() or "Bowling" in k
    ) == 0:
        observations.append(
            "Bowling roles in this set are limited; death-bowling coverage may be thin."
        )
    if not observations:
        observations.append(
            "Observed roles in this candidate set: "
            + ", ".join(f"{role} ({n})" for role, n in counts.most_common())
            + "."
        )

    return {
        "role_counts": dict(counts),
        "low_confidence_players": low_confidence,
        "observations": observations,
        "note": (
            "Balance is descriptive of the selected candidates only. "
            "It is not a predicted XI rating."
        ),
    }
