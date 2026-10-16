"""Records-manager decisions applied to the analysed rows, each recorded in the audit chain."""
from .classify import PLAN
from .pipeline import retention, sensitivity_for

ACTIONS = {"approve", "reclassify", "exclude", "dispose", "hold"}


class DecisionError(ValueError):
    pass


def recommended_decisions(rows):
    """Conservative suggestions for the review queue. A human can accept them in one click or override."""
    out = []
    for r in rows:
        if r["status"] != "review":
            continue
        why = r["reasons"]
        if "prompt injection" in why or r["record_class"] in ("", "unclassified") or "no extractable text" in why:
            act = "hold"
        elif "near-duplicate" in why:
            act = "exclude"
        elif "retention expired" in why:
            act = "dispose"
        elif "low confidence" in why and float(r["confidence"] or 0) >= 0.5:
            act = "approve"
        else:
            act = "hold"
        out.append({"source_path": r["source_path"], "action": act, "reason": f"recommended: {why[:100]}"})
    return out


def apply_decisions(rows, decisions, reviewer, audit):
    by = {r["source_path"]: r for r in rows}
    applied = 0
    for d in decisions:
        r = by.get(d["source_path"])
        if r is None:
            raise DecisionError(f"unknown item {d['source_path']}")
        if r["status"] != "review":
            raise DecisionError(f"{d['source_path']} is not awaiting review (status {r['status']})")
        act = d["action"]
        if act not in ACTIONS:
            raise DecisionError(f"unknown action {act!r}")
        before = r["status"], r["record_class"]
        if act == "approve":
            if r["record_class"] in ("", "unclassified"):
                raise DecisionError("cannot approve an unclassified item: reclassify it first")
            r["status"] = "migrate"
        elif act == "reclassify":
            cls = d.get("record_class")
            if cls not in PLAN or cls == "unclassified":
                raise DecisionError(f"invalid record class {cls!r}")
            label, disp, _ = retention(cls, r["document_date"])
            pii_found = {k: 1 for k in r["pii"].split(",") if k}
            r.update(record_class=cls, retention_label=label, disposition_date=disp or "",
                     sensitivity=sensitivity_for(cls, pii_found), status="migrate", confidence=1.0)
        elif act == "exclude":
            r["status"] = "exclude"
        elif act == "dispose":
            r["status"] = "dispose"
        else:
            r["status"] = "hold"
        r["reasons"] = (r["reasons"] + f"; reviewed: {act} by {reviewer}").strip("; ")
        audit.append(reviewer, f"decision:{act}", d["source_path"], {"from": before, "to": [r["status"], r["record_class"]], "reason": d.get("reason", "")})
        applied += 1
    return applied
