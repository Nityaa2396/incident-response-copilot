"""
run_ood_retrieval.py
Out-of-scope retrieval test: do incidents with NO matching runbook score
clearly lower than the in-scope incidents?

Runs only Diagnostics + Runbook Search (no fix/postmortem agents), ~10s each.
Saves evals/results/ood_retrieval.json.

Run:  python -m run_ood_retrieval      (or: python run_ood_retrieval.py)
"""
import json
import time
from pathlib import Path

from src.diagnostics_agent import run_diagnostics
from src.runbook_search_agent import build_search_query, search_runbooks

IN_SCOPE = Path("evals/results/incident_outputs.json")
OUT = Path("evals/results/ood_retrieval.json")

# None of these has a matching runbook in the library (RB001-RB015).
OOD_INCIDENTS = [
    {
        "id": "ood1",
        "title": "DNS resolution failure",
        "log": """ERROR 2026-10-12 09:14:02 [checkout-service] java.net.UnknownHostException: payments.internal.example.com
WARN  2026-10-12 09:14:02 [checkout-service] DNS lookup failed, no retry configured
ERROR 2026-10-12 09:14:05 [inventory-service] getaddrinfo ENOTFOUND cache.internal.example.com
Resolver 10.0.0.2 returning SERVFAIL for *.internal.example.com since 09:13:40.
Public hostnames resolve normally.""",
    },
    {
        "id": "ood2",
        "title": "API rate limit lockout (429s)",
        "log": """ERROR 2026-10-12 11:02:41 [partner-sync] HTTP 429 Too Many Requests from api.vendor.com
Retry-After: 3600
WARN  2026-10-12 11:02:41 [partner-sync] retry loop sending 40 req/s ignoring Retry-After header
Account suspended from the vendor API for 1 hour after 3 violations. Nightly partner sync not delivered.""",
    },
    {
        "id": "ood3",
        "title": "Elasticsearch cluster status red",
        "log": """WARN  2026-10-12 14:20:11 [search-api] Elasticsearch cluster health status RED
unassigned_shards: 48, active_shards_percent: 71.2
Node es-data-03 left the cluster at 14:18:55 (heap pressure, GC overhead limit)
ERROR [search-api] SearchPhaseExecutionException: all shards failed for index products-v7
Product search returning empty results.""",
    },
    {
        "id": "ood4",
        "title": "Terraform state lock blocking deploys",
        "log": """Error: Error acquiring the state lock
Lock Info:
  ID:        3f9a2c1e-77b0-4e2a-9c41-0d5f1a8b6e22
  Operation: OperationTypeApply
  Who:       ci-runner-17@build-agent
  Created:   2026-10-12 08:01:09 UTC
CI job apply-prod failed. The previous apply was cancelled mid-run. No infrastructure changes can be deployed.""",
    },
    {
        "id": "ood5",
        "title": "Push notification delivery failure (APNs)",
        "log": """ERROR 2026-10-12 16:45:30 [notify-service] APNs responded 403 ExpiredProviderToken
ERROR 2026-10-12 16:45:30 [notify-service] 12,400 push notifications failed in the last 10 minutes
Provider auth token generated 62 minutes ago; APNs requires refresh within 60 minutes.
Android (FCM) notifications are delivering normally.""",
    },
]


def attr(obj, *names, default=None):
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def in_scope_stats():
    scores, margins = [], []
    for o in json.loads(IN_SCOPE.read_text()):
        rr = o.get("runbooks_retrieved") or []
        if rr:
            scores.append(rr[0]["score"])
            margins.append(rr[0]["score"] - (rr[1]["score"] if len(rr) > 1 else 0.0))
    return scores, margins


def main():
    results = []
    for inc in OOD_INCIDENTS:
        t0 = time.time()
        diag = run_diagnostics(inc["log"])
        query = build_search_query(diag.to_dict())
        matches = search_runbooks(query, top_k=3)
        scores = [attr(m, "score", default=0.0) for m in matches]
        ids = [attr(m, "id", "runbook_id", default="?") for m in matches]
        margin = scores[0] - scores[1] if len(scores) > 1 else scores[0]
        row = {
            "id": inc["id"], "title": inc["title"],
            "category": getattr(diag, "category", None),
            "top_runbook": ids[0] if ids else None,
            "top_score": round(scores[0], 3) if scores else None,
            "margin": round(margin, 3) if scores else None,
            "seconds": round(time.time() - t0, 1),
        }
        results.append(row)
        print(f"  {row['id']} {row['title'][:38]:38} -> {row['top_runbook']} "
              f"score={row['top_score']} margin={row['margin']} cat={row['category']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))

    s, m = in_scope_stats()
    ood_s = [r["top_score"] for r in results]
    ood_m = [r["margin"] for r in results]
    print("\nIn-scope (15):      score {:.3f}-{:.3f} | margin {:.3f}-{:.3f}".format(min(s), max(s), min(m), max(m)))
    print("Out-of-scope ({}):   score {:.3f}-{:.3f} | margin {:.3f}-{:.3f}".format(
        len(results), min(ood_s), max(ood_s), min(ood_m), max(ood_m)))
    below = sum(1 for x in ood_s if x < min(s))
    print(f"Out-of-scope scores below the lowest in-scope score ({min(s):.3f}): {below}/{len(results)}")
    if max(ood_s) >= min(s):
        print("Overlap: a score cutoff cannot reliably separate out-of-scope from in-scope incidents.")
    print(f"\nSaved: {OUT}")


if __name__ == "__main__":
    main()
