"""Collective risk model Monte Carlo: aggregate loss, VaR/TVaR, reinsurance effect."""
import numpy as np


def simulate_aggregate(lam, sev_mu, sev_sigma, n_sims=100_000, seed=1, overdispersion=0.0):
    """N ~ Poisson(lam) (or Gamma-mixed Poisson = NegBin if overdispersion>0), X ~ Lognormal(sev_mu, sev_sigma).
    Returns (aggregate ground-up losses[n_sims], per-sim list handled internally via flat arrays)."""
    rng = np.random.default_rng(seed)
    lam_s = lam if overdispersion <= 0 else rng.gamma(1 / overdispersion, lam * overdispersion, n_sims)
    n = rng.poisson(lam_s, n_sims)
    total = int(n.sum())
    x = rng.lognormal(sev_mu, sev_sigma, total)
    idx = np.repeat(np.arange(n_sims), n)
    return n, x, idx


def aggregate(n_sims, x, idx):
    return np.bincount(idx, weights=x, minlength=n_sims)


def apply_reinsurance(x, idx, n_sims, quota_share=0.0, xol_retention=None, xol_limit=None):
    """Per-claim excess of loss first, then quota share on the retained part. Returns net aggregate per sim."""
    net = x.copy()
    if xol_retention is not None:
        ceded = np.clip(net - xol_retention, 0, xol_limit if xol_limit is not None else np.inf)
        net = net - ceded
    net = net * (1 - quota_share)
    return np.bincount(idx, weights=net, minlength=n_sims)


def var_tvar(agg, level):
    v = float(np.quantile(agg, level))
    tail = agg[agg >= v]
    return v, float(tail.mean())
