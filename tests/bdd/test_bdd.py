import sys
import tempfile
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers"))
import handlers as h  # noqa: E402
from ai.client import LLMClient  # noqa: E402
from ai.observe import TraceStore  # noqa: E402
from insurance.underwriting.risk import life_rating  # noqa: E402
from ledger import connect  # noqa: E402
from records import pipeline, report, review, synth  # noqa: E402
from records.audit import AuditLog  # noqa: E402

FEATURES = Path(__file__).resolve().parents[2] / "features"
scenarios(str(FEATURES / "records_review.feature"), str(FEATURES / "underwriting.feature"), str(FEATURES / "payments.feature"))
pytestmark = pytest.mark.bdd


@pytest.fixture
def ctx(tmp_path):
    return {"tmp": tmp_path}


@given(parsers.parse("an analysed legacy library of {n:d} documents"))
def _lib(ctx, n):
    d = Path(tempfile.mkdtemp()); synth.build(d / "lib", n=n)
    ctx["rows"] = pipeline.analyse(d / "lib", LLMClient(store=TraceStore(":memory:")))
    ctx["audit"] = AuditLog(ctx["tmp"] / "audit.jsonl")


@given("the review queue is not empty")
def _nonempty(ctx):
    assert any(r["status"] == "review" for r in ctx["rows"])


@when(parsers.parse('the records manager "{who}" applies the recommended dispositions'))
def _apply(ctx, who):
    ctx["n"] = review.apply_decisions(ctx["rows"], review.recommended_decisions(ctx["rows"]), who, ctx["audit"])


@when("the records manager tries to approve an unclassified item")
def _bad(ctx):
    r = next(r for r in ctx["rows"] if r["status"] == "review" and r["record_class"] == "unclassified")
    try:
        review.apply_decisions(ctx["rows"], [{"source_path": r["source_path"], "action": "approve"}], "bob", ctx["audit"])
        ctx["err"] = ""
    except review.DecisionError as e:
        ctx["err"] = str(e)


@then("the review queue is empty")
def _empty(ctx):
    assert not any(r["status"] == "review" for r in ctx["rows"])


@then("every decision appears in the audit log")
def _audited(ctx):
    assert len(ctx["audit"].entries()) == ctx["n"] > 0


@then("the audit chain verifies")
def _chain(ctx):
    assert ctx["audit"].verify() == (True, None)


@then(parsers.parse('the readiness verdict is "{v}"'))
def _verdict(ctx, v):
    text, _ = report.readiness(ctx["rows"], ctx["audit"])
    assert f"**Verdict: {v}**" in text


@then(parsers.parse('the decision is rejected with "{msg}"'))
def _rejected(ctx, msg):
    assert msg in ctx["err"]


@then("the audit log has no entries")
def _no_audit(ctx):
    assert ctx["audit"].entries() == []


@when(parsers.parse("a {age:d} year old {smoking} {occupation} with income {income:d} applies for cover of {cover:d}"))
def _apply_uw(ctx, age, smoking, occupation, income, cover):
    ctx["rating"] = life_rating(age, smoking == "smoker", occupation, income, cover)


@then(parsers.parse('the rating class is "{cls}"'))
def _cls(ctx, cls):
    assert ctx["rating"]["rating_class"] == cls


@given(parsers.parse("a policy with an invoiced premium of {amt:d}"))
def _policy(ctx, amt):
    con = connect(":memory:")
    v = dict(applicant="A", product="motor", age=40, sumInsured=1000, premium=float(amt))
    v.update(h.issue_policy(con, v)); h.create_invoice(con, v)
    ctx.update(con=con, v=v)


@when(parsers.parse("a payment of {paid:d} is received"))
def _pay(ctx, paid):
    h.reconcile_payment(ctx["con"], {**ctx["v"], "amountPaid": float(paid), "paymentRef": "T"})


@then(parsers.parse('the policy status is "{s}"'))
def _pstatus(ctx, s):
    assert ctx["con"].execute("SELECT status FROM policies").fetchone()[0] == s


@then(parsers.parse('the payment status is "{s}"'))
def _paystatus(ctx, s):
    assert ctx["con"].execute("SELECT status FROM payments").fetchone()[0] == s
