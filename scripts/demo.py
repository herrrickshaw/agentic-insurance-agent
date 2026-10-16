#!/usr/bin/env python3
"""Deploy models and drive one policy + one claim end to end (use short DUE_IN/PAYOUT_SLA for timer demos)."""
import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

E = os.environ.get("ENGINE_URL", "http://localhost:8080/engine-rest")
MODELS = Path(__file__).resolve().parent.parent / "models"


def req(method, path, body=None, raw=None, headers=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = headers or ({"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(urllib.request.Request(E + path, data, h, method=method)) as r:
            b = r.read()
            return json.loads(b) if b else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:400]}") from None


def deploy():
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="deployment-name"\r\n\r\ninsurance\r\n'.encode()]
    for f in sorted(MODELS.glob("*")):
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{f.name}"; filename="{f.name}"\r\n'
                     f'Content-Type: application/xml\r\n\r\n'.encode() + f.read_bytes() + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    r = req("POST", "/deployment/create", raw=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    print("deployed:", r["id"])


def V(**kw):
    def t(v):
        return {"value": v, "type": "Boolean" if isinstance(v, bool) else "Long" if isinstance(v, int) else "String"}
    return {k: t(v) for k, v in kw.items()}


def wait_var(pid, name, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            return req("GET", f"/process-instance/{pid}/variables/{name}")["value"]
        except Exception:
            time.sleep(1)
    sys.exit(f"timeout waiting for {name} on {pid}")


def main():
    deploy()
    pol = req("POST", "/process-definition/key/policy_issuance/start", {"variables": {**V(
        applicant="Asha Rao", product="term_life", age=34, sumInsured=5_000_000, smoker=False, occupation="office"),
        "annualIncome": {"value": 1_200_000, "type": "Double"}}})
    num = wait_var(pol["id"], "policyNumber")
    premium = wait_var(pol["id"], "premium")
    print("policy issued:", num, "premium", premium)
    req("POST", "/message", {"messageName": "PaymentReceived", "correlationKeys": V(policyNumber=num),
                             "processVariables": {"amountPaid": {"value": premium, "type": "Double"},
                                                  **V(paymentRef="UPI-123")}})
    time.sleep(3)
    clm = req("POST", "/process-definition/key/claim_handling/start", {"variables": V(
        policyNumber=num, claimAmount=8000, description="hospital bill, network hospital", lossDate="2026-10-01",
        claimDocuments=json.dumps([{"name": "claim.txt", "text": f"CLAIM FORM\nPolicy No: {num}\nClaimant: Asha Rao\nDate of loss: 2026-10-01\nClaim amount: INR 8,000"},
                                   {"name": "inv.txt", "text": "INVOICE\nInvoice No: INV-1\nVendor: City Hospital\nGST 5%\nTotal amount: INR 8,000"}]))})
    cno = wait_var(clm["id"], "claimNumber")
    print("claim filed:", cno)
    time.sleep(4)
    req("POST", "/message", {"messageName": "PayoutConfirmed", "correlationKeys": V(claimNumber=cno),
                             "processVariables": V(payoutRef="NEFT-998")})
    print("payout confirmed")


if __name__ == "__main__":
    main()
