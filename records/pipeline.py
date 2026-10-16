"""Analyse a legacy library into a migration manifest: classify, ROT, PII, retention, path fixes, review routing."""
import asyncio
import csv
import json
import re
from datetime import date
from pathlib import Path

from . import inventory, pii, rot
from .classify import PLAN, classify_many
from .targets import resolve

CONF_REVIEW = 0.7
SENS_ORDER = ["Internal", "Confidential", "Highly Confidential"]


def sanitise(rel_path, target=None):
    """Make a target-safe path (replace illegal chars, rename reserved names, cap length) without changing the tree."""
    prof = resolve(target)
    parts = [prof.illegal.sub("-", p).strip().rstrip(".") or "_" for p in Path(rel_path).parts]
    for sub in prof.reserved_substrings:
        parts = [re.sub(re.escape(sub), sub.strip("_") + "-", p, flags=re.I) for p in parts]
    parts = ["_" + p if p.lower().split(".")[0] in prof.reserved_names or p.lower() in prof.reserved_names else p for p in parts]
    out = "/".join(parts)
    if len(out) > prof.max_path:
        stem, ext = Path(parts[-1]).stem, Path(parts[-1]).suffix
        keep = prof.max_path - len("/".join(parts[:-1])) - len(ext) - 2
        out = "/".join(parts[:-1] + [stem[:max(keep, 8)] + ext])
    return out


def retention(cls, doc_date):
    r = PLAN[cls]
    if r["trigger"] == "permanent":
        return "Permanent", None, "permanent"
    if r["retention_years"] is None:
        return "None", None, "none"
    label = f"{cls}-{r['retention_years']}y-{'event' if r['trigger'] == 'event' else 'date'}"
    if r["trigger"] == "document_date" and doc_date:
        y, rest = int(doc_date[:4]) + r["retention_years"], doc_date[4:]
        return label, f"{y}{rest}", "document_date"
    return label, None, r["trigger"]


def sensitivity_for(cls, found):
    """Class default, raised when PII is detected: identifiers/financial numbers -> Highly Confidential."""
    sens = PLAN[cls]["sensitivity"]
    if found and SENS_ORDER.index(sens) < 2:
        sens = "Highly Confidential" if set(found) & pii.HIGH_SENSITIVITY else "Confidential"
    return sens


def analyse(root, llm, today=None, cache=None, max_llm_calls=None, target=None, pii_regions=None):
    prof = resolve(target)
    today = today or date.today().isoformat()
    items = list(inventory.scan(root, profile=prof))
    dups, near = rot.find_exact_duplicates(items), rot.find_near_duplicates(items)
    need = [i for i in items if not rot.is_trivial(i) and i.rel_path not in dups]       # don't pay to classify what is excluded anyway
    results = asyncio.run(classify_many(llm, need, cache=cache, max_calls=max_llm_calls))
    rows = []
    for it in items:
        row = {"source_path": it.rel_path, "size": it.size, "sha256": it.sha256[:16], "status": "migrate", "reasons": [],
               "record_class": "", "confidence": "", "document_date": "", "entity": "", "retention_label": "",
               "disposition_date": "", "sensitivity": "", "pii": "", "target_path": sanitise(it.rel_path, prof), "model": ""}
        if it.path_issues:
            row["reasons"].append("path fixed: " + "; ".join(it.path_issues))
        if rot.is_trivial(it):
            row.update(status="exclude"); row["reasons"].append("trivial (temp/empty/tiny)")
        elif it.rel_path in dups:
            row.update(status="exclude"); row["reasons"].append(f"duplicate of {dups[it.rel_path]}")
        else:
            res = results.get(it.rel_path)
            if res is None:                                   # binary/unsupported without text
                row.update(status="review"); row["reasons"].append("no extractable text (needs OCR/office parser)")
                row["record_class"] = "unclassified"
            else:
                rc, meta, inj = res
                if rc is None:
                    row.update(status="review", record_class="unclassified")
                    row["reasons"].append(meta["error"] if meta.get("budget") else f"AI unavailable: {meta['error']}")
                else:
                    cls = rc.record_class
                    label, disp, trig = retention(cls, rc.document_date)
                    found = pii.detect(it.text, pii_regions)
                    sens = sensitivity_for(cls, found)
                    row.update(record_class=cls, confidence=rc.confidence, document_date=rc.document_date, entity=rc.entity,
                               retention_label=label, disposition_date=disp or "", sensitivity=sens, model=meta.get("model", ""),
                               pii=",".join(found))
                    if cls == "unclassified" or rc.confidence < CONF_REVIEW:
                        row["status"] = "review"; row["reasons"].append(f"low confidence {rc.confidence}")
                    if inj:
                        row["status"] = "review"; row["reasons"].append("possible prompt injection in document text")
                    if it.rel_path in near:
                        row["status"] = "review"; row["reasons"].append(f"near-duplicate of {near[it.rel_path]}")
                    if disp and disp < today:
                        row["status"] = "review"; row["reasons"].append(f"retention expired {disp}: disposal candidate, not migration")
        row["reasons"] = "; ".join(row["reasons"])
        rows.append(row)
    return rows


def preflight(rows, target=None):
    """Block the manifest if anything the target platform would reject, or any item without governance labels, slipped through."""
    prof = resolve(target)
    errs, seen = [], {}
    for r in rows:
        if r["status"] != "migrate":
            continue
        if inventory.path_issues(r["target_path"], prof):
            errs.append(f"{r['target_path']}: {inventory.path_issues(r['target_path'], prof)}")
        if r["target_path"].lower() in seen:
            errs.append(f"duplicate target path {r['target_path']} (from {seen[r['target_path'].lower()]} and {r['source_path']})")
        seen[r["target_path"].lower()] = r["source_path"]
        if not r["retention_label"] or not r["sensitivity"]:
            errs.append(f"{r['source_path']}: missing retention or sensitivity label")
    return errs


def write_manifest(rows, out_dir):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    with open(out / "manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    summary = {s: sum(r["status"] == s for r in rows) for s in ("migrate", "review", "exclude")}
    (out / "summary.json").write_text(json.dumps({"counts": summary, "total": len(rows)}, indent=1))
    return summary
