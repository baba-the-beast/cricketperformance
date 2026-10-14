"""Explainable role labels from batting order and bowling volume. No pace/spin invention."""

from __future__ import annotations

import sqlite3
from typing import Any

from cricket_agent.analytics.confidence import LOW, MEDIUM, batting_confidence, bowling_confidence
from cricket_agent.analytics.phases import T20_PHASES
from cricket_agent.analytics.statistics import compute_bowling_stats
from cricket_agent.dismissals import BOWLER_WICKET_KINDS_SQL


SPIN_PACE_NOTE = (
    "Insufficient data: Cricsheet deliveries in this project do not record "
    "bowling style (pace vs spin), so Spin specialist and Pace specialist "
    "cannot be assigned honestly."
)


def _first_appearances(
    conn: sqlite3.Connection, player: str, match_ids: list[str]
) -> list[dict[str, Any]]:
    if not match_ids:
        return []
    placeholders = ",".join("?" for _ in match_ids)
    rows = conn.execute(
        f"""
        SELECT match_id, innings_index, MIN(over_number) AS first_over,
               MIN(ball_in_over) AS first_ball
        FROM deliveries
        WHERE batter = ? AND match_id IN ({placeholders})
        GROUP BY match_id, innings_index
        """,
        (player, *match_ids),
    ).fetchall()
    return [dict(r) for r in rows]


