"""Boundary-focused unit tests added after mutation testing showed survivors in rot.py and review.py."""
import pytest

from records import review
from records.audit import AuditLog
from records.inventory import Item
from records.rot import find_exact_duplicates, find_near_duplicates, is_trivial, jaccard, shingles


def item(path, text="x" * 100, sha=None, size=None, ext=".txt", temp=False):
    return Item(path, len(text) if size is None else size, ext, sha or f"sha-{path}", text, [], temp)


@pytest.mark.story("REC-2")
def test_shingles_and_jaccard_exact_values():
    assert shingles("a b c d e") == {"a b c d e"}                      # exactly k words -> one shingle
    assert shingles("a b c d") == set()                                # fewer than k -> none
    assert len(shingles("a b c d e f g")) == 3
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert jaccard(set(), {"a"}) == 0.0 and jaccard({"a"}, {"a"}) == 1.0


@pytest.mark.story("REC-2")
def test_exact_duplicates_keep_primary_tree_copy():
    a, b, c = item("Legal/loan.txt", sha="S"), item("Archive/Copy of loan.txt", sha="S"), item("Legal/Drafts/loan_v2.txt", sha="S")
    assert find_exact_duplicates([a, b, c]) == {b.rel_path: a.rel_path, c.rel_path: a.rel_path}
    t1, t2 = item("a/zz.txt", sha="T"), item("a/aa.txt", sha="T")      # same junk score and length -> alphabetical
    assert find_exact_duplicates([t1, t2]) == {"a/zz.txt": "a/aa.txt"}
    assert find_exact_duplicates([item("a.txt", "", sha="E", size=0), item("b.txt", "", sha="E", size=0)]) == {}   # empty files are not 'duplicates'
    assert find_exact_duplicates([item("only.txt", sha="U")]) == {}


@pytest.mark.story("REC-2")
def test_near_duplicates_threshold_blocking_and_length_boundary():
    base = " ".join(f"word{i}" for i in range(30))                    # 26 shingles
    near = base + " extra"                                             # +1 shingle -> J = 26/27 = 0.963
    far = " ".join(f"other{i}" for i in range(30))
    a, b = item("Legal/a.txt", base), item("Legal/b.txt", near)
    assert find_near_duplicates([a, b]) == {"Legal/b.txt": "Legal/a.txt"}   # later maps to earlier
    assert find_near_duplicates([a, item("Legal/c.txt", far)]) == {}
    assert find_near_duplicates([a, item("Other/b.txt", near)]) == {}      # different top-level folder: not compared
    assert find_near_duplicates([a, item("Legal/b2.txt", base, sha=a.sha256)]) == {}   # identical hash is exact, not near
    assert find_near_duplicates([a, b], threshold=0.99) == {}
    short = "w " * 30                                                   # 60 chars: boundary is strictly greater
    assert len(short) == 60 and find_near_duplicates([item("L/a.txt", short), item("L/b.txt", short + "z")]) == {}


@pytest.mark.story("REC-2")
def test_trivial_rules():
    assert is_trivial(item("~$x.docx", temp=True, ext=".docx"))
    assert is_trivial(item("empty.pdf", "", size=0, ext=".pdf"))
    assert is_trivial(item("n.txt", "y" * 39)) and not is_trivial(item("n.txt", "y" * 40))
    assert not is_trivial(item("scan.pdf", "", size=5000, ext=".pdf"))      # binary with no text is not trivial, needs OCR


def row(path, status="review", cls="invoice", reasons="", conf=0.9, date="2020-01-01", pii=""):
    return {"source_path": path, "status": status, "record_class": cls, "reasons": reasons, "confidence": conf, "document_date": date,
            "pii": pii, "retention_label": "x", "disposition_date": "", "sensitivity": "Internal"}


