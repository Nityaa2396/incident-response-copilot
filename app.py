"""
Streamlit UI for Incident Response Copilot.

Paste an error log or describe an incident.
The pipeline runs all 4 agents and displays the full report.
"""

from __future__ import annotations

import time
import streamlit as st
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Incident Response Copilot",
    page_icon="🚨",
    layout="wide",
)

# ── Styles ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .severity-critical { color: #dc2626; font-weight: 700; }
    .severity-high      { color: #ea580c; font-weight: 700; }
    .severity-medium    { color: #ca8a04; font-weight: 700; }
    .severity-low       { color: #16a34a; font-weight: 700; }
    .agent-header { font-size: 13px; font-weight: 600; color: #6b7280;
                    text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
    .confidence-bar { height: 6px; border-radius: 3px; background: #e5e7eb; margin-top: 4px; }
    .trace-box { background: #f8fafc; border: 1px solid #e2e8f0;
                 border-radius: 8px; padding: 12px 16px; font-size: 12px;
                 font-family: monospace; }
</style>
""", unsafe_allow_html=True)


EXAMPLE_INCIDENTS = {
    "PostgreSQL Connection Refused": """ERROR 2026-09-24 03:42:11 [order-service] Failed to connect to PostgreSQL after 3 retries
psycopg2.OperationalError: could not connect to server: Connection refused
    Is the server running on host "db-prod-01" (10.0.1.45) and accepting
    TCP/IP connections on port 5432?
CRITICAL: Order processing halted. 847 requests queued.
Last successful DB connection: 2026-09-24 03:38:44
Recent deployment: order-service v2.4.1 deployed at 03:35:00""",

    "Kubernetes CrashLoopBackOff": """WARN  2026-09-24 08:15:33 [payment-service] Pod payment-service-7d9f8b-xkp2q restarting (attempt 4)
Error: Back-off restarting failed container
Exit Code: 1
Last log before crash:
  ERROR: DATABASE_URL environment variable is not set
  Failed to initialize connection pool
  Application startup failed
kubectl describe pod shows: CrashLoopBackOff
Recent deployment: payment-service v1.8.0 deployed 08:10:00""",

    "API Gateway 502 Bad Gateway": """[2026-09-24 14:22:05] ERROR nginx: upstream prematurely closed connection
[2026-09-24 14:22:05] 502 Bad Gateway - /api/v2/checkout
Upstream: http://checkout-service:8080
All health checks failing for checkout-service
Error rate: 100% on /api/v2/* endpoints
Response time before failure: avg 4800ms (normal: 120ms)
No recent deployments in last 2 hours""",
}


def severity_badge(severity: str) -> str:
    color = {
        "critical": "#dc2626",
        "high": "#ea580c",
        "medium": "#ca8a04",
        "low": "#16a34a",
    }.get(severity.lower(), "#6b7280")
    return (
        f'<span style="background:{color};color:white;padding:2px 10px;'
        f'border-radius:12px;font-size:12px;font-weight:600;">'
        f'{severity.upper()}</span>'
    )


def confidence_color(confidence: float) -> str:
    if confidence >= 0.8:
        return "#16a34a"
    elif confidence >= 0.6:
        return "#ca8a04"
    return "#dc2626"


def render_diagnostics(diagnostics: dict):
    st.markdown('<div class="agent-header">🔍 Agent 1 — Diagnostics</div>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**Error Type**")
        st.code(diagnostics.get("error_type", ""), language=None)
    with col2:
        st.markdown("**Severity**")
        st.markdown(severity_badge(diagnostics.get("severity", "")), unsafe_allow_html=True)
    with col3:
        confidence = diagnostics.get("confidence", 0)
        st.markdown("**Confidence**")
        st.markdown(
            f'<div style="font-size:24px;font-weight:700;color:{confidence_color(confidence)}">'
            f'{confidence:.0%}</div>',
            unsafe_allow_html=True
        )

    st.markdown(f"**Summary:** {diagnostics.get('summary', '')}")

    with st.expander("Root cause & components"):
        st.markdown(f"**Root Cause:** {diagnostics.get('root_cause', '')}")
        st.markdown("**Affected Components:**")
        for c in diagnostics.get("affected_components", []):
            st.markdown(f"- {c}")
        st.markdown("**Contributing Factors:**")
        for f in diagnostics.get("contributing_factors", []):
            st.markdown(f"- {f}")
        if diagnostics.get("needs_more_info"):
            st.markdown("**Needs More Info:**")
            for n in diagnostics.get("needs_more_info", []):
                st.markdown(f"- {n}")


def render_runbooks(runbook_matches: list[dict]):
    st.markdown('<div class="agent-header">📚 Agent 2 — Runbook Search</div>', unsafe_allow_html=True)
    st.markdown(f"Found **{len(runbook_matches)}** relevant runbooks")

    for match in runbook_matches:
        score = match.get("score", 0)
        with st.expander(f"[{match.get('id')}] {match.get('title')} — relevance {score:.0%}"):
            st.markdown("**Resolution Steps:**")
            for step in match.get("resolution_steps", []):
                st.markdown(f"{step}")
            st.markdown(f"**Escalation:** {match.get('escalation', '')}")
            st.markdown(f"**Est. Resolution:** {match.get('resolution_time_estimate', '')}")


def render_fix(fix: dict):
    st.markdown('<div class="agent-header">🔧 Agent 3 — Fix Recommendation</div>', unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        risk = fix.get("risk_level", "").upper()
        risk_color = {"HIGH": "#dc2626", "MEDIUM": "#ca8a04", "LOW": "#16a34a"}.get(risk, "#6b7280")
        st.markdown(
            f'**Risk Level:** <span style="color:{risk_color};font-weight:700">{risk}</span>',
            unsafe_allow_html=True
        )
    with col2:
        st.markdown(f"**Est. Resolution:** {fix.get('estimated_resolution_time', '')}")

    st.markdown(f"**Summary:** {fix.get('summary', '')}")

    st.markdown("**Immediate Actions:**")
    for i, action in enumerate(fix.get("immediate_actions", []), 1):
        confidence = action.get("confidence", 0)
        col_a, col_b = st.columns([0.08, 0.92])
        with col_a:
            st.markdown(
                f'<div style="font-size:11px;color:{confidence_color(confidence)};'
                f'font-weight:700;margin-top:4px">{confidence:.0%}</div>',
                unsafe_allow_html=True
            )
        with col_b:
            st.markdown(f"{i}. {action.get('step', '')}")

    if fix.get("warnings"):
        st.warning("⚠️ " + " | ".join(fix.get("warnings", [])))

    with st.expander("Verification & rollback"):
        st.markdown("**Verification Steps:**")
        for step in fix.get("verification_steps", []):
            st.markdown(f"- {step}")
        st.markdown(f"**Rollback Option:** {fix.get('rollback_option', '')}")


def render_postmortem(postmortem: dict):
    st.markdown('<div class="agent-header">📋 Agent 4 — Postmortem</div>', unsafe_allow_html=True)
    st.markdown(f"### {postmortem.get('title', '')}")

    tab1, tab2, tab3, tab4 = st.tabs(["Timeline", "Root Cause", "Action Items", "Lessons"])

    with tab1:
        for event in postmortem.get("timeline", []):
            col1, col2 = st.columns([0.25, 0.75])
            with col1:
                st.markdown(f"`{event.get('time', '')}`")
            with col2:
                st.markdown(event.get("event", ""))

    with tab2:
        st.markdown("**Impact**")
        st.info(postmortem.get("impact", ""))
        st.markdown("**Root Cause**")
        st.markdown(postmortem.get("root_cause", ""))
        st.markdown("**Contributing Factors**")
        for f in postmortem.get("contributing_factors", []):
            st.markdown(f"- {f}")
        st.markdown("**Detection Gap**")
        st.markdown(postmortem.get("detection_gap", ""))

    with tab3:
        action_items = postmortem.get("action_items", [])
        priority_colors = {"P1": "🔴", "P2": "🟡", "P3": "🟢"}
        for item in action_items:
            priority = item.get("priority", "")
            emoji = priority_colors.get(priority, "⚪")
            st.markdown(
                f"{emoji} **{priority}** — {item.get('task', '')}  \n"
                f"*Owner: {item.get('owner', '')}*"
            )
            st.divider()

    with tab4:
        for lesson in postmortem.get("lessons_learned", []):
            st.markdown(f"- {lesson}")
        st.markdown("**Resolution Summary**")
        st.markdown(postmortem.get("resolution_summary", ""))


def render_trace(report: dict):
    st.markdown('<div class="agent-header">🔎 Pipeline Trace — Context Flow</div>', unsafe_allow_html=True)

    diag = report.get("diagnostics", {})
    matches = report.get("runbook_matches", [])
    fix = report.get("fix_recommendation", {})
    postmortem = report.get("postmortem", {})

    trace = f"""Agent 1 → INPUT:  raw incident text
           OUTPUT: error_type={diag.get('error_type')} | severity={diag.get('severity')} | confidence={diag.get('confidence')}

Agent 2 → INPUT:  structured diagnostics (not raw text)
           OUTPUT: {len(matches)} runbooks — {', '.join(f"[{m.get('id')}] {m.get('title')}" for m in matches)}

Agent 3 → INPUT:  diagnostics + runbook steps only (not raw text, not full runbooks)
           OUTPUT: {len(fix.get('immediate_actions', []))} actions | risk={fix.get('risk_level')} | time={fix.get('estimated_resolution_time')}

Agent 4 → INPUT:  raw incident + diagnosis summary + fix summary (not runbooks)
           OUTPUT: {len(postmortem.get('action_items', []))} action items | {len(postmortem.get('timeline', []))} timeline events

Total pipeline duration: {report.get('pipeline_duration_seconds', 0):.1f}s"""

    st.markdown(f'<div class="trace-box"><pre>{trace}</pre></div>', unsafe_allow_html=True)


# ── Main UI ────────────────────────────────────────────────────────────────

st.title("🚨 Incident Response Copilot")
st.markdown("Paste an error log or describe a production incident. The pipeline diagnoses, finds runbooks, recommends fixes, and writes a postmortem.")

st.divider()

# sidebar
with st.sidebar:
    st.markdown("### Try an example")
    for name, text in EXAMPLE_INCIDENTS.items():
        if st.button(name, use_container_width=True):
            st.session_state["incident_input"] = text

    st.divider()
    st.markdown("### Pipeline")
    st.markdown("1. 🔍 Diagnostics Agent")
    st.markdown("2. 📚 Runbook Search Agent")
    st.markdown("3. 🔧 Fix Recommendation Agent")
    st.markdown("4. 📋 Postmortem Agent")
    st.divider()
    st.markdown("### About")
    st.markdown("Built to demonstrate context engineering and multi-agent orchestration for forward deployed AI engineering.")

# input
incident_input = st.text_area(
    "Error log or incident description",
    value=st.session_state.get("incident_input", ""),
    height=200,
    placeholder="Paste your error log here...",
    key="incident_input",
)

run_button = st.button("🚀 Run Incident Analysis", type="primary", use_container_width=True)

if run_button and incident_input.strip():
    from src.orchestrator import run_pipeline

    # progress tracking
    progress_placeholder = st.empty()
    steps = {
        "diagnostics": "🔍 Running Diagnostics Agent...",
        "runbook_search": "📚 Searching Runbook Knowledge Base...",
        "fix_recommendation": "🔧 Generating Fix Recommendation...",
        "postmortem": "📋 Writing Postmortem...",
    }

    def update_progress(step_name: str, status: str):
        if status == "running":
            progress_placeholder.info(steps.get(step_name, step_name))

    client = Anthropic()

    with st.spinner("Running pipeline..."):
        try:
            report = run_pipeline(
                incident_input,
                progress_callback=update_progress,
                client=client,
            )
            progress_placeholder.empty()
            st.session_state["report"] = report.to_dict()
            st.success(f"✅ Analysis complete in {report.pipeline_duration_seconds:.1f}s")
        except Exception as e:
            progress_placeholder.empty()
            st.error(f"Pipeline error: {e}")
            st.stop()

# render report if available
if "report" in st.session_state:
    report = st.session_state["report"]
    st.divider()

    render_diagnostics(report.get("diagnostics", {}))
    st.divider()

    render_runbooks(report.get("runbook_matches", []))
    st.divider()

    render_fix(report.get("fix_recommendation", {}))
    st.divider()

    render_postmortem(report.get("postmortem", {}))
    st.divider()

    with st.expander("🔎 Pipeline Trace"):
        render_trace(report)

elif run_button and not incident_input.strip():
    st.warning("Please paste an error log or describe the incident first.")