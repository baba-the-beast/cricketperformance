"""
Command-line ingestion entry point.

Term: Ingestion pipeline
    The sequence: read JSON files from disk -> parse and validate each one
    -> write the normalized result into SQLite. This module is the glue
    that runs that sequence end-to-end for a whole directory of files.

Why we use it:
    Every other module (parser, database) does one job. Something has to
    call them in order, for every file in a directory, and report what
    succeeded and what was skipped -- that is this module's only job.

In our project:
    Run as `python -m cricket_agent.ingest <directory>`. Used both for the
    real Cricsheet data a user downloads (see README "Dataset setup") and
    for the fixture data used in demo_phase1.py and the test suite.
"""

from __future__ import annotations

import argparse
import logging
import sys
import sqlite3
from pathlib import Path

from cricket_agent.config import DATABASE_PATH, ensure_data_dirs
from cricket_agent.database import connect, create_schema, insert_match, replace_dataset_metadata, clear_database
from cricket_agent.exceptions import DataValidationError
from cricket_agent.ingestion.downloader import download_dataset, download_url, import_zip
from cricket_agent.ingestion.parser import parse_match_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def ingest_directory(source_dir: Path, db_path: Path = DATABASE_PATH) -> tuple[int, int]:
    """Parse and insert every .json file in source_dir into the database.

    Returns:
        (succeeded_count, failed_count)
    """
    json_files = sorted(source_dir.rglob("*.json"))
    if not json_files:
        logger.warning("No .json files found in %s.", source_dir)
        return (0, 0)

    conn = connect(db_path)
    create_schema(conn)

    succeeded = 0
    failed = 0
    try:
        for path in json_files:
            logger.info("Parsing %s ...", path.name)
            try:
                match = parse_match_file(path)
            except DataValidationError as exc:
                logger.error("Skipping %s: %s", path.name, exc)
                failed += 1
                continue

            insert_match(conn, match)
            logger.info(
                "Inserted match %s (%d innings).",
                match.match_id,
                len(match.innings),
            )
            succeeded += 1
    finally:
        conn.close()

    return (succeeded, failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest a directory of Cricsheet-format JSON match files into SQLite."
    )
    parser.add_argument("legacy_source_dir", nargs="?", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--source", choices=("fixtures", "ipl", "custom"), help="Dataset source to ingest.")
    parser.add_argument("--path", type=Path, help="ZIP file or directory for --source custom.")
    parser.add_argument("--url", help="Optional ZIP URL for --source custom.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DATABASE_PATH,
        help=f"SQLite database path (default: {DATABASE_PATH}).",
    )
    args = parser.parse_args(argv)

    ensure_data_dirs()
    if args.legacy_source_dir:
        source_dir = args.legacy_source_dir
        dataset_name = "College Demo Dataset" if "fixture" in source_dir.name.lower() else source_dir.name
        is_target_demo = "fixture" in source_dir.name.lower()
    elif args.source == "fixtures":
        source_dir = Path(__file__).resolve().parents[2] / "data" / "raw" / "smoke_fixtures"
        dataset_name = "College Demo Dataset"
        is_target_demo = True
    elif args.source == "ipl":
        source_dir = download_dataset("ipl")
        dataset_name = "Cricsheet"
        is_target_demo = False
    elif args.source == "custom" and args.path:
        source_dir = import_zip(args.path) if args.path.suffix.lower() == ".zip" else args.path
        dataset_name = "Cricsheet"
        is_target_demo = False
    elif args.source == "custom" and args.url:
        source_dir = download_url(args.url, Path(__file__).resolve().parents[2] / "data" / "raw" / "custom")
        dataset_name = "Cricsheet"
        is_target_demo = False
    else:
        parser.error("choose --source fixtures|ipl|custom --path <zip-or-directory>, or provide a directory")

    conn = connect(args.db_path)
    try:
        row = conn.execute("SELECT is_demo FROM dataset_metadata WHERE metadata_key = 'current'").fetchone()
        is_currently_demo = bool(row["is_demo"]) if row else False
        if row and is_currently_demo != is_target_demo:
            clear_database(conn)
    except sqlite3.OperationalError:
        pass # Table doesn't exist yet
    finally:
        conn.close()

    succeeded, failed = ingest_directory(source_dir, args.db_path)
    if succeeded:
        conn = connect(args.db_path)
        create_schema(conn)
        replace_dataset_metadata(
            conn,
            dataset_name=dataset_name,
            source="tests/fixtures" if is_target_demo else (args.url or str(source_dir)),
            is_demo=is_target_demo,
        )
        conn.close()
    logger.info("Ingestion complete: %d succeeded, %d failed.", succeeded, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
