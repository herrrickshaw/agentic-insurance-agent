#!/usr/bin/env python3
"""Run onboarding -> portfolio review -> actuarially priced policy on the live engine, completing human tasks."""
import json, sys, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import demo
from demo import req, V, wait_var


def wait_task(pid, key, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        t = req("GET", f"/task?processInstanceId={pid}&taskDefinitionKey={key}")
        if t:
            return t[0]
        time.sleep(1)
    sys.exit(f"timeout waiting for task {key}")


def finish(pid):
    end = time.time() + 90
    while time.time() < end:
        if not req("GET", f"/process-instance?processInstanceIds={pid}"):
            return
        time.sleep(1)
    sys.exit("process did not finish")


demo.deploy()
# 1. onboarding
o = req("POST", "/process-definition/key/customer_onboarding/start", {"variables": V(
    sourceFile="/data/inbox/customers_agent_app.csv", sourceChannel="agent_app")})
rp = wait_var(o["id"], "reviewPending")
print("onboarding: accepted", wait_var(o["id"], "accepted"), "rejected", wait_var(o["id"], "rejected"),
      "auto-merged", wait_var(o["id"], "autoMerged"), "pending review", rp)
if rp:
    t = wait_task(o["id"], "steward")
    print("steward task sample:", wait_var(o["id"], "reviewSample")[:160])
    req("POST", f"/task/{t['id']}/complete", {"variables": {"mergeAll": {"value": True, "type": "Boolean"}}})
finish(o["id"])
print("profiles built:", req("GET", f"/history/variable-instance?processInstanceId={o['id']}&variableName=profilesBuilt")[0]["value"])
# 2. portfolio review (own funds chosen so the limits DMN returns 'review')
r = req("POST", "/process-definition/key/portfolio_risk_review/start", {"variables": {
    "ownFunds": {"value": 1.5e9, "type": "Double"}, **V(portfolioLabel="demo")}})
print("metrics:", wait_var(r["id"], "metricsSummary"))
t = wait_task(r["id"], "committee")
print("committee task opened; limits decision -> review")
req("POST", f"/task/{t['id']}/complete", {"variables": V(committeeDecision="Cap single-state exposure at 25% of sum insured")})
finish(r["id"])
# 3. actuarially priced policies: non-smoker vs smoker
for smoker in (False, True):
    p = req("POST", "/process-definition/key/policy_issuance/start", {"variables": {**V(
        applicant="Test Person", product="term_life", age=35, sumInsured=10_000_000, smoker=smoker, termYears=20,
        occupation="office"), "annualIncome": {"value": 1_500_000, "type": "Double"}}})
    print("smoker" if smoker else "non-smoker", "->", wait_var(p["id"], "ratingClass"), "x", wait_var(p["id"], "mortalityMultiple"),
          "premium", wait_var(p["id"], "premium"))
