"""Frequency/severity pricing with log-link GLMs fitted by IRLS (numpy only)."""
import numpy as np


def _irls(X, y, offset, family, w_obs=None, iters=50, tol=1e-9):
    n, p = X.shape
    w_obs = np.ones(n) if w_obs is None else w_obs
    beta = np.zeros(p)
    beta[0] = np.log(max(y.mean(), 1e-9)) - (offset.mean() if offset is not None else 0)
    off = np.zeros(n) if offset is None else offset
    for _ in range(iters):
        eta = X @ beta + off
        mu = np.exp(eta)
        if family == "poisson":          # canonical-ish: W = mu, z = eta - off + (y - mu)/mu
            W = mu * w_obs
        elif family == "gamma":          # log link: W = 1 (times prior weights)
            W = w_obs
        else:
            raise ValueError(family)
        z = eta - off + (y - mu) / mu
        XtW = X.T * W
        new = np.linalg.solve(XtW @ X + 1e-10 * np.eye(p), XtW @ z)
        if np.max(np.abs(new - beta)) < tol:
            beta = new
            break
        beta = new
    cov = np.linalg.inv((X.T * (np.exp(X @ beta + off) * w_obs if family == "poisson" else w_obs)) @ X)
    return beta, cov


def fit_frequency(X, claims, exposure):
    """Poisson GLM, log link, offset log(exposure). Returns (beta, standard errors)."""
    beta, cov = _irls(X, claims.astype(float), np.log(exposure), "poisson")
    return beta, np.sqrt(np.diag(cov))


def fit_severity(X, avg_severity, n_claims):
    """Gamma GLM, log link, weighted by claim counts. Only rows with claims."""
    m = n_claims > 0
    beta, cov = _irls(X[m], avg_severity[m], None, "gamma", w_obs=n_claims[m].astype(float))
    resid = (avg_severity[m] - np.exp(X[m] @ beta)) / np.exp(X[m] @ beta)
    phi = float((n_claims[m] * resid ** 2).sum() / (m.sum() - X.shape[1]))
    return beta, np.sqrt(np.diag(cov) * phi)


def pure_premium(x_row, freq_beta, sev_beta):
    return float(np.exp(x_row @ freq_beta) * np.exp(x_row @ sev_beta))


def poisson_deviance(y, mu):
    y, mu = np.asarray(y, float), np.asarray(mu, float)
    t = np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0.0)
    return float(2 * np.sum(t - (y - mu)))
