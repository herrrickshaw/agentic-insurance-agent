#!/usr/bin/env python3
"""Run the records migration process on the live engine, completing the records-manager task."""
import sys
import time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import demo
from demo import req, V, wait_var
from demo_customers_portfolio import wait_task, finish  # noqa: E402  (re-uses helpers; runs nothing on import)

demo.deploy()
r = req("POST", "/process-definition/key/records_migration/start", {"variables": V(
    sourceDir="/data/records_in/legacy_library", batchLabel="batch-" + time.strftime("%H%M%S"))})
print("analysis:", wait_var(r["id"], "analysisSummary"))
t = wait_task(r["id"], "review")
print("records-manager task open; applying recommended dispositions")
req("POST", f"/task/{t['id']}/complete", {"variables": {"applyRecommended": {"value": True, "type": "Boolean"}, **V(reviewer="m.records")}})
finish(r["id"])
h = {v["name"]: v["value"] for v in req("GET", f"/history/variable-instance?processInstanceId={r['id']}") if not isinstance(v["value"], dict)}
print({k: h.get(k) for k in ("decisionsApplied", "reviewCount", "migrationReady", "manifestPath")})
