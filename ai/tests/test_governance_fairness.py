import json
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ai import fairness, governance
from insurance.actuarial.fair_pricing import discrimination_free_price, proxy_gap

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.story("GOV-1")
def test_tiering_is_proportionate_and_flags_ai_act_pattern():
    low = governance.assess(governance.UseCase("u1", "spell checker", "internal"))
    assert low.tier == "limited" and low.required_controls == ["inventory"] and low.ai_act_flag == "not flagged by this screen"
    uw = governance.assess(governance.UseCase("u2", "life rating", "x", scale="high", personal_data="special", autonomy="high", customer_facing=True,
                                              decision_effect="automated", prudential_impact="high", life_health_underwriting_or_pricing=True))
    assert uw.tier == "enhanced" and "fairness_metrics" in uw.required_controls and "likely high-risk" in uw.ai_act_flag
    fraud = governance.assess(governance.UseCase("u3", "fraud", "x", life_health_underwriting_or_pricing=True, fraud_detection_only=True))
    assert fraud.ai_act_flag == "not flagged by this screen"                       # fraud detection is carved out
    mid = governance.assess(governance.UseCase("u4", "doc ai", "x", scale="medium", personal_data="special", decision_effect="advisory", controls_in_place=["inventory"]))
    assert mid.tier == "standard" and set(mid.gaps) == {"data_governance", "monitoring", "redress"}


@pytest.mark.story("GOV-1")
def test_committed_inventory_matches_generator():
    cases = [governance.UseCase(**c) for c in json.loads((ROOT / "docs/governance/use_cases.json").read_text())]
    assert (ROOT / "docs/governance/AI_INVENTORY.md").read_text() == governance.render_markdown([governance.assess(c) for c in cases], cases)


@pytest.mark.story("GOV-2")
def test_fairness_metrics_on_known_cases():
    g = ["a"] * 10 + ["b"] * 10
    y = [1] * 8 + [0] * 2 + [1] * 4 + [0] * 6
    assert fairness.selection_rates(y, g) == {"a": 0.8, "b": 0.4}
    assert fairness.demographic_parity_difference(y, g) == pytest.approx(0.4)
    assert fairness.disparate_impact_ratio(y, g) == pytest.approx(0.5)
    rep = fairness.report(y_true=y, y_pred=y, scores=np.array(y, float), groups=g)
    assert {"demographic_parity", "disparate_impact"} <= set(rep["flags"]) and rep["calibration_gap"] == pytest.approx(0.0)
    assert fairness.equal_opportunity_difference([1, 1, 1, 1], [1, 1, 1, 0], ["a", "a", "b", "b"]) == pytest.approx(0.5)
    assert fairness.calibration_gap([1, 0, 1, 0], [0.9, 0.9, 0.5, 0.5], ["a", "a", "b", "b"]) == pytest.approx(0.4)


@pytest.mark.property
@pytest.mark.story("GOV-2")
@given(st.lists(st.tuples(st.sampled_from("abc"), st.integers(0, 1)), min_size=3, max_size=60))
def test_fairness_metric_invariants(rows):
    g, y = [r[0] for r in rows], [r[1] for r in rows]
    assert 0 <= fairness.demographic_parity_difference(y, g) <= 1 and 0 <= fairness.disparate_impact_ratio(y, g) <= 1
    renamed = [{"a": "z", "b": "y", "c": "x"}[k] for k in g]                      # relabelling groups changes nothing
    assert fairness.demographic_parity_difference(y, g) == pytest.approx(fairness.demographic_parity_difference(y, renamed))
    assert fairness.demographic_parity_difference(y, ["same"] * len(y)) == 0


@pytest.mark.property
@pytest.mark.story("PRC-1")
@given(base=st.floats(1, 1000), uplift=st.floats(0, 5), p=st.floats(0, 1))
def test_discrimination_free_price_marginalises_the_protected_attribute(base, uplift, p):
    mu = lambda x, d: base * (1 + x) * (1 + uplift * d)
    assert discrimination_free_price(mu, 1.0, [0, 1], [1 - p, p]) == pytest.approx(2 * base * (1 + uplift * p))
    flat = lambda x, d: base * (1 + x)                                              # model ignoring d: price unchanged
    assert discrimination_free_price(flat, 2.0, [0, 1], [0.3, 0.7]) == pytest.approx(3 * base)
    with pytest.raises(ValueError):
        discrimination_free_price(mu, 1.0, [0, 1], [0.5, 0.6])


@pytest.mark.story("PRC-1")
def test_proxy_gap_is_positive_when_x_predicts_the_protected_attribute():
    mu = lambda x, d: 100 * (1 + 0.5 * d)
    p_d_given_x = lambda x, d: (0.9 if d == 1 else 0.1) if x == "urban" else (0.1 if d == 1 else 0.9)
    gaps = proxy_gap(mu, ["urban", "rural"], [0, 1], [0.5, 0.5], p_d_given_x)
    assert gaps[0] > 0 > gaps[1]                              # unawareness price over/under-charges via the proxy; the free price does not
    assert proxy_gap(mu, ["urban"], [0, 1], [0.5, 0.5], lambda x, d: 0.5)[0] == pytest.approx(0)
