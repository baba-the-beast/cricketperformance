"""In-process call log so the UI can show real tool usage without faking MCP."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_MAX = 50
_LOG: list[dict[str, Any]] = []


def record_call(name: str, *, channel: str, arguments: dict[str, Any] | None = None) -> None:
    _LOG.append(
        {
            "tool": name,
            "channel": channel,
            "arguments": arguments or {},
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    if len(_LOG) > _MAX:
        del _LOG[: len(_LOG) - _MAX]


def recent_calls(limit: int = 20) -> list[dict[str, Any]]:
    return list(_LOG[-limit:])
