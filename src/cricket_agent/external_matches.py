"""Optional upcoming-match provider boundary."""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen
from dataclasses import dataclass
from typing import Protocol
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ExternalMatch:
    match_id: str
    team_a: str
    team_b: str
    date: str | None = None
    time: str | None = None
    venue: str | None = None
    format: str | None = None
    competition: str | None = None
    source: str = "unknown"
    status: str | None = None


class ExternalMatchProvider(Protocol):
    name: str

    def upcoming(self) -> list[ExternalMatch]:
        """Return only externally retrieved upcoming matches."""


class UnavailableMatchProvider:
    name = "unavailable"

    def upcoming(self) -> list[ExternalMatch]:
        return []


class CricketDataMatchProvider:
    """Near-live fixture feed from CricketData.org (formerly CricAPI)."""

    name = "CricketData.org"
    base_url = "https://api.cricapi.com/v1"
    cache_seconds = 3600
    failure_cache_seconds = 60

    def __init__(self, api_key: str, *, timeout: float = 8.0) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self._cache: list[ExternalMatch] | None = None
        self._cache_expires = 0.0
        # After a failed fetch, re-raise the same error for a short while
        # instead of calling the API again on every request.
        self._failure: Exception | None = None
        self._failure_expires = 0.0
        self._lock = threading.Lock()

    def _get_rows(self, endpoint: str) -> list[dict]:
        query = urlencode({"apikey": self.api_key, "offset": 0})
        request_url = f"{self.base_url}/{endpoint}?{query}"
        with urlopen(request_url, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("status") != "success":
            info = payload.get("info") or {}
            message = info.get("results", "CricketData.org request failed")
            raise RuntimeError(str(message))
        rows = payload.get("data") or []
        return rows if isinstance(rows, list) else []

    @staticmethod
    def _to_match(row: dict, *, live: bool = False) -> ExternalMatch | None:
        teams = row.get("teams") or []
        name = row.get("name") or ""
        if len(teams) < 2 and " vs " in name:
            teams = [team.strip() for team in name.split(" vs ", 1)]
        if len(teams) < 2:
            return None

        date_time = None
        raw_date_time = row.get("dateTimeGMT")
        if raw_date_time:
            try:
                date_time = datetime.fromisoformat(str(raw_date_time).replace("Z", "+00:00"))
                if date_time.tzinfo is None:
                    date_time = date_time.replace(tzinfo=timezone.utc)
                date_time = date_time.astimezone(ZoneInfo("Asia/Kolkata"))
            except ValueError:
                date_time = None

        raw_date = row.get("date")
        match_date = date_time.date().isoformat() if date_time else (str(raw_date)[:10] if raw_date else None)
        match_time = date_time.strftime("%I:%M %p IST") if date_time else None
        return ExternalMatch(
            match_id=str(row.get("id") or name),
            team_a=str(teams[0]),
            team_b=str(teams[1]),
            date=match_date,
            time=match_time,
            venue=row.get("venue"),
            format=row.get("matchType"),
            competition=row.get("seriesName") or row.get("series"),
            source="CricketData.org (near-live)",
            status=row.get("status") or ("Live" if live else "Scheduled"),
        )

    def _fetch_upcoming(self) -> dict[str, ExternalMatch]:
        today = date.today().isoformat()
        matches_by_id: dict[str, ExternalMatch] = {}
        for row in self._get_rows("matches"):
            match = self._to_match(row)
            if match and match.date and match.date >= today:
                matches_by_id[match.match_id] = match
        for row in self._get_rows("currentMatches"):
            # currentMatches also lists recently finished games. No date
            # filter here: a Test in progress started days ago.
            if row.get("matchEnded"):
                continue
            match = self._to_match(row, live=True)
            if match:
                matches_by_id[match.match_id] = match
        return matches_by_id

    def upcoming(self) -> list[ExternalMatch]:
        # Held across the fetch on purpose: concurrent requests wait for one
        # API call instead of each making their own.
        with self._lock:
            now = datetime.now().timestamp()
            if self._cache is not None and now < self._cache_expires:
                return list(self._cache)
            if self._failure is not None and now < self._failure_expires:
                raise self._failure

            try:
                matches_by_id = self._fetch_upcoming()
            except (OSError, RuntimeError, ValueError) as exc:
                self._failure = exc
                self._failure_expires = now + self.failure_cache_seconds
                raise
            self._failure = None

            self._cache = sorted(
                matches_by_id.values(),
                key=lambda match: (match.date or "", match.time or "", match.match_id),
            )
            self._cache_expires = now + self.cache_seconds
            return list(self._cache)


_provider_lock = threading.Lock()
_provider_key: str | None = None
_provider_instance: CricketDataMatchProvider | None = None


def get_upcoming_provider() -> ExternalMatchProvider:
    global _provider_instance, _provider_key
    api_key = os.environ.get("CRICKETDATA_API_KEY", "").strip()
    if not api_key:
        return UnavailableMatchProvider()
    with _provider_lock:
        if _provider_instance is None or _provider_key != api_key:
            _provider_instance = CricketDataMatchProvider(api_key)
            _provider_key = api_key
        return _provider_instance