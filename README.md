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

## Setup and usage

### Requirements

- Python 3.12 (recommended)
- API keys for Anthropic and Voyage AI, and a Qdrant Cloud cluster (the free tier works)

### Install

```bash
git clone https://github.com/Nityaa2396/incident-response-copilot
cd incident-response-copilot
pip install -r requirements.txt
```

### Configure

Create a `.env` file in the project root (it is gitignored):

```
ANTHROPIC_API_KEY=...
QDRANT_URL=...
QDRANT_API_KEY=...
VOYAGE_API_KEY=...
```

When deployed on Streamlit Cloud, put the same four values in the app's Secrets settings as top-level `KEY = "value"` lines. Locally, `.streamlit/secrets.toml` also works and is gitignored. Never commit keys.

### Run

```bash
# Web app
streamlit run app.py

# Single-agent smoke test
python -m src.diagnostics_agent
```

The first runbook search indexes the 15 runbooks into Qdrant automatically. Run modules with `python -m src.<module>` from the project root.

### Note on free-tier Qdrant

Free Qdrant Cloud clusters are suspended after a period of inactivity. If the app reports a connection error, resume the cluster from the Qdrant dashboard and, if it was recreated empty, re-index the runbooks.

## Evaluation

The pipeline was evaluated on 15 synthetic incidents in three difficulty tiers: 7 clear-cut, 5 moderately ambiguous and 3 multi-cause or vague. Each has an expected error type, severity, top runbook and root-cause keywords. Scoring is deterministic code, not an LLM judge.

### Results

| Metric | v1 | v2 |
|---|---|---|
| Error type accuracy (exact) | 53.3% (33.3%) | 66.7% (26.7%) |
| Category accuracy | not measured | 80.0% |
| Severity exact | 26.7% | 40.0% |
| Severity within one level | 93.3% | 100% |
| Severity over / under rated | 11 / 0 | 8 / 1 |
| Runbook top-1 | 73.3% | 86.7% |
| Runbook top-3 | 93.3% | 93.3% |
| Root-cause keywords | 65.7% | 61.0% |

v2 added the category taxonomy and the severity rubric. The error-type gain comes from partial matches, and exact matches fell, so category accuracy is the better headline. Category has no v1 baseline.

### Findings

- **Retrieval errors came from diagnosis.** Both remaining top-1 misses trace to a wrong category from the Diagnostics agent. The search answered a wrong query correctly. Improving the retriever would not have fixed either one.
- **Severity is better calibrated, not solved.** Every severity is within one level of the label, but the model still tends to rate incidents higher than the labels do. Severity labels are subjective, so exact-match accuracy should be read with care.
- **Similarity score does not detect wrong matches.** Wrong top-1 retrievals scored inside the range of correct ones. A lower threshold does separate incidents with no matching runbook: in a small out-of-scope test, 4 of 5 scored below every in-scope incident.

### Limitations of the evaluation

15 incidents and a single run per version is a small sample (one incident is 6.7 points), and the incidents are synthetic. The out-of-scope set was chosen by the author and the threshold was set on the same data. Treat the numbers as indicative.

Full write-up with methodology and caveats: [docs/incident_eval_case_study.md](docs/incident_eval_case_study.md).

### Reproduce

```bash
python run_incident_baseline.py   # about 25 minutes, 4 API calls per incident
python run_incident_eval.py       # scores the saved outputs
```
