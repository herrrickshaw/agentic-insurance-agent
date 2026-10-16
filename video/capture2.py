"""Collect evidence for the customers/portfolio/actuarial video from the live engine + ledger."""
import json, os, sqlite3, subprocess, sys, tempfile
import numpy as np
sys.path.insert(0, "scripts"); sys.path.insert(0, ".")
from demo import req
from insurance.actuarial import mortality as mo
from insurance.portfolio import risk

env = {**os.environ, "DOCKER_HOST": "unix:///var/folders/f8/hg4mr3p16f912xp717r1wx_r0000gn/T/podman/podman-machine-default-api.sock"}
tmp = tempfile.mkdtemp(); db = os.path.join(tmp, "ledger.db")
subprocess.run(["podman", "cp", "insurance-workflow_workers_1:/data/ledger.db", db], check=True, env=env)
con = sqlite3.connect(db); con.row_factory = sqlite3.Row
q = lambda s: [dict(r) for r in con.execute(s)]
out = {}
out["batch"] = q("SELECT * FROM ingest_batches ORDER BY ts DESC LIMIT 1")[0]
out["reject_reasons"] = q("SELECT substr(reasons,1,instr(reasons||':',':')-1) AS field, COUNT(*) n FROM rejects GROUP BY 1 ORDER BY 2 DESC")
out["raw"], out["golden"] = q("SELECT COUNT(*) n FROM customers_raw")[0]["n"], q("SELECT COUNT(*) n FROM customers")[0]["n"]
out["queue"] = q("SELECT status, COUNT(*) n FROM match_queue GROUP BY 1")
out["sample_pairs"] = q("SELECT a.full_name na,b.full_name nb,q.score FROM match_queue q JOIN customers_raw a ON a.raw_id=q.raw_a JOIN customers_raw b ON b.raw_id=q.raw_b ORDER BY q.score DESC LIMIT 4")
out["segments"] = q("SELECT segment, COUNT(*) n FROM profiles GROUP BY 1 ORDER BY 2 DESC")
out["kyc"] = q("SELECT kyc_level, COUNT(*) n FROM profiles GROUP BY 1")
out["completeness"] = q("SELECT ROUND(AVG(completeness),3) c FROM profiles")[0]["c"]
out["cover_to_income_p95"] = float(np.nanpercentile([r["cover_to_income"] for r in q("SELECT cover_to_income FROM profiles WHERE cover_to_income IS NOT NULL")], 95))
# portfolio + distribution (recomputed from the same ledger; seed fixed)
from insurance.db import connect
c2 = connect(db)
m, agg, net = risk.snapshot(c2, own_funds=1.5e9, n_sims=50_000, label="video", return_samples=True)
out["portfolio"] = m
hist, edges = np.histogram(agg / 1e6, bins=60)
nh, _ = np.histogram(net / 1e6, bins=edges)
out["agg_hist"] = {"counts": hist.tolist(), "net_counts": nh.tolist(), "edges": edges.tolist(),
                   "mean": float(agg.mean() / 1e6), "var995": m["var995"] / 1e6, "tvar99": m["tvar99"] / 1e6, "net_var995": m["var995_net_of_reinsurance"] / 1e6}
out["product_si"] = m["product_mix_pct"]
rows = risk.policy_frame(c2)
st = {}
for r in rows: st[r["state"]] = st.get(r["state"], 0) + r["sum_insured"]
tot = sum(st.values()); out["state_share"] = {k: round(v / tot * 100, 1) for k, v in sorted(st.items(), key=lambda x: -x[1])}
nl = [r for r in rows if r["product"] != "term_life"]
out["reserving"] = risk.reserving_run(c2, float(risk.expected_loss(nl).sum()))
# engine history
hv = lambda pid, n: (req("GET", f"/history/variable-instance?processInstanceId={pid}&variableName={n}") or [{}])[0].get("value")
for key in ("customer_onboarding", "portfolio_risk_review"):
    pi = req("GET", f"/history/process-instance?processDefinitionKey={key}&sortBy=startTime&sortOrder=desc&maxResults=1")[0]
    acts = req("GET", f"/history/activity-instance?processInstanceId={pi['id']}&sortBy=startTime&sortOrder=asc")
    out[key] = {"activities": [a["activityName"] for a in acts if a["activityType"] not in ("startEvent", "endEvent", "exclusiveGateway") and a["activityName"]],
                "vars": {n: hv(pi["id"], n) for n in ("autoMerged", "reviewPending", "goldenCustomers", "profilesBuilt", "var995PctOwnFunds", "chainLadderReserve", "bfReserve", "metricsSummary")}}
pol = []
for pi in req("GET", "/history/process-instance?processDefinitionKey=policy_issuance&sortBy=startTime&sortOrder=desc&maxResults=2"):
    pol.append({n: hv(pi["id"], n) for n in ("smoker", "ratingClass", "mortalityMultiple", "premium", "premiumBasis", "age", "sumInsured")})
out["policies"] = pol[::-1]
# actuarial illustrations straight from the library
cm = mo.Commutation(mo.MortalityTable.gompertz_makeham(), 0.06)
cs = mo.Commutation(mo.MortalityTable.gompertz_makeham(multiple=1.8), 0.06)
ages = list(range(25, 61, 5))
out["premium_by_age"] = {"ages": ages, "nonsmoker": [mo.term_net_premium(cm, a, 20, 10_000_000) for a in ages],
                         "smoker": [mo.term_net_premium(cs, a, 20, 10_000_000) for a in ages]}
P = mo.term_net_premium(cm, 35, 20, 1e7)
out["reserve_path"] = [mo.term_reserve(cm, 35, 20, t, 1e7, P) for t in range(0, 21)]
out["identity"] = {"A40": cm.A(40), "one_minus_d_a": 1 - cm.d * cm.a_due(40), "q40": float(cm.t.qx[40]), "q60": float(cm.t.qx[60]), "e35": cm.t.life_expectancy(35)}
json.dump(out, open("video/capture2.json", "w"), indent=1, default=float)
print(json.dumps({k: out[k] for k in ("batch", "queue", "reserving", "policies", "identity")}, indent=1, default=float)[:2200])
