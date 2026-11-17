"""Edge cases in the BPMN worker handlers: calendar edges and explicit assessor values."""
import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "workers"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import handlers as h  # noqa: E402
from ledger import connect  # noqa: E402


@pytest.fixture
def con():
    return connect(":memory:")


def _freeze_today(monkeypatch, day):
    class FrozenDate(dt.date):
        @classmethod
        def today(cls):
            return cls(day.year, day.month, day.day)
    monkeypatch.setattr(h, "dt", SimpleNamespace(date=FrozenDate, timedelta=dt.timedelta))


def test_issue_policy_on_leap_day_does_not_crash(con, monkeypatch):
    _freeze_today(monkeypatch, dt.date(2028, 2, 29))
    out = h.issue_policy(con, dict(applicant="A", product="motor", sumInsured=1000, premium=35.0))
    start, end = con.execute("SELECT start_date, end_date FROM policies WHERE policy_number=?", (out["policyNumber"],)).fetchone()
    assert start == "2028-02-29" and end == "2029-02-28"


def test_issue_policy_ordinary_day_runs_one_year(con, monkeypatch):
    _freeze_today(monkeypatch, dt.date(2026, 10, 25))
    out = h.issue_policy(con, dict(applicant="A", product="motor", sumInsured=1000, premium=35.0))
    row = con.execute("SELECT start_date, end_date, status FROM policies WHERE policy_number=?", (out["policyNumber"],)).fetchone()
    assert tuple(row) == ("2026-10-25", "2027-10-25", "pending_payment")


def _covered_claim(con, amount):
    v = dict(applicant="A", product="motor", sumInsured=100_000, premium=3500.0)
    v.update(h.issue_policy(con, v)); h.create_invoice(con, v)
    h.reconcile_payment(con, {**v, "amountPaid": 3500.0})
    c = h.verify_coverage(con, dict(policyNumber=v["policyNumber"], claimAmount=amount))
    assert c["covered"]
    return dict(policyNumber=v["policyNumber"], claimAmount=amount, **c)


def test_settle_claim_uses_assessor_amount(con):
    cv = _covered_claim(con, 50_000)
    assert h.settle_claim(con, {**cv, "approvedAmount": 30_000})["approvedAmount"] == 30_000


def test_settle_claim_fast_track_without_assessor_pays_claim_amount(con):
    cv = _covered_claim(con, 8_000)
    assert h.settle_claim(con, cv)["approvedAmount"] == 8_000


def test_settle_claim_explicit_zero_approval_is_not_replaced_by_full_claim(con):
    cv = _covered_claim(con, 50_000)
    out = h.settle_claim(con, {**cv, "approvedAmount": 0})
    assert out["approvedAmount"] == 0
    assert con.execute("SELECT amount FROM payments WHERE kind='payout' AND ref=?", (cv["claimNumber"],)).fetchone()[0] == 0


def test_validate_application_rejects_non_positive_float_sum_insured(con):
    out = h.validate_application(con, dict(applicant="A", product="motor", age=40, sumInsured=-5.0))
    assert not out["valid"] and "sumInsured must be positive" in out["validationErrors"]


def test_verify_coverage_rejects_claim_above_sum_insured_and_unknown_policy(con):
    cv = _covered_claim(con, 1)
    over = h.verify_coverage(con, dict(policyNumber=cv["policyNumber"], claimAmount=100_001))
    assert not over["covered"] and over["rejectReason"] == "claim exceeds sum insured"
    unk = h.verify_coverage(con, dict(policyNumber="POL-0000-000000", claimAmount=1))
    assert not unk["covered"] and unk["rejectReason"] == "unknown policy"


def test_fraud_score_keywords_and_large_amount_are_additive_and_capped():
    base = h.fraud_score(None, dict(claimNumber="CLM-1", claimAmount=10, description=""))["fraudScore"]
    assert 0 <= base < 25
    hot = h.fraud_score(None, dict(claimNumber="CLM-1", claimAmount=600_000, description="URGENT cash please"))["fraudScore"]
    assert hot == base + 60
