"""
Postmortem Agent for Incident Response Copilot.

Takes the full incident context — raw input, diagnostics, runbook matches, fix recommendation.
Produces a structured postmortem document ready to share with the team.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from anthropic import Anthropic
from src.config import get_secret
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a senior engineering lead writing an incident postmortem.

You receive the full incident context:
1. The original incident description / error log
2. The structured diagnosis
3. The fix recommendation that was applied

Write a clear, structured postmortem that the team can learn from.

Rules:
- Write in past tense — the incident has been resolved.
- Be specific — use actual component names, timestamps, and error details from the input.
- "timeline" — key events in chronological order, each with a time label and description.
- "impact" — who was affected, what was broken, for how long.
- "root_cause" — one clear paragraph explaining the actual root cause.
- "contributing_factors" — list of conditions that made this worse.
- "resolution_summary" — what was done to fix it, in plain English.
- "action_items" — specific, assignable tasks to prevent recurrence. Each must have an owner role and priority.
- "lessons_learned" — 2-3 honest takeaways for the team.
- "detection_gap" — how long between the issue starting and being detected, and how to close that gap.

Output MUST be a single JSON object with exactly these keys:
  "title": string (incident title, e.g. "PostgreSQL Outage — Order Service — 2026-09-24")
  "severity": string
  "timeline": list of objects with "time" (string) and "event" (string)
  "impact": string
  "root_cause": string
  "contributing_factors": list of strings
  "resolution_summary": string
  "action_items": list of objects with "task" (string), "owner" (string), "priority" ("P1"|"P2"|"P3")
  "lessons_learned": list of strings
  "detection_gap": string

Do not include any text outside the JSON object."""


@dataclass
class PostmortemResult:
    title: str
    severity: str
    timeline: list[dict]
    impact: str
    root_cause: str
    contributing_factors: list[str]
    resolution_summary: str
    action_items: list[dict]
    lessons_learned: list[str]
    detection_gap: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "severity": self.severity,
            "timeline": self.timeline,
            "impact": self.impact,
            "root_cause": self.root_cause,
            "contributing_factors": self.contributing_factors,
            "resolution_summary": self.resolution_summary,
            "action_items": self.action_items,
            "lessons_learned": self.lessons_learned,
            "detection_gap": self.detection_gap,
        }

    def to_markdown(self) -> str:
        """Render postmortem as a readable markdown document."""
        lines = [
            f"# {self.title}",
            f"\n**Severity:** {self.severity.upper()}",
            "\n---\n",
            "## Timeline\n",
        ]
        for event in self.timeline:
            lines.append(f"- **{event.get('time', '')}** — {event.get('event', '')}")

        lines += [
            "\n## Impact\n",
            self.impact,
            "\n## Root Cause\n",
            self.root_cause,
            "\n## Contributing Factors\n",
        ]
        for factor in self.contributing_factors:
            lines.append(f"- {factor}")

        lines += [
            "\n## Resolution Summary\n",
            self.resolution_summary,
            "\n## Action Items\n",
            "| Priority | Task | Owner |",
            "|---|---|---|",
        ]
        for item in self.action_items:
            lines.append(
                f"| {item.get('priority', '')} "
                f"| {item.get('task', '')} "
                f"| {item.get('owner', '')} |"
            )

        lines += [
            "\n## Lessons Learned\n",
        ]
        for lesson in self.lessons_learned:
            lines.append(f"- {lesson}")

        lines += [
            "\n## Detection Gap\n",
            self.detection_gap,
        ]

        return "\n".join(lines)


def run_postmortem(
    incident_input: str,
    diagnostics: dict,
    fix_recommendation: dict,
    client: Anthropic | None = None,
) -> PostmortemResult:
    """
    Generate a structured postmortem from the full incident context.

    Args:
        incident_input: Original raw error log or incident description
        diagnostics: Output from DiagnosticsAgent.to_dict()
        fix_recommendation: Output from FixRecommendationAgent.to_dict()
        client: Optional Anthropic client

    Returns:
        PostmortemResult with full structured postmortem
    """
    client = client or Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))

    # context engineering — postmortem agent gets full picture
    # but we trim fix steps to just the summary to avoid token bloat
    postmortem_context = {
        "incident_description": incident_input.strip(),
        "diagnosis": {
            "error_type": diagnostics.get("error_type"),
            "severity": diagnostics.get("severity"),
            "root_cause": diagnostics.get("root_cause"),
            "affected_components": diagnostics.get("affected_components", []),
            "contributing_factors": diagnostics.get("contributing_factors", []),
            "summary": diagnostics.get("summary"),
        },
        "fix_applied": {
            "summary": fix_recommendation.get("summary"),
            "immediate_actions": [
                a.get("step") for a in fix_recommendation.get("immediate_actions", [])
            ],
            "estimated_resolution_time": fix_recommendation.get("estimated_resolution_time"),
            "risk_level": fix_recommendation.get("risk_level"),
            "warnings_observed": fix_recommendation.get("warnings", []),
        },
    }

    user_message = (
        f"<incident_context>\n{json.dumps(postmortem_context, indent=2)}\n</incident_context>\n\n"
        "Write a structured postmortem for this incident. "
        "Return the JSON object described in the system prompt."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = _extract_text(response)
    payload = _parse_json(raw)

    return PostmortemResult(
        title=str(payload.get("title", "Incident Postmortem")).strip(),
        severity=str(payload.get("severity", "")).strip(),
        timeline=list(payload.get("timeline", [])),
        impact=str(payload.get("impact", "")).strip(),
        root_cause=str(payload.get("root_cause", "")).strip(),
        contributing_factors=list(payload.get("contributing_factors", [])),
        resolution_summary=str(payload.get("resolution_summary", "")).strip(),
        action_items=list(payload.get("action_items", [])),
        lessons_learned=list(payload.get("lessons_learned", [])),
        detection_gap=str(payload.get("detection_gap", "")).strip(),
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
    from src.diagnostics_agent import run_diagnostics
    from src.runbook_search_agent import build_search_query, index_runbooks, search_runbooks
    from src.fix_recommendation_agent import run_fix_recommendation

    test_input = """
    ERROR 2026-09-24 03:42:11 [order-service] Failed to connect to PostgreSQL after 3 retries
    psycopg2.OperationalError: could not connect to server: Connection refused
        Is the server running on host "db-prod-01" (10.0.1.45) and accepting
        TCP/IP connections on port 5432?
    CRITICAL: Order processing halted. 847 requests queued.
    Last successful DB connection: 2026-09-24 03:38:44
    Recent deployment: order-service v2.4.1 deployed at 03:35:00
    """

    print("Running full pipeline...\n")

    print("Step 1: Diagnostics...")
    diagnostics = run_diagnostics(test_input)
    print(f"  {diagnostics.error_type} — {diagnostics.severity}")

    print("Step 2: Runbook search...")
    index_runbooks()
    query = build_search_query(diagnostics.to_dict())
    matches = search_runbooks(query, top_k=3)
    print(f"  Found {len(matches)} runbooks")

    print("Step 3: Fix recommendation...")
    fix = run_fix_recommendation(
        diagnostics.to_dict(),
        [m.to_dict() for m in matches],
    )
    print(f"  Risk: {fix.risk_level} — {fix.estimated_resolution_time}")

    print("Step 4: Generating postmortem...")
    postmortem = run_postmortem(
        test_input,
        diagnostics.to_dict(),
        fix.to_dict(),
    )

    print("\n" + "=" * 60)
    print(postmortem.to_markdown())
    print("=" * 60)