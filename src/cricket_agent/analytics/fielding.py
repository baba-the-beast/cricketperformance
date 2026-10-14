"""Fielding credits that Cricsheet actually records: catch / stumping / run-out names."""

from __future__ import annotations

import sqlite3
from typing import Any

SMALL_MATCH_WINDOW = 50
"""Up to this many matches, look fielding credits up match by match."""


def fielding_credits(
    conn: sqlite3.Connection, player: str, match_ids: list[str] | None = None
) -> dict[str, Any]:
    """Count wicket credits where this player's name appears in `fielders`.

    Cricsheet does not record dropped catches, fielding positions, or runs
    saved. Those are not estimated here. match_ids=None means the whole
    career; an empty list is an empty window and returns no credits.
    """
    # CROSS JOIN pins the join order. For a handful of matches, walk those
    # matches' deliveries; for a whole career it is far cheaper to scan the
    # small wickets table once than to probe every delivery of every match.
    if match_ids and len(match_ids) <= SMALL_MATCH_WINDOW:
        tables = "deliveries CROSS JOIN wickets"
    else:
        tables = "wickets CROSS JOIN deliveries"
    # LIKE is only a prefilter; the exact name match happens below.
    sql = f"""
        SELECT wickets.kind, wickets.player_out, wickets.fielders,
               deliveries.match_id
        FROM {tables} ON wickets.delivery_pk = deliveries.delivery_pk
        WHERE wickets.fielders LIKE '%' || ? || '%'
    """
    params: list[Any] = [player]
    if match_ids:
        placeholders = ",".join("?" for _ in match_ids)
        sql += f" AND deliveries.match_id IN ({placeholders})"
        params.extend(match_ids)

    rows = [] if match_ids is not None and not match_ids else conn.execute(sql, params).fetchall()
    events: list[dict[str, Any]] = []
    catches = stumpings = run_outs = other = 0
    for row in rows:
        names = [n.strip() for n in (row["fielders"] or "").split(",") if n.strip()]
        if player not in names:
            continue
        kind = (row["kind"] or "").lower()
        events.append(
            {
                "match_id": row["match_id"],
                "kind": row["kind"],
                "player_out": row["player_out"],
            }
        )
        if kind == "caught" or kind == "caught and bowled":
            # caught-and-bowled credits the bowler, who may also appear as fielder
            catches += 1
        elif kind == "stumped":
            stumpings += 1
        elif kind == "run out":
            run_outs += 1
        else:
            other += 1

    available = bool(events)
    return {
        "player": player,
        "catches": catches,
        "stumpings": stumpings,
        "run_outs": run_outs,
        "other_credits": other,
        "total_credits": len(events),
        "events": events,
        "available": available,
        "note": (
            None
            if available
            else (
                "Not available in the current dataset: no wicket entries "
                f"credit {player!r} as a fielder. Cricsheet does not record "
                "dropped catches or fielding opportunities."
            )
        ),
    }
