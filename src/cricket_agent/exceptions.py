"""
Custom exceptions for the Cricket Team Performance Agent.

Term: Custom exception
    A named error type (a Python class) that describes exactly what went
    wrong, instead of letting the caller catch a generic `Exception` or
    look at raw stack traces.

Why we use it:
    Section 19 of the project brief is explicit: normal, expected problems
    (an unknown match ID, an unknown player, a replay that has ended) must
    never surface as confusing tracebacks. They are not bugs; they are
    ordinary outcomes a caller needs to be able to handle. Giving each one
    its own class lets the MCP layer catch them precisely and translate
    them into a clean, structured error message for a client instead of a
    Python traceback.

In our project:
    `tools/cricket_tools.py` raises these. `mcp_server.py` catches them and
    turns them into MCP tool error results.
"""

from __future__ import annotations


class CricketAgentError(Exception):
    """Base class for every error this project raises on purpose."""


class MatchNotFoundError(CricketAgentError):
    """Raised when a requested match_id does not exist in the database."""

    def __init__(self, match_id: str) -> None:
        super().__init__(f"No match found with match_id={match_id!r}.")
        self.match_id = match_id


class PlayerNotFoundError(CricketAgentError):
    """Raised when a requested player has no deliveries in the database."""

    def __init__(self, player_name: str) -> None:
        super().__init__(
            f"No data found for player {player_name!r}. Check spelling and "
            "capitalization; player names must match the Cricsheet name "
            "exactly (e.g. 'MS Dhoni')."
        )
        self.player_name = player_name


class InvalidRangeError(CricketAgentError):
    """Raised when a statistics range request does not make sense.

    Examples: last_n_matches <= 0, or start_match after end_match.
    """


class ReplayEndedError(CricketAgentError):
    """Raised by get_next_ball() when there are no more deliveries."""

    def __init__(self, match_id: str) -> None:
        super().__init__(
            f"Replay for match_id={match_id!r} has reached the end of the "
            "available deliveries."
        )
        self.match_id = match_id


class ReplaySessionNotFoundError(CricketAgentError):
    """Raised when get_next_ball() is called with an unknown session id."""

    def __init__(self, session_id: str) -> None:
        super().__init__(
            f"No active replay session with session_id={session_id!r}. "
            "Start one first."
        )
        self.session_id = session_id


class ReplayAtStartError(CricketAgentError):
    """Raised by get_previous_ball() when the replay is already at the first delivery."""

    def __init__(self, match_id: str) -> None:
        super().__init__(
            f"Replay for match_id={match_id!r} is already at the first "
            "delivery; there is no previous ball."
        )
        self.match_id = match_id


class InsufficientDataError(CricketAgentError):
    """Raised when a requested analysis cannot be computed from stored data."""


class ReplayAtStartError(CricketAgentError):
    """Raised by get_previous_ball() when no earlier delivery is available."""

    def __init__(self, match_id: str) -> None:
        super().__init__(
            f"Replay for match_id={match_id!r} is already at the first "
            "delivery; there is no previous ball."
        )
        self.match_id = match_id


class InsufficientDataError(CricketAgentError):
    """Raised when an analysis cannot be computed from the available sample."""


class DataValidationError(CricketAgentError):
    """Raised by the parser when a Cricsheet file is missing required data.

    This is intentionally distinct from a JSON syntax error: this means the
    JSON parsed fine, but the *cricket* data inside it does not satisfy the
    minimum structure this project depends on (see docs/data_model.md).
    """
