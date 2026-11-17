"""Reserving and credibility against published / hand-computed reference values (not just self-consistency)."""
import numpy as np
import pytest

from insurance.actuarial import credibility, reserving as rs

# Taylor & Ashe (1983) incremental paid triangle, as used in Mack (1993, ASTIN Bulletin 23(2)) and R ChainLadder::GenIns.
TAYLOR_ASHE = [
    [357848, 766940, 610542, 482940, 527326, 574398, 146342, 139950, 227229, 67948],
    [352118, 884021, 933894, 1183289, 445745, 320996, 527804, 266172, 425046],
    [290507, 1001799, 926219, 1016654, 750816, 146923, 495992, 280405],
    [310608, 1108250, 776189, 1562400, 272482, 352053, 206286],
    [443160, 693190, 991983, 769488, 504851, 470639],
    [396132, 937085, 847498, 805037, 705960],
    [440832, 847631, 1131398, 1063269],
    [359480, 1061648, 1443370],
    [376686, 986608],
    [344014],
]


def cumulative(inc):
    t = np.full((len(inc), len(inc)), np.nan)
    for i, r in enumerate(inc):
        t[i, :len(r)] = np.cumsum(r)
    return t


def test_chain_ladder_reproduces_mack_1993_taylor_ashe_reserve():
    cl = rs.chain_ladder(cumulative(TAYLOR_ASHE))
    # published age-to-age factors (Mack 1993, table 2) and total reserve 18,680,856
    assert np.allclose(cl["factors"], [3.491, 1.747, 1.457, 1.174, 1.104, 1.086, 1.054, 1.077, 1.018], atol=5e-4)
    assert cl["reserve"] == pytest.approx(18_680_856, abs=1)
    assert cl["ibnr"][0] == 0
    assert np.allclose(cl["ibnr"][[1, 5, 9]], [94_634, 1_419_459, 4_625_811], atol=1)


def test_bornhuetter_ferguson_with_chain_ladder_ultimates_as_prior_equals_chain_ladder():
    tri = cumulative(TAYLOR_ASHE)
    cl = rs.chain_ladder(tri)
    bf = rs.bornhuetter_ferguson(tri, cl["ultimate"])
    # BF IBNR = prior * (1 - 1/CDF); with prior = CL ultimate = latest*CDF that is exactly latest*(CDF-1) = CL IBNR
    assert np.allclose(bf["ibnr"], cl["ibnr"])


def test_bornhuetter_ferguson_ignores_prior_for_fully_developed_origin():
    tri = cumulative(TAYLOR_ASHE)
    bf = rs.bornhuetter_ferguson(tri, np.full(10, 1e9))
    assert bf["ibnr"][0] == 0                   # oldest year is at ultimate: nothing unreported
    assert bf["ibnr"][9] > bf["ibnr"][1] > 0


def test_buhlmann_straub_hand_computed_two_risks():
    # mu = 15; EPV = 2; VHM = (25 + 25 - 1*2) / (2 - 2/2) = 48; k = 2/48; Z = 1/(1+k) = 0.96
    mu, k, Z, est = credibility.buhlmann_straub([10, 20], [1, 1], [2, 2])
    assert mu == 15 and k == pytest.approx(1 / 24)
    assert np.allclose(Z, 0.96)
    assert np.allclose(est, [10.2, 19.8])


def test_buhlmann_straub_no_between_risk_variance_gives_collective_mean():
    # identical risk means -> VHM estimate negative -> floored -> Z ~ 0 -> everyone gets the collective mean
    mu, k, Z, est = credibility.buhlmann_straub([50, 50, 50], [10, 20, 30], [400, 400, 400])
    assert mu == 50 and np.all(Z < 1e-9) and np.allclose(est, 50)


def test_buhlmann_straub_exposure_weighting_of_collective_mean():
    mu, *_ = credibility.buhlmann_straub([100, 200], [3, 1], [10, 10])
    assert mu == pytest.approx(125.0)
