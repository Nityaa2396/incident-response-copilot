"""
Diagnostics Agent for Incident Response Copilot.

Takes a raw error log or incident description.
Returns structured findings: error type, severity, root cause, affected components.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Literal

from anthropic import Anthropic
from src.config import get_secret
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"

Severity = Literal["critical", "high", "medium", "low"]

SYSTEM_PROMPT = """You are a senior site reliability engineer specializing in incident diagnosis.

You receive a raw error log or incident description. You return a structured diagnosis.

Rules:
- Identify the most likely root cause based only on what is in the input.
- Be specific — name the exact component, service, or line that is failing if visible.
- Severity levels:
  - "critical" — system is down, data loss possible, or revenue impact
  - "high" — major feature broken, significant user impact
  - "medium" — partial degradation, workaround exists
  - "low" — minor issue, cosmetic or edge case
- "affected_components" — list every service, database, API, or module mentioned or implied.
- "contributing_factors" — list conditions that likely made this worse (high load, recent deploy, missing retry logic, etc).
- "confidence" — how confident are you in this diagnosis? (0.0 to 1.0)
- "needs_more_info" — list any missing information that would improve the diagnosis.

Output MUST be a single JSON object with exactly these keys and no others:
  "error_type": string (e.g. "DatabaseConnectionError", "OOMKill", "TimeoutError")
  "severity": "critical" | "high" | "medium" | "low"
  "root_cause": string (one clear sentence)
  "affected_components": list of strings
  "contributing_factors": list of strings
  "confidence": float (0.0 to 1.0)
  "needs_more_info": list of strings
  "summary": string (2-3 sentences, plain English, what happened and why)

Do not include any text outside the JSON object."""


@dataclass
class DiagnosticsResult:
    error_type: str
    severity: Severity
    root_cause: str
    affected_components: list[str]
    contributing_factors: list[str]
    confidence: float
    needs_more_info: list[str]
    summary: str

    def to_dict(self) -> dict:
        return asdict(self)


def run_diagnostics(
    incident_input: str,
    client: Anthropic | None = None,
) -> DiagnosticsResult:
    """
    Run the Diagnostics Agent on a raw error log or incident description.
    Returns structured DiagnosticsResult.
    """
    client = client or Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))

    user_message = (
        f"<incident>\n{incident_input.strip()}\n</incident>\n\n"
        "Diagnose this incident and return the JSON object described in the system prompt."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = _extract_text(response)
    payload = _parse_json(raw)

    return DiagnosticsResult(
        error_type=str(payload.get("error_type", "Unknown")).strip(),
        severity=_normalize_severity(payload.get("severity", "medium")),
        root_cause=str(payload.get("root_cause", "")).strip(),
        affected_components=list(payload.get("affected_components", [])),
        contributing_factors=list(payload.get("contributing_factors", [])),
        confidence=float(payload.get("confidence", 0.5)),
        needs_more_info=list(payload.get("needs_more_info", [])),
        summary=str(payload.get("summary", "")).strip(),
    )


def _extract_text(response) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ValueError("No text block in response")


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON found in response: {raw!r}")
    return json.loads(raw[start:end + 1])


def _normalize_severity(value: str) -> Severity:
    normalized = value.strip().lower()
    if normalized in ("critical", "high", "medium", "low"):
        return normalized  # type: ignore
    return "medium"


if __name__ == "__main__":
    # Quick smoke test
    test_input = """
    ERROR 2026-09-24 03:42:11 [order-service] Failed to connect to PostgreSQL after 3 retries
    psycopg2.OperationalError: could not connect to server: Connection refused
        Is the server running on host "db-prod-01" (10.0.1.45) and accepting
        TCP/IP connections on port 5432?
    CRITICAL: Order processing halted. 847 requests queued.
    Last successful DB connection: 2026-09-24 03:38:44
    Recent deployment: order-service v2.4.1 deployed at 03:35:00
    """

    result = run_diagnostics(test_input)
    print(json.dumps(result.to_dict(), indent=2))