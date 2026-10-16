import json
import tempfile
from pathlib import Path

import pytest

from ai.client import LLMClient
from ai.observe import TraceStore
from records import pipeline, report, review, synth
from records.audit import AuditLog


@pytest.fixture(scope="module")
def analysed():
    d = Path(tempfile.mkdtemp()); synth.build(d / "lib")
    return pipeline.analyse(d / "lib", LLMClient(store=TraceStore(str(d / "t.db"))))


def fresh(rows):
    return json.loads(json.dumps(rows))


@pytest.mark.story("REC-6")


def test_audit_chain_detects_tampering(tmp_path):
    a = AuditLog(tmp_path / "a.jsonl")
    for i in range(5):
        a.append("u", "act", f"s{i}", {"i": i})
    assert a.verify() == (True, None)
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    e = json.loads(lines[2]); e["detail"]["i"] = 99
    lines[2] = json.dumps(e); (tmp_path / "a.jsonl").write_text("\n".join(lines) + "\n")
    assert a.verify() == (False, 3)
    (tmp_path / "a.jsonl").write_text("\n".join(lines[:1] + lines[2:]) + "\n")        # deletion
    assert a.verify()[0] is False


@pytest.mark.story("REC-5")


@pytest.mark.story("REC-6")


def test_recommended_decisions_clear_queue_and_are_audited(analysed, tmp_path):
    rows, audit = fresh(analysed), AuditLog(tmp_path / "a.jsonl")
    n_review = sum(r["status"] == "review" for r in rows)
    assert n_review > 0
    n = review.apply_decisions(rows, review.recommended_decisions(rows), "alice", audit)
    assert n == n_review and not any(r["status"] == "review" for r in rows)
    assert len(audit.entries()) == n and audit.verify()[0]
    assert any(r["status"] == "hold" for r in rows)              # injection / unclassified are never auto-migrated
    assert all(r["status"] != "migrate" or r["record_class"] not in ("", "unclassified") for r in rows)


@pytest.mark.story("REC-5")


def test_invalid_decisions_rejected(analysed, tmp_path):
    rows, audit = fresh(analysed), AuditLog(tmp_path / "a.jsonl")
    unclassified = next(r for r in rows if r["status"] == "review" and r["record_class"] == "unclassified")
    migrated = next(r for r in rows if r["status"] == "migrate")
    for d in ({"source_path": unclassified["source_path"], "action": "approve"},
              {"source_path": unclassified["source_path"], "action": "reclassify", "record_class": "made_up"},
              {"source_path": migrated["source_path"], "action": "exclude"},
              {"source_path": "nope", "action": "approve"},
              {"source_path": unclassified["source_path"], "action": "delete"}):
        with pytest.raises(review.DecisionError):
            review.apply_decisions(rows, [d], "bob", audit)
    assert audit.entries() == []                                 # failed decisions leave no trace of success


@pytest.mark.story("REC-5")


def test_reclassify_recomputes_retention_and_sensitivity(analysed, tmp_path):
    rows, audit = fresh(analysed), AuditLog(tmp_path / "a.jsonl")
    r = next(r for r in rows if r["status"] == "review" and r["record_class"] == "unclassified")
    review.apply_decisions(rows, [{"source_path": r["source_path"], "action": "reclassify", "record_class": "invoice"}], "carol", audit)
    assert r["status"] == "migrate" and r["retention_label"].startswith("invoice-7y") and r["sensitivity"]


@pytest.mark.story("REC-9")


def test_readiness_verdict(analysed, tmp_path):
    rows, audit = fresh(analysed), AuditLog(tmp_path / "a.jsonl")
    text, ready = report.readiness(rows, audit)
    assert not ready and "NOT READY" in text and "Review queue empty: NO" in text
    review.apply_decisions(rows, review.recommended_decisions(rows), "alice", audit)
    text, ready = report.readiness(rows, audit)
    assert ready and "READY" in text and "NOT READY" not in text
