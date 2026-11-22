"""Worker handlers and ledger paths the BPMN flows exercise but the main suites skip: notices, escalation, AI HTTP client,
premium tables, validation boundaries, and ledger numbering."""
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "workers"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import handlers as h  # noqa: E402
import ledger  # noqa: E402


@pytest.fixture
def con():
    return ledger.connect(":memory:")


def events(con):
    return [(r["ref"], r["event"]) for r in con.execute("SELECT ref, event FROM events ORDER BY id")]


def issue(con, **kw):
    v = dict(applicant="A", product="motor", sumInsured=100_000, premium=3500.0, **kw)
    v.update(h.issue_policy(con, v))
    return v


# ---- validate_application ---------------------------------------------------------------------

def test_validate_reports_each_missing_field_and_unknown_product(con):
    out = h.validate_application(con, {})
    assert not out["valid"]
    assert out["validationErrors"] == "missing applicant; missing product; missing age; missing sumInsured; unknown product None"
    out = h.validate_application(con, dict(applicant="", product="boat", age=None, sumInsured=5))
    assert out["validationErrors"] == "missing applicant; missing age; unknown product 'boat'"


def test_validate_zero_age_and_zero_sum_are_present_but_zero_sum_not_positive(con):
    ok = h.validate_application(con, dict(applicant="A", product="health", age=0, sumInsured=1))
    assert ok == {"valid": True, "validationErrors": ""}               # 0 is a value, not 'missing'
    bad = h.validate_application(con, dict(applicant="A", product="health", age=30, sumInsured=0))
    assert bad["validationErrors"] == "sumInsured must be positive"


def test_validate_ignores_bool_and_string_sum_for_positivity_check(con):
    assert h.validate_application(con, dict(applicant="A", product="home", age=3, sumInsured=False))["valid"]
    assert h.validate_application(con, dict(applicant="A", product="home", age=3, sumInsured="-5"))["valid"]


# ---- premium ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("product,rate", sorted(h.BASE_RATE.items()))
def test_legacy_premium_uses_base_rate_per_product(con, product, rate):
    assert h.calculate_premium_legacy(con, dict(product=product, sumInsured=1_000_000))["premium"] == round(1_000_000 * rate, 2)


def test_legacy_premium_adds_both_loadings_as_percent(con):
    out = h.calculate_premium_legacy(con, dict(product="motor", sumInsured=100_000, loadingPct=10, manualLoadingPct="5"))
    assert out["premium"] == round(100_000 * 0.035 * 1.15, 2)
    assert h.calculate_premium_legacy(con, dict(product="motor", sumInsured=100_000, loadingPct=None))["premium"] == 3500.0


def test_calculate_premium_non_life_delegates_to_legacy(con):
    v = dict(product="home", sumInsured=2_000_000, loadingPct=20)
    assert h.calculate_premium(con, v) == h.calculate_premium_legacy(con, v)


def test_calculate_premium_term_life_is_actuarial_and_loading_raises_it(con, monkeypatch):
    monkeypatch.delenv("VALUATION_RATE", raising=False)
    v = dict(product="term_life", age=35, sumInsured=5_000_000, termYears=15)
    base = h.calculate_premium(con, v)
    assert base["premium"] > 0 and base["premiumBasis"].startswith("term 15y, mortality x1.00, i=6.00%")
    loaded = h.calculate_premium(con, {**v, "loadingPct": 50, "mortalityMultiple": 1.0})
    assert loaded["premium"] > base["premium"] and "mortality x1.50" in loaded["premiumBasis"]
    default_term = h.calculate_premium(con, {k: x for k, x in v.items() if k != "termYears"})
    assert default_term["premiumBasis"].startswith("term 20y")
    monkeypatch.setenv("VALUATION_RATE", "0.04")
    assert "i=4.00%" in h.calculate_premium(con, v)["premiumBasis"]


# ---- notices / reminders -------------------------------------------------------------------------------

def test_send_documents_logs_against_policy(con):
    assert h.send_documents(con, dict(policyNumber="POL-1", applicant="Asha")) == {}
    assert events(con) == [("POL-1", "documents sent to Asha")]


