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


def _ins(con):
    from insurance.db import ensure_schema
    ensure_schema(con)


def calculate_premium_legacy(con, v):
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


def ingest_customers(con, v):
    from insurance.customers.ingest import ingest_file
    _ins(con)
    r = ingest_file(con, v["sourceFile"], v.get("sourceChannel", "unknown"))
    log(con, r["batch_id"], f"ingested {r['rows_in']} rows: {r['accepted']} ok, {r['rejected']} rejected")
    return {"batchId": r["batch_id"], "rowsIn": r["rows_in"], "accepted": r["accepted"], "rejected": r["rejected"], "rejectRate": r["reject_rate"]}


def resolve_entities(con, v):
    from insurance.customers.resolve import resolve_batch
    r = resolve_batch(con, v["batchId"])
    pend = con.execute("SELECT q.id,a.full_name,b.full_name,q.score FROM match_queue q JOIN customers_raw a ON a.raw_id=q.raw_a "
                       "JOIN customers_raw b ON b.raw_id=q.raw_b WHERE q.batch_id=? AND q.status='pending' ORDER BY q.score DESC", (v["batchId"],)).fetchall()
    sample = "; ".join(f"{p[1]} ~ {p[2]} ({p[3]:.2f})" for p in pend[:5])
    log(con, v["batchId"], f"entity resolution: {r['auto_merged']} auto-merged, {len(pend)} pending steward review")
    return {"autoMerged": r["auto_merged"], "reviewPending": len(pend), "reviewSample": sample[:900], "goldenCustomers": r["golden_customers"]}


def apply_steward_decisions(con, v):
    from insurance.customers.resolve import apply_steward_decision
    ids = [r[0] for r in con.execute("SELECT id FROM match_queue WHERE batch_id=? AND status='pending'", (v["batchId"],))]
    for i in ids:
        apply_steward_decision(con, i, bool(v.get("mergeAll")))
    log(con, v["batchId"], f"steward decided {len(ids)} pairs: {'merged' if v.get('mergeAll') else 'kept separate'}")
    return {"reviewPending": 0}


def build_profiles_h(con, v):
    from insurance.customers.profile import build_profiles
    if os.environ.get("DEMO_SEED") == "1" and con.execute("SELECT COUNT(*) FROM policy_book").fetchone()[0] == 0:
        from insurance.portfolio.synth import make_policy_book
        make_policy_book(con)                          # DEMO ONLY: stands in for the policy admin system feed
    r = build_profiles(con)
    log(con, v["batchId"], f"profiles built: {r['profiles']} (completeness {r['avg_completeness']})")
    return {"profilesBuilt": r["profiles"], "avgCompleteness": r["avg_completeness"]}


def portfolio_metrics(con, v):
    from insurance.portfolio.risk import snapshot
    m = snapshot(con, own_funds=float(v["ownFunds"]), label=v.get("portfolioLabel", "run"))
    log(con, "portfolio", f"VaR99.5 {m['var995']:.0f} = {m['var995_pct_own_funds']}% of own funds; LR {m['expected_loss_ratio']}")
    return {"var995": m["var995"], "tvar99": m["tvar99"], "var995PctOwnFunds": m["var995_pct_own_funds"],
            "maxStateSharePct": m["max_state_share_pct"], "maxState": m["max_state"], "expectedLossRatio": m["expected_loss_ratio"],
            "top10Pct": m["top10_customer_share_pct"], "totalSumInsured": m["total_sum_insured"], "portfolioPremium": m["premium"],
            "metricsSummary": f"VaR99.5={m['var995']:.3e} ({m['var995_pct_own_funds']}% of own funds); largest state {m['max_state']} {m['max_state_share_pct']}%; expected loss ratio {m['expected_loss_ratio']}"}


def run_reserving(con, v):
    from insurance.portfolio import risk as pr
    rows = [r for r in pr.policy_frame(con) if r["product"] != "term_life"]
    nl = float(pr.expected_loss(rows).sum()) if rows else 0.0
    r = pr.reserving_run(con, nl)
    return {"chainLadderReserve": r["chain_ladder_reserve"], "bfReserve": r["bf_reserve"]}


def update_guidelines(con, v):
    log(con, "portfolio", f"committee decision recorded: {v.get('committeeDecision', 'n/a')}")
    return {"guidelineVersion": f"UW-GL-{int(__import__('time').time())}"}


def notify(con, v):
    msg = v.get("notifyMessage") or (f"batch rejected: reject rate {v['rejectRate']}" if "rejectRate" in v else "attention required")
    log(con, v.get("batchId", "ops"), f"notified owner: {msg}")
    return {}


HANDLERS = {
    "rate-risk": rate_risk, "ingest-customers": ingest_customers, "resolve-entities": resolve_entities,
    "apply-steward-decisions": apply_steward_decisions, "build-profiles": build_profiles_h,
    "portfolio-metrics": portfolio_metrics, "run-reserving": run_reserving,
    "update-guidelines": update_guidelines, "notify-owner": notify,
    "ai-process-documents": ai_process_documents, "ai-triage": ai_triage,
    "validate-application": validate_application, "calculate-premium": calculate_premium,
    "issue-policy": issue_policy, "send-documents": send_documents, "notify-applicant": notify_applicant,
    "create-invoice": create_invoice, "send-reminder": send_reminder, "reconcile-payment": reconcile_payment,
    "lapse-policy": lapse_policy, "verify-coverage": verify_coverage, "fraud-score": fraud_score,
    "settle-claim": settle_claim, "escalate-payout": escalate_payout, "close-claim": close_claim,
    "reject-claim": reject_claim,
}
