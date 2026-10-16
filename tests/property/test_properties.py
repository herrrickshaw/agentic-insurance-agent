"""Property-based tests (hypothesis): invariants that must hold for ALL inputs, not just chosen examples."""
import json

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ai.guardrails import redact_pii
from insurance.actuarial import mortality as mo
from insurance.actuarial import reserving
from insurance.customers import resolve, validate
from records import inventory, pipeline
from records.audit import AuditLog

pytestmark = pytest.mark.property
seg = st.one_of(st.text(min_size=1, max_size=60), st.sampled_from(["con", "AUX", "nul.txt", "Forms", "_vti_bin", "desktop.ini", "Thumbs.db", ".", "..", " a ", "x."]))
paths = st.lists(seg, min_size=1, max_size=12).map("/".join)


@pytest.mark.story("REC-3")
@given(paths)
def test_sanitised_paths_are_always_spo_safe(p):
    out = pipeline.sanitise(p)
    assert inventory.spo_issues(out) == []
    assert len(out) <= inventory.MAX_PATH
    assert pipeline.sanitise(out) == out                       # idempotent


@pytest.mark.story("REC-6")
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40)
@given(n=st.integers(1, 12), data=st.data())
def test_any_single_field_edit_breaks_the_audit_chain(tmp_path_factory, n, data):
    log = AuditLog(tmp_path_factory.mktemp("a") / "a.jsonl")
    for i in range(n):
        log.append("u", "act", f"s{i}", {"i": i}, ts=1000.0 + i)
    assert log.verify()[0]
    lines = log.path.read_text().splitlines()
    k = data.draw(st.integers(0, n - 1))
    e = json.loads(lines[k])
    e["actor"] = e["actor"] + "x"
    lines[k] = json.dumps(e)
    log.path.write_text("\n".join(lines) + "\n")
    assert log.verify() == (False, k + 1)


@pytest.mark.story("CUS-2")
@given(st.text(alphabet=st.characters(whitelist_categories=("L",)), min_size=1, max_size=20),
       st.text(alphabet=st.characters(whitelist_categories=("L",)), min_size=1, max_size=20))
def test_jaro_winkler_is_symmetric_bounded_and_reflexive(a, b):
    s = resolve.jaro_winkler(a, b)
    assert 0.0 <= s <= 1.0 + 1e-12 and abs(s - resolve.jaro_winkler(b, a)) < 1e-12
    assert resolve.jaro_winkler(a, a) == 1.0


@pytest.mark.story("CUS-1")
@given(st.text(alphabet="0123456789", min_size=1, max_size=18), st.data())
def test_verhoeff_roundtrip_and_detects_any_single_digit_error(num, data):
    full = validate.verhoeff_append(num)
    assert validate.verhoeff_valid(full)
    i = data.draw(st.integers(0, len(full) - 1))
    wrong = data.draw(st.sampled_from([d for d in "0123456789" if d != full[i]]))
    assert not validate.verhoeff_valid(full[:i] + wrong + full[i + 1:])


@pytest.mark.story("CLM-3")
@given(st.text(alphabet="0123456789", min_size=12, max_size=12))
def test_redaction_never_leaks_a_12_digit_identifier(digits):
    spaced = f"{digits[:4]} {digits[4:8]} {digits[8:]}"
    out, _ = redact_pii(f"id {spaced} end")
    assert digits[:4] not in out.replace(" ", "")[:0] and spaced not in out and digits not in out.replace(" ", "")


@pytest.mark.story("POL-1")
@given(age=st.integers(18, 70), term=st.integers(5, 30), i=st.floats(0.01, 0.12), mult=st.floats(1.0, 4.0))
def test_actuarial_identities_hold_everywhere(age, term, i, mult):
    if age + term > 100:
        return
    c = mo.Commutation(mo.MortalityTable.gompertz_makeham(multiple=mult), i)
    assert abs(c.A(age) - (1 - c.d * c.a_due(age))) < 1e-8
    assert 0 < c.A_term(age, term) < 1
    P = mo.term_net_premium(c, age, term, 1.0)
    assert P > 0 and abs(mo.term_reserve(c, age, term, 0, 1.0, P)) < 1e-9
    assert all(mo.term_reserve(c, age, term, t, 1.0, P) > -1e-9 for t in range(term + 1))
    base = mo.Commutation(mo.MortalityTable.gompertz_makeham(), i)
    assert mo.term_net_premium(c, age, term, 1.0) >= mo.term_net_premium(base, age, term, 1.0) - 1e-12


@pytest.mark.story("PRT-2")
@given(scale=st.floats(0.01, 1000), seed=st.integers(0, 10_000))
def test_chain_ladder_reserve_scales_linearly(scale, seed):
    rng = np.random.default_rng(seed)
    full = np.cumsum(rng.uniform(50, 150, (5, 5)), axis=1)
    tri = full.copy()
    for k in range(5):
        tri[k, 5 - k:] = np.nan
    r1 = reserving.chain_ladder(tri)["reserve"]
    r2 = reserving.chain_ladder(tri * scale)["reserve"]
    assert abs(r2 - r1 * scale) < 1e-6 * max(abs(r2), 1)
