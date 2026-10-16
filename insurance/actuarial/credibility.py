"""Buhlmann-Straub credibility for segment experience rating."""
import numpy as np


def buhlmann_straub(means, exposures, within_var_by_risk):
    """means[i]: average claim cost for risk i; exposures[i]: its weight; within_var_by_risk[i]: s_i^2 estimate.
    Returns (collective_mean, k, Z[i], credibility_estimate[i])."""
    m, w, s2 = map(np.asarray, (means, exposures, within_var_by_risk))
    mu = float((m * w).sum() / w.sum())
    epv = float((s2 * w).sum() / w.sum())
    r = len(m)
    vhm = max(float((w * (m - mu) ** 2).sum() - (r - 1) * epv) / (w.sum() - (w ** 2).sum() / w.sum()), 1e-12)
    k = epv / vhm
    Z = w / (w + k)
    return mu, k, Z, Z * m + (1 - Z) * mu
