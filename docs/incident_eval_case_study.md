# Incident Response Copilot: Evaluation Case Study

A four-agent pipeline (Diagnostics → Runbook Search → Fix Recommendation → Postmortem) was evaluated on 15 synthetic incidents across three difficulty tiers. This document covers the first measurement (v1), one round of changes (v2), and what the numbers do and do not support.

## Setup

- **Dataset:** 15 incidents (7 Tier 1 clear-cut, 5 Tier 2 moderately ambiguous, 3 Tier 3 multi-cause or vague). Each has an expected error type, severity, top runbook and root-cause keywords.
- **Pipeline:** Claude Sonnet diagnoses the incident. The diagnosis is turned into a query, and Voyage embeddings (1024-d) in Qdrant retrieve the top 3 of 15 runbooks. Two further agents produce the fix plan and postmortem.
- **Scoring:** error type (normalized exact or keyword-overlap), severity (exact and within one level), runbook top-1 and top-3, root-cause keyword coverage. Scoring is deterministic code (`run_incident_eval.py`), not an LLM judge.
- **Scale caveat:** n = 15, one run per version. One incident is 6.7 percentage points. Differences of one or two incidents are not statistically meaningful.

## v1 baseline

| Metric | v1 |
|---|---|
| Error type accuracy | 53.3% (exact 33.3%) |
| Severity exact / within one level | 26.7% / 93.3% |
| Severity over / under rated | 11 / 0 |
| Runbook top-1 / top-3 | 73.3% / 93.3% |
| Root-cause keywords | 65.7% |

Two problems stood out.

1. **Error type scored low partly because of label granularity.** The agent returned specific names (`ConsumerGroupLagSpike`, `InvalidSignatureError`) where the ground truth used broader ones (`MessageQueueBacklog`, `AuthenticationError`). By manual review, about 5 of the 7 misses were granularity mismatches and 2 (inc011, inc013) were real diagnostic weaknesses, where the agent named a symptom (`TimeoutError`) and missed the systemic pattern. That split is a judgment call, not a measured result.
2. **Severity was systematically cautious.** The model rated incidents higher than ground truth 11 times and lower never.

## Changes for v2

1. **Fixed category taxonomy** (11 values) added to the diagnostics output, derived from the runbook library. Expected categories come from `expected_top_runbook` through a lookup table, not from hand-labelling after seeing outputs.
2. **Severity rubric** rewritten to judge actual current impact rather than worst case, and to ignore log-level labels such as `CRITICAL:` inside the log text.
3. No changes to retrieval, ground truth, or the other agents.

## v2 results

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

By tier (v2): Tier 1 type 71.4%, severity 57.1%, top-1 71.4%, top-3 85.7%. Tier 2 type 40.0%, severity 20.0%, top-1 100%. Tier 3 type 100%, severity 33.3%, top-1 100%, top-3 100%.

## Findings

**1. Retrieval failures originated upstream, in diagnostics.** Both remaining top-1 misses trace to a wrong category from the Diagnostics agent. inc003 (a gateway 502) was categorized `cascading`, so retrieval returned the cascading-failure runbook. inc005 (CrashLoopBackOff) was categorized `deployment`, so retrieval returned the rollback runbook. In each case retrieval answered a wrong query correctly. In a multi-agent pipeline, an early error propagates, and tuning the retriever would not have fixed either miss.

**2. Severity is better calibrated but not solved.** Every severity is now within one level of ground truth, and exact accuracy rose from 26.7% to 40.0%. The model still over-rates (8 of 15), mostly by one level. One regression: inc014 (a memory leak that has not yet reached OOM) is now under-rated as `low`, because the rubric's "no current user impact → low" rule over-corrects for problems that will become critical if left alone.

**3. Severity ground truth is subjective.** Several one-level disagreements are defensible, for example a cascading multi-service outage (inc011) labelled `high` where `critical` is reasonable. The labels were not changed to match outputs, since that would be fitting to the test set. The within-one-level figure is the more trustworthy number.

## Limitations

- **Category accuracy has no baseline.** The metric did not exist in v1, so 80% cannot be reported as an improvement. The taxonomy was designed after seeing where v1 failed, so it is not an independent measure.
- **Error-type gains come from partial matches.** Exact matches fell from 33.3% to 26.7%. The headline improvement (53.3% → 66.7%) counts keyword overlap as a hit.
- **Single run.** The model is not deterministic. Retrieval top-1 moved from 11 to 13 of 15 with no change to retrieval itself, which shows that run-to-run variation is of the same size as the improvement. Repeated runs would be needed to claim a real gain.
- **Keyword scoring is blunt.** It is a substring match, so wording differences move it a lot. The small drop (65.7% → 61.0%) should not be over-read.
- **One taxonomy overlap.** inc002 (Kubernetes pod OOM kill) fits both `orchestration` and `resource_exhaustion`. The ground truth follows the runbook, and the model's answer is defensible.
- **Synthetic data.** Incidents were written to test the pipeline, not sampled from production.

## Open issue: low-confidence warning is miscalibrated

The pipeline warns when the best retrieval score is below 0.65. That threshold came from trigram-era scores. With Voyage embeddings, correct matches commonly score 0.60 to 0.64, so the warning fired on 10 of 15 incidents including several correct hits. It does not currently separate good matches from bad ones. The fix is to pick a threshold from the score distribution of correct versus incorrect top-1 retrievals, and to report honestly if the two distributions overlap.

## Next steps

1. Calibrate the retrieval-confidence threshold from the observed score distribution.
2. Add a category sanity check before the category drives retrieval, targeting the failure mode in finding 1.
3. Add a rubric clause for incidents that are not yet failing but will become critical if untreated (inc014).
4. Repeat each evaluation run several times and report variance before claiming further gains.