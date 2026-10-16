"""Migration readiness report (markdown) for the records manager and project sponsor."""
from collections import Counter

from . import pipeline


def readiness(rows, audit):
    ok, bad = audit.verify()
    pf = pipeline.preflight(rows)
    st = Counter(r["status"] for r in rows)
    cls = Counter(r["record_class"] for r in rows if r["status"] == "migrate")
    sens = Counter(r["sensitivity"] for r in rows if r["status"] == "migrate")
    reasons = Counter(x.split(":")[0].strip() for r in rows for x in r["reasons"].split(";") if x.strip())
    ready = ok and not pf and st.get("review", 0) == 0
    L = ["# Migration readiness", "", f"**Verdict: {'READY' if ready else 'NOT READY'}**", ""]
    L += [f"- Files analysed: {len(rows)}"] + [f"- {k}: {v}" for k, v in sorted(st.items())]
    L += ["", "## Gates", f"- Review queue empty: {'yes' if not st.get('review') else 'NO (' + str(st['review']) + ' open)'}",
          f"- Preflight (SharePoint constraints, labels): {'pass' if not pf else f'FAIL ({len(pf)} errors)'}",
          f"- Audit chain intact: {'yes' if ok else f'NO (first bad entry {bad})'}; head {audit.head()[:16]}", "",
          "## Migrating by record class"] + [f"- {k}: {v}" for k, v in cls.most_common()]
    L += ["", "## Migrating by sensitivity"] + [f"- {k}: {v}" for k, v in sens.most_common()]
    L += ["", "## Most common reasons"] + [f"- {k}: {v}" for k, v in reasons.most_common(6)]
    if pf:
        L += ["", "## Preflight errors (first 10)"] + [f"- {e}" for e in pf[:10]]
    return "\n".join(L) + "\n", ready
