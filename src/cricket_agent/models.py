"""
Data models for the Cricket Team Performance Agent (Phase 1).

These are plain, small dataclasses. They exist to give every layer of the
system (parser -> database -> analytics -> tools) a single, agreed-upon
shape for "what a match looks like" and "what a delivery looks like",
instead of passing raw dictionaries around and hoping the keys line up.

Design choice: dataclasses instead of a full ORM.
    A college-project SQLite database with ~6 tables does not need an ORM
    layer such as SQLAlchemy. Dataclasses give us type hints and clarity
    without hiding the actual SQL from us (or from our professor).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Wicket:
    """A single dismissal that happened on a delivery.

    Cricsheet allows more than one wicket entry per delivery in extremely
    rare cases (e.g. a batter retires hurt on the same ball as a stumping).
    We store wickets as their own rows, one per dismissal, so that rare
    case is handled without any special casing.
    """

    kind: str
    player_out: str
    fielders: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Delivery:
    """One ball bowled, normalized from Cricsheet's `deliveries` entries.

    `over_number` and `ball_in_over` together with `actual_delivery` let us
    reconstruct both "legal delivery order" (for strike rate / economy) and
    "chronological order" (for get_next_ball() replay, which must include
    wides and no-balls exactly as they were bowled).
    """

    match_id: str
    innings_index: int
    over_number: int
    ball_in_over: int
    actual_delivery: str
    batter: str
    bowler: str
    non_striker: str
    runs_batter: int
    runs_total: int
    # Extras are stored as their individual amounts (not just flags) so
    # that a rare delivery combining two extra types -- for example byes
    # taken after the umpire has already called a wide -- can still be
    # split correctly between "charged to the bowler" (wides, no-balls)
    # and "not charged to the bowler" (byes, leg-byes). See
    # analytics/statistics.py for where this distinction matters.
    runs_wides: int = 0
    runs_noballs: int = 0
    runs_byes: int = 0
    runs_legbyes: int = 0
    runs_penalty: int = 0
    wickets: tuple[Wicket, ...] = field(default_factory=tuple)

    @property
    def is_wide(self) -> bool:
        return self.runs_wides > 0

    @property
    def is_noball(self) -> bool:
        return self.runs_noballs > 0

    @property
    def is_bye(self) -> bool:
        return self.runs_byes > 0

    @property
    def is_legbye(self) -> bool:
        return self.runs_legbyes > 0

    @property
    def runs_extras(self) -> int:
        return (
            self.runs_wides
            + self.runs_noballs
            + self.runs_byes
            + self.runs_legbyes
            + self.runs_penalty
        )

    @property
    def is_legal_delivery(self) -> bool:
        """A legal delivery is one that counts towards the 6-ball over.

        Wides and no-balls do not count towards the bowler's over, per the
        laws of cricket. This matters for strike rate (balls faced by a
        batter excludes wides, but a no-ball IS faced by the batter) and
        for economy rate (an over must contain 6 legal deliveries).
        """
        return not (self.is_wide or self.is_noball)

    @property
    def is_faced_by_batter(self) -> bool:
        """Whether this delivery counts as a ball "faced" for strike rate.

        Per common cricket scoring convention: wides are NOT faced by the
        batter (the batter never gets a genuine attempt at the ball).
        No-balls ARE faced by the batter (they can still hit it for runs).
        """
        return not self.is_wide

    @property
    def runs_charged_to_bowler(self) -> int:
        """Runs that count against the bowler's figures.

        Everything except byes, leg-byes, and penalty runs (none of which
        are the bowler's responsibility under the laws of cricket).
        """
        return self.runs_total - self.runs_byes - self.runs_legbyes - self.runs_penalty


@dataclass(frozen=True)
class Innings:
    """One team's batting innings within a match."""

    match_id: str
    innings_index: int
    batting_team: str
    deliveries: tuple[Delivery, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class MatchInfo:
    """Normalized match-level metadata (the Cricsheet `info` section)."""

    match_id: str
    teams: tuple[str, ...]
    dates: tuple[str, ...]
    match_type: str
    gender: str
    venue: str | None
    city: str | None
    season: str | None
    event_name: str | None
    winner: str | None
    outcome_summary: str | None
    player_of_match: tuple[str, ...] = field(default_factory=tuple)
    # Roster from Cricsheet info.players when present. Identity remains the
    # name string used in deliveries; this is only a convenience roster.
    players_by_team: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Cricsheet's optional registry mapping preserves official player IDs
    # without changing the existing name-based public APIs.
    player_registry: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Match:
    """A fully parsed Cricsheet match: metadata plus every innings."""

    info: MatchInfo
    innings: tuple[Innings, ...] = field(default_factory=tuple)

    @property
    def match_id(self) -> str:
        return self.info.match_id
