"""Discrimination-free pricing by marginalisation (Lindholm, Richman, Tsanakas, Wuthrich, ASTIN Bulletin 52(1), 2022).

Fit a best-estimate model mu(x, d) that MAY use the protected attribute d, then price on x alone by averaging d out with
its portfolio distribution: h(x) = sum_d mu(x, d) * P(d). This removes direct discrimination and the proxy channel
(x predicting d), at the cost of needing d for (a sample of) policyholders when the model is fitted."""
import numpy as np


def discrimination_free_price(mu, x, d_values, d_probs):
    """mu(x, d) -> expected cost. d_values/d_probs: portfolio distribution of the protected attribute."""
    p = np.asarray(d_probs, float)
    if abs(p.sum() - 1) > 1e-9 or (p < 0).any():
        raise ValueError("d_probs must be a probability distribution")
    return float(sum(pi * mu(x, d) for d, pi in zip(d_values, p)))


def proxy_gap(mu, xs, d_values, d_probs, x_given_d):
    """How much does using the unawareness price (average over P(d | x)) differ from the discrimination-free price?
    x_given_d(x, d) -> P(d | x). Positive gaps mean x acts as a proxy for d."""
    out = []
    for x in xs:
        w = np.array([x_given_d(x, d) for d in d_values], float)
        w = w / w.sum()
        unaware = float(sum(wi * mu(x, d) for d, wi in zip(d_values, w)))
        out.append(unaware - discrimination_free_price(mu, x, d_values, d_probs))
    return out