def classify_roles(
    conn: sqlite3.Connection,
    player: str,
    match_ids: list[str],
    batting: dict[str, Any] | None,
    bowling: dict[str, Any] | None,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    appearances = _first_appearances(conn, player, match_ids)
    if appearances:
        first_overs = [int(a["first_over"]) for a in appearances]
        avg_first = sum(first_overs) / len(first_overs)
        opener_count = sum(1 for o in first_overs if o <= 1)
        death_count = sum(1 for o in first_overs if o >= T20_PHASES["death"][0])
        middle_count = sum(
            1
            for o in first_overs
            if T20_PHASES["middle"][0] <= o <= T20_PHASES["middle"][1]
        )
        n = len(first_overs)
        bat_conf = batting_confidence(
            matches=len(match_ids),
            innings=batting["innings"] if batting else n,
            balls_faced=batting["balls_faced"] if batting else 0,
        )
        # Primary batting role from where innings typically begin.
        if opener_count / n >= 0.5:
            candidates.append(
                {
                    "role": "Opener",
                    "reason": (
                        f"{opener_count} of {n} observed batting innings begin "
                        "in overs 1-2."
                    ),
                    "supporting_metrics": {
                        "innings_observed": n,
                        "opener_starts": opener_count,
                        "average_first_over_0based": avg_first,
                    },
                    "confidence": bat_conf["level"],
                    "confidence_reason": bat_conf["reason"],
                }
            )
        elif death_count / n >= 0.5:
            sr = batting["strike_rate"] if batting else None
            candidates.append(
                {
                    "role": "Finisher",
                    "reason": (
                        f"{death_count} of {n} observed batting innings begin "
                        "in the death overs"
                        + (
                            f", with a strike rate of {sr:.1f}."
                            if sr is not None
                            else "."
                        )
                    ),
                    "supporting_metrics": {
                        "innings_observed": n,
                        "death_starts": death_count,
                        "strike_rate": sr,
                    },
                    "confidence": bat_conf["level"],
                    "confidence_reason": bat_conf["reason"],
                }
            )
        elif middle_count / n >= 0.4 or (2 <= avg_first <= 14):
            fours = batting["fours"] if batting else 0
            sixes = batting["sixes"] if batting else 0
            balls = batting["balls_faced"] if batting else 0
            boundary_pct = (
                ((fours + sixes) / balls * 100) if balls else None
            )
            candidates.append(
                {
                    "role": "Middle-order batter" if avg_first >= 6 else "Top-order batter",
                    "reason": (
                        "Most observed innings begin in the "
                        f"{'middle' if avg_first >= 6 else 'top'} order "
                        f"(average first over {avg_first + 1:.1f} as 1-based). "
                        + (
                            f"Boundary balls are {boundary_pct:.1f}% of balls faced."
                            if boundary_pct is not None
                            else ""
                        )
                    ),
                    "supporting_metrics": {
                        "innings_observed": n,
                        "average_first_over_0based": avg_first,
                        "boundary_percentage": boundary_pct,
                    },
                    "confidence": bat_conf["level"],
                    "confidence_reason": bat_conf["reason"],
                }
            )

    if bowling and batting:
        bowl_conf = bowling_confidence(
            matches=len(match_ids),
            legal_deliveries=bowling["legal_deliveries"],
            wickets=bowling["wickets"],
        )
        if bowling["legal_deliveries"] >= 6 and batting["balls_faced"] >= 1:
            candidates.append(
                {
                    "role": "Bowling all-rounder",
                    "reason": (
                        f"The player both batted ({batting['runs']} runs, "
                        f"{batting['balls_faced']} balls) and bowled "
                        f"({bowling['legal_deliveries']} legal deliveries, "
                        f"{bowling['wickets']} wickets) in this sample."
                    ),
                    "supporting_metrics": {
                        "batting_runs": batting["runs"],
                        "bowling_wickets": bowling["wickets"],
                        "legal_deliveries": bowling["legal_deliveries"],
                    },
                    "confidence": bowl_conf["level"]
                    if bowl_conf["level"] == batting_confidence(
                        matches=len(match_ids),
                        innings=batting["innings"],
                        balls_faced=batting["balls_faced"],
                    )["level"]
                    else MEDIUM if len(match_ids) >= 3 else LOW,
                    "confidence_reason": bowl_conf["reason"],
                }
            )

        death_rows = conn.execute(
            f"""
            SELECT * FROM deliveries
            WHERE bowler = ? AND over_number BETWEEN ? AND ?
              AND match_id IN ({",".join("?" for _ in match_ids)})
            """,
            (player, T20_PHASES["death"][0], T20_PHASES["death"][1], *match_ids),
        ).fetchall() if match_ids else []
        death_wickets = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM wickets
            JOIN deliveries ON wickets.delivery_pk = deliveries.delivery_pk
            WHERE deliveries.bowler = ?
              AND wickets.kind IN ({BOWLER_WICKET_KINDS_SQL})
              AND deliveries.over_number BETWEEN ? AND ?
              AND deliveries.match_id IN ({",".join("?" for _ in match_ids)})
            """,
            (player, T20_PHASES["death"][0], T20_PHASES["death"][1], *match_ids),
        ).fetchone()["n"] if match_ids else 0
        if len(death_rows) >= 6:
            death_stats = compute_bowling_stats(death_rows, death_wickets).to_dict()
            candidates.append(
                {
                    "role": "Death bowler",
                    "reason": (
                        f"{death_stats['legal_deliveries']} legal death-over "
                        "deliveries are on record"
                        + (
                            f" at an economy of {death_stats['economy']:.2f}."
                            if death_stats["economy"] is not None
                            else "."
                        )
                    ),
                    "supporting_metrics": death_stats,
                    "confidence": LOW if len(match_ids) < 3 else MEDIUM,
                    "confidence_reason": (
                        f"Death-over sample is {len(death_rows)} deliveries "
                        f"across {len(match_ids)} match(es)."
                    ),
                }
            )

    # Always document why spin/pace cannot be assigned.
    unavailable = [
        {
            "role": "Spin specialist",
            "reason": SPIN_PACE_NOTE,
            "supporting_metrics": {},
            "confidence": LOW,
            "confidence_reason": SPIN_PACE_NOTE,
            "assigned": False,
        },
        {
            "role": "Pace specialist",
            "reason": SPIN_PACE_NOTE,
            "supporting_metrics": {},
            "confidence": LOW,
            "confidence_reason": SPIN_PACE_NOTE,
            "assigned": False,
        },
        {
            "role": "Fielding specialist",
            "reason": (
                "Not assigned automatically: Cricsheet only names fielders "
                "on dismissals, which is too thin to claim a specialist "
                "fielding role."
            ),
            "supporting_metrics": {},
            "confidence": LOW,
            "confidence_reason": "Dismissal credits are not a fielding workload.",
            "assigned": False,
        },
    ]

    primary = None
    for c in candidates:
        c["assigned"] = True
        if primary is None:
            primary = c

    return {
        "player": player,
        "primary": primary,
        "candidates": candidates,
        "not_assigned": unavailable,
        "note": None
        if primary
        else "Insufficient data to assign a batting or bowling role.",
    }
