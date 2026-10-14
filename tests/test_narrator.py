"""The optional LLM explanation step: grounding check and every fallback path.

The model call is replaced by a stub, so these tests need no network or API key.
"""

import pytest

from cricket_agent.agents import narrator
from cricket_agent.agents.runner import run_analysis

REPORT = {
    "question": "Who should open?",
    "context": {"opponent": None, "last_n_matches": None, "available_players": ["A Sharma", "D Singh"]},
    "selection_summary": "A Sharma currently has the stronger evidence-backed case (MEDIUM confidence).",
    "recommendations": [
        {"player": "A Sharma", "role": "Top-order batter", "role_reason": "Bats in overs 0-5",
         "confidence": "MEDIUM", "notes": ["412 runs at a strike rate of 131.4."], "evidence": {}},
        {"player": "D Singh", "role": None, "role_reason": None,
         "confidence": "LOW", "notes": ["No batting or bowling sample."], "evidence": {}},
    ],
    "conflicts_resolved": [],
    "risks_flags": ["D Singh: LOW CONFIDENCE — specialist reports are based on a small sample."],
    "team_balance": {},
}


@pytest.fixture(autouse=True)
def llm_on(monkeypatch):
    monkeypatch.setenv("CRICKET_LLM", "on")


def stub_model(monkeypatch, text, stop_reason="end_turn", model="claude-opus-5-5"):
    calls = []

    def fake(evidence_json, question):
        calls.append((evidence_json, question))
        return text, model, stop_reason

    monkeypatch.setattr(narrator, "_call_model", fake)
    return calls


def test_grounded_explanation_is_used(monkeypatch):
    calls = stub_model(
        monkeypatch,
        "A Sharma (MEDIUM confidence) is the stronger case: 412 runs at a strike rate of 131.4.\n"
        "- D Singh is LOW confidence; the sample is small.",
    )
    out = narrator.narrate(REPORT)
    assert out["source"] == "llm"
    assert out["model"] == "claude-opus-5-5"
    assert "412 runs" in out["text"]
    # The model only ever sees the coordinator's evidence, never the database.
    evidence_json, question = calls[0]
    assert question == "Who should open?"
    assert "412 runs" in evidence_json


def test_invented_statistic_is_rejected(monkeypatch):
    stub_model(monkeypatch, "A Sharma averages 57.3 and scored 1250 runs last season.")
    out = narrator.narrate(REPORT)
    assert out["source"] == "rules"
    assert out["text"] == REPORT["selection_summary"]
    assert "57.3" in out["note"] and "1250" in out["note"]


def test_small_counts_are_not_treated_as_statistics():
    assert narrator.unsupported_numbers("Pick 2 of these 11 players.", "{}") == []
    assert narrator.unsupported_numbers("He hit 99.", '{"runs": 99}') == []
    assert narrator.unsupported_numbers("He hit 98.", '{"runs": 99}') == ["98"]


def test_refusal_falls_back_to_rules(monkeypatch):
    stub_model(monkeypatch, "", stop_reason="refusal")
    out = narrator.narrate(REPORT)
    assert out["source"] == "rules"
    assert "declined" in out["note"]


def test_truncated_answer_falls_back_to_rules(monkeypatch):
    stub_model(monkeypatch, "A Sharma is the", stop_reason="max_tokens")
    assert narrator.narrate(REPORT)["source"] == "rules"


def test_missing_credentials_fall_back_to_rules(monkeypatch):
    def no_credentials(evidence_json, question):
        raise TypeError("Could not resolve authentication method")

    monkeypatch.setattr(narrator, "_call_model", no_credentials)
    out = narrator.narrate(REPORT)
    assert out["source"] == "rules"
    assert "ANTHROPIC_API_KEY" in out["note"]


def test_switch_off_skips_the_model(monkeypatch):
    monkeypatch.setenv("CRICKET_LLM", "off")
    calls = stub_model(monkeypatch, "should not be used")
    out = narrator.narrate(REPORT)
    assert out["source"] == "rules"
    assert calls == []


def test_agent_run_attaches_an_explanation(populated_db, monkeypatch):
    stub_model(monkeypatch, "A Sharma is the stronger case.")
    result = run_analysis(populated_db, ["A Sharma", "D Singh"], "Who should play?")
    story = result["report"]["narrative"]
    assert story["source"] == "llm"
    # The deterministic verdict is untouched by the explanation step.
    assert result["report"]["selection_summary"]
    assert result["session"]["coordinator"]["narrative"] == story
