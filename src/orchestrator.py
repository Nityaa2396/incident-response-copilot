"""
Orchestrator for Incident Response Copilot.

Single entry point that runs all four agents in sequence:
  1. Diagnostics Agent     — diagnose the incident
  2. Runbook Search Agent  — find relevant runbooks
  3. Fix Recommendation    — generate ranked fix steps
  4. Postmortem Agent      — produce structured postmortem

Manages what context each agent receives (context engineering layer).
Returns a complete IncidentReport.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from anthropic import Anthropic
from src.config import get_secret
from dotenv import load_dotenv

from src.diagnostics_agent import DiagnosticsResult, run_diagnostics
from src.runbook_search_agent import (
    RunbookMatch,
    build_search_query,
    index_runbooks,
    search_runbooks,
)
from src.fix_recommendation_agent import FixRecommendation, run_fix_recommendation
from src.postmortem_agent import PostmortemResult, run_postmortem

load_dotenv()


@dataclass
class IncidentReport:
    """Complete output from the full incident response pipeline."""
    incident_input: str
    diagnostics: DiagnosticsResult
    runbook_matches: list[RunbookMatch]
    fix_recommendation: FixRecommendation
    postmortem: PostmortemResult
    pipeline_duration_seconds: float

    def to_dict(self) -> dict:
        return {
            "incident_input": self.incident_input,
            "diagnostics": self.diagnostics.to_dict(),
            "runbook_matches": [r.to_dict() for r in self.runbook_matches],
            "fix_recommendation": self.fix_recommendation.to_dict(),
            "postmortem": self.postmortem.to_dict(),
            "pipeline_duration_seconds": round(self.pipeline_duration_seconds, 2),
        }


@dataclass
class PipelineStep:
    """Tracks progress of each pipeline step for UI updates."""
    name: str
    status: str  # "pending" | "running" | "done" | "error"
    duration_seconds: float = 0.0
    error: str = ""


def run_pipeline(
    incident_input: str,
    top_k_runbooks: int = 3,
    progress_callback=None,
    client: Anthropic | None = None,
) -> IncidentReport:
    """
    Run the full incident response pipeline.

    Args:
        incident_input: Raw error log or incident description
        top_k_runbooks: Number of runbooks to retrieve (default 3)
        progress_callback: Optional callable(step_name, status) for UI updates
        client: Optional shared Anthropic client

    Returns:
        IncidentReport with all agent outputs
    """
    client = client or Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))
    pipeline_start = time.time()

    def update(step_name: str, status: str):
        if progress_callback:
            progress_callback(step_name, status)

    # ── Step 1: Diagnostics ────────────────────────────────────────────────
    update("diagnostics", "running")
    step_start = time.time()
    diagnostics = run_diagnostics(incident_input, client=client)
    diag_duration = time.time() - step_start
    update("diagnostics", "done")

    # ── Step 2: Runbook Search ─────────────────────────────────────────────
    # context engineering: build search query from diagnostics output only
    # the runbook search agent does not need the raw incident text
    update("runbook_search", "running")
    step_start = time.time()
    index_runbooks()
    search_query = build_search_query(diagnostics.to_dict())
    runbook_matches = search_runbooks(search_query, top_k=top_k_runbooks)
    runbook_duration = time.time() - step_start
    update("runbook_search", "done")

    # ── Step 3: Fix Recommendation ─────────────────────────────────────────
    # context engineering: fix agent gets diagnostics + runbooks
    # does NOT get raw incident text — structured data only
    update("fix_recommendation", "running")
    step_start = time.time()
    fix_recommendation = run_fix_recommendation(
        diagnostics.to_dict(),
        [m.to_dict() for m in runbook_matches],
        client=client,
    )
    fix_duration = time.time() - step_start
    update("fix_recommendation", "done")

    # ── Step 4: Postmortem ─────────────────────────────────────────────────
    # context engineering: postmortem agent gets raw input + diagnosis + fix summary
    # does NOT get runbook details — only what it needs to write the postmortem
    update("postmortem", "running")
    step_start = time.time()
    postmortem = run_postmortem(
        incident_input,
        diagnostics.to_dict(),
        fix_recommendation.to_dict(),
        client=client,
    )
    postmortem_duration = time.time() - step_start
    update("postmortem", "done")

    pipeline_duration = time.time() - pipeline_start

    return IncidentReport(
        incident_input=incident_input,
        diagnostics=diagnostics,
        runbook_matches=runbook_matches,
        fix_recommendation=fix_recommendation,
        postmortem=postmortem,
        pipeline_duration_seconds=pipeline_duration,
    )


def format_pipeline_trace(report: IncidentReport) -> str:
    """
    Format a trace of what each agent received and produced.
    This is the observability layer — shows context flow between agents.
    """
    lines = [
        "=" * 60,
        "PIPELINE TRACE — Context Flow Between Agents",
        "=" * 60,
        "",
        "── Agent 1: Diagnostics ──────────────────────────────────",
        f"  INPUT : Raw incident text ({len(report.incident_input.strip())} chars)",
        f"  OUTPUT: error_type={report.diagnostics.error_type}",
        f"          severity={report.diagnostics.severity}",
        f"          confidence={report.diagnostics.confidence}",
        f"          affected_components={report.diagnostics.affected_components}",
        "",
        "── Agent 2: Runbook Search ───────────────────────────────",
        f"  INPUT : error_type + root_cause + components (structured)",
        f"  OUTPUT: {len(report.runbook_matches)} runbooks retrieved",
    ]
    for m in report.runbook_matches:
        lines.append(f"          [{m.id}] {m.title} (score: {m.score})")

    lines += [
        "",
        "── Agent 3: Fix Recommendation ──────────────────────────",
        f"  INPUT : diagnostics (structured) + runbook steps only",
        f"  OUTPUT: {len(report.fix_recommendation.immediate_actions)} immediate actions",
        f"          risk_level={report.fix_recommendation.risk_level}",
        f"          estimated_time={report.fix_recommendation.estimated_resolution_time}",
        "",
        "── Agent 4: Postmortem ───────────────────────────────────",
        f"  INPUT : raw incident + diagnosis summary + fix summary",
        f"  OUTPUT: {len(report.postmortem.action_items)} action items",
        f"          {len(report.postmortem.timeline)} timeline events",
        f"          title={report.postmortem.title}",
        "",
        "── Pipeline Summary ──────────────────────────────────────",
        f"  Total duration: {report.pipeline_duration_seconds:.1f}s",
        "=" * 60,
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    test_input = """
    ERROR 2026-09-24 03:42:11 [order-service] Failed to connect to PostgreSQL after 3 retries
    psycopg2.OperationalError: could not connect to server: Connection refused
        Is the server running on host "db-prod-01" (10.0.1.45) and accepting
        TCP/IP connections on port 5432?
    CRITICAL: Order processing halted. 847 requests queued.
    Last successful DB connection: 2026-09-24 03:38:44
    Recent deployment: order-service v2.4.1 deployed at 03:35:00
    """

    def progress(step, status):
        print(f"  [{status.upper()}] {step}")

    print("Running full incident response pipeline...\n")
    report = run_pipeline(test_input, progress_callback=progress)

    print()
    print(format_pipeline_trace(report))