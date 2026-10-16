import pytest
import tempfile
from pathlib import Path

from insurance.customers import ingest, profile, resolve, validate as v
from insurance.db import connect
from insurance.portfolio import risk, synth
from insurance.underwriting import risk as uw


@pytest.mark.story("CUS-1")


def test_verhoeff_and_validation():
    a = v.verhoeff_append("23456789012")
    assert len(a) == 12 and v.verhoeff_valid(a)
    bad = a[:-1] + str((int(a[-1]) + 1) % 10)
    assert not v.verhoeff_valid(bad)
    r = v.CustomerRecord(full_name=" asha   RAO ", dob="05/03/1990", phone="+91 98765 43210", pan="abcde1234f", email="A@B.com")
    assert (r.full_name, r.phone, r.pan, r.email) == ("Asha Rao", "+919876543210", "ABCDE1234F", "a@b.com")
    for kw in ({"dob": "2999-01-01"}, {"phone": "12345"}, {"pan": "123"}):
        try:
            v.CustomerRecord(full_name="Asha Rao", **{"dob": "1990-01-01", **kw}); assert False, kw
        except Exception as e:
            assert "assert" not in str(type(e)).lower()


@pytest.mark.story("CUS-2")


def test_jaro_winkler_and_scoring():
    assert resolve.jaro_winkler("MARTHA", "MARHTA") > 0.96 and resolve.jaro_winkler("abc", "xyz") == 0
    a = dict(pan_hash=None, aadhaar_hash=None, dob="1990-01-01", full_name="Asha Rao", phone="+919876543210", email="", pincode="560001")
    b = dict(a, full_name="Aasha Rao")
    assert resolve.score(a, b) >= resolve.AUTO
    assert resolve.score(a, dict(b, dob="1991-01-01")) < resolve.REVIEW
    sparse = dict(a, phone="", email="", pincode="")
    assert resolve.REVIEW <= resolve.score(sparse, dict(sparse)) < resolve.AUTO      # name+dob only -> steward review
    assert resolve.score(dict(a, pan_hash="x"), dict(b, pan_hash="x", dob="1980-01-01", full_name="Zed")) == 1.0


@pytest.mark.story("CUS-1")


@pytest.mark.story("CUS-2")


@pytest.mark.story("CUS-3")


@pytest.mark.story("PRT-1")


def test_pipeline_end_to_end():
    d = Path(tempfile.mkdtemp())
    n = synth.make_customers_csv(d / "c.csv", n=600, seed=1)
    con = connect()
    res = ingest.ingest_file(con, d / "c.csv", "agent_app")
    assert res["rows_in"] == n and res["rejected"] > 0 and res["accepted"] + res["rejected"] == n
    assert con.execute("SELECT COUNT(*) FROM rejects").fetchone()[0] == res["rejected"]
    assert con.execute("SELECT COUNT(*) FROM customers_raw WHERE aadhaar_hash IS NOT NULL AND aadhaar_valid=0").fetchone()[0] == 0
    r = resolve.resolve_batch(con, res["batch_id"])
    gold = con.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    assert gold < res["accepted"] and r["golden_customers"] == gold             # duplicates merged
    assert con.execute("SELECT COUNT(*) FROM customers_raw WHERE golden_id IS NULL").fetchone()[0] == 0
    pb = synth.make_policy_book(con)
    prof = profile.build_profiles(con)
    assert pb > 100 and prof["profiles"] == gold
    m = risk.snapshot(con, own_funds=2e9, n_sims=5000)
    assert m["var995"] >= m["expected_loss"] > 0 and m["tvar99"] > 0 and m["var995_net_of_reinsurance"] <= m["var995"] * 1.001
    assert 0 < m["max_state_share_pct"] <= 100


@pytest.mark.story("POL-2")


def test_underwriting_rating():
    assert uw.life_rating(35, False, "office", 1_000_000, 10_000_000)["rating_class"] == "preferred"
    assert uw.life_rating(35, True, "office", 1_000_000, 10_000_000)["rating_class"] == "substandard"
    assert uw.life_rating(35, False, "office", 100_000, 10_000_000)["rating_class"] == "refer"
    r = uw.life_rating(40, True, "miner", 1_000_000, 5_000_000)
    assert r["mortality_multiple"] == 2.7 and r["rating_class"] == "substandard"
    uw.SMOKER_MULTIPLE = 2.5                                  # illustrative threshold: multiple > 3 declines
    try:
        assert uw.life_rating(40, True, "miner", 1_000_000, 5_000_000)["rating_class"] == "decline"
    finally:
        uw.SMOKER_MULTIPLE = 1.8
