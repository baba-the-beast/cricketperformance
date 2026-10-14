"""Dismissal-kind rules shared by every query that counts wickets.

Keep these in one place: a rule change (say, how 'retired out' is treated)
must reach batting, bowling, venue and matchup numbers alike.
"""

from __future__ import annotations

from typing import Iterable

# Dismissals credited to the bowler; run-outs and other fielding or
# non-bowler dismissals are excluded.
BOWLER_WICKET_KINDS = (
    "bowled", "caught", "lbw", "stumped", "hit wicket", "caught and bowled",
)

# Recorded in the wickets table but not dismissals: the batter may return.
NON_DISMISSAL_KINDS = ("retired hurt", "retired not out")


def sql_list(values: Iterable[str]) -> str:
    """Render constant strings as a SQL list body: 'a', 'b'."""
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


BOWLER_WICKET_KINDS_SQL = sql_list(BOWLER_WICKET_KINDS)
NON_DISMISSAL_KINDS_SQL = sql_list(NON_DISMISSAL_KINDS)
