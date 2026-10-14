"""Explainable HIGH / MEDIUM / LOW confidence from sample sizes only.

This is not a machine-learning probability. Every category is derived from
counts of matches, innings, balls, or matchup deliveries that actually exist
in the local database.
"""

from __future__ import annotations

from typing import Any

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"

HIGH_MATCHES = 8
HIGH_BALLS_BATTING = 60
HIGH_LEGAL_BOWLING = 36
MEDIUM_MATCHES = 3
MATCHUP_LOW_BALLS = 12


def _level(matches: int, volume: int, high_volume: int) -> str:
    if matches >= HIGH_MATCHES and volume >= high_volume:
        return HIGH
    if matches >= MEDIUM_MATCHES:
        return MEDIUM
    return LOW


def batting_confidence(
    *, matches: int, innings: int, balls_faced: int
) -> dict[str, Any]:
    level = _level(matches, balls_faced, HIGH_BALLS_BATTING)
    reason = (
        f"{matches} match(es), {innings} innings, {balls_faced} balls faced. "
    )
    if level == HIGH:
        reason += (
            "Sample meets the high-confidence thresholds "
            f"({HIGH_MATCHES}+ matches and {HIGH_BALLS_BATTING}+ balls)."
        )
    elif level == MEDIUM:
        reason += (
            f"At least {MEDIUM_MATCHES} matches are available, but batting "
            "volume is still below the high-confidence threshold."
        )
    else:
        reason += (
            "The observed strike rate and average may not represent "
            "longer-term performance."
        )
    return {"level": level, "reason": reason, "matches": matches, "innings": innings, "balls": balls_faced}


def bowling_confidence(*, matches: int, legal_deliveries: int, wickets: int) -> dict[str, Any]:
    level = _level(matches, legal_deliveries, HIGH_LEGAL_BOWLING)
    reason = (
        f"{matches} match(es), {legal_deliveries} legal deliveries, "
        f"{wickets} wicket(s) credited. "
    )
    if level == HIGH:
        reason += (
            "Sample meets the high-confidence bowling thresholds "
            f"({HIGH_MATCHES}+ matches and {HIGH_LEGAL_BOWLING}+ legal balls)."
        )
    elif level == MEDIUM:
        reason += (
            f"At least {MEDIUM_MATCHES} matches are available, but bowling "
            "volume is still below the high-confidence threshold."
        )
    else:
        reason += (
            "Bowling rates from this sample may not represent longer-term "
            "performance."
        )
    return {
        "level": level,
        "reason": reason,
        "matches": matches,
        "legal_deliveries": legal_deliveries,
        "wickets": wickets,
    }


def matchup_confidence(*, balls: int, dismissals: int) -> dict[str, Any]:
    if balls >= MATCHUP_LOW_BALLS * 3:
        level = HIGH
        reason = (
            f"{balls} deliveries in this matchup. That is a usable sample, "
            "though still not a full career history."
        )
    elif balls >= MATCHUP_LOW_BALLS:
        level = MEDIUM
        reason = (
            f"{balls} deliveries in this matchup. Treat rates as indicative "
            "rather than conclusive."
        )
    else:
        level = LOW
        reason = (
            f"Only {balls} delivery(ies) and {dismissals} dismissal(s) exist "
            "for this batter-vs-bowler pairing. Strike rate and dismissal "
            "rate may not represent a true matchup."
        )
    return {"level": level, "reason": reason, "balls": balls, "dismissals": dismissals}


def more_conservative(a: str, b: str) -> str:
    order = {LOW: 0, MEDIUM: 1, HIGH: 2}
    return a if order.get(a, 0) <= order.get(b, 0) else b


def overall_from_parts(*parts: dict[str, Any] | None) -> dict[str, Any]:
    present = [p for p in parts if p]
    if not present:
        return {
            "level": LOW,
            "reason": "No domain statistics were available to estimate confidence.",
        }
    level = present[0]["level"]
    for p in present[1:]:
        level = more_conservative(level, p["level"])
    reasons = [p["reason"] for p in present]
    return {"level": level, "reason": " ".join(reasons), "parts": present}
