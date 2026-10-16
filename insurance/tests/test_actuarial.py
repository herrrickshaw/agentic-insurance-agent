import numpy as np
from insurance.actuarial import credibility, glm, mortality as mo, reserving as rs, simulation as sim


def cm(i=0.06, **kw):
    return mo.Commutation(mo.MortalityTable.gompertz_makeham(**kw), i)


def test_life_table_and_identities():
    t = mo.MortalityTable.gompertz_makeham()
    assert t.qx[-1] == 1 and np.all(np.diff(t.lx()) <= 0)
    assert 0.001 < t.qx[30] < 0.002 and 0.05 < t.qx[80] < 0.2          # plausible shape
    c = cm()
    for x in (25, 40, 60):                                              # A_x = 1 - d * a_due_x
        assert abs(c.A(x) - (1 - c.d * c.a_due(x))) < 1e-9
    assert abs(c.a_due(40) - (1 + c.v * (1 - t.qx[40]) * c.a_due(41))) < 1e-9   # recursion


def test_premium_reserve_and_ratings():
    c = cm()
    P = mo.term_net_premium(c, 35, 20, 1_000_000)
    assert abs(P * c.a_due_temp(35, 20) - 1_000_000 * c.A_term(35, 20)) < 1e-6    # equivalence principle
    assert abs(mo.term_reserve(c, 35, 20, 0, 1e6, P)) < 1e-6 and mo.term_reserve(c, 35, 20, 20, 1e6, P) == 0
    assert mo.term_reserve(c, 35, 20, 10, 1e6, P) > 0
    G = mo.term_gross_premium(c, 35, 20, 1e6, initial_expense=2000, renewal_expense=300, commission=0.1)
    assert G > P
    smoker = cm(multiple=2.0)
    assert mo.term_net_premium(smoker, 35, 20, 1e6) > 1.8 * P
    assert mo.term_net_premium(c, 55, 20, 1e6) > mo.term_net_premium(c, 35, 20, 1e6)


def test_glm_recovers_known_coefficients():
    rng = np.random.default_rng(7)
    n = 60_000
    age_young, veh_old, urban = rng.random(n) < .25, rng.random(n) < .4, rng.random(n) < .5
    X = np.column_stack([np.ones(n), age_young, veh_old, urban])
    expo = rng.uniform(0.3, 1, n)
    bf, bs = np.array([-2.3, 0.5, 0.2, 0.3]), np.array([8.0, 0.1, 0.25, 0.15])
    claims = rng.poisson(expo * np.exp(X @ bf))
    sev = np.where(claims > 0, rng.gamma(2.0 * np.maximum(claims, 1), np.exp(X @ bs) / 2.0) / np.maximum(claims, 1), 0)
    f, se = glm.fit_frequency(X, claims, expo)
    assert np.all(np.abs(f - bf) < 4 * se) and np.all(np.abs(f - bf) < 0.08)
    s, se_s = glm.fit_severity(X, sev, claims)
    assert np.all(np.abs(s - bs) < 0.08)
    assert abs(glm.pure_premium(X[0], f, s) - np.exp(X[0] @ bf) * np.exp(X[0] @ bs)) / np.exp(X[0] @ bf + X[0] @ bs) < 0.15


def test_chain_ladder_recovers_pattern():
    pattern = np.array([0.4, 0.7, 0.88, 0.96, 1.0])
    ults = np.array([1000., 1100, 1200, 1300, 1400])
    full = ults[:, None] * pattern[None, :]
    tri = full.copy()
    for i in range(5):
        tri[i, 5 - i:] = np.nan
    cl = rs.chain_ladder(tri)
    assert np.allclose(cl["ultimate"], ults, rtol=0.02) and abs(cl["reserve"] - (ults.sum() - np.array([full[i, 4 - i] for i in range(5)]).sum())) < 25
    bf = rs.bornhuetter_ferguson(tri, ults)
    assert abs(bf["reserve"] - cl["reserve"]) < 25


def test_credibility_and_simulation():
    mu, k, Z, est = credibility.buhlmann_straub([100, 140, 90], [500, 50, 1000], [900, 900, 900])
    assert 0 < Z.min() and Z.max() < 1 and Z[2] > Z[1]
    assert all(min(m, mu) - 1e-9 <= e <= max(m, mu) + 1e-9 for m, e in zip([100, 140, 90], est))
    n, x, idx = sim.simulate_aggregate(50, 9.0, 1.0, n_sims=40_000)
    agg = sim.aggregate(40_000, x, idx)
    assert abs(agg.mean() / (50 * np.exp(9.5)) - 1) < 0.02
    v, t = sim.var_tvar(agg, 0.995)
    assert t >= v >= agg.mean()
    net = sim.apply_reinsurance(x, idx, 40_000, quota_share=0.2, xol_retention=30_000, xol_limit=500_000)
    assert sim.var_tvar(net, 0.995)[0] < v and net.mean() < agg.mean()
