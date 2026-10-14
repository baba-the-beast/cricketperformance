from concurrent.futures import ThreadPoolExecutor
import threading

from cricket_agent.agents.coordinator import synthesize
from cricket_agent.agents import runner as runner_mod
from cricket_agent.agents.runner import run_analysis
from cricket_agent.agents.specialists import batting_analyst, bowling_analyst, fielding_analyst
from cricket_agent.analytics.player_profile import build_player_profile
from cricket_agent.memory import load_player_snapshot


def test_batting_analyst_structured_output(populated_db):
    report = batting_analyst(populated_db, "A Sharma")
    assert report["agent"] == "Batting Analyst"
    assert report["metrics"]["runs"] == 29
    assert report["confidence"] == "LOW"
    assert report["evidence"]


def test_fielding_analyst_does_not_invent_dropped_catches(populated_db):
    report = fielding_analyst(populated_db, "A Sharma")
    assert report["agent"] == "Fielding Analyst"
    if not report["metrics"].get("available"):
        assert "dropped" in report["observations"][0]["text"].lower() or "not available" in report["recommendation"].lower() or "fielder" in (report["metrics"].get("note") or "").lower()


def test_coordinator_flags_low_confidence_and_does_not_score():
    bat = {
        "agent": "Batting Analyst",
        "player": "A",
        "metrics": {"runs": 10},
        "observations": [],
        "recommendation": "Small sample.",
        "confidence": "LOW",
        "evidence": [],
    }
    bowl = {
        "agent": "Bowling Analyst",
        "player": "A",
        "metrics": {"wickets": 2},
        "observations": [],
        "recommendation": "Bowling usable.",
        "confidence": "MEDIUM",
        "evidence": [],
    }
    report = synthesize(
        question="Who to pick?",
        players=["A"],
        batting_reports=[bat],
        bowling_reports=[bowl],
        fielding_reports=[{"player": "A", "confidence": "LOW", "observations": [], "metrics": {}}],
        role_reports=[{"player": "A", "primary": {"role": "Bowling all-rounder", "assigned": True, "confidence": "LOW"}}],
    )
    assert "unexplained numeric" in report["note"] or "does not produce an unexplained" in report["note"]
    assert report["conflicts_resolved"]
    assert report["recommendations"][0]["confidence"] == "LOW"


def test_runner_stores_memory_and_session(populated_db):
    result = run_analysis(
        populated_db,
        ["A Sharma", "D Singh"],
        "Which player should be considered for the next selection?",
    )
    assert result["channel"] == "in-process"
    assert result["report"]["question"].startswith("Which player")
    assert result["session"]["coordinator"]
    snap = load_player_snapshot(populated_db, "A Sharma")
    assert snap is not None
    assert snap["batting"]["runs"] == 29


def test_specialists_execute_through_thread_pool_without_changing_results(
    populated_db, monkeypatch
):
    players = ["A Sharma", "D Singh"]
    profiles = [build_player_profile(populated_db, name) for name in players]
    expected_batting = [
        batting_analyst(populated_db, profile["player"], profile) for profile in profiles
    ]
    expected_bowling = [
        bowling_analyst(populated_db, profile["player"], profile) for profile in profiles
    ]
    expected_fielding = [
        fielding_analyst(populated_db, profile["player"], profile) for profile in profiles
    ]

    submitted: list[object] = []
    thread_ids: set[int] = set()
    constructed_workers: list[int] = []

    class RecordingPool(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            constructed_workers.append(kwargs.get("max_workers", args[0] if args else None))
            super().__init__(*args, **kwargs)

        def submit(self, fn, *args, **kwargs):
            submitted.append(fn)
            return super().submit(fn, *args, **kwargs)

    original_worker = runner_mod._run_specialist_on_own_connection

    def tracked_worker(db_path, analyst, profiles_arg):
        thread_ids.add(threading.get_ident())
        return original_worker(db_path, analyst, profiles_arg)

    monkeypatch.setattr(runner_mod, "ThreadPoolExecutor", RecordingPool)
    monkeypatch.setattr(runner_mod, "_run_specialist_on_own_connection", tracked_worker)

    result = run_analysis(
        populated_db,
        players,
        "Which player should be considered for the next selection?",
    )

    assert constructed_workers == [3]
    assert len(submitted) == 3
    assert submitted == [tracked_worker, tracked_worker, tracked_worker]
    assert threading.get_ident() not in thread_ids
    assert result["report"]["batting_analysis"] == expected_batting
    assert result["report"]["bowling_analysis"] == expected_bowling
    assert result["report"]["fielding_analysis"] == expected_fielding
