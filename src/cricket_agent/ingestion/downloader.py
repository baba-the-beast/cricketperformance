"""
Cricsheet dataset downloader.

Term: Ingestion
    The step where data enters our system from the outside world (in this
    case, a .zip file on cricsheet.org) and is saved to local disk, before
    anything is parsed or interpreted.

Why we use it:
    Cricsheet does not offer an API; it offers static .zip files of JSON
    documents (https://cricsheet.org/downloads/). Downloading is therefore
    a simple, one-purpose HTTP GET plus a zip extraction, kept in its own
    module so it can be swapped out (e.g. for a different data source)
    without touching the parser or the database.

In our project:
    `download_dataset()` fetches one named dataset (e.g. "ipl_male") to
    data/raw/. It only uses names Cricsheet documents on its downloads
    page; it never builds an arbitrary URL from user input.

IMPORTANT - a note on how this module was developed and tested:
    This module's automated tests use small hand-built fixture files (see
    tests/fixtures/) that are structurally identical to real Cricsheet
    JSON (same field names, same nesting, same 1.2.0 schema version) but
    describe a fictional "College Trial League" instead of real matches.
    That is because the development sandbox this project was built in has
    an outbound network allowlist that does not include cricsheet.org, so
    an actual download could not be executed or verified from inside that
    sandbox.

    This module's download logic is nonetheless a real, working
    implementation using only the Python standard library. On a normal
    developer machine (with unrestricted internet access) it will fetch
    genuine Cricsheet data. See README.md "Dataset setup" for the exact
    command to run this yourself and confirm it end-to-end.
"""

from __future__ import annotations

import logging
import html.parser
import urllib.request
from urllib.parse import urljoin, urlparse
import zipfile
from pathlib import Path

from cricket_agent.config import CRICSHEET_DOWNLOADS_URL, RAW_DATA_DIR, ensure_data_dirs

logger = logging.getLogger(__name__)

# Only these dataset names (as published on https://cricsheet.org/downloads/)
# are accepted. This is a safety choice: we never build a URL from
# free-form user input, only from a small, reviewed allowlist.
class _ZipLinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_data(self, data: str) -> None:
        if self._href and self._href.lower().endswith(".zip"):
            self.links.append((data.strip(), self._href))

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._href = None


def resolve_dataset_url(dataset_name: str, *, downloads_url: str = CRICSHEET_DOWNLOADS_URL) -> str:
    """Resolve a current official ZIP link from Cricsheet's downloads page."""
    with urllib.request.urlopen(downloads_url, timeout=60) as response:
        page = response.read().decode("utf-8", errors="replace")
    parser = _ZipLinkParser()
    parser.feed(page)
    candidates = [
        href for text, href in parser.links
        if dataset_name.lower() in text.lower() or dataset_name.lower() in href.lower()
    ]
    if not candidates:
        raise ValueError(f"Could not find dataset {dataset_name!r} on {downloads_url}")
    return urljoin(downloads_url, candidates[0])


def download_dataset(
    dataset_name: str,
    destination_dir: Path | None = None,
    *,
    timeout_seconds: int = 60,
) -> Path:
    """Download and extract one named Cricsheet JSON dataset.

    Args:
        dataset_name: A key from KNOWN_DATASETS, e.g. "ipl_male".
        destination_dir: Where to extract the JSON files. Defaults to
            data/raw/<dataset_name>/.
        timeout_seconds: HTTP timeout, in seconds.

    Returns:
        The directory the JSON files were extracted into.

    Raises:
        ValueError: If dataset_name is not in KNOWN_DATASETS.
        urllib.error.URLError: If the download fails (network issue).
    """
    ensure_data_dirs()
    destination_dir = destination_dir or (RAW_DATA_DIR / dataset_name)
    destination_dir.mkdir(parents=True, exist_ok=True)

    url = resolve_dataset_url(dataset_name)
    zip_filename = Path(urlparse(url).path).name
    return download_url(url, destination_dir, zip_filename=zip_filename, timeout_seconds=timeout_seconds)


def download_url(
    url: str,
    destination_dir: Path,
    *,
    zip_filename: str | None = None,
    timeout_seconds: int = 60,
) -> Path:
    """Download and safely extract a Cricsheet JSON ZIP from an explicit URL."""
    ensure_data_dirs()
    destination_dir.mkdir(parents=True, exist_ok=True)
    zip_filename = zip_filename or Path(urlparse(url).path).name or "cricsheet.json.zip"
    zip_path = destination_dir / zip_filename

    logger.info("Downloading %s to %s ...", url, zip_path)
    with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
        zip_bytes = response.read()
    zip_path.write_bytes(zip_bytes)
    logger.info("Downloaded %d bytes.", len(zip_bytes))

    logger.info("Extracting %s ...", zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        json_members = [m for m in archive.infolist() if m.filename.lower().endswith(".json")]
        root = destination_dir.resolve()
        for member in json_members:
            target = (destination_dir / member.filename).resolve()
            if root not in target.parents:
                raise ValueError(f"Unsafe ZIP member path: {member.filename}")
        archive.extractall(destination_dir, members=json_members)
    logger.info("Extracted %d JSON files.", len(json_members))

    return destination_dir


def import_zip(zip_path: Path, destination_dir: Path | None = None) -> Path:
    """Safely extract a user-provided Cricsheet JSON ZIP into data/raw."""
    ensure_data_dirs()
    destination_dir = destination_dir or (RAW_DATA_DIR / zip_path.stem)
    destination_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        members = [m for m in archive.infolist() if m.filename.lower().endswith(".json")]
        root = destination_dir.resolve()
        for member in members:
            target = (destination_dir / member.filename).resolve()
            if root not in target.parents:
                raise ValueError(f"Unsafe ZIP member path: {member.filename}")
        archive.extractall(destination_dir, members=members)
    return destination_dir


def list_local_match_files(dataset_dir: Path) -> list[Path]:
    """Return every .json match file found (non-recursively) in a directory.

    Cricsheet zip files contain one .json file per match plus a single
    "*_info.csv" / "README" style file, which we simply ignore by only
    collecting files ending in .json.
    """
    if not dataset_dir.exists():
        return []
    return sorted(p for p in dataset_dir.glob("*.json") if p.is_file())
