"""Needs the compose stack (skipped automatically otherwise): a policy runs through BPMN + DMN + actuarial pricing."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import demo  # noqa: E402

pytestmark = pytest.mark.integration


def issue(smoker):
    p = demo.req("POST", "/process-definition/key/policy_issuance/start", {"variables": {**demo.V(
        applicant="IT", product="term_life", age=35, sumInsured=10_000_000, smoker=smoker, termYears=20, occupation="office"),
        "annualIncome": {"value": 1_500_000, "type": "Double"}}})
    return demo.wait_var(p["id"], "ratingClass"), demo.wait_var(p["id"], "premium")


@pytest.mark.story("POL-2")
def test_smoker_is_rated_and_priced_higher_end_to_end():
    demo.deploy()
    (c1, p1), (c2, p2) = issue(False), issue(True)
    assert (c1, c2) == ("preferred", "substandard")
    assert 1.5 < p2 / p1 < 1.9                          # mortality x1.8 diluted by fixed expenses; no double-counted loading