def test_notify_applicant_prefers_validation_errors_over_uw_decision(con):
    h.notify_applicant(con, dict(applicant="Asha", validationErrors="missing age", uwDecision="decline"))
    h.notify_applicant(con, dict(applicant="Ravi", uwDecision="decline"))
    h.notify_applicant(con, {})
    assert events(con) == [("Asha", "rejection notice sent (missing age)"),
                           ("Ravi", "rejection notice sent (underwriting: decline)"),
                           ("?", "rejection notice sent (underwriting: None)")]


def test_notify_applicant_blank_validation_errors_fall_back(con):
    h.notify_applicant(con, dict(applicant="A", validationErrors="", uwDecision="refer"))
    assert events(con)[0][1] == "rejection notice sent (underwriting: refer)"


def test_send_reminder_logs(con):
    assert h.send_reminder(con, dict(policyNumber="POL-9")) == {}
    assert events(con) == [("POL-9", "payment reminder sent")]


def test_create_invoice_creates_premium_payment_due_in_30_days(con):
    import datetime as dt
    h.create_invoice(con, dict(policyNumber="POL-1", premium=1200.5))
    r = con.execute("SELECT kind, ref, amount, status, due_date FROM payments").fetchone()
    assert tuple(r)[:4] == ("premium", "POL-1", 1200.5, "invoiced")
    assert r["due_date"] == (dt.date.today() + dt.timedelta(days=30)).isoformat()
    assert events(con) == [("POL-1", "invoice raised 1200.5")]


# ---- payment & claim lifecycle ----------------------------------------------------------------------

def test_reconcile_payment_boundaries(con):
    v = issue(con)
    h.create_invoice(con, v)
    assert h.reconcile_payment(con, {**v, "amountPaid": 3499.99})["premiumPaid"] is False
    assert con.execute("SELECT status FROM policies WHERE policy_number=?", (v["policyNumber"],)).fetchone()[0] == "pending_payment"
    assert h.reconcile_payment(con, {**v, "amountPaid": 3500.0, "paymentRef": "UTR1"})["premiumPaid"] is True
    row = con.execute("SELECT status, paid_amount, payment_ref FROM payments").fetchone()
    assert tuple(row) == ("paid", 3500.0, "UTR1")
    assert con.execute("SELECT status FROM policies").fetchone()[0] == "active"
    assert h.reconcile_payment(con, {**v, "amountPaid": None})["premiumPaid"] is False       # missing amount = 0 paid
    assert con.execute("SELECT status FROM policies").fetchone()[0] == "pending_payment"


def test_reconcile_payment_tolerance_is_one_millionth(con):
    v = issue(con)
    h.create_invoice(con, {**v, "premium": 1e-6})
    assert h.reconcile_payment(con, {**v, "amountPaid": 0})["premiumPaid"] is True        # 0 + 1e-6 >= 1e-6
    con.execute("UPDATE payments SET amount=?", (2e-6,))
    assert h.reconcile_payment(con, {**v, "amountPaid": 0})["premiumPaid"] is False


def test_lapse_policy_marks_policy_and_premium_overdue(con):
    v = issue(con)
    h.create_invoice(con, v)
    h.lapse_policy(con, v)
    assert con.execute("SELECT status FROM policies").fetchone()[0] == "lapsed"
    assert con.execute("SELECT status FROM payments").fetchone()[0] == "overdue"
    assert events(con)[-1] == (v["policyNumber"], "policy lapsed for non-payment")


def active_policy(con):
    v = issue(con)
    h.create_invoice(con, v)
    h.reconcile_payment(con, {**v, "amountPaid": 3500.0})
    return v


def test_verify_coverage_rejects_inactive_policy_with_status_in_reason(con):
    v = issue(con)                                                   # still pending_payment
    out = h.verify_coverage(con, dict(policyNumber=v["policyNumber"], claimAmount=10))
    assert out["covered"] is False and out["rejectReason"] == "policy is pending_payment"
    assert con.execute("SELECT status, reason FROM claims").fetchone()[:] == ("rejected", "policy is pending_payment")


def test_verify_coverage_sum_insured_boundary_is_inclusive(con):
    v = active_policy(con)
    assert h.verify_coverage(con, dict(policyNumber=v["policyNumber"], claimAmount=100_000))["covered"] is True
    assert h.verify_coverage(con, dict(policyNumber=v["policyNumber"], claimAmount=100_001))["covered"] is False


