"""Characterisation tests: pin the current outputs of the policy/claim/ops handlers and the worker loop."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "workers"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import handlers as h  # noqa: E402
import runner  # noqa: E402
from ledger import connect  # noqa: E402


@pytest.fixture
def con():
    return connect(":memory:")


def events(con):
    return [(r["ref"], r["event"]) for r in con.execute("SELECT ref,event FROM events ORDER BY id")]


def test_registry_has_every_topic():
    assert set(h.HANDLERS) == {
        "records-analyse", "records-apply-decisions", "records-finalize", "rate-risk", "ingest-customers",
        "resolve-entities", "apply-steward-decisions", "build-profiles", "portfolio-metrics", "run-reserving",
        "update-guidelines", "notify-owner", "ai-process-documents", "ai-triage", "validate-application",
        "calculate-premium", "issue-policy", "send-documents", "notify-applicant", "create-invoice",
        "send-reminder", "reconcile-payment", "lapse-policy", "verify-coverage", "fraud-score", "settle-claim",
        "escalate-payout", "close-claim", "reject-claim"}
    assert all(callable(f) for f in h.HANDLERS.values())


def test_validate_application_messages():
    out = h.validate_application(None, {"applicant": "", "product": "boat", "age": 3, "sumInsured": -5})
    assert out == {"valid": False, "validationErrors": "missing applicant; unknown product 'boat'; sumInsured must be positive"}
    assert h.validate_application(None, {"applicant": "A", "product": "motor", "age": 3, "sumInsured": 5}) == {
        "valid": True, "validationErrors": ""}


def test_flat_rate_premium_and_loadings(con):
    assert h.calculate_premium(con, {"product": "home", "sumInsured": 1_000_000, "loadingPct": 25, "manualLoadingPct": 25}) == {"premium": 6000.0}
    assert h.calculate_premium_legacy(con, {"product": "health", "sumInsured": 100})["premium"] == 2.0


def test_fraud_score_is_deterministic():
    base = {"claimNumber": "CLM-2026-000001", "claimAmount": 100, "description": ""}
    s0 = h.fraud_score(None, base)["fraudScore"]
    assert s0 == h.fraud_score(None, base)["fraudScore"] and 0 <= s0 < 25
    assert h.fraud_score(None, {**base, "description": "URGENT cash"})["fraudScore"] == s0 + 40
    assert h.fraud_score(None, {**base, "claimAmount": 600_000})["fraudScore"] == s0 + 20
    assert h.fraud_score(None, {**base, "description": "cash", "claimAmount": 10**7})["fraudScore"] == min(s0 + 60, 100)


def test_policy_lifecycle_state_and_events(con):
    v = {"applicant": "A", "product": "motor", "sumInsured": 1000, "premium": 35.0}
    out = h.issue_policy(con, v)
    assert out["policyNumber"].startswith("POL-") and out["policyNumber"].endswith("-000001")
    assert (out["dueIn"], out["graceIn"]) == (h.DUE_IN, h.GRACE_IN)
    pol = {**v, **out}
    h.create_invoice(con, pol)
    assert h.reconcile_payment(con, {**pol, "amountPaid": 10, "paymentRef": "r"}) == {"premiumPaid": False}
    assert con.execute("SELECT status FROM payments").fetchone()[0] == "partial"
    assert h.reconcile_payment(con, {**pol, "amountPaid": 35, "paymentRef": "r"}) == {"premiumPaid": True}
    assert con.execute("SELECT status FROM policies").fetchone()[0] == "active"
    h.lapse_policy(con, pol)
    assert con.execute("SELECT status FROM policies").fetchone()[0] == "lapsed"
    assert con.execute("SELECT status FROM payments").fetchone()[0] == "overdue"
    assert h.send_documents(con, pol) == {} and h.send_reminder(con, pol) == {}
    assert [e for _, e in events(con)] == [
        "policy issued", "invoice raised 35.0", "payment 10.0 reconciled (partial)", "payment 35.0 reconciled (full)",
        "policy lapsed for non-payment", "documents sent to A", "payment reminder sent"]


def test_claim_branches(con):
    h.issue_policy(con, {"applicant": "A", "product": "motor", "sumInsured": 1000, "premium": 1.0})
    unpaid = h.verify_coverage(con, {"policyNumber": "POL-X", "claimAmount": 5})
    assert unpaid["covered"] is False and unpaid["rejectReason"] == "unknown policy"
    pending = h.verify_coverage(con, {"policyNumber": con.execute("SELECT policy_number FROM policies").fetchone()[0], "claimAmount": 5})
    assert pending["rejectReason"] == "policy is pending_payment" and pending["payoutSla"] == h.PAYOUT_SLA
    con.execute("UPDATE policies SET status='active'")
    num = con.execute("SELECT policy_number FROM policies").fetchone()[0]
    assert h.verify_coverage(con, {"policyNumber": num, "claimAmount": 2000})["rejectReason"] == "claim exceeds sum insured"
    ok = h.verify_coverage(con, {"policyNumber": num, "claimAmount": 500})
    assert ok["covered"] is True and ok["rejectReason"] == ""
    assert h.settle_claim(con, {"claimNumber": ok["claimNumber"], "claimAmount": 500}) == {"approvedAmount": 500}
    assert h.settle_claim(con, {"claimNumber": ok["claimNumber"], "claimAmount": 500, "approvedAmount": 400}) == {"approvedAmount": 400}
    h.escalate_payout(con, {"claimNumber": ok["claimNumber"]})
    assert con.execute("SELECT status FROM payments WHERE kind='payout' ORDER BY id LIMIT 1").fetchone()[0] == "escalated"
    h.reject_claim(con, {"claimNumber": ok["claimNumber"]})
    row = con.execute("SELECT status,reason FROM claims WHERE claim_number=?", (ok["claimNumber"],)).fetchone()
    assert (row[0], row[1]) == ("rejected", "denied by assessor")


def test_notifications(con):
    h.notify_applicant(con, {"applicant": "A", "validationErrors": "bad"})
    h.notify_applicant(con, {"uwDecision": "decline"})
    h.notify(con, {"rejectRate": 0.5, "batchId": "B1"})
    h.notify(con, {"notifyMessage": "hi"})
    out = h.update_guidelines(con, {"committeeDecision": "ok"})
    assert out["guidelineVersion"].startswith("UW-GL-")
    assert events(con)[:4] == [("A", "rejection notice sent (bad)"), ("?", "rejection notice sent (underwriting: decline)"),
                               ("B1", "notified owner: batch rejected: reject rate 0.5"), ("ops", "notified owner: hi")]


def test_ai_url_must_be_http(monkeypatch):
    monkeypatch.setattr(h, "AI_URL", "file:///etc/passwd")
    with pytest.raises(ValueError, match="http"):
        h._ai("/x", {})


def test_runner_main_completes_and_fails_tasks(con, monkeypatch):
    sent = []
    task = lambda tid, topic, **k: {"id": tid, "topicName": topic, "processInstanceId": "abcdefghijk",
                                    "variables": {"applicant": {"value": "A"}, "product": {"value": "motor"},
                                                  "age": {"value": 3}, "sumInsured": {"value": 5}}, **k}
    batches = [[task("1", "validate-application"), task("2", "no-such-topic", retries=None), task("3", "validate-application", retries=2)]]

    def fake_call(path, body):
        sent.append((path, body))
        if path.endswith("fetchAndLock"):
            if batches:
                return batches.pop()
            raise KeyboardInterrupt
        return None
    monkeypatch.setattr(runner, "call", fake_call)
    monkeypatch.setattr(runner, "connect", lambda: con)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        runner.main()
    by_path = {p: b for p, b in sent if not p.endswith("fetchAndLock")}
    assert by_path["/external-task/1/complete"] == {"workerId": runner.WORKER_ID, "variables": {
        "valid": {"value": True, "type": "Boolean"}, "validationErrors": {"value": "", "type": "String"}}}
    assert by_path["/external-task/2/failure"]["retries"] == 2 and by_path["/external-task/2/failure"]["retryTimeout"] == 5000
    assert by_path["/external-task/3/complete"]["workerId"] == runner.WORKER_ID
    first = sent[0][1]
    assert first["maxTasks"] == 10 and {t["topicName"] for t in first["topics"]} == set(h.HANDLERS)


def test_runner_survives_engine_outage(con, monkeypatch):
    calls = []

    def fake_call(path, body):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("down")
        raise KeyboardInterrupt
    monkeypatch.setattr(runner, "call", fake_call)
    monkeypatch.setattr(runner, "connect", lambda: con)
    slept = []
    monkeypatch.setattr(runner.time, "sleep", slept.append)
    with pytest.raises(KeyboardInterrupt):
        runner.main()
    assert slept == [3]


def test_ai_handlers_accept_an_injected_client(con):
    def fake(path, body):
        return {"/v1/documents/process": {"doc_type": "invoice", "issues": ["x"], "injection_flags": ["ignore previous"]},
                "/v1/documents/summarize": {"summary": "s"},
                "/v1/claims/triage": {"route": "siu", "confidence": 0.9, "rationale": "r", "trace_id": "t9"}}[path]
    d = h.ai_process_documents(con, {"claimNumber": "C", "claimDocuments": '[{"text": "a"}]'}, ai=fake)
    assert d == {"docsComplete": False, "docIssues": "x; injection flags: ['ignore previous']", "docTypes": "invoice", "claimSummary": "s"}
    t = h.ai_triage(con, {"claimNumber": "C", "policyNumber": "P", "claimAmount": 5}, ai=fake)
    assert t == {"aiRoute": "siu", "aiRationale": "r", "aiTraceId": "t9"}


def test_records_analyse_accepts_an_injected_llm(con, tmp_path, monkeypatch):
    from ai.client import LLMClient
    from records import synth
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("RECORDS_OUT", str(tmp_path / "out"))
    src = tmp_path / "lib"
    synth.build(src, n=30)
    out = h.records_analyse(con, {"sourceDir": str(src), "batchLabel": "b"}, llm=LLMClient())
    assert out["filesAnalysed"] > 0
