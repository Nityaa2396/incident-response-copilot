"""
analyze_threshold.py
Does the retrieval confidence score separate correct top-1 matches from wrong ones?
Uses saved results only (no API calls).

Run:  python analyze_threshold.py
"""
import json
from pathlib import Path

DATASET = Path("evals/eval_incidents.json")
OUTPUTS = Path("evals/results/incident_outputs.json")
CURRENT_THRESHOLD = 0.65


def load():
    data = {d["id"]: d for d in json.loads(DATASET.read_text())}
    rows = []
    for o in json.loads(OUTPUTS.read_text()):
        rr = o.get("runbooks_retrieved") or []
        if not rr:
            continue
        top = rr[0]
        second = rr[1]["score"] if len(rr) > 1 else 0.0
        rows.append({
            "id": o["id"],
            "top_id": top["id"],
            "score": top["score"],
            "margin": top["score"] - second,
            "correct": top["id"] == data[o["id"]]["expected_top_runbook"].strip(),
        })
    return rows


def evaluate(rows, key, thr):
    """Warn when value < thr. Report how warnings split across correct/wrong."""
    warned = [r for r in rows if r[key] < thr]
    caught_wrong = sum(1 for r in warned if not r["correct"])
    false_alarms = sum(1 for r in warned if r["correct"])
    return caught_wrong, false_alarms


def main():
    rows = sorted(load(), key=lambda r: r["score"])
    n_wrong = sum(1 for r in rows if not r["correct"])
    n_right = len(rows) - n_wrong

    print(f"{len(rows)} incidents: {n_right} correct top-1, {n_wrong} wrong\n")
    print(f"{'id':8} {'top':6} {'score':>6} {'margin':>7}  result")
    for r in rows:
        print(f"{r['id']:8} {r['top_id']:6} {r['score']:6.3f} {r['margin']:7.3f}  "
              f"{'ok' if r['correct'] else 'WRONG'}")

    for key in ("score", "margin"):
        print(f"\n--- warn when {key} < threshold ---")
        vals = sorted({round(r[key], 3) for r in rows})
        cands = sorted({round(v + 0.0005, 4) for v in vals})
        print(f"{'thr':>7} {'wrong caught':>13} {'false alarms':>13}")
        for thr in cands:
            cw, fa = evaluate(rows, key, thr)
            print(f"{thr:7.4f} {cw:>8}/{n_wrong:<4} {fa:>8}/{n_right:<4}")

    cw, fa = evaluate(rows, "score", CURRENT_THRESHOLD)
    print(f"\nCurrent threshold {CURRENT_THRESHOLD}: catches {cw}/{n_wrong} wrong, "
          f"false alarms on {fa}/{n_right} correct")

    right = [r["score"] for r in rows if r["correct"]]
    wrong = [r["score"] for r in rows if not r["correct"]]
    if right and wrong:
        print(f"Score range  correct: {min(right):.3f}-{max(right):.3f}   "
              f"wrong: {min(wrong):.3f}-{max(wrong):.3f}")
        if min(right) <= max(wrong):
            print("Ranges overlap: no score threshold separates them cleanly.")


if __name__ == "__main__":
    main()
