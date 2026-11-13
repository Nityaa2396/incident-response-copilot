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
