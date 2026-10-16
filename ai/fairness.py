"""Group-fairness screening metrics (EIOPA Opinion Annex I lists examples such as demographic parity and calibration).

Caveat from the Opinion itself: group metrics can conflict with actuarial fairness (same risk, same price), so treat
these as monitoring signals to investigate, not automatic pass/fail verdicts."""
import numpy as np


def _by_group(groups):
    g = np.asarray(groups)
    return g, np.unique(g)


def selection_rates(y_pred, groups):
    y, (g, ks) = np.asarray(y_pred, float), _by_group(groups)
    return {k: float(y[g == k].mean()) for k in ks}


def demographic_parity_difference(y_pred, groups):
    r = list(selection_rates(y_pred, groups).values())
    return max(r) - min(r)


def disparate_impact_ratio(y_pred, groups):
    """min selection rate / max selection rate (1.0 = parity). 0.8 is a common screening heuristic, not an insurance rule."""
    r = list(selection_rates(y_pred, groups).values())
    return 1.0 if max(r) == 0 else min(r) / max(r)


def equal_opportunity_difference(y_true, y_pred, groups):
    """Gap in true-positive rate between groups (only rows with a positive true outcome)."""
    yt, yp, (g, ks) = np.asarray(y_true), np.asarray(y_pred, float), _by_group(groups)
    tprs = [yp[(g == k) & (yt == 1)].mean() for k in ks if ((g == k) & (yt == 1)).any()]
    return float(max(tprs) - min(tprs)) if tprs else 0.0


def calibration_gap(y_true, scores, groups):
    """Largest group difference in (mean predicted - mean observed): 0 means the score is equally calibrated in aggregate."""
    yt, sc, (g, ks) = np.asarray(y_true, float), np.asarray(scores, float), _by_group(groups)
    gaps = [float(sc[g == k].mean() - yt[g == k].mean()) for k in ks]
    return max(gaps) - min(gaps)


def report(y_true, y_pred, scores, groups, dpd_limit=0.1, di_floor=0.8, eod_limit=0.1, cal_limit=0.05):
    out = {"demographic_parity_difference": demographic_parity_difference(y_pred, groups),
           "disparate_impact_ratio": disparate_impact_ratio(y_pred, groups),
           "equal_opportunity_difference": equal_opportunity_difference(y_true, y_pred, groups),
           "calibration_gap": calibration_gap(y_true, scores, groups), "selection_rates": selection_rates(y_pred, groups)}
    out["flags"] = [k for k, bad in {"demographic_parity": out["demographic_parity_difference"] > dpd_limit,
                                     "disparate_impact": out["disparate_impact_ratio"] < di_floor,
                                     "equal_opportunity": out["equal_opportunity_difference"] > eod_limit,
                                     "calibration": out["calibration_gap"] > cal_limit}.items() if bad]
    return out
