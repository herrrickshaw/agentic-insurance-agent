"""Customer validation/ingest/resolution, profiles, underwriting, GLM and portfolio risk: boundaries and failure paths."""
import json
import sqlite3
from datetime import date, timedelta

import numpy as np
import pytest
from pydantic import ValidationError

from insurance import db
from insurance.actuarial import glm
from insurance.customers import ingest, profile, resolve
from insurance.customers.validate import CustomerRecord, h, verhoeff_append, verhoeff_valid
from insurance.portfolio import risk as pr
from insurance.underwriting.risk import AGE_REFER, MAX_COVER_TO_INCOME, OCCUPATION_MULTIPLE, SMOKER_MULTIPLE, life_rating

GOOD = dict(full_name="asha  rao", dob="1990-05-17")


def rec(**kw):
    return CustomerRecord(**{**GOOD, **kw})


def errs(**kw):
    with pytest.raises(ValidationError) as e:
        rec(**kw)
    return [(x["loc"][0], x["msg"]) for x in e.value.errors()]


# ---- validators ---------------------------------------------------------------------------------------

def test_name_normalised_and_title_cased():
    assert rec(full_name="  asha   RAO ").full_name == "Asha Rao"
    assert rec(full_name="o'neil-smith jr.").full_name == "O'Neil-Smith Jr."


@pytest.mark.parametrize("name", ["ab", "  a ", "R2D2 Rao", "Asha_Rao", "Asha@Rao", ""])
def test_name_rejected(name):
    assert errs(full_name=name)[0][0] == "full_name"


def test_name_three_chars_is_the_minimum():
    assert rec(full_name="Raj").full_name == "Raj"


@pytest.mark.parametrize("raw,iso", [("2000-01-31", "2000-01-31"), ("31/01/2000", "2000-01-31"), ("31-01-2000", "2000-01-31"),
                                     ("31-Jan-2000", "2000-01-31"), ("31 Jan 2000", "2000-01-31"), ("01/31/2000", "2000-01-31"),
                                     ("  2000-01-31 ", "2000-01-31")])
def test_dob_formats(raw, iso):
    assert rec(dob=raw).dob.isoformat() == iso


def test_dob_ambiguous_day_month_prefers_day_first():
    assert rec(dob="03/04/1990").dob == date(1990, 4, 3)


def test_dob_date_object_passes_through_unvalidated():
    assert rec(dob=date(1850, 1, 1)).dob == date(1850, 1, 1)


def test_dob_unparseable_and_implausible_ages():
    assert "unparseable dob" in errs(dob="not a date")[0][1]
    today = date.today()
    assert "implausible age" in errs(dob=(today + timedelta(days=400)).isoformat())[0][1]
    assert "implausible age" in errs(dob="1850-01-01")[0][1]
    assert rec(dob=today.isoformat()).dob == today                                   # age 0 allowed
    assert rec(dob=(today - timedelta(days=int(100 * 365.25) - 5)).isoformat()).dob      # just under 100
    assert "implausible age" in errs(dob=(today - timedelta(days=int(100 * 365.25) + 40)).isoformat())[0][1]


@pytest.mark.parametrize("raw,out", [("m", "M"), (" Male ", "M"), ("FEMALE", "F"), ("f", "F"), ("x", ""), (None, ""), ("", "")])
def test_gender_mapping(raw, out):
    assert rec(gender=raw).gender == out


@pytest.mark.parametrize("raw,out", [("9876543210", "+919876543210"), ("+91 98765-43210", "+919876543210"), ("0091 9876543210", "+919876543210"),
                                     ("", ""), (None, ""), ("abc", ""), ("6000000000", "+916000000000")])
def test_phone_normalised(raw, out):
    assert rec(phone=raw).phone == out


@pytest.mark.parametrize("raw", ["5876543210", "98765", "12345678901234"[-10:]])
def test_phone_rejected(raw):
    assert errs(phone=raw)[0] == ("phone", "Value error, invalid mobile")


@pytest.mark.parametrize("raw,out", [(" Asha@Example.COM ", "asha@example.com"), ("a.b+c@x-y.co.in", "a.b+c@x-y.co.in"), ("", ""), (None, "")])
def test_email(raw, out):
    assert rec(email=raw).email == out


@pytest.mark.parametrize("raw", ["no-at.com", "a@b", "a@@b.com", "a b@c.com"])
def test_email_rejected(raw):
    assert errs(email=raw)[0][0] == "email"


def test_pan_uppercased_and_validated():
    assert rec(pan=" abcde1234f ").pan == "ABCDE1234F"
    assert rec(pan="").pan == "" and rec(pan=None).pan == ""
    for bad in ("ABCD12345F", "ABCDE1234", "ABCDE12345", "1BCDE1234F"):
        assert errs(pan=bad)[0][0] == "pan"


def test_aadhaar_digits_only_and_length_12():
    assert rec(aadhaar="1234 5678 9012").aadhaar == "123456789012"
    assert rec(aadhaar="").aadhaar == "" and rec(aadhaar=None).aadhaar == ""
    assert errs(aadhaar="12345678901")[0] == ("aadhaar", "Value error, aadhaar must be 12 digits")
    assert errs(aadhaar="1234567890123")[0][0] == "aadhaar"