def test_verify_coverage_numbers_claims_sequentially_and_returns_sla(con, monkeypatch):
    monkeypatch.setattr(h, "PAYOUT_SLA", "PT1H")
    v = active_policy(con)
    a = h.verify_coverage(con, dict(policyNumber=v["policyNumber"], claimAmount=1))
    b = h.verify_coverage(con, dict(policyNumber=v["policyNumber"], claimAmount=1))
    assert a["claimNumber"].endswith("-000001") and b["claimNumber"].endswith("-000002")
    assert a["payoutSla"] == "PT1H" and a["rejectReason"] == ""


def test_escalate_payout_marks_only_that_claims_payout(con):
    v = active_policy(con)
    c1 = h.verify_coverage(con, dict(policyNumber=v["policyNumber"], claimAmount=5))
    c2 = h.verify_coverage(con, dict(policyNumber=v["policyNumber"], claimAmount=6))
    h.settle_claim(con, {**c1, "claimAmount": 5})
    h.settle_claim(con, {**c2, "claimAmount": 6})
    assert h.escalate_payout(con, c1) == {}
    st = {r["ref"]: r["status"] for r in con.execute("SELECT ref, status FROM payments WHERE kind='payout'")}
    assert st == {c1["claimNumber"]: "escalated", c2["claimNumber"]: "pending"}
    assert events(con)[-1] == (c1["claimNumber"], "payout SLA breached - escalated to finance")


def test_close_claim_pays_out_and_records_reference(con):
    v = active_policy(con)
    c = h.verify_coverage(con, dict(policyNumber=v["policyNumber"], claimAmount=40))
    h.settle_claim(con, {**c, "claimAmount": 40})
    h.close_claim(con, {**c, "payoutRef": "PAY-7"})
    p = con.execute("SELECT status, payment_ref, paid_amount, amount FROM payments WHERE kind='payout'").fetchone()
    assert tuple(p) == ("paid", "PAY-7", 40.0, 40.0)
    assert con.execute("SELECT status FROM claims WHERE claim_number=?", (c["claimNumber"],)).fetchone()[0] == "paid"


def test_reject_claim_keeps_existing_reason_but_fills_blank_one(con):
    con.execute("INSERT INTO claims VALUES('C1','P',1,NULL,'open',NULL)")
    con.execute("INSERT INTO claims VALUES('C2','P',1,NULL,'open','')")
    con.execute("INSERT INTO claims VALUES('C3','P',1,NULL,'open','exclusion 4')")
    con.commit()
    for c in ("C1", "C2", "C3"):
        assert h.reject_claim(con, dict(claimNumber=c)) == {}
    got = {r[0]: (r[1], r[2]) for r in con.execute("SELECT claim_number, status, reason FROM claims")}
    assert got == {"C1": ("rejected", "denied by assessor"), "C2": ("rejected", "denied by assessor"),
                   "C3": ("rejected", "exclusion 4")}
    assert events(con)[0] == ("C1", "claim rejected, claimant notified")


def test_fraud_score_amount_threshold_and_each_keyword():
    base = h.fraud_score(None, dict(claimNumber="CLM-7", claimAmount=0))["fraudScore"]
    assert h.fraud_score(None, dict(claimNumber="CLM-7", claimAmount=500_000))["fraudScore"] == base          # not strictly above
    assert h.fraud_score(None, dict(claimNumber="CLM-7", claimAmount=500_001))["fraudScore"] == base + 20
    for w in ("cash", "no police", "total loss", "urgent"):
        assert h.fraud_score(None, dict(claimNumber="CLM-7", claimAmount=0, description=f"This is {w.upper()}"))["fraudScore"] == base + 40
    assert h.fraud_score(None, dict(claimNumber="CLM-7", claimAmount=0, description=None))["fraudScore"] == base


def test_fraud_score_is_deterministic_per_claim_number_and_capped_at_100():
    a = h.fraud_score(None, dict(claimNumber="X", claimAmount=1))
    assert a == h.fraud_score(None, dict(claimNumber="X", claimAmount=1))
    seen = {h.fraud_score(None, dict(claimNumber=f"C{i}", claimAmount=1))["fraudScore"] for i in range(50)}
    assert len(seen) > 5 and max(seen) < 25


# ---- AI service client ------------------------------------------------------------------------------

