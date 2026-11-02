"""Policy-issuance and premium-collection handlers: (con, vars) -> dict of output variables."""
import datetime as dt
import os

from ledger import log, next_number

DUE_IN = os.environ.get("DUE_IN", "P30D")      # ISO-8601; set PT30S for demos
GRACE_IN = os.environ.get("GRACE_IN", "P15D")
BASE_RATE = {"term_life": 0.0012, "health": 0.02, "motor": 0.035, "home": 0.004}  # per unit of sum insured, annual


def validate_application(con, v):
    errs = []
    for f in ("applicant", "product", "age", "sumInsured"):
        if v.get(f) in (None, ""):
            errs.append(f"missing {f}")
    if v.get("product") not in BASE_RATE:
        errs.append(f"unknown product {v.get('product')!r}")
    if isinstance(v.get("sumInsured"), int) and v["sumInsured"] <= 0:
        errs.append("sumInsured must be positive")
    return {"valid": not errs, "validationErrors": "; ".join(errs)}


def calculate_premium_legacy(con, v):
    loading = int(v.get("loadingPct") or 0) + int(v.get("manualLoadingPct") or 0)
    premium = v["sumInsured"] * BASE_RATE[v["product"]] * (1 + loading / 100)
    return {"premium": round(premium, 2)}


def calculate_premium(con, v):
    """Term life: actuarial gross premium (equivalence principle) at the underwriting mortality multiple.
    Other products keep the flat-rate table."""
    if v["product"] != "term_life":
        return calculate_premium_legacy(con, v)
    from insurance.actuarial import mortality as mo
    mult = float(v.get("mortalityMultiple") or 1.0) * (1 + int(v.get("loadingPct") or 0) / 100)
    cm = mo.Commutation(mo.MortalityTable.gompertz_makeham(multiple=mult), float(os.environ.get("VALUATION_RATE", "0.06")))
    term = int(v.get("termYears") or 20)
    g = mo.term_gross_premium(cm, int(v["age"]), term, v["sumInsured"], initial_expense=2000, renewal_expense=300, commission=0.10)
    return {"premium": round(g, 2), "premiumBasis": f"term {term}y, mortality x{mult:.2f}, i={cm.i:.2%}, illustrative table"}


def rate_risk(con, v):
    from insurance.underwriting.risk import life_rating
    if v["product"] != "term_life":
        return {"ratingClass": "standard", "mortalityMultiple": 1.0, "ratingFlags": ""}
    r = life_rating(int(v["age"]), bool(v.get("smoker")), v.get("occupation", ""), float(v.get("annualIncome") or 0), v["sumInsured"])
    return {"ratingClass": r["rating_class"], "mortalityMultiple": r["mortality_multiple"], "ratingFlags": "; ".join(r["flags"])}


def issue_policy(con, v):
    number = next_number(con, "POL", "policies", "policy_number")
    start = dt.date.today()
    con.execute("INSERT INTO policies VALUES(?,?,?,?,?,?,?,?)",
                (number, v["applicant"], v["product"], v["sumInsured"], v["premium"], "pending_payment",
                 start.isoformat(), start.replace(year=start.year + 1).isoformat()))
    con.commit()
    log(con, number, "policy issued")
    return {"policyNumber": number, "dueIn": DUE_IN, "graceIn": GRACE_IN}


def send_documents(con, v):
    log(con, v["policyNumber"], f"documents sent to {v['applicant']}")  # plug in email/PDF generation here
    return {}


def notify_applicant(con, v):
    reason = v.get("validationErrors") or f"underwriting: {v.get('uwDecision')}"
    log(con, v.get("applicant", "?"), f"rejection notice sent ({reason})")
    return {}


def create_invoice(con, v):
    due = dt.date.today() + dt.timedelta(days=30)
    con.execute("INSERT INTO payments(kind,ref,amount,due_date,status,updated_at) VALUES('premium',?,?,?,'invoiced',strftime('%s','now'))",
                (v["policyNumber"], v["premium"], due.isoformat()))
    con.commit()
    log(con, v["policyNumber"], f"invoice raised {v['premium']}")
    return {}


def send_reminder(con, v):
    log(con, v["policyNumber"], "payment reminder sent")
    return {}


def reconcile_payment(con, v):
    paid = float(v.get("amountPaid") or 0)
    row = con.execute("SELECT amount FROM payments WHERE kind='premium' AND ref=?", (v["policyNumber"],)).fetchone()
    full = paid + 1e-6 >= row["amount"]
    con.execute("UPDATE payments SET status=?, paid_amount=?, payment_ref=?, updated_at=strftime('%s','now') WHERE kind='premium' AND ref=?",
                ("paid" if full else "partial", paid, v.get("paymentRef"), v["policyNumber"]))
    con.execute("UPDATE policies SET status=? WHERE policy_number=?", ("active" if full else "pending_payment", v["policyNumber"]))
    con.commit()
    log(con, v["policyNumber"], f"payment {paid} reconciled ({'full' if full else 'partial'})")
    return {"premiumPaid": full}


def lapse_policy(con, v):
    con.execute("UPDATE policies SET status='lapsed' WHERE policy_number=?", (v["policyNumber"],))
    con.execute("UPDATE payments SET status='overdue' WHERE kind='premium' AND ref=?", (v["policyNumber"],))
    con.commit()
    log(con, v["policyNumber"], "policy lapsed for non-payment")
    return {}
