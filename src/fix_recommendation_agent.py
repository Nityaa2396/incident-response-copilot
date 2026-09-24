"""
Fix Recommendation Agent for Incident Response Copilot.

Takes structured diagnostics findings + relevant runbook matches.
Produces a ranked, actionable fix plan with confidence scores per step.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from anthropic import Anthropic
from src.config import get_secret
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a senior site reliability engineer creating an incident fix plan.

You receive:
1. A structured diagnosis of the incident (error type, severity, root cause, affected components)
2. Relevant runbooks retrieved from the knowledge base

Your job is to produce a concrete, ranked fix plan tailored to THIS specific incident.

Rules:
- Combine insights from the diagnosis AND the runbooks — do not just copy runbook steps verbatim.
- Tailor each step to the specific details in the diagnosis (use actual component names, error details).
- Rank steps by priority — immediate actions first, then verification, then cleanup.
- Each step must have a confidence score (0.0 to 1.0) — how confident you are this step applies.
- "immediate_actions" — steps to take RIGHT NOW to restore service (max 5).
- "verification_steps" — how to confirm the fix worked (max 3).
- "rollback_option" — what to do if the fix makes things worse (one clear sentence).
- "estimated_resolution_time" — realistic time estimate given the diagnosis.
- "risk_level" — risk of applying these fixes: "low", "medium", or "high".
- "warnings" — anything the responder must NOT do, or risks to watch for.

Output MUST be a single JSON object with exactly these keys:
  "immediate_actions": list of objects with "step" (string) and "confidence" (float)
  "verification_steps": list of strings
  "rollback_option": string
  "estimated_resolution_time": string
  "risk_level": "low" | "medium" | "high"
  "warnings": list of strings
  "summary": string (2-3 sentences — what to do and why)

Do not include any text outside the JSON object."""


@dataclass
class FixStep:
    step: str
    confidence: float


@dataclass
class FixRecommendation:
    immediate_actions: list[FixStep]
    verification_steps: list[str]
    rollback_option: str
    estimated_resolution_time: str
    risk_level: str
    warnings: list[str]
    summary: str

    def to_dict(self) -> dict:
        return {
            "immediate_actions": [asdict(a) for a in self.immediate_actions],
            "verification_steps": self.verification_steps,
            "rollback_option": self.rollback_option,
            "estimated_resolution_time": self.estimated_resolution_time,
            "risk_level": self.risk_level,
            "warnings": self.warnings,
            "summary": self.summary,
        }


def run_fix_recommendation(
    diagnostics: dict,
    runbook_matches: list[dict],
    client: Anthropic | None = None,
) -> FixRecommendation:
    """
    Generate a fix recommendation from diagnostics + runbook context.

    Args:
        diagnostics: Output from DiagnosticsAgent.to_dict()
        runbook_matches: List of RunbookMatch.to_dict() from RunbookSearchAgent
        client: Optional Anthropic client

    Returns:
        FixRecommendation with ranked steps and confidence scores
    """
    client = client or Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))

    # context engineering — only send what the fix agent needs
    # strip prevention and tags from runbooks — not needed for fix steps
    runbook_context = []
    for rb in runbook_matches:
        runbook_context.append({
            "id": rb.get("id"),
            "title": rb.get("title"),
            "root_causes": rb.get("root_causes", []),
            "resolution_steps": rb.get("resolution_steps", []),
            "escalation": rb.get("escalation", ""),
            "resolution_time_estimate": rb.get("resolution_time_estimate", ""),
            "relevance_score": rb.get("score", 0),
        })

    # diagnostics context — send full findings
    diag_context = {
        "error_type": diagnostics.get("error_type"),
        "severity": diagnostics.get("severity"),
        "root_cause": diagnostics.get("root_cause"),
        "affected_components": diagnostics.get("affected_components", []),
        "contributing_factors": diagnostics.get("contributing_factors", []),
        "confidence": diagnostics.get("confidence"),
        "summary": diagnostics.get("summary"),
    }

    user_message = (
        f"<diagnosis>\n{json.dumps(diag_context, indent=2)}\n</diagnosis>\n\n"
        f"<runbooks>\n{json.dumps(runbook_context, indent=2)}\n</runbooks>\n\n"
        "Create a tailored fix plan for this specific incident. "
        "Return the JSON object described in the system prompt."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = _extract_text(response)
    payload = _parse_json(raw)

    immediate_actions = [
        FixStep(
            step=str(item.get("step", "")),
            confidence=float(item.get("confidence", 0.5)),
        )
        for item in payload.get("immediate_actions", [])
    ]

    return FixRecommendation(
        immediate_actions=immediate_actions,
        verification_steps=list(payload.get("verification_steps", [])),
        rollback_option=str(payload.get("rollback_option", "")).strip(),
        estimated_resolution_time=str(payload.get("estimated_resolution_time", "")).strip(),
        risk_level=str(payload.get("risk_level", "medium")).strip().lower(),
        warnings=list(payload.get("warnings", [])),
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


if __name__ == "__main__":
    # smoke test using outputs from previous two agents
    from src.diagnostics_agent import run_diagnostics
    from src.runbook_search_agent import (
        build_search_query,
        index_runbooks,
        search_runbooks,
    )

    test_input = """
    ERROR 2026-09-24 03:42:11 [order-service] Failed to connect to PostgreSQL after 3 retries
    psycopg2.OperationalError: could not connect to server: Connection refused
        Is the server running on host "db-prod-01" (10.0.1.45) and accepting
        TCP/IP connections on port 5432?
    CRITICAL: Order processing halted. 847 requests queued.
    Last successful DB connection: 2026-09-24 03:38:44
    Recent deployment: order-service v2.4.1 deployed at 03:35:00
    """

    print("Step 1: Running diagnostics...")
    diagnostics = run_diagnostics(test_input)
    print(f"  Error type: {diagnostics.error_type}")
    print(f"  Severity: {diagnostics.severity}")
    print(f"  Confidence: {diagnostics.confidence}")

    print("\nStep 2: Searching runbooks...")
    index_runbooks()
    query = build_search_query(diagnostics.to_dict())
    matches = search_runbooks(query, top_k=3)
    print(f"  Found {len(matches)} runbooks")
    for m in matches:
        print(f"  - [{m.id}] {m.title} (score: {m.score})")

    print("\nStep 3: Generating fix recommendation...")
    fix = run_fix_recommendation(
        diagnostics.to_dict(),
        [m.to_dict() for m in matches],
    )

    print("\n--- Fix Recommendation ---")
    print(f"Risk level: {fix.risk_level.upper()}")
    print(f"Estimated resolution: {fix.estimated_resolution_time}")
    print(f"\nSummary: {fix.summary}")
    print("\nImmediate actions:")
    for i, action in enumerate(fix.immediate_actions, 1):
        print(f"  {i}. [{action.confidence:.0%}] {action.step}")
    print("\nVerification steps:")
    for step in fix.verification_steps:
        print(f"  - {step}")
    print(f"\nRollback option: {fix.rollback_option}")
    if fix.warnings:
        print("\nWarnings:")
        for w in fix.warnings:
            print(f"  ⚠️  {w}")