class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ai_posts_json_to_service_url(monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen.update(url=req.full_url, body=json.loads(req.data), timeout=timeout, ct=req.get_header("Content-type"))
        return Resp(b'{"x": 1}')
    monkeypatch.setattr(h, "AI_URL", "https://ai.internal:8090")
    monkeypatch.setattr(h.urllib.request, "urlopen", fake)
    assert h._ai("/v1/ping", {"a": [1]}) == {"x": 1}
    assert seen == {"url": "https://ai.internal:8090/v1/ping", "body": {"a": [1]}, "timeout": 90, "ct": "application/json"}


@pytest.mark.parametrize("bad", ["file:///etc", "ftp://x", "localhost:8090", ""])
def test_ai_rejects_non_http_urls_before_any_request(monkeypatch, bad):
    monkeypatch.setattr(h, "AI_URL", bad)
    monkeypatch.setattr(h.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not be called"))
    with pytest.raises(ValueError, match="AI_SERVICE_URL must be http"):
        h._ai("/x", {})


def fake_ai(monkeypatch, process, summary="sum"):
    seen = []

    def f(path, body):
        seen.append((path, body))
        if path == "/v1/documents/process":
            return process(body)
        return {"summary": summary}
    monkeypatch.setattr(h, "_ai", f)
    return seen


def test_ai_process_documents_flags_issues_and_injection(con, monkeypatch):
    def proc(body):
        if "bad" in body["text"]:
            return {"doc_type": "invoice", "issues": ["amount mismatch"], "injection_flags": ["ignore previous"]}
        return {"doc_type": "claim_form", "issues": [], "injection_flags": []}
    seen = fake_ai(monkeypatch, proc, summary="S" * 1200)
    docs = [{"text": "good"}, {"text": "bad doc"}]
    out = h.ai_process_documents(con, {"claimNumber": "C1", "claimDocuments": json.dumps(docs)})
    assert out["docsComplete"] is False
    assert out["docIssues"] == "amount mismatch; injection flags: ['ignore previous']"
    assert out["docTypes"] == "claim_form,invoice"
    assert len(out["claimSummary"]) == 900
    assert seen[-1] == ("/v1/documents/summarize", {"documents": [{"text": "good"}, {"text": "bad doc"}]})
    assert events(con) == [("C1", "AI processed 2 docs types=['claim_form', 'invoice'] issues=2")]


def test_ai_process_documents_needs_a_claim_form_and_no_issues(con, monkeypatch):
    fake_ai(monkeypatch, lambda b: {"doc_type": "invoice", "issues": [], "injection_flags": []})
    out = h.ai_process_documents(con, {"claimNumber": "C", "claimDocuments": json.dumps([{"text": "x"}])})
    assert out["docsComplete"] is False and out["docIssues"] == ""
    fake_ai(monkeypatch, lambda b: {"doc_type": "claim_form", "issues": [], "injection_flags": []})
    out = h.ai_process_documents(con, {"claimNumber": "C", "claimDocuments": json.dumps([{"text": "x"}])})
    assert out["docsComplete"] is True


def test_ai_process_documents_truncates_issue_text_to_900(con, monkeypatch):
    fake_ai(monkeypatch, lambda b: {"doc_type": "claim_form", "issues": ["i" * 2000], "injection_flags": []})
    out = h.ai_process_documents(con, {"claimNumber": "C", "claimDocuments": json.dumps([{"text": "x"}])})
    assert len(out["docIssues"]) == 900


def test_ai_process_documents_empty_inputs(con):
    for v in ({"claimNumber": "C"}, {"claimNumber": "C", "claimDocuments": ""}, {"claimNumber": "C", "claimDocuments": "[]"}):
        assert h.ai_process_documents(con, v) == {"docsComplete": False, "docIssues": "no documents submitted",
                                                  "claimSummary": "No documents."}
    assert events(con) == []


def test_ai_triage_request_body_and_output_truncation(con, monkeypatch):
    seen = []
    monkeypatch.setattr(h, "_ai", lambda p, b: seen.append((p, b)) or {"route": "siu", "confidence": 0.9, "rationale": "r" * 700, "trace_id": "T"})
    out = h.ai_triage(con, {"claimNumber": "C1", "policyNumber": "P1", "claimAmount": "250000"})
    assert seen == [("/v1/claims/triage", {"claim_number": "C1", "policy_number": "P1", "claim_amount": 250000, "description": ""})]
    seen.clear()
    h.ai_triage(con, {"claimNumber": "C1", "policyNumber": "P1", "claimAmount": 5, "description": "rear-ended at a light"})
    assert seen[0][1]["description"] == "rear-ended at a light"
    assert out["aiRoute"] == "siu" and len(out["aiRationale"]) == 500 and out["aiTraceId"] == "T"
    assert events(con)[0] == ("C1", "AI triage recommends siu (conf 0.9, trace T)")


# ---- misc handlers -----------------------------------------------------------------------------------

def test_notify_message_precedence(con):
    h.notify(con, {"batchId": "B1", "notifyMessage": "custom", "rejectRate": 0.4})
    h.notify(con, {"batchId": "B2", "rejectRate": 0.4})
    h.notify(con, {"batchId": "B3"})
    h.notify(con, {"notifyMessage": ""})
    assert events(con) == [("B1", "notified owner: custom"), ("B2", "notified owner: batch rejected: reject rate 0.4"),
                           ("B3", "notified owner: attention required"), ("ops", "notified owner: attention required")]


def test_update_guidelines_versions_with_timestamp(con, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1234.9)
    assert h.update_guidelines(con, {"committeeDecision": "tighten"}) == {"guidelineVersion": "UW-GL-1234"}
    h.update_guidelines(con, {})
    assert events(con) == [("portfolio", "committee decision recorded: tighten"), ("portfolio", "committee decision recorded: n/a")]


def test_rate_risk_non_term_life_is_standard_and_term_life_flags_join(con):
    assert h.rate_risk(con, dict(product="motor")) == {"ratingClass": "standard", "mortalityMultiple": 1.0, "ratingFlags": ""}
    r = h.rate_risk(con, dict(product="term_life", age=70, smoker=False, occupation="", sumInsured=10_000_000, annualIncome=0))
    assert r["ratingClass"] == "refer" and r["ratingFlags"] == "income not declared; age 70 >= 65"


def test_handlers_registry_maps_every_topic_to_a_callable():
    assert len(h.HANDLERS) == 29
    assert all(callable(f) for f in h.HANDLERS.values())
    assert h.HANDLERS["notify-owner"] is h.notify and h.HANDLERS["build-profiles"] is h.build_profiles_h


# ---- ledger ------------------------------------------------------------------------------------------

def test_next_number_counts_rows_and_formats_year(con):
    import time
    yr = time.strftime("%Y")
    assert ledger.next_number(con, "POL", "policies", "policy_number") == f"POL-{yr}-000001"
    con.execute("INSERT INTO claims VALUES('C','P',1,NULL,'open',NULL)")
    assert ledger.next_number(con, "CLM", "claims", "claim_number") == f"CLM-{yr}-000002"


@pytest.mark.parametrize("table,col", [("policies", "claim_number"), ("claims", "policy_number"), ("payments", "id"),
                                       ("policies; DROP TABLE policies", "policy_number")])
def test_next_number_rejects_non_allow_listed_targets(con, table, col):
    with pytest.raises(ValueError, match="unsupported numbering target"):
        ledger.next_number(con, "X", table, col)
    assert con.execute("SELECT COUNT(*) FROM policies").fetchone()[0] == 0


def test_connect_creates_schema_and_row_factory(tmp_path):
    c = ledger.connect(str(tmp_path / "l.db"))
    assert c.row_factory is sqlite3.Row
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"policies", "claims", "payments", "events"} <= names
    assert "customers_raw" in names                                  # insurance.db.ensure_schema also ran


def test_connect_default_path_comes_from_module_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "DB_PATH", str(tmp_path / "default.db"))
    ledger.connect().close()
    assert (tmp_path / "default.db").exists()


def test_connect_tolerates_missing_insurance_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "insurance.db", None)           # makes `from insurance.db import ...` raise ImportError
    c = ledger.connect(":memory:")
    assert c.execute("SELECT COUNT(*) FROM policies").fetchone()[0] == 0
    assert "customers_raw" not in {r[0] for r in c.execute("SELECT name FROM sqlite_master")}


def test_log_stamps_time_and_commits(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger.time, "time", lambda: 42.5)
    p = str(tmp_path / "l.db")
    c = ledger.connect(p)
    ledger.log(c, "R", "E")
    other = sqlite3.connect(p)                                       # a second connection sees it only if committed
    assert other.execute("SELECT ts, ref, event FROM events").fetchall() == [(42.5, "R", "E")]
