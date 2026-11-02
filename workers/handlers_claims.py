"""Claim-handling handlers: (con, vars) -> dict of output variables."""
import hashlib
import os

from ledger import log, next_number

PAYOUT_SLA = os.environ.get("PAYOUT_SLA", "P5D")


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