@pytest.mark.story("REC-5")
@pytest.mark.parametrize("reasons,cls,conf,expected", [
    ("possible prompt injection in document text", "invoice", 0.9, "hold"),
    ("low confidence 0.2", "unclassified", 0.2, "hold"),
    ("no extractable text (needs OCR/office parser)", "unclassified", "", "hold"),
    ("near-duplicate of a/b.txt", "invoice", 0.9, "exclude"),
    ("retention expired 2020-01-01: disposal candidate, not migration", "invoice", 0.9, "dispose"),
    ("low confidence 0.5", "invoice", 0.5, "approve"),
    ("low confidence 0.49", "invoice", 0.49, "hold"),
    ("something unexpected", "invoice", 0.9, "hold"),
])
def test_recommended_decision_per_reason(reasons, cls, conf, expected):
    rec = review.recommended_decisions([row("p", reasons=reasons, cls=cls, conf=conf), row("q", status="migrate")])
    assert [(d["source_path"], d["action"]) for d in rec] == [("p", expected)]          # non-review rows never get decisions
    assert rec[0]["reason"].startswith("recommended: ")


@pytest.mark.story("REC-5")
def test_each_action_sets_status_and_audit_detail(tmp_path):
    audit = AuditLog(tmp_path / "a.jsonl")
    rows = [row(p, reasons="why") for p in ("a", "b", "c", "d", "e")]
    acts = [{"source_path": "a", "action": "approve"}, {"source_path": "b", "action": "exclude", "reason": "dup"},
            {"source_path": "c", "action": "dispose"}, {"source_path": "d", "action": "hold"},
            {"source_path": "e", "action": "reclassify", "record_class": "board_minutes"}]
    assert review.apply_decisions(rows, acts, "zed", audit) == 5
    assert [r["status"] for r in rows] == ["migrate", "exclude", "dispose", "hold", "migrate"]
    assert rows[4]["record_class"] == "board_minutes" and rows[4]["retention_label"] == "Permanent" and rows[4]["confidence"] == 1.0
    assert all("reviewed:" in r["reasons"] and "by zed" in r["reasons"] for r in rows) and rows[0]["reasons"].startswith("why; reviewed")
    e = audit.entries()
    assert [x["action"] for x in e] == ["decision:approve", "decision:exclude", "decision:dispose", "decision:hold", "decision:reclassify"]
    assert e[1]["detail"]["reason"] == "dup" and e[1]["detail"]["from"] == ["review", "invoice"] and e[1]["actor"] == "zed"


@pytest.mark.story("REC-5")
def test_error_messages_are_specific(tmp_path):
    audit, rows = AuditLog(tmp_path / "a.jsonl"), [row("a"), row("m", status="migrate"), row("u", cls="unclassified")]
    cases = [({"source_path": "zz", "action": "approve"}, "unknown item"), ({"source_path": "m", "action": "approve"}, "not awaiting review"),
             ({"source_path": "a", "action": "nuke"}, "unknown action"), ({"source_path": "u", "action": "approve"}, "reclassify it first"),
             ({"source_path": "a", "action": "reclassify", "record_class": "unclassified"}, "invalid record class"),
             ({"source_path": "a", "action": "reclassify"}, "invalid record class")]
    for d, msg in cases:
        with pytest.raises(review.DecisionError, match=msg):
            review.apply_decisions(rows, [d], "x", audit)


@pytest.mark.story("REC-6")
def test_audit_genesis_and_each_link_is_checked(tmp_path):
    import json
    log = AuditLog(tmp_path / "a.jsonl")
    assert log.head() == "0" * 64
    e1 = log.append("u", "a", "s1", ts=1.0); e2 = log.append("u", "a", "s2", ts=2.0)
    assert e1["prev"] == "0" * 64 and e2["prev"] == e1["hash"] and log.head() == e2["hash"]
    lines = log.path.read_text().splitlines()
    for field, bad in (("prev", "f" * 64), ("seq", 7)):             # a lone wrong link/sequence, with hash recomputed, is still caught
        from records.audit import _h
        e = json.loads(lines[1]); e[field] = bad; e["hash"] = _h(e)
        log.path.write_text(lines[0] + "\n" + json.dumps(e) + "\n")
        assert log.verify() == (False, 2)
