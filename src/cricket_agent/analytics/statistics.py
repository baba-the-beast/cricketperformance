"""
Deterministic cricket statistics engine.

Term: Deterministic function
    A function that, given the same input, always produces exactly the
    same output -- no randomness, no LLM call, no external service. Every
    function in this module is deterministic: the same rows from
    deliveries/wickets always produce the same averages and rates.

Why we use it:
    Section 25 of the project brief ("No Fake AI") requires that Phase 1
    contain verified, explainable arithmetic before any autonomous
    reasoning is introduced. A future agent should be able to trust these
    numbers precisely because they come from a fixed, auditable formula,
    not from a model's guess.

In our project:
    `compute_batting_stats()` and `compute_bowling_stats()` are called by
    tools/cricket_tools.py's compute_stats(). Both take a list of
    sqlite3.Row-like delivery dictionaries already filtered to the
    relevant player and match range -- this module has no SQL in it, so it
    can be unit tested with plain Python dictionaries (see
    tests/test_statistics.py).

Cricket-specific rules this module encodes (see also docs/tools.md):
    - Strike rate = (runs off the bat / balls faced) * 100.
      Balls faced excludes wides, but INCLUDES no-balls (see
      models.Delivery.is_faced_by_batter for the cricket-law justification).
    - Batting average = runs off the bat / times dismissed. If a player was
      never dismissed in the range ("not out" every innings), the average
      is undefined by convention, NOT infinite and NOT zero. We return
      `None` for this and the caller must not silently coerce it to 0.
    - Overs bowled are reported as "completed overs.balls" (cricket
      notation, e.g. 4.3 means 4 completed overs plus 3 balls) computed
      from legal deliveries only (wides/no-balls do not count as an over
      progressing).
    - Economy rate = runs conceded / overs bowled, where "overs bowled" is
      the true decimal number of overs (legal deliveries / 6), NOT the
      cricket-notation string.
    - Bowling average = runs conceded / wickets taken. If a bowler took no
      wickets in the range, this is undefined (`None`), not infinite.
    - Bowling strike rate = legal deliveries bowled / wickets taken. Same
      zero-wicket handling as bowling average.
    - Runs conceded by a bowler include wides, no-balls, and any runs off
      the bat, but explicitly EXCLUDE byes and leg-byes (those are not the
      bowler's fault under the laws of cricket).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

DeliveryRow = Mapping[str, Any]
"""A delivery, expressed as a mapping with database column names as keys.

Using a Mapping instead of the models.Delivery dataclass directly means
this module works equally well with sqlite3.Row objects (which support
[]-style access) and with plain dicts in tests, without a conversion step.
"""


@dataclass(frozen=True)
class BattingStats:
    innings: int
    runs: int
    balls_faced: int
    dismissals: int
    fours: int
    sixes: int
    average: float | None
    strike_rate: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "innings": self.innings,
            "runs": self.runs,
            "balls_faced": self.balls_faced,
            "dismissals": self.dismissals,
            "not_outs": self.innings - self.dismissals,
            "fours": self.fours,
            "sixes": self.sixes,
            "average": self.average,
            "strike_rate": self.strike_rate,
        }


@dataclass(frozen=True)
class BowlingStats:
    legal_deliveries: int
    runs_conceded: int
    wickets: int
    dot_balls: int
    economy: float | None
    average: float | None
    strike_rate: float | None

    @property
    def overs_display(self) -> str:
        """Overs in cricket notation, e.g. 18 legal balls -> '3.0'."""
        completed_overs, remaining_balls = divmod(self.legal_deliveries, 6)
        return f"{completed_overs}.{remaining_balls}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "legal_deliveries": self.legal_deliveries,
            "overs_display": self.overs_display,
            "runs_conceded": self.runs_conceded,
            "wickets": self.wickets,
            "dot_balls": self.dot_balls,
            "economy": self.economy,
            "average": self.average,
            "strike_rate": self.strike_rate,
        }


def compute_batting_stats(
    deliveries_faced: Sequence[DeliveryRow],
    dismissals_of_player: int,
    innings_count: int,
) -> BattingStats:
    """Compute batting statistics from every delivery a player faced.

    Args:
        deliveries_faced: All deliveries where this player was the
            `batter` (wides excluded by the caller's SQL, per convention
            -- see docs/tools.md), across the requested match range.
        dismissals_of_player: How many of those innings ended in the
            player being out (from the wickets table).
        innings_count: How many separate innings the player batted in,
            across the requested match range.
    """
    runs = sum(int(d["runs_batter"]) for d in deliveries_faced)
    balls_faced = len(deliveries_faced)
    fours = sum(1 for d in deliveries_faced if int(d["runs_batter"]) == 4)
    sixes = sum(1 for d in deliveries_faced if int(d["runs_batter"]) == 6)

    average = (
        runs / dismissals_of_player if dismissals_of_player > 0 else None
    )
    strike_rate = (runs / balls_faced * 100) if balls_faced > 0 else None

    return BattingStats(
        innings=innings_count,
        runs=runs,
        balls_faced=balls_faced,
        dismissals=dismissals_of_player,
        fours=fours,
        sixes=sixes,
        average=average,
        strike_rate=strike_rate,
    )


def compute_bowling_stats(
    deliveries_bowled: Sequence[DeliveryRow],
    wickets_taken: int,
) -> BowlingStats:
    """Compute bowling statistics from every delivery a player bowled.

    Args:
        deliveries_bowled: All deliveries where this player was the
            `bowler`, across the requested match range.
        wickets_taken: Wickets credited to this bowler (run-outs are
            excluded by the caller's SQL -- a run-out is not credited to
            the bowler under the laws of cricket).
    """
    def _is_wide(d: DeliveryRow) -> bool:
        return int(d["runs_wides"]) > 0

    def _is_noball(d: DeliveryRow) -> bool:
        return int(d["runs_noballs"]) > 0

    legal_deliveries = sum(
        1 for d in deliveries_bowled if not _is_wide(d) and not _is_noball(d)
    )
    # Runs conceded: everything except byes, leg-byes, and penalty runs
    # (none of those are the bowler's responsibility). Subtracting the
    # exact byes/legbyes/penalty amounts -- rather than the whole
    # 'extras' total -- correctly handles the rare delivery that combines
    # two extra types, e.g. byes taken after a wide has already been
    # called.
    runs_conceded = sum(
        int(d["runs_total"])
        - int(d["runs_byes"])
        - int(d["runs_legbyes"])
        - int(d["runs_penalty"])
        for d in deliveries_bowled
    )
    dot_balls = sum(
        1
        for d in deliveries_bowled
        if not _is_wide(d) and not _is_noball(d) and int(d["runs_total"]) == 0
    )

    overs_bowled_decimal = legal_deliveries / 6
    economy = (
        runs_conceded / overs_bowled_decimal if overs_bowled_decimal > 0 else None
    )
    average = runs_conceded / wickets_taken if wickets_taken > 0 else None
    strike_rate = (
        legal_deliveries / wickets_taken if wickets_taken > 0 else None
    )

    return BowlingStats(
        legal_deliveries=legal_deliveries,
        runs_conceded=runs_conceded,
        wickets=wickets_taken,
        dot_balls=dot_balls,
        economy=economy,
        average=average,
        strike_rate=strike_rate,
    )
