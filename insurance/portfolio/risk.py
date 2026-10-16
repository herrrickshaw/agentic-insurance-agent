"""Portfolio construction and risk: exposure, concentration, expected loss, simulated VaR/TVaR, reserving."""
import json
import time
from datetime import date

import numpy as np

from ..actuarial import mortality as mo, reserving, simulation as sim

URBAN = {"MH", "KA", "DL", "TN", "TS", "WB"}
# Non-life severity: lognormal(mu, sigma) per claim. Calibrate from claims data in production.
SEV = {"health": (11.0, 0.9), "motor": (10.8, 0.9), "home": (11.5, 1.0)}
FREQ = {"health": 0.12, "motor": 0.09, "home": 0.03}


def policy_frame(con):
    rows = [dict(r) for r in con.execute(
        "SELECT p.*, c.dob, c.smoker, c.occupation FROM policy_book p JOIN customers c USING(golden_id) WHERE p.status='in_force'")]
    for r in rows:
        r["age"] = date.today().year - int(r["dob"][:4])
    return rows


def expected_loss(rows, table=None):
    """Per-policy expected annual loss. Life: q_x * multiple * sum insured. Non-life: frequency * mean severity."""
    from ..underwriting.risk import life_rating
    base = table or mo.MortalityTable.gompertz_makeham()
    out = []
    for r in rows:
        if r["product"] == "term_life":
            mult = life_rating(r["age"], bool(r["smoker"]), r["occupation"], 0, 0)["mortality_multiple"]
            q = min(base.qx[min(r["age"], mo.OMEGA)] * mult, 1.0)
            out.append(q * r["sum_insured"])
        else:
            mu, sg = SEV[r["product"]]
            f = FREQ[r["product"]] * (1.6 if r["product"] == "motor" and r["age"] < 25 else 1)
            out.append(f * np.exp(mu + sg ** 2 / 2))
    return np.array(out)


def concentration(rows, key):
    tot = sum(r["sum_insured"] for r in rows) or 1
    share = {}
    for r in rows:
        share[r[key]] = share.get(r[key], 0) + r["sum_insured"] / tot
    return share, float(sum(s * s for s in share.values()))


def snapshot(con, own_funds, n_sims=50_000, seed=3, life_retention=5_000_000, label="run"):
    rows = policy_frame(con)
    if not rows:
        raise ValueError("empty policy book")
    el = expected_loss(rows)
    premium = sum(r["premium"] for r in rows)
    tot_si = sum(r["sum_insured"] for r in rows)
    by_state, hhi_state = concentration(rows, "state")
    by_prod, _ = concentration(rows, "product")
    cust_si = {}
    for r in rows:
        cust_si[r["golden_id"]] = cust_si.get(r["golden_id"], 0) + r["sum_insured"]
    top10 = sum(sorted(cust_si.values(), reverse=True)[:10]) / tot_si
    # --- Monte Carlo: lines are independent here (a stated simplification: no catastrophe/correlation) ---
    rng_seed, agg, net = seed, np.zeros(n_sims), np.zeros(n_sims)
    life = [r for r in rows if r["product"] == "term_life"]
    base = mo.MortalityTable.gompertz_makeham()
    if life:
        lam = float(el[[i for i, r in enumerate(rows) if r["product"] == "term_life"]].sum() / np.mean([r["sum_insured"] for r in life]))
        n, _, idx = sim.simulate_aggregate(max(lam, 1e-9), 0, 0.01, n_sims, seed=rng_seed)
        rng = np.random.default_rng(rng_seed + 1)
        x = rng.choice([r["sum_insured"] for r in life], size=int(n.sum()))   # empirical severity = actual sums insured
        agg += sim.aggregate(n_sims, x, idx)
        net += sim.apply_reinsurance(x, idx, n_sims, xol_retention=life_retention)
    for k, (mu, sg) in SEV.items():
        sub = [r for r in rows if r["product"] == k]
        if not sub:
            continue
        lam = sum(FREQ[k] * (1.6 if k == "motor" and r["age"] < 25 else 1) for r in sub)
        n, x, idx = sim.simulate_aggregate(lam, mu, sg, n_sims, seed=rng_seed + 2, overdispersion=0.05)
        agg += sim.aggregate(n_sims, x, idx)
        net += sim.apply_reinsurance(x, idx, n_sims)
    v995, t99 = sim.var_tvar(agg, 0.995)[0], sim.var_tvar(agg, 0.99)[1]
    nv995 = sim.var_tvar(net, 0.995)[0]
    m = {"policies": len(rows), "customers": len(cust_si), "total_sum_insured": tot_si, "premium": round(premium, 0),
         "expected_loss": round(float(el.sum()), 0), "expected_loss_ratio": round(float(el.sum()) / premium, 3),
         "var995": round(v995, 0), "tvar99": round(t99, 0), "var995_net_of_reinsurance": round(nv995, 0),
         "own_funds": own_funds, "var995_pct_own_funds": round(v995 / own_funds * 100, 1),
         "max_state": max(by_state, key=by_state.get), "max_state_share_pct": round(max(by_state.values()) * 100, 1),
         "hhi_state": round(hhi_state, 3), "top10_customer_share_pct": round(top10 * 100, 2),
         "largest_single_cover": max(r["sum_insured"] for r in rows),
         "product_mix_pct": {k: round(v * 100, 1) for k, v in by_prod.items()}}
    con.execute("INSERT INTO portfolio_snapshots(ts,label,metrics) VALUES(?,?,?)", (time.time(), label, json.dumps(m)))
    con.commit()
    return m


def synthetic_triangle(ultimate_by_origin, pattern=(0.45, 0.72, 0.88, 0.96, 1.0), noise=0.03, seed=2):
    """NO development data exists in the ledger, so run-off is synthesised from a known pattern + noise."""
    rng = np.random.default_rng(seed)
    n = len(pattern)
    full = np.array(ultimate_by_origin)[:, None] * np.array(pattern)[None, :] * rng.normal(1, noise, (n, n))
    full = np.maximum.accumulate(full, axis=1)
    tri = full.copy()
    for i in range(n):
        tri[i, n - i:] = np.nan
    return tri


def reserving_run(con, annual_nonlife_expected):
    ults = [annual_nonlife_expected * g for g in (0.92, 0.97, 1.0, 1.05, 1.1)]
    tri = synthetic_triangle(ults)
    cl = reserving.chain_ladder(tri)
    bf = reserving.bornhuetter_ferguson(tri, ults)
    return {"chain_ladder_reserve": round(cl["reserve"], 0), "bf_reserve": round(bf["reserve"], 0),
            "factors": [round(float(f), 4) for f in cl["factors"]], "basis": "synthetic triangle (no payment-development data in ledger)"}
