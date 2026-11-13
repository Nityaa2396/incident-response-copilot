# Incident Response Copilot

A multi-agent pipeline that turns a raw production error log into a diagnosis, a matched runbook, a ranked fix plan and a draft postmortem.

**Live demo:** https://incident-response-copilott.streamlit.app/

## What it does

Paste an error log or incident description. Four agents run in sequence and return:

1. **Diagnosis:** error type, category, severity, root cause, affected components and contributing factors.
2. **Runbook match:** the three most relevant runbooks from a library of 15, retrieved by semantic search.
3. **Fix plan:** ranked remediation steps with a confidence score for each, plus verification steps, a rollback option, a risk level and warnings.
4. **Postmortem:** a draft with a timeline, root cause, action items and lessons learned.

A pipeline trace in the app shows what each stage produced.

## Why this project exists

It is a worked example of context engineering for a multi-agent system: deciding what information each agent sees, measuring whether the pipeline is actually right, and finding where errors in one agent carry into the next. The evaluation is as much a part of the project as the app. See [Evaluation](#evaluation).

## How it works

```
incident log
    |
    v
Diagnostics Agent        -> category, severity, root cause
    |   (the diagnosis becomes the search query)
    v
Runbook Search Agent     -> top 3 runbooks (Voyage embeddings + Qdrant)
    |
    v
Fix Recommendation Agent -> ranked steps, verification, rollback, risk
    |
    v
Postmortem Agent         -> timeline, root cause, action items, lessons
```

| Component | Role |
|---|---|
| Claude (`claude-sonnet-4-6`) | Reasoning in the diagnostics, fix and postmortem agents |
| Voyage AI (`voyage-3`, 1024 dimensions) | Semantic embeddings for runbook search |
| Qdrant Cloud | Vector store for the runbook library |
| Streamlit | Web interface and deployment |

### Design decisions

- **Diagnosis drives retrieval.** The Diagnostics agent's output is turned into the runbook search query, so the search sees a cleaned-up summary of the incident, not the raw log. The evaluation shows the upside and the risk of this: see the findings below.
- **Fixed category taxonomy.** Diagnostics returns one of 11 categories (database, resource_exhaustion, network_gateway, cache, orchestration, messaging, security, deployment, external_dependency, cascading, data_pipeline) alongside a free-form error name. The fixed list makes the output measurable.
- **Impact-based severity rubric.** Severity reflects the impact shown in the log, not worst-case potential, and the model is told to ignore log-level labels such as `CRITICAL:` in the text.
- **Weak-match warning.** If the best runbook match scores below 0.55 (similarity), the pipeline warns that no runbook closely matches and proceeds with general reasoning. See the case study for how this threshold was set and how far to trust it.
- **Pipeline trace.** Each stage's results are recorded and shown in the app.