def test_pincode_rules():
    assert rec(pincode=" 560001 ").pincode == "560001" and rec(pincode="").pincode == ""
    for bad in ("060001", "56001", "5600011", "56000a"):
        assert errs(pincode=bad)[0][0] == "pincode"


def test_state_uppercased_and_unvalidated():
    assert rec(state=" ka ").state == "KA" and rec(state=None).state == ""


def test_income_parsing_and_negative_rejected():
    assert rec(annual_income="12,50,000").annual_income == 1250000.0
    assert rec(annual_income="").annual_income == 0.0 and rec(annual_income=None).annual_income == 0.0
    assert rec(annual_income=0).annual_income == 0.0
    assert errs(annual_income="-1")[0] == ("annual_income", "Value error, negative income")
    assert errs(annual_income="abc")[0][0] == "annual_income"


@pytest.mark.parametrize("raw,out", [("1", True), ("Y", True), (" yes ", True), ("TRUE", True), ("smoker", True), (True, True),
                                     ("0", False), ("no", False), ("", False), (None, False), ("n", False), (False, False)])
def test_smoker_flag(raw, out):
    assert rec(smoker=raw).smoker is out


def test_verhoeff_roundtrip_and_detects_single_digit_errors():
    base = "23456789012"
    full = verhoeff_append(base)
    assert len(full) == 12 and verhoeff_valid(full)
    for i in range(12):
        bad = full[:i] + str((int(full[i]) + 1) % 10) + full[i + 1:]
        assert not verhoeff_valid(bad), i
    assert verhoeff_valid("2363") is True                 # textbook Verhoeff example (236 + check digit 3)


def test_h_is_salted_sha256_hex_and_deterministic():
    assert h("x") == h("x") and h("x") != h("y") and len(h("x")) == 64 and h("x") != __import__("hashlib").sha256(b"x").hexdigest()


# ---- ingest -------------------------------------------------------------------------------------------------

@pytest.fixture
def con():
    return db.connect(":memory:")


