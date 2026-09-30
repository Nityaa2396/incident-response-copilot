"""
Incident baseline runner for Incident Response Copilot eval pipeline.

Runs all 15 incidents in eval_incidents.json through the full pipeline.
Saves raw outputs to evals/results/incident_outputs.json for scoring.

Usage:
    python run_incident_baseline.py

Run from the root of incident-response-copilot repo.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

DATASET_PATH = Path("evals/eval_incidents.json")
RESULTS_PATH = Path("evals/results/incident_outputs.json")


def load_dataset() -> list[dict]:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def run_pipeline_on_incident(
    incident: dict,
    client: Anthropic,
) -> dict:
    """
    Run the full 4-agent pipeline on a single incident.
    Returns raw outputs from all agents.
    """
    from src.orchestrator import run_pipeline

    incident_log = incident["incident_log"]

    try:
        report = run_pipeline(incident_log, client=client)

        return {
            "id": incident["id"],
            "tier": incident["tier"],
            "title": incident["title"],
            "incident_log": incident_log,
            "expected_error_type": incident["expected_error_type"],
            "expected_severity": incident["expected_severity"],
            "expected_top_runbook": incident["expected_top_runbook"],
            "expected_root_cause_keywords": incident["expected_root_cause_keywords"],
            "actual_error_type": report.diagnostics.error_type,
            "actual_severity": report.diagnostics.severity,
            "actual_confidence": report.diagnostics.confidence,
            "actual_root_cause": report.diagnostics.root_cause,
            "actual_summary": report.diagnostics.summary,
            "runbooks_retrieved": [
                {"id": m.id, "title": m.title, "score": m.score}
                for m in report.runbook_matches
            ],
            "top_runbook_retrieved": report.runbook_matches[0].id if report.runbook_matches else None,
            "fix_actions_count": len(report.fix_recommendation.immediate_actions),
            "fix_risk_level": report.fix_recommendation.risk_level,
            "fix_summary": report.fix_recommendation.summary,
            "postmortem_title": report.postmortem.title,
            "postmortem_action_items_count": len(report.postmortem.action_items),
            "pipeline_duration_seconds": report.pipeline_duration_seconds,
            "error": None,
        }

    except Exception as e:
        print(f"    ERROR: {e}")
        return {
            "id": incident["id"],
            "tier": incident["tier"],
            "title": incident["title"],
            "incident_log": incident_log,
            "expected_error_type": incident["expected_error_type"],
            "expected_severity": incident["expected_severity"],
            "expected_top_runbook": incident["expected_top_runbook"],
            "expected_root_cause_keywords": incident["expected_root_cause_keywords"],
            "actual_error_type": "",
            "actual_severity": "",
            "actual_confidence": 0,
            "actual_root_cause": "",
            "actual_summary": "",
            "runbooks_retrieved": [],
            "top_runbook_retrieved": None,
            "fix_actions_count": 0,
            "fix_risk_level": "",
            "fix_summary": "",
            "postmortem_title": "",
            "postmortem_action_items_count": 0,
            "pipeline_duration_seconds": 0,
            "error": str(e),
        }


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set.")

    client = Anthropic(api_key=api_key)
    dataset = load_dataset()

    print(f"Running {len(dataset)} incidents through the pipeline...\n")
    print("Note: Each incident takes ~90 seconds (4 Claude API calls).")
    print(f"Estimated total time: ~{len(dataset) * 90 // 60} minutes\n")

    outputs = []
    total_start = time.time()

    for i, incident in enumerate(dataset, 1):
        print(f"[{i}/{len(dataset)}] {incident['id']} — {incident['title']}")
        result = run_pipeline_on_incident(incident, client)
        outputs.append(result)

        status = "✅" if not result["error"] else "❌"
        duration = result.get("pipeline_duration_seconds", 0)
        top_rb = result.get("top_runbook_retrieved", "none")
        print(f"  {status} error_type={result['actual_error_type']} | "
              f"severity={result['actual_severity']} | "
              f"top_runbook={top_rb} | "
              f"duration={duration:.1f}s")

        # save after each incident in case of interruption
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_PATH.write_text(json.dumps(outputs, indent=2), encoding="utf-8")

        # rate limit pause between incidents
        if i < len(dataset):
            time.sleep(2)

    total_duration = time.time() - total_start
    errors = sum(1 for o in outputs if o.get("error"))

    print(f"\nDone. {len(outputs)} incidents processed in {total_duration/60:.1f} minutes.")
    print(f"Errors: {errors}")
    print(f"Saved to {RESULTS_PATH}")
    print("\nNext step: run run_incident_eval.py to score the results.")


if __name__ == "__main__":
    main()
