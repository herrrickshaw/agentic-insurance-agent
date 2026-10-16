"""Business logic per external-task topic. Each handler: (con, vars) -> dict of output variables.

Variables arrive as plain Python values; integers are returned as ints and typed by the runner.
"""
import datetime as dt
import hashlib
import json
import os
import urllib.request

from ledger import log, next_number

DUE_IN = os.environ.get("DUE_IN", "P30D")      # ISO-8601; set PT30S for demos
GRACE_IN = os.environ.get("GRACE_IN", "P15D")
PAYOUT_SLA = os.environ.get("PAYOUT_SLA", "P5D")
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


def calculate_premium(con, v):
    loading = int(v.get("loadingPct") or 0) + int(v.get("manualLoadingPct") or 0)
    premium = v["sumInsured"] * BASE_RATE[v["product"]] * (1 + loading / 100)
    return {"premium": round(premium, 2)}


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


def verify_coverage(con, v):
    claim_no = next_number(con, "CLM", "claims", "claim_number")
    pol = con.execute("SELECT * FROM policies WHERE policy_number=?", (v["policyNumber"],)).fetchone()
    reason = None
    if not pol:
        reason = "unknown policy"
    elif pol["status"] != "active":
        reason = f"policy is {pol['status']}"
    elif v["claimAmount"] > pol["sum_insured"]:
        reason = "claim exceeds sum insured"
    con.execute("INSERT INTO claims VALUES(?,?,?,?,?,?)",
                (claim_no, v["policyNumber"], v["claimAmount"], None, "open" if not reason else "rejected", reason))
    con.commit()
    log(con, claim_no, f"coverage check: {'covered' if not reason else reason}")
    return {"claimNumber": claim_no, "covered": reason is None, "rejectReason": reason or "", "payoutSla": PAYOUT_SLA}


def fraud_score(con, v):
    """Deterministic placeholder. Replace with a model / rules service (e.g. an ML endpoint)."""
    text = (v.get("description") or "").lower()
    score = int(hashlib.sha256(v["claimNumber"].encode()).hexdigest(), 16) % 25
    score += 40 if any(w in text for w in ("cash", "no police", "total loss", "urgent")) else 0
    score += 20 if v["claimAmount"] > 500_000 else 0
    return {"fraudScore": min(score, 100)}


def settle_claim(con, v):
    amount = int(v.get("approvedAmount") or v["claimAmount"])
    con.execute("UPDATE claims SET status='approved', approved_amount=? WHERE claim_number=?", (amount, v["claimNumber"]))
    con.execute("INSERT INTO payments(kind,ref,amount,status,updated_at) VALUES('payout',?,?,'pending',strftime('%s','now'))",
                (v["claimNumber"], amount))
    con.commit()
    log(con, v["claimNumber"], f"payout of {amount} instructed")
    return {"approvedAmount": amount}


def escalate_payout(con, v):
    con.execute("UPDATE payments SET status='escalated' WHERE kind='payout' AND ref=?", (v["claimNumber"],))
    con.commit()
    log(con, v["claimNumber"], "payout SLA breached - escalated to finance")
    return {}


def close_claim(con, v):
    con.execute("UPDATE payments SET status='paid', payment_ref=?, paid_amount=amount, updated_at=strftime('%s','now') WHERE kind='payout' AND ref=?",
                (v.get("payoutRef"), v["claimNumber"]))
    con.execute("UPDATE claims SET status='paid' WHERE claim_number=?", (v["claimNumber"],))
    con.commit()
    log(con, v["claimNumber"], "payout confirmed, claim closed")
    return {}


def reject_claim(con, v):
    con.execute("UPDATE claims SET status='rejected', reason=COALESCE(NULLIF(reason,''),'denied by assessor') WHERE claim_number=?",
                (v["claimNumber"],))
    con.commit()
    log(con, v["claimNumber"], "claim rejected, claimant notified")
    return {}


AI_URL = os.environ.get("AI_SERVICE_URL", "http://localhost:8090")


def _ai(path, body):
    req = urllib.request.Request(AI_URL + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def ai_process_documents(con, v):
    """Classify/extract each submitted document, flag gaps, summarise for the adjuster (GenAI service)."""
    docs = json.loads(v.get("claimDocuments") or "[]")
    if not docs:
        return {"docsComplete": False, "docIssues": "no documents submitted", "claimSummary": "No documents."}
    results = [_ai("/v1/documents/process", {"text": d["text"]}) for d in docs]
    types = {r["doc_type"] for r in results}
    issues = [i for r in results for i in r["issues"]] + [f"injection flags: {r['injection_flags']}" for r in results if r["injection_flags"]]
    summary = _ai("/v1/documents/summarize", {"documents": [{"text": d["text"]} for d in docs]})
    log(con, v["claimNumber"], f"AI processed {len(docs)} docs types={sorted(types)} issues={len(issues)}")
    return {"docsComplete": "claim_form" in types and not issues, "docIssues": "; ".join(issues)[:900],
            "docTypes": ",".join(sorted(types)), "claimSummary": summary["summary"][:900]}


def ai_triage(con, v):
    r = _ai("/v1/claims/triage", {"claim_number": v["claimNumber"], "policy_number": v["policyNumber"],
                                   "claim_amount": int(v["claimAmount"]), "description": v.get("description", "")})
    log(con, v["claimNumber"], f"AI triage recommends {r['route']} (conf {r['confidence']}, trace {r['trace_id']})")
    return {"aiRoute": r["route"], "aiRationale": r["rationale"][:500], "aiTraceId": r["trace_id"]}


HANDLERS = {
    "ai-process-documents": ai_process_documents, "ai-triage": ai_triage,
    "validate-application": validate_application, "calculate-premium": calculate_premium,
    "issue-policy": issue_policy, "send-documents": send_documents, "notify-applicant": notify_applicant,
    "create-invoice": create_invoice, "send-reminder": send_reminder, "reconcile-payment": reconcile_payment,
    "lapse-policy": lapse_policy, "verify-coverage": verify_coverage, "fraud-score": fraud_score,
    "settle-claim": settle_claim, "escalate-payout": escalate_payout, "close-claim": close_claim,
    "reject-claim": reject_claim,
}
