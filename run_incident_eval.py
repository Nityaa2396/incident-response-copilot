"""
run_incident_eval.py
Scores incident_outputs.json against eval_incidents.json.

Metrics:
  - error_type accuracy (normalized exact, or keyword-overlap match)
  - severity exact + adjacent (within one level)
  - runbook retrieval top-1 and top-3
  - root-cause keyword coverage
Breakdown overall and by tier.

Run:  python run_incident_eval.py
"""
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

DATASET = Path("evals/eval_incidents.json")
OUTPUTS = Path("evals/results/incident_outputs.json")
CSV_OUT = Path("evals/results/incident_eval_results.csv")
SUMMARY_OUT = Path("evals/results/incident_eval_summary.json")

SEVERITY_ORDER = ["low", "medium", "high", "critical"]
STOPWORDS = {"error", "failure", "issue", "problem", "the", "and", "of"}


def normalize(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").strip().lower())


def split_words(s):
    # split CamelCase / snake_case / spaces into lowercase words
    s = (s or "").strip()
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    return {w for w in re.split(r"[^a-zA-Z0-9]+", s.lower()) if w and w not in STOPWORDS}


def score_error_type(actual, expected):
    if normalize(actual) == normalize(expected):
        return 1, "exact"
    if split_words(actual) & split_words(expected):
        return 1, "partial"
    return 0, "miss"


def score_severity(actual, expected):
    a, e = (actual or "").strip().lower(), (expected or "").strip().lower()
    if a not in SEVERITY_ORDER or e not in SEVERITY_ORDER:
        return 0, 0, "unknown"
    diff = SEVERITY_ORDER.index(a) - SEVERITY_ORDER.index(e)
    direction = "match" if diff == 0 else ("over" if diff > 0 else "under")
    return int(diff == 0), int(abs(diff) <= 1), direction


def score_runbook(retrieved, expected_id):
    ids = [r.get("id") for r in (retrieved or [])]
    top1 = int(bool(ids) and ids[0] == expected_id)
    top3 = int(expected_id in ids[:3])
    return top1, top3


def score_keywords(root_cause, keywords):
    if not keywords:
        return 1.0
    text = (root_cause or "").lower()
    hits = sum(1 for k in keywords if k.lower() in text)
    return round(hits / len(keywords), 3)


def score_all(dataset, outputs):
    out_by_id = {o["id"]: o for o in outputs}
    rows = []
    for item in dataset:
        o = out_by_id.get(item["id"])
        if not o or o.get("error"):
            rows.append({"id": item["id"], "tier": item.get("tier"), "title": item.get("title", ""),
                         "error": (o or {}).get("error", "missing output")})
            continue
        et, et_kind = score_error_type(o.get("actual_error_type"), item["expected_error_type"])
        sev, sev_adj, sev_dir = score_severity(o.get("actual_severity"), item["expected_severity"])
        t1, t3 = score_runbook(o.get("runbooks_retrieved"), item["expected_top_runbook"])
        kw = score_keywords(o.get("actual_root_cause"), item.get("expected_root_cause_keywords", []))
        rows.append({
            "id": item["id"], "tier": item.get("tier"), "title": item.get("title", ""),
            "expected_error_type": item["expected_error_type"].strip(),
            "actual_error_type": o.get("actual_error_type"),
            "error_type_score": et, "error_type_match": et_kind,
            "expected_severity": item["expected_severity"], "actual_severity": o.get("actual_severity"),
            "severity_exact": sev, "severity_adjacent": sev_adj, "severity_direction": sev_dir,
            "expected_runbook": item["expected_top_runbook"], "top_runbook": o.get("top_runbook_retrieved"),
            "runbook_top1": t1, "runbook_top3": t3,
            "keyword_score": kw,
            "duration_s": o.get("pipeline_duration_seconds"),
            "error": "",
        })
    return rows


def mean(vals):
    vals = list(vals)
    return round(sum(vals) / len(vals), 3) if vals else 0.0


def summarize(rows):
    ok = [r for r in rows if not r.get("error")]
    def agg(rs):
        return {
            "n": len(rs),
            "error_type_acc": mean(r["error_type_score"] for r in rs),
            "error_type_exact": mean(r["error_type_match"] == "exact" for r in rs),
            "severity_exact": mean(r["severity_exact"] for r in rs),
            "severity_adjacent": mean(r["severity_adjacent"] for r in rs),
            "severity_over_rated": sum(r["severity_direction"] == "over" for r in rs),
            "severity_under_rated": sum(r["severity_direction"] == "under" for r in rs),
            "runbook_top1": mean(r["runbook_top1"] for r in rs),
            "runbook_top3": mean(r["runbook_top3"] for r in rs),
            "root_cause_keywords": mean(r["keyword_score"] for r in rs),
            "avg_duration_s": mean(r["duration_s"] for r in rs if r["duration_s"]),
        }
    by_tier = defaultdict(list)
    for r in ok:
        by_tier[r["tier"]].append(r)
    return {
        "total": len(rows), "errors": len(rows) - len(ok),
        "overall": agg(ok) if ok else {},
        "by_tier": {str(t): agg(rs) for t, rs in sorted(by_tier.items(), key=lambda x: str(x[0]))},
        "retrieval_misses": [
            {"id": r["id"], "expected": r["expected_runbook"], "got": r["top_runbook"]}
            for r in ok if not r["runbook_top1"]
        ],
    }


def save_csv(rows):
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def pct(x):
    return f"{x * 100:5.1f}%"


def print_report(s):
    o = s["overall"]
    print("\n" + "=" * 60)
    print(f"INCIDENT COPILOT EVAL  ({s['total']} incidents, {s['errors']} errors)")
    print("=" * 60)
    print(f"Error type accuracy   : {pct(o['error_type_acc'])}  (exact {pct(o['error_type_exact'])})")
    print(f"Severity exact        : {pct(o['severity_exact'])}")
    print(f"Severity within 1     : {pct(o['severity_adjacent'])}")
    print(f"  over / under rated  : {o['severity_over_rated']} / {o['severity_under_rated']}")
    print(f"Runbook top-1         : {pct(o['runbook_top1'])}")
    print(f"Runbook top-3         : {pct(o['runbook_top3'])}")
    print(f"Root-cause keywords   : {pct(o['root_cause_keywords'])}")
    print(f"Avg pipeline time     : {o['avg_duration_s']}s")
    print("\nBy tier:")
    for t, a in s["by_tier"].items():
        print(f"  Tier {t} (n={a['n']}): type {pct(a['error_type_acc'])} | sev {pct(a['severity_exact'])} "
              f"| RB top1 {pct(a['runbook_top1'])} top3 {pct(a['runbook_top3'])} | kw {pct(a['root_cause_keywords'])}")
    if s["retrieval_misses"]:
        print("\nRunbook top-1 misses:")
        for m in s["retrieval_misses"]:
            print(f"  {m['id']}: expected {m['expected']}, got {m['got']}")
    print(f"\nSaved: {CSV_OUT}\n       {SUMMARY_OUT}")


def main():
    dataset = json.loads(DATASET.read_text())
    outputs = json.loads(OUTPUTS.read_text())
    rows = score_all(dataset, outputs)
    summary = summarize(rows)
    save_csv(rows)
    SUMMARY_OUT.write_text(json.dumps(summary, indent=2))
    print_report(summary)


if __name__ == "__main__":
    main()
