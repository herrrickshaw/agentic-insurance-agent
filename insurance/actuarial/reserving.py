"""Claims reserving on cumulative run-off triangles: chain-ladder and Bornhuetter-Ferguson."""
import numpy as np


def development_factors(tri):
    """Volume-weighted age-to-age factors. `tri`: (origin x dev) cumulative, NaN below the diagonal."""
    n = tri.shape[1]
    f = np.ones(n - 1)
    for j in range(n - 1):
        m = ~np.isnan(tri[:, j + 1])
        f[j] = tri[m, j + 1].sum() / tri[m, j].sum()
    return f


def chain_ladder(tri):
    f = development_factors(tri)
    n = tri.shape[0]
    cdf_to_ult = np.append(np.cumprod(f[::-1])[::-1], 1.0)        # CDF at each dev age
    latest = np.array([tri[i, n - 1 - i] for i in range(n)])      # latest diagonal (square triangle)
    ult = latest * cdf_to_ult[::-1]
    return {"factors": f, "cdf": cdf_to_ult, "latest": latest, "ultimate": ult, "ibnr": ult - latest,
            "reserve": float((ult - latest).sum())}


def bornhuetter_ferguson(tri, prior_ultimate):
    cl = chain_ladder(tri)
    pct_unreported = 1 - 1 / cl["cdf"][::-1]
    ult = cl["latest"] + np.asarray(prior_ultimate, float) * pct_unreported
    return {"ultimate": ult, "ibnr": ult - cl["latest"], "reserve": float((ult - cl["latest"]).sum())}
