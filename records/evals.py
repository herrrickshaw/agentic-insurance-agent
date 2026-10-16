"""Score the pipeline against synthetic ground truth. Exits 1 if below thresholds (CI gate)."""
import json
import sys
import tempfile
from pathlib import Path

from ai.client import LLMClient
from ai.observe import TraceStore
from . import pipeline, synth

THRESH = {"class_accuracy": 0.95, "rot_precision": 0.95, "rot_recall": 0.9, "spo_recall": 1.0, "pii_recall": 0.95, "review_recall": 0.9}


def score(rows, truth):
    by = {r["source_path"]: r for r in rows}
    cls_ok = cls_n = tp = fp = fn = spo_ok = spo_n = pii_ok = pii_n = rv_ok = rv_n = 0
    for rel, t in truth.items():
        r = by[rel]
        if t.get("cls") and not t.get("review") and not t.get("rot"):
            cls_n += 1; cls_ok += r["record_class"] == t["cls"]
        is_rot = t.get("rot") in ("duplicate", "trivial")
        flagged = r["status"] == "exclude"
        tp += is_rot and flagged; fp += (not is_rot) and flagged; fn += is_rot and not flagged
        if t.get("spo"):
            spo_n += 1; spo_ok += r["target_path"] != rel and "SPO fixed" in r["reasons"] or ("path length" in r["reasons"])
        if t.get("pii") and r["status"] != "exclude":
            pii_n += 1; pii_ok += r["sensitivity"] == "Highly Confidential"
        if t.get("review") or t.get("injection") or t.get("rot") == "near":
            rv_n += 1; rv_ok += r["status"] == "review"
    return {"class_accuracy": cls_ok / max(cls_n, 1), "rot_precision": tp / max(tp + fp, 1), "rot_recall": tp / max(tp + fn, 1),
            "spo_recall": spo_ok / max(spo_n, 1), "pii_recall": pii_ok / max(pii_n, 1), "review_recall": rv_ok / max(rv_n, 1)}


def main():
    d = Path(tempfile.mkdtemp()); truth = synth.build(d / "lib")
    llm = LLMClient(store=TraceStore(str(d / "t.db")))
    rows = pipeline.analyse(d / "lib", llm)
    s = score(rows, truth)
    bad = {k: (round(s[k], 3), t) for k, t in THRESH.items() if s[k] < t}
    print(json.dumps({"scores": {k: round(v, 3) for k, v in s.items()}, "below": bad, "files": len(rows),
                      "preflight_errors": len(pipeline.preflight(rows))}, indent=1))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