def write_csv(path, rows, header):
    path.write_text("﻿" + ",".join(header) + "\n" + "\n".join(",".join(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_normalise_keys_aliases_and_spacing():
    row = {"Customer Name": "A", " Date of Birth ": "d", "PAN No": "p", "UID": "u", "zip": "z", "Income": "1", "id": "9", "weird col": "w"}
    assert ingest.normalise_keys(row) == {"full_name": "A", "dob": "d", "pan": "p", "aadhaar": "u", "pincode": "z",
                                          "annual_income": "1", "external_id": "9", "weird_col": "w"}


def test_read_rows_csv_with_bom_and_jsonl_skipping_blank_lines(tmp_path):
    c = write_csv(tmp_path / "a.csv", [["Asha", "1990-01-01"]], ["name", "dob"])
    assert list(ingest.read_rows(c)) == [{"name": "Asha", "dob": "1990-01-01"}]            # BOM stripped from first header
    j = tmp_path / "a.jsonl"
    j.write_text('{"name": "A"}\n\n   \n{"name": "B"}\n')
    assert list(ingest.read_rows(j)) == [{"name": "A"}, {"name": "B"}]


def test_ingest_accepts_good_rows_hashes_ids_and_rejects_bad_ones(con, tmp_path):
    aad = verhoeff_append("23456789012")
    p = tmp_path / "in.jsonl"
    rows = [dict(name="Asha Rao", date_of_birth="1990-05-17", pan="abcde1234f", aadhaar=aad, income="50,000", smoker="yes", mobile="9876543210", extra="ignored"),
            dict(name="Ba", date_of_birth="1990-05-17"),
            dict(name="Ravi Kumar", date_of_birth="31/12/1985", aadhaar="123456789012", pan="")]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    out = ingest.ingest_file(con, p, "agent_app", batch_id="B1")
    assert out == {"batch_id": "B1", "rows_in": 3, "accepted": 2, "rejected": 1, "reject_rate": 0.3333}
    a, r = [dict(x) for x in con.execute("SELECT * FROM customers_raw ORDER BY raw_id")]
    assert a["full_name"] == "Asha Rao" and a["row_no"] == 2 and r["row_no"] == 4        # row numbers count the header as row 1
    assert a["pan_hash"] == h("ABCDE1234F") and a["pan_last4"] == "234F" and a["aadhaar_last4"] == aad[-4:]
    assert a["aadhaar_valid"] == 1 and r["aadhaar_valid"] == 0 and r["pan_hash"] is None and r["pan_last4"] is None
    assert (a["smoker"], a["annual_income"], a["channel"], a["phone"]) == (1, 50000.0, "agent_app", "+919876543210")
    rej = con.execute("SELECT * FROM rejects").fetchall()
    assert len(rej) == 1 and rej[0]["row_no"] == 3 and "full_name" in rej[0]["reasons"]
    batch = con.execute("SELECT * FROM ingest_batches").fetchone()
    assert (batch["batch_id"], batch["source"], batch["channel"], batch["rows_in"], batch["rows_ok"], batch["rows_rejected"]) == ("B1", str(p), "agent_app", 3, 2, 1)


def test_reject_log_never_stores_pan_or_aadhaar(con, tmp_path):
    p = tmp_path / "in.jsonl"
    p.write_text(json.dumps(dict(name="X", dob="1990-01-01", pan="ABCDE1234F", aadhaar="123456789012", city="Pune")))
    ingest.ingest_file(con, p)
    raw = json.loads(con.execute("SELECT raw FROM rejects").fetchone()[0])
    assert "pan" not in raw and "aadhaar" not in raw and raw["city"] == "Pune" and raw["full_name"] == "X"


def test_ingest_reasons_join_multiple_field_errors(con, tmp_path):
    p = tmp_path / "in.jsonl"
    p.write_text(json.dumps(dict(name="Asha Rao", dob="1990-01-01", pan="bad", pincode="1")))
    ingest.ingest_file(con, p)
    reasons = con.execute("SELECT reasons FROM rejects").fetchone()[0]
    assert reasons.count("; ") == 1 and "pan: Value error, invalid PAN format" in reasons and "pincode:" in reasons


def test_ingest_empty_file_and_default_channel_and_generated_batch_id(con, tmp_path):
    p = tmp_path / "e.csv"
    p.write_text("name,dob\n")
    out = ingest.ingest_file(con, p)
    assert out["rows_in"] == 0 and out["reject_rate"] == 0 and out["batch_id"].startswith("B") and len(out["batch_id"]) == 9
    assert con.execute("SELECT channel FROM ingest_batches").fetchone()[0] == "unknown"
    assert ingest.ingest_file(con, p)["batch_id"] != out["batch_id"]


# ---- entity resolution --------------------------------------------------------------------------------

def test_jaro_winkler_reference_values_and_symmetry_properties():
    jw = resolve.jaro_winkler
    assert jw("MARTHA", "MARHTA") == pytest.approx(0.9611, abs=1e-4)
    assert jw("DWAYNE", "DUANE") == pytest.approx(0.84, abs=1e-4)
    assert jw("DIXON", "DICKSONX") == pytest.approx(0.8133, abs=1e-4)
    assert jw("abc", "xyz") == 0.0 and jw("", "a") == 0.0
    assert jw("Asha", "ASHA") == 1.0                                   # case-insensitive
    assert jw("prefix_a", "prefix_b") > jw("a_prefix", "b_prefix")      # common-prefix bonus
    assert jw("abcd", "abce") == pytest.approx(jw("abce", "abcd"))


BASE = dict(pan_hash=None, aadhaar_hash=None, dob="1990-01-01", full_name="Asha Rao", phone="+919876543210", email="a@x.com", pincode="560001")


def test_score_government_id_hash_is_decisive_even_with_different_dob_and_name():
    assert resolve.score({**BASE, "pan_hash": "h"}, {**BASE, "pan_hash": "h", "dob": "1999-09-09", "full_name": "Other"}) == 1.0
    assert resolve.score({**BASE, "aadhaar_hash": "a"}, {**BASE, "aadhaar_hash": "a", "dob": "1999-09-09"}) == 1.0
    assert resolve.score({**BASE, "pan_hash": "h1", "phone": "+911111111111", "email": "q@z.com", "pincode": "1"},
                         {**BASE, "pan_hash": "h2"}) < 1.0                                    # different PANs are not 'equal hashes'
    no_ids = resolve.score({**BASE, "phone": "+911", "email": "q@z.com", "pincode": "1"}, {**BASE, "pan_hash": None})
    assert no_ids < 1.0                                                                       # None == None is not a PAN match


def test_score_different_dob_capped_at_point_six():
    assert resolve.score(BASE, {**BASE, "dob": "1991-01-01"}) == 0.6
    assert resolve.score(BASE, {**BASE, "dob": "1991-01-01", "full_name": "Zzzz"}) == 0.0
    s = resolve.score(BASE, {**BASE, "dob": "1991-01-01", "full_name": "Asha Roa"})
    assert s == pytest.approx(0.6 * resolve.jaro_winkler("Asha Rao", "Asha Roa")) and s < 0.6


def test_score_requires_corroboration_to_reach_auto_merge():
    nothing = {**BASE, "phone": "", "email": "", "pincode": ""}
    assert resolve.score(nothing, nothing) == pytest.approx(resolve.AUTO - 0.07)
    assert resolve.score(BASE, BASE) == 1.0
    assert resolve.score(BASE, {**BASE, "phone": "+911", "email": "other@x.com", "pincode": "999999"}) < resolve.REVIEW


def test_score_only_fields_present_on_both_sides_count():
    one_sided = {**BASE, "phone": "", "email": "", "pincode": ""}
    assert resolve.score(BASE, one_sided) == pytest.approx(resolve.AUTO - 0.07)       # nothing comparable -> uncorroborated
    only_phone_differs = {**BASE, "phone": "+919876543211", "email": "", "pincode": ""}
    assert resolve.score(BASE, only_phone_differs) == 0.75                          # (0.4+0.2)/(0.4+0.2+0.2), the REVIEW boundary
    assert resolve.score(BASE, {**BASE, "phone": "+919876543211", "email": "a@x.com", "pincode": ""}) == 0.7778


def test_score_thresholds_are_ordered():
    assert 0 < resolve.REVIEW < resolve.AUTO < 1


def test_union_find():
    uf = resolve.UnionFind()
    assert uf.find(1) == 1
    uf.union(1, 2); uf.union(3, 4)
    assert uf.find(1) == uf.find(2) != uf.find(3)
    uf.union(2, 4)
    assert len({uf.find(i) for i in (1, 2, 3, 4)}) == 1
    assert uf.find(99) == 99


def test_blocks_keys():
    rows = [dict(raw_id=1, pan_hash="p", aadhaar_hash=None, phone="+91", email="", dob="1990-01-01", full_name="Asha"),
            dict(raw_id=2, pan_hash="p", aadhaar_hash="a", phone="", email="e", dob="1990-01-01", full_name="Anil")]
    b = resolve.blocks(rows)
    assert b[("pan_hash", "p")] == {1, 2} and b[("phone", "+91")] == {1} and b[("email", "e")] == {2} and b[("aadhaar_hash", "a")] == {2}
    assert b[("dob_initial", "1990-01-01", "A")] == {1, 2}
    assert ("phone", "") not in b and ("email", "") not in b


def load(con, batch, **kw):
    base = dict(batch_id=batch, row_no=2, external_id="", full_name="Asha Rao", dob="1990-01-01", gender="F", phone="+919876543210", email="",
                pan_hash=None, pan_last4=None, aadhaar_hash=None, aadhaar_last4=None, aadhaar_valid=None, address="", pincode="",
                state="KA", occupation="", annual_income=0.0, smoker=0, channel="web", golden_id=None)
    base.update(kw)
    cols = ",".join(base)
    cur = con.execute(f"INSERT INTO customers_raw({cols}) VALUES({','.join('?' * len(base))})", list(base.values()))
    con.commit()
    return cur.lastrowid


def test_resolve_batch_auto_merges_and_builds_golden_with_survivorship(con):
    a = load(con, "B1", annual_income=100.0, channel="app", email="old@x.com", smoker=0)
    b = load(con, "B2", annual_income=900.0, channel="call", email="", smoker=1, state="")
    out = resolve.resolve_batch(con, "B2")
    assert out == {"auto_merged": 1, "queued_for_review": 0, "golden_customers": 1}
    g = dict(con.execute("SELECT * FROM customers").fetchone())
    assert g["email"] == "old@x.com"                        # newer record blank -> falls back to older non-empty value
    assert g["state"] == "KA" and g["annual_income"] == 900.0 and g["smoker"] == 1
    assert g["channels"] == "app,call" and g["source_records"] == 2
    gids = {r[0] for r in con.execute("SELECT golden_id FROM customers_raw")}
    assert len(gids) == 1 and gids == {g["golden_id"]}


def test_resolve_batch_queues_review_band_and_leaves_distinct_customers_apart(con):
    load(con, "B1", phone="+919876543210")
    load(con, "B2", phone="+919876543211")                 # score 0.75: exactly REVIEW -> queued, not merged
    load(con, "B2", full_name="Zed Quinn", dob="1970-02-02", phone="+911111111111")
    out = resolve.resolve_batch(con, "B2")
    assert out["auto_merged"] == 0 and out["queued_for_review"] == 1 and out["golden_customers"] == 3
    q = con.execute("SELECT * FROM match_queue").fetchone()
    assert q["status"] == "pending" and q["score"] == 0.75 and q["batch_id"] == "B2"


def test_resolve_batch_ignores_pairs_entirely_outside_the_batch(con):
    load(con, "OLD", phone="+919876543210")
    load(con, "OLD", phone="+919876543210")
    load(con, "NEW", full_name="Zed Quinn", dob="1970-02-02", phone="+911")
    out = resolve.resolve_batch(con, "NEW")
    assert out["auto_merged"] == 0 and out["queued_for_review"] == 0 and out["golden_customers"] == 3


def test_resolve_batch_keeps_earlier_golden_ids_stable_on_rerun(con):
    load(con, "B1")
    load(con, "B1", phone="+919876543210")
    resolve.resolve_batch(con, "B1")
    before = {r[0] for r in con.execute("SELECT golden_id FROM customers")}
    load(con, "B2", full_name="Zed Quinn", dob="1970-02-02", phone="+911")
    resolve.resolve_batch(con, "B2")
    after = {r[0] for r in con.execute("SELECT golden_id FROM customers")}
    assert before < after and len(after) == 2


def test_resolve_batch_does_not_rescore_pairs_already_linked_by_a_golden_id(con):
    load(con, "B1", golden_id="GOLD", phone="+919876543210")
    load(con, "B2", golden_id="GOLD", phone="+919876543211")            # would score 0.75 (review) if it were scored again
    out = resolve.resolve_batch(con, "B2")
    assert out == {"auto_merged": 0, "queued_for_review": 0, "golden_customers": 1}
    assert con.execute("SELECT golden_id, source_records FROM customers").fetchone()[:] == ("GOLD", 2)


def test_steward_merge_and_distinct_decisions(con):
    load(con, "B1", phone="+919876543210")
    load(con, "B2", phone="+919876543211", annual_income=7.0)
    resolve.resolve_batch(con, "B2")
    (qid,) = [r[0] for r in con.execute("SELECT id FROM match_queue")]
    resolve.apply_steward_decision(con, qid, False)
    assert con.execute("SELECT status FROM match_queue").fetchone()[0] == "distinct"
    assert con.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 2
    resolve.apply_steward_decision(con, qid, True)
    assert con.execute("SELECT status FROM match_queue").fetchone()[0] == "merged"
    assert con.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1
    assert con.execute("SELECT annual_income, source_records FROM customers").fetchone()[:] == (7.0, 2)
    assert len({r[0] for r in con.execute("SELECT golden_id FROM customers_raw")}) == 1
    resolve.apply_steward_decision(con, qid, True)                    # already merged: no error, no change
    assert con.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1


# ---- profiles ----------------------------------------------------------------------------------------------

def test_age_on_birthday_boundary():
    assert profile.age_on("2000-06-15", date(2020, 6, 14)) == 19
    assert profile.age_on("2000-06-15", date(2020, 6, 15)) == 20
    assert profile.age_on("2000-06-15", date(2020, 6, 16)) == 20
    assert profile.age_on("2000-12-31", date(2001, 1, 1)) == 0
    assert profile.age_on("2000-06-15") == date.today().year - 2000 - ((date.today().month, date.today().day) < (6, 15))


def test_completeness_counts_non_empty_non_zero_fields():
    full = dict(full_name="a", dob="d", gender="F", phone="p", email="e", pan_last4="1", aadhaar_last4="2", pincode="x", state="s", occupation="o", annual_income=1.0)
    assert profile.completeness(full) == 1.0
    assert profile.completeness({**full, "email": "", "annual_income": 0, "state": None}) == round(8 / 11, 3)
    assert profile.completeness({k: None for k in full}) == 0.0


@pytest.mark.parametrize("valid,pan,level", [(1, "1234", "full_docs"), (1, None, "partial_docs"), (0, "1234", "partial_docs"),
                                             (None, "", "none"), (0, None, "none")])
def test_kyc_level(valid, pan, level):
    assert profile.kyc_level({"aadhaar_valid": valid, "pan_last4": pan}) == level


@pytest.mark.parametrize("si,n,prem,tier", [(10_000_000, 1, 0, "high"), (9_999_999, 1, 0, "mid"), (0, 3, 0, "high"),
                                            (0, 2, 0, "mid"), (0, 1, 100_000, "high"), (0, 1, 99_999, "low"), (2_000_000, 1, 0, "mid"),
                                            (1_999_999, 1, 0, "low"), (0, 0, 0, "low")])
def test_value_tier_boundaries(si, n, prem, tier):
    assert profile.value_tier(si, n, prem) == tier


@pytest.mark.parametrize("claims,ratio,late,age,tier", [
    (0, 0, 0, 59, "low"), (0, 0, 0, 60, "low"), (1, 0.61, 0, 30, "medium"), (1, 0.6, 0, 30, "low"), (3, 0, 0, 30, "medium"),
    (2, 0.9, 0, 30, "medium"), (2, 0.91, 0, 30, "medium"), (3, 0.91, 0, 30, "high"), (0, 0, 3, 60, "medium"), (1, 0.7, 1, 60, "high"),
    (0, 0.95, 1, 30, "medium"), (0, 0.95, 3, 30, "high")])
def test_risk_tier_points(claims, ratio, late, age, tier):
    assert profile.risk_tier(claims, ratio, late, age) == tier


def test_build_profiles_end_to_end(con):
    gid = "G1"
    con.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (gid, "Asha Rao", "1960-03-01", "F", "+91", "a@x.com", "234F", "9012", 1, "560001", "KA", "office", 1_000_000.0, 0, "web", 1))
    con.execute("INSERT INTO customers VALUES('G2','No Policies','1990-01-01',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,0,0,'',1)")
    pol = [("P1", gid, "health", 6_000_000, 60_000, 10, 2020, "in_force", 2, 10_000, 1, "KA", "x"),
           ("P2", gid, "motor", 5_000_000, 50_000, 1, 2024, "in_force", 1, None, None, "KA", "x"),
           ("P3", gid, "home", 99_000_000, 9_000_000, 1, 2000, "cancelled", 9, 9, 9, "KA", "x")]
    con.executemany("INSERT INTO policy_book VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", pol)
    out = profile.build_profiles(con, today=date(2026, 3, 1))
    assert out["profiles"] == 2
    p = dict(con.execute("SELECT * FROM profiles WHERE golden_id='G1'").fetchone())
    assert (p["age"], p["tenure_years"], p["n_policies"], p["total_sum_insured"]) == (66, 6, 2, 11_000_000)      # cancelled policy excluded
    assert (p["claims_5y"], p["late_payments"], p["claim_ratio"], p["cover_to_income"]) == (3, 1, round(10_000 / (110_000 * 5), 4), 11.0)
    assert (p["kyc_level"], p["value_tier"], p["risk_tier"], p["segment"]) == ("full_docs", "high", "high", "high-value/high-risk")   # 2 (claims) + 0 + 1 (late) + 1 (age 66) = 4 pts
    q = dict(con.execute("SELECT * FROM profiles WHERE golden_id='G2'").fetchone())
    assert (q["n_policies"], q["tenure_years"], q["cover_to_income"], q["kyc_level"], q["segment"]) == (0, 0, None, "none", "low-value/low-risk")
    assert out["avg_completeness"] == round((p["completeness"] + q["completeness"]) / 2, 3)


def test_build_profiles_with_no_customers(con):
    assert profile.build_profiles(con) == {"profiles": 0, "avg_completeness": 0}


# ---- underwriting -------------------------------------------------------------------------------------

def test_life_rating_classes_and_flags():
    ok = life_rating(35, False, "office", 1_000_000, 10_000_000)
    assert ok == {"mortality_multiple": 1.0, "rating_class": "preferred", "flags": []}
    assert life_rating(35, False, "driver", 1_000_000, 1)["rating_class"] == "standard"          # 1.15
    assert life_rating(35, False, "miner", 1_000_000, 1)["rating_class"] == "standard"           # 1.5 is the standard ceiling
    s = life_rating(35, True, "office", 1_000_000, 1)
    assert (s["rating_class"], s["mortality_multiple"]) == ("substandard", SMOKER_MULTIPLE)
    assert life_rating(35, True, "MINER", 1_000_000, 1)["mortality_multiple"] == 2.7
    assert life_rating(35, False, None, 1_000_000, 1)["mortality_multiple"] == 1.0


def test_life_rating_cover_to_income_and_age_refer_boundaries():
    at_cap = life_rating(40, False, "office", 1_000, MAX_COVER_TO_INCOME * 1_000)
    assert at_cap["rating_class"] == "preferred" and at_cap["flags"] == []                       # exactly 25x is allowed
    over = life_rating(40, False, "office", 1_000, MAX_COVER_TO_INCOME * 1_000 + 1)
    assert over["rating_class"] == "refer" and over["flags"] == ["cover/income 25x exceeds 25x (financial underwriting)"]
    assert life_rating(AGE_REFER - 1, False, "office", 1, 1)["flags"] == []
    old = life_rating(AGE_REFER, False, "office", 1, 1)
    assert old["rating_class"] == "refer" and old["flags"] == ["age 65 >= 65"]
    nodecl = life_rating(40, False, "office", 0, 1_000_000)
    assert nodecl["flags"] == ["income not declared"] and nodecl["rating_class"] == "refer"
    assert life_rating(40, False, "office", None, 1)["flags"] == ["income not declared"]


def test_decline_class_is_unreachable_with_the_current_factor_tables():
    """Dead branch: the largest possible multiple is smoker x miner = 2.7, below the >3 decline threshold.
    If this fails, a factor was raised and the decline path now needs its own tests."""
    worst = max(SMOKER_MULTIPLE * m for m in OCCUPATION_MULTIPLE.values())
    assert worst == pytest.approx(2.7) and worst <= 3


def test_life_rating_decline_when_multiple_exceeds_three(monkeypatch):
    monkeypatch.setitem(OCCUPATION_MULTIPLE, "stuntman", 2.0)           # 1.8 x 2.0 = 3.6
    r = life_rating(30, True, "stuntman", 1_000_000, 1)
    assert (r["rating_class"], r["mortality_multiple"]) == ("decline", 3.6)
    assert life_rating(30, True, "stuntman", 0, 1)["rating_class"] == "decline"        # decline outranks refer flags
    monkeypatch.setitem(OCCUPATION_MULTIPLE, "edge", 3.0 / 1.8)
    assert life_rating(30, True, "edge", 1_000_000, 1)["rating_class"] == "substandard"    # exactly 3.0 is not > 3


def test_life_rating_flags_beat_loading_class_and_accumulate():
    r = life_rating(70, True, "office", 100, 10_000)
    assert r["rating_class"] == "refer" and len(r["flags"]) == 2


# ---- GLM ---------------------------------------------------------------------------------------------------

def test_glm_rejects_unknown_family():
    X = np.ones((3, 1))
    with pytest.raises(ValueError, match="tweedie"):
        glm._irls(X, np.array([1.0, 2.0, 3.0]), None, "tweedie")


def test_frequency_glm_recovers_poisson_effects():
    rng = np.random.default_rng(0)
    n = 4000
    x = rng.integers(0, 2, n)
    X = np.column_stack([np.ones(n), x])
    exposure = rng.uniform(0.5, 1.5, n)
    y = rng.poisson(np.exp(-2.0 + 0.7 * x) * exposure)
    beta, se = glm.fit_frequency(X, y, exposure)
    assert beta[0] == pytest.approx(-2.0, abs=0.15) and beta[1] == pytest.approx(0.7, abs=0.15)
    assert se.shape == (2,) and (se > 0).all() and se[1] < 0.2


def test_frequency_offset_scales_estimates():
    n = 500
    X = np.ones((n, 1))
    y = np.full(n, 1.0)
    b1, _ = glm.fit_frequency(X, y, np.full(n, 1.0))
    b2, _ = glm.fit_frequency(X, y, np.full(n, 2.0))
    assert b1[0] == pytest.approx(0.0, abs=1e-6) and b2[0] == pytest.approx(-np.log(2), abs=1e-6)


def test_severity_glm_ignores_zero_claim_rows_and_recovers_effect():
    rng = np.random.default_rng(1)
    n = 3000
    x = rng.integers(0, 2, n)
    X = np.column_stack([np.ones(n), x])
    counts = rng.integers(0, 4, n)
    sev = np.exp(8.0 + 0.5 * x) * rng.gamma(20, 1 / 20, n)
    sev = np.where(counts > 0, sev, 0.0)
    beta, se = glm.fit_severity(X, sev, counts)
    assert beta[0] == pytest.approx(8.0, abs=0.1) and beta[1] == pytest.approx(0.5, abs=0.1)
    assert se.shape == (2,) and (se > 0).all()


def test_pure_premium_is_product_of_frequency_and_severity():
    assert glm.pure_premium(np.array([1.0, 1.0]), np.array([-2.0, 0.5]), np.array([8.0, 0.0])) == pytest.approx(np.exp(-1.5) * np.exp(8.0))
    assert glm.pure_premium(np.array([1.0, 0.0]), np.array([-2.0, 0.5]), np.array([8.0, 0.0])) == pytest.approx(np.exp(-2.0 + 8.0))


def test_poisson_deviance():
    assert glm.poisson_deviance([1, 2, 3], [1, 2, 3]) == pytest.approx(0.0)
    assert glm.poisson_deviance([0], [2.0]) == pytest.approx(4.0)                         # 2*(0 - (0-2)) with the y=0 term dropped
    assert glm.poisson_deviance([4], [2.0]) == pytest.approx(2 * (4 * np.log(2) - 2))
    assert glm.poisson_deviance([1, 0], [2.0, 2.0]) > glm.poisson_deviance([1, 0], [0.5, 0.5])


# ---- portfolio risk -----------------------------------------------------------------------------------------

def add_customer(con, gid, dob="1985-01-01", smoker=0, occ="office"):
    con.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (gid, gid, dob, "M", "", "", "", "", 0, "", "KA", occ, 0, smoker, "", 1))


def add_policy(con, pid, gid, product, si, premium=1000.0, state="KA", status="in_force"):
    con.execute("INSERT INTO policy_book VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (pid, gid, product, si, premium, 1, 2025, status, 0, 0, 0, state, "x"))


def test_snapshot_empty_book_raises(con):
    with pytest.raises(ValueError, match="empty policy book"):
        pr.snapshot(con, 1_000_000)
    add_customer(con, "G")
    add_policy(con, "P", "G", "health", 100, status="lapsed")           # only in_force policies count
    with pytest.raises(ValueError, match="empty policy book"):
        pr.snapshot(con, 1_000_000)


def test_policy_frame_joins_customers_and_derives_age(con):
    add_customer(con, "G", dob="1985-06-01")
    add_policy(con, "P1", "G", "health", 100)
    add_policy(con, "P2", "G", "motor", 100, status="cancelled")
    add_policy(con, "P3", "NOBODY", "motor", 100)                          # no matching customer: inner join drops it
    rows = pr.policy_frame(con)
    assert [r["policy_id"] for r in rows] == ["P1"] and rows[0]["age"] == date.today().year - 1985


def test_expected_loss_non_life_and_young_motor_loading():
    rows = [dict(product="health", age=40, sum_insured=1, smoker=0, occupation=""),
            dict(product="motor", age=40, sum_insured=1, smoker=0, occupation=""),
            dict(product="motor", age=24, sum_insured=1, smoker=0, occupation=""),
            dict(product="motor", age=25, sum_insured=1, smoker=0, occupation="")]
    el = pr.expected_loss(rows)
    mean = lambda k: np.exp(pr.SEV[k][0] + pr.SEV[k][1] ** 2 / 2)
    assert el[0] == pytest.approx(pr.FREQ["health"] * mean("health"))
    assert el[1] == pytest.approx(pr.FREQ["motor"] * mean("motor")) and el[3] == el[1]
    assert el[2] == pytest.approx(el[1] * 1.6)


def test_expected_loss_life_uses_rating_multiple_and_caps_at_one():
    base = dict(product="term_life", age=50, sum_insured=1_000_000, smoker=0, occupation="office")
    smoker = {**base, "smoker": 1}
    el = pr.expected_loss([base, smoker])
    assert el[1] == pytest.approx(el[0] * 1.8) and el[0] > 0
    huge_age = pr.expected_loss([{**base, "age": 400}])                      # clamped to OMEGA, q <= 1
    assert 0 < huge_age[0] <= 1_000_000


def test_concentration_shares_and_hhi():
    rows = [dict(state="A", sum_insured=75), dict(state="B", sum_insured=25)]
    share, hhi = pr.concentration(rows, "state")
    assert share == {"A": 0.75, "B": 0.25} and hhi == pytest.approx(0.625)
    assert pr.concentration([], "state") == ({}, 0.0)
    assert pr.concentration([dict(state="Z", sum_insured=0)], "state") == ({"Z": 0.0}, 0.0)


def test_snapshot_metrics_for_mixed_book_and_persistence(con):
    for i in range(6):
        add_customer(con, f"G{i}", dob="1990-01-01")
    add_policy(con, "L1", "G0", "term_life", 10_000_000, 20_000, "KA")
    add_policy(con, "L2", "G1", "term_life", 2_000_000, 5_000, "KA")
    add_policy(con, "H1", "G2", "health", 500_000, 30_000, "MH")
    add_policy(con, "M1", "G3", "motor", 800_000, 25_000, "DL")
    add_policy(con, "O1", "G3", "home", 4_000_000, 9_000, "DL")
    m, agg, net = pr.snapshot(con, 50_000_000, n_sims=2000, label="t", return_samples=True)
    assert (m["policies"], m["customers"], m["total_sum_insured"], m["premium"]) == (5, 4, 17_300_000, 89_000)
    assert m["max_state"] == "KA" and m["max_state_share_pct"] == round(12 / 17.3 * 100, 1)
    assert m["largest_single_cover"] == 10_000_000
    assert m["top10_customer_share_pct"] == 100.0                             # fewer than 10 customers: all of them
    assert m["product_mix_pct"] == {"term_life": 69.4, "health": 2.9, "motor": 4.6, "home": 23.1}
    assert m["own_funds"] == 50_000_000 and m["var995_pct_own_funds"] == round(m["var995"] / 50_000_000 * 100, 1)
    assert agg.shape == net.shape == (2000,) and (net <= agg + 1e-6).all()
    assert m["var995_net_of_reinsurance"] <= m["var995"] + 1
    saved = con.execute("SELECT label, metrics FROM portfolio_snapshots").fetchone()
    assert saved["label"] == "t" and json.loads(saved["metrics"])["policies"] == 5
    plain = pr.snapshot(con, 50_000_000, n_sims=500)
    assert isinstance(plain, dict) and plain["policies"] == 5


def test_snapshot_non_life_only_and_life_only_skip_the_other_branch(con):
    add_customer(con, "G")
    add_policy(con, "H", "G", "health", 1_000_000, 5_000)
    m1, a1, n1 = pr.snapshot(con, 1e7, n_sims=500, return_samples=True)
    assert m1["product_mix_pct"] == {"health": 100.0} and a1.shape == (500,)
    con2 = db.connect(":memory:")
    add_customer(con2, "G")
    add_policy(con2, "L", "G", "term_life", 1_000_000, 5_000)
    m2, a2, n2 = pr.snapshot(con2, 1e7, n_sims=500, return_samples=True)
    assert m2["product_mix_pct"] == {"term_life": 100.0} and a2.shape == (500,)


def test_snapshot_is_deterministic_for_a_seed_and_differs_across_seeds(con):
    add_customer(con, "G")
    add_policy(con, "H", "G", "health", 1_000_000, 5_000)
    a = pr.snapshot(con, 1e7, n_sims=1000, seed=3)["var995"]
    assert pr.snapshot(con, 1e7, n_sims=1000, seed=3)["var995"] == a
    assert pr.snapshot(con, 1e7, n_sims=1000, seed=4)["var995"] != a


def test_synthetic_triangle_shape_and_run_off_pattern():
    tri = pr.synthetic_triangle([100.0] * 5, noise=0.0)
    assert tri.shape == (5, 5)
    assert np.isnan(tri[1, 4]) and not np.isnan(tri[1, 3]) and np.isnan(tri[4, 1]) and not np.isnan(tri[4, 0])
    assert not np.isnan(tri[0]).any()
    assert tri[0].tolist() == pytest.approx([45.0, 72.0, 88.0, 96.0, 100.0])
    noisy = pr.synthetic_triangle([100.0] * 5)
    assert (np.diff(noisy[0]) >= 0).all()                                       # cumulative: never decreases
    assert np.array_equal(noisy, pr.synthetic_triangle([100.0] * 5), equal_nan=True)


def test_reserving_run_reports_both_methods():
    r = pr.reserving_run(None, 1_000_000.0)
    assert r["chain_ladder_reserve"] > 0 and r["bf_reserve"] > 0
    assert len(r["factors"]) == 4 and all(f >= 1 for f in r["factors"])
    assert "synthetic" in r["basis"]
    assert pr.reserving_run(None, 2_000_000.0)["chain_ladder_reserve"] > r["chain_ladder_reserve"]


def test_sqlite_row_factory_is_set_by_db_connect():
    assert db.connect().row_factory is sqlite3.Row
