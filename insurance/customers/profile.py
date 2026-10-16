"""Customer 360 profile + segmentation from golden record, policy book and payment behaviour."""
import time
from datetime import date


def age_on(dob_iso, today=None):
    d = date.fromisoformat(dob_iso)
    t = today or date.today()
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


def completeness(c):
    fields = ["full_name", "dob", "gender", "phone", "email", "pan_last4", "aadhaar_last4", "pincode", "state",
              "occupation", "annual_income"]
    return round(sum(1 for f in fields if c[f] not in (None, "", 0)) / len(fields), 3)


def kyc_level(c):
    """Format-level KYC readiness only (checksum-valid Aadhaar + PAN present); NOT identity verification."""
    if c["aadhaar_valid"] and c["pan_last4"]:
        return "full_docs"
    if c["aadhaar_valid"] or c["pan_last4"]:
        return "partial_docs"
    return "none"


def value_tier(total_si, n_policies, premium):
    if total_si >= 10_000_000 or n_policies >= 3 or premium >= 100_000:
        return "high"
    return "mid" if total_si >= 2_000_000 or n_policies == 2 else "low"


def risk_tier(claims_5y, claim_ratio, late_payments, age):
    pts = (2 if claims_5y >= 3 else 1 if claims_5y >= 1 else 0) + (2 if claim_ratio > 0.9 else 1 if claim_ratio > 0.6 else 0) \
        + (2 if late_payments >= 3 else 1 if late_payments >= 1 else 0) + (1 if age >= 60 else 0)
    return "high" if pts >= 4 else "medium" if pts >= 2 else "low"


def build_profiles(con, today=None):
    out = 0
    for c in con.execute("SELECT * FROM customers").fetchall():
        c = dict(c)
        pol = [dict(r) for r in con.execute("SELECT * FROM policy_book WHERE golden_id=? AND status!='cancelled'", (c["golden_id"],))]
        total_si = sum(p["sum_insured"] for p in pol)
        prem = sum(p["premium"] for p in pol)
        paid = sum(p["claims_paid_5y"] or 0 for p in pol)
        claims = sum(p["claims_5y"] or 0 for p in pol)
        late = sum(p["late_payments"] or 0 for p in pol)
        ratio = paid / max(prem * 5, 1)
        age = age_on(c["dob"], today)
        vt, rt = value_tier(total_si, len(pol), prem), risk_tier(claims, ratio, late, age)
        tenure = max([(today or date.today()).year - p["start_year"] for p in pol], default=0)
        con.execute("INSERT OR REPLACE INTO profiles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (c["golden_id"], age, tenure, len(pol), total_si, claims, round(ratio, 4), late,
                     round(total_si / c["annual_income"], 2) if c["annual_income"] else None, completeness(c),
                     kyc_level(c), vt, rt, f"{vt}-value/{rt}-risk", time.time()))
        out += 1
    con.commit()
    return {"profiles": out, "avg_completeness": round(con.execute("SELECT AVG(completeness) FROM profiles").fetchone()[0] or 0, 3)}
