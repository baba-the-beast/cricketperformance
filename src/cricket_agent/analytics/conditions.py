"""Optional match-conditions boundary: manual pitch/weather notes, or a future live provider.

Term: Conditions
    Anything about the day of the match that Cricsheet's historical
    ball-by-ball data cannot tell you: today's weather forecast, and any
    pitch report a coach/analyst has from the ground staff. This project
    has no licensed live weather or pitch-report API wired in, and it will
    not fabricate one.

Design, matching external_matches.py:
    A caller may pass conditions in manually (a coach types "overcast,
    heavy dew expected" into the dashboard). If no manual input is given
    and no provider is configured, `get_conditions_provider()` returns an
    `UnavailableConditionsProvider`, and every consumer must display that
    honestly rather than inventing a forecast.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class MatchConditions:
    pitch_note: str | None = None
    weather_note: str | None = None
    source: str = "manual"


class ConditionsProvider(Protocol):
    name: str

    def for_venue(self, venue: str) -> MatchConditions | None:
        """Return live conditions for a venue, or None if unavailable."""


class UnavailableConditionsProvider:
    name = "unavailable"

    def for_venue(self, venue: str) -> MatchConditions | None:
        return None


def get_conditions_provider() -> ConditionsProvider:
    return UnavailableConditionsProvider()


def resolve_conditions(
    *,
    venue: str | None,
    pitch_note: str | None = None,
    weather_note: str | None = None,
) -> dict[str, Any]:
    """Combine manual input (if any) with the configured provider (if any).

    Manual input always wins over a provider, since a coach's own report
    from the ground is more current than anything this project can fetch.
    Returns a plain dict (not the dataclass) so it serialises directly to
    JSON for the dashboard and the coordinator's report.
    """
    if pitch_note or weather_note:
        return {
            "available": True,
            "source": "manual",
            "pitch_note": pitch_note,
            "weather_note": weather_note,
        }

    provider = get_conditions_provider()
    live = provider.for_venue(venue) if venue else None
    if live is not None:
        return {
            "available": True,
            "source": provider.name,
            "pitch_note": live.pitch_note,
            "weather_note": live.weather_note,
        }

    return {
        "available": False,
        "source": provider.name,
        "pitch_note": None,
        "weather_note": None,
        "note": (
            "No live pitch/weather provider is configured and no manual note was "
            "supplied. The recommendation below falls back to historical venue "
            "scoring patterns only; it does not guess today's forecast."
        ),
    }
