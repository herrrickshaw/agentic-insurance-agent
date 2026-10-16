"""CLI: python -m records.run <library_dir> <out_dir> [--apply-recommended]"""
import sys
from pathlib import Path

from ai.client import LLMClient

from . import pipeline, report, review
from .audit import AuditLog


def main(argv):
    src, out = Path(argv[1]), Path(argv[2])
    audit = AuditLog(out / "audit.jsonl")
    rows = pipeline.analyse(src, LLMClient())
    audit.append("system", "analyse", str(src), {"files": len(rows)})
    if "--apply-recommended" in argv:
        n = review.apply_decisions(rows, review.recommended_decisions(rows), "records-manager", audit)
        print(f"applied {n} recommended decisions")
    pipeline.write_manifest(rows, out)
    text, ready = report.readiness(rows, audit)
    (out / "readiness.md").write_text(text)
    print(text)
    return 0 if ready else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
