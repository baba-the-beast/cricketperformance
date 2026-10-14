"""Optional LLM step: Claude explains the coordinator's verdict in plain English.

Division of labour (the reason this module exists):

* The specialists and the coordinator are deterministic. They compute every
  number from stored deliveries and decide the ordering and confidence.
* Claude only *explains* that result for a coach. It receives the coordinator's
  evidence as JSON and writes a short narrative from it. It never sees the raw
  database and cannot change the verdict.

Because a language model can state a statistic that was never computed, the
narrative passes a grounding check before it is shown: every number in it must
appear in the evidence it was given. If the check fails, if no API credentials
are configured, or if the call fails for any reason, the report falls back to
the rule-based summary, and the response says which path was taken and why.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from cricket_agent.observability import record_call

logger = logging.getLogger(__name__)

MODEL = os.environ.get("CRICKET_LLM_MODEL", "claude-opus-5-5")

SYSTEM_PROMPT = """You are the coordinator agent in a cricket selection-support system used by coaches.

Batting, bowling and fielding analyst agents have already reviewed a shortlist of players, and a deterministic coordinator has ordered them and assigned each a confidence level (HIGH, MEDIUM or LOW). You receive that result as JSON. Your job is to explain it to the coach in plain English.

Rules:
- Use only facts in the EVIDENCE JSON. Do not add players, matches, opponents, venues or statistics that are not there, and do not use outside knowledge about the players.
- Copy every number exactly as it appears in the evidence; do not round or compute new figures.
- Keep the coordinator's order and confidence levels. You explain the verdict; you do not change it.
- Say each player's confidence level, and repeat every risk flag. If confidence is LOW, say plainly that the sample is small and the order is not a ranking.
- Write 120 to 200 words for a coach: a one-sentence verdict, then a short reason for each player, then the risks. Plain text; short "- " bullets are fine; no headings or markdown emphasis."""

# Small whole numbers ("two players", "an XI") are ordinary prose, not statistics.
_FREE_INTEGERS = {str(n) for n in range(0, 21)}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _evidence(report: dict[str, Any]) -> dict[str, Any]:
    """The part of the coordinator report the model is allowed to use."""
    return {
        "question": report.get("question"),
        "context": report.get("context"),
        "selection_summary": report.get("selection_summary"),
        "recommendations": [
            {
                "player": r.get("player"),
                "role": r.get("role"),
                "role_reason": r.get("role_reason"),
                "confidence": r.get("confidence"),
                "notes": r.get("notes"),
                "evidence": r.get("evidence"),
            }
            for r in report.get("recommendations", [])
        ],
        "conflicts_resolved": report.get("conflicts_resolved"),
        "risks_flags": report.get("risks_flags"),
        "team_balance": report.get("team_balance"),
    }


def unsupported_numbers(text: str, evidence_json: str) -> list[str]:
    """Numbers in the narrative that do not occur anywhere in the evidence."""
    allowed = set(_NUMBER.findall(evidence_json)) | _FREE_INTEGERS
    seen: list[str] = []
    for number in _NUMBER.findall(text):
        if number not in allowed and number not in seen:
            seen.append(number)
    return seen


def _rules(report: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "source": "rules",
        "model": None,
        "text": report.get("selection_summary", ""),
        "note": reason,
    }


def _call_model(evidence_json: str, question: str) -> tuple[str, str, str]:
    """Send the evidence to Claude. Returns (text, served_model, stop_reason).

    Kept separate so tests can replace it without network access.
    """
    import anthropic

    client = anthropic.Anthropic(timeout=90.0, max_retries=1)
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"QUESTION: {question}\n\nEVIDENCE (JSON):\n{evidence_json}",
        }],
        # A short explanation of given facts needs little deliberation.
        output_config={"effort": "low"},
        # If a safety classifier declines, the API retries on a fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return text, response.model, response.stop_reason or ""


def narrate(report: dict[str, Any]) -> dict[str, Any]:
    """Explain the coordinator's report with Claude, or fall back to rules.

    Returns {"source": "llm" | "rules", "model", "text", "note"}.
    """
    if os.environ.get("CRICKET_LLM", "on").strip().lower() in {"0", "off", "false", "no"}:
        return _rules(report, "LLM step switched off (CRICKET_LLM=off).")
    if not report.get("recommendations"):
        return _rules(report, "No players to explain.")
    try:
        import anthropic
    except ImportError:
        return _rules(report, "The anthropic package is not installed (pip install anthropic).")

    evidence_json = json.dumps(_evidence(report), sort_keys=True, ensure_ascii=False)
    record_call("llm_narrator", channel="anthropic-api", arguments={"model": MODEL})
    try:
        text, served_model, stop_reason = _call_model(evidence_json, report.get("question") or "")
    except anthropic.AuthenticationError:
        return _rules(report, "No valid Anthropic API key; set ANTHROPIC_API_KEY to enable the LLM step.")
    except anthropic.RateLimitError:
        return _rules(report, "The Anthropic API is rate limiting requests; showing the rule-based summary.")
    except anthropic.APIConnectionError:
        return _rules(report, "Could not reach the Anthropic API (offline?); showing the rule-based summary.")
    except anthropic.APIStatusError as exc:
        logger.warning("LLM narrator failed: %s", exc)
        return _rules(report, f"Anthropic API error {exc.status_code}; showing the rule-based summary.")
    except (anthropic.AnthropicError, TypeError) as exc:
        # Raised before any request when no credentials can be resolved.
        logger.info("LLM narrator unavailable: %s", exc)
        return _rules(report, "No Anthropic credentials configured; set ANTHROPIC_API_KEY to enable the LLM step.")

    if stop_reason == "refusal":
        return _rules(report, "The model declined this request; showing the rule-based summary.")
    if stop_reason == "max_tokens" or not text:
        return _rules(report, "The model's answer was cut off; showing the rule-based summary.")

    invented = unsupported_numbers(text, evidence_json)
    if invented:
        logger.warning("LLM narrative rejected; numbers not in evidence: %s", invented)
        return _rules(
            report,
            "The model's explanation was rejected by the grounding check because it "
            f"contained figures not in the evidence ({', '.join(invented[:5])}).",
        )

    return {
        "source": "llm",
        "model": served_model,
        "text": text,
        "note": "Written by Claude from the analysts' evidence; every figure was checked against that evidence.",
    }
