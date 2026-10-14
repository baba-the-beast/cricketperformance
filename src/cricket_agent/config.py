"""
Central configuration for the Cricket Team Performance Agent.

Term: Configuration module
    A single place that defines where files live and what the small set of
    tunable constants are, so that no other module hard-codes a path.

Why we use it:
    The project must run unmodified on both Windows and macOS/Linux. Paths
    like "/Users/name/project" or "C:\\Users\\name\\project" are not
    portable. `pathlib.Path` builds paths in a way that works on both, and
    keeping every path derived from one BASE_DIR means we never need a
    second place to fix if the project folder moves.

In our project:
    Every other module imports paths from here instead of writing its own
    literal path strings. `DATABASE_PATH` can additionally be overridden
    with the `CRICKET_DB_PATH` environment variable -- this exists mainly
    so automated tests (see tests/test_mcp_stdio.py) can point a real,
    separately-launched server subprocess at a temporary test database
    without touching the project's real data/processed/cricket.db.
"""

from __future__ import annotations

import os
from pathlib import Path

# BASE_DIR is the project root, calculated relative to this file so it is
# correct no matter which directory the user launches Python from, and on
# any operating system.
#   config.py -> src/cricket_agent/ -> src/ -> <project root>
BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent

DATA_DIR: Path = BASE_DIR / "data"
RAW_DATA_DIR: Path = DATA_DIR / "raw"
PROCESSED_DATA_DIR: Path = DATA_DIR / "processed"

# The single SQLite file that holds all normalized match data.
# Override with the CRICKET_DB_PATH environment variable if set.
DATABASE_PATH: Path = Path(os.environ.get("CRICKET_DB_PATH", str(PROCESSED_DATA_DIR / "cricket.db")))

# Cricsheet download configuration.
# We only ever download a named .zip file that Cricsheet documents at
# https://cricsheet.org/downloads/ -- never an arbitrary path -- to avoid
# depending on undocumented URLs.
CRICSHEET_BASE_URL: str = "https://cricsheet.org/downloads"
CRICSHEET_DOWNLOADS_URL: str = os.environ.get(
    "CRICSHEET_DOWNLOADS_URL", f"{CRICSHEET_BASE_URL}/"
)

# The JSON format version this project was built and tested against.
# See docs/data_model.md for what happens if a newer file format appears.
SUPPORTED_CRICSHEET_JSON_VERSION: str = "1.2.0"


def ensure_data_dirs() -> None:
    """Create data/raw and data/processed if they do not already exist.

    Safe to call repeatedly (idempotent) and safe to call before the
    directories exist, e.g. on a fresh clone of the repository.
    """
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
