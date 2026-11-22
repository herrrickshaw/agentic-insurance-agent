"""MockProvider: the deterministic offline 'model' the evals and CI depend on."""
import asyncio
import json

import httpx
import pytest

from ai.providers import MockProvider
from ai.types import ToolSpec

run = asyncio.run
M = MockProvider()


def call(task, text, **meta):
    msgs = [{"role": "user", "content": text}]
    r = run(M.complete(model="mock", messages=msgs, meta={"task": task, **meta}))
    return json.loads(r.text), r


def test_force_error_raises_before_any_task():
    with pytest.raises(httpx.ConnectError):
        run(M.complete(model="m", messages=[{"role": "user", "content": "x"}], meta={"task": "classify", "force_error": True}))


def test_unknown_or_missing_task_returns_empty_json_and_token_estimate():
    out, r = call("nope", "x" * 40)
    assert out == {} and r.text == "{}"
    assert (r.input_tokens, r.output_tokens, r.model) == (10, 0, "mock")   # len//4 for each side
    r2 = run(M.complete(model="m", messages=[{"role": "user", "content": "abcd" * 3}]))
    assert r2.text == "{}" and r2.input_tokens == 3


def test_no_user_message_means_empty_text():
    r = run(M.complete(model="m", messages=[{"role": "system", "content": "s"}], meta={"task": "classify"}))
    out = json.loads(r.text)
    assert out["doc_type"] == "other" and out["confidence"] == 0.3 and out["rationale"] == "0 keyword hits"


def test_classify_scoring_and_confidence_cap():
    out, _ = call("classify", "Invoice No 7. Bill to X.")          # 'invoice no', 'invoice', 'bill to'
    assert out["doc_type"] == "invoice" and out["rationale"] == "3 keyword hits"
    assert out["confidence"] == pytest.approx(0.95)
    out, _ = call("classify", "gst only")
    assert out["doc_type"] == "invoice" and out["confidence"] == pytest.approx(0.65)
    text = "claim form claimant date of loss loss date"            # 4 hits -> 0.5+0.6 capped at 0.97
    out, _ = call("classify", text)
    assert out["doc_type"] == "claim_form" and out["confidence"] == 0.97


def test_classify_tie_keeps_first_best():
    # one hit each for claim_form ('claimant') and invoice ('invoice'): the earlier dict key wins, strictly greater needed
    out, _ = call("classify", "claimant invoice")
    assert out["doc_type"] == "claim_form"


def test_summarize_splits_sentences_and_drops_short_ones():
    text = "Short one. This sentence is clearly long enough to keep. Another sufficiently long sentence follows here. " \
           "Third long sentence that is also kept in the list. Fourth long sentence that should be dropped by the cap."
    out, _ = call("summarize", text)
    assert len(out["key_points"]) == 3
    assert "Short one." not in out["key_points"]
    assert out["summary"] == " ".join(out["key_points"]) and out["open_questions"] == []
    big = ". ".join("word" * 8 for _ in range(60)) + "."
    out, _ = call("summarize", big)
    assert len(out["summary"]) <= 600


def test_summarize_boundary_length_exactly_20_is_dropped():
    out, _ = call("summarize", "a" * 19 + ". " + "b" * 20 + ".")
    assert out["key_points"] == ["b" * 20 + "."]


def test_records_classify_picks_best_class_and_extracts_metadata():
    classes = {"contract": ["agreement", "party"], "invoice": ["invoice"], "empty": []}
    text = "Customer: Acme Ltd\nThis agreement between each party dated 2024-03-05"
    out, _ = call("records_classify", text, classes=classes)
    assert out == {"record_class": "contract", "confidence": 0.79, "rationale": "2 keyword hits",
                   "document_date": "2024-03-05", "entity": "Acme Ltd"}


def test_records_classify_no_hits_and_no_classes():
    out, _ = call("records_classify", "nothing relevant", classes={"contract": ["agreement"]})
    assert (out["record_class"], out["confidence"], out["document_date"], out["entity"]) == ("unclassified", 0.2, "", "")
    out, _ = call("records_classify", "agreement", classes={"x": []})
    assert out["record_class"] == "unclassified" and out["rationale"] == "0 keyword hits"


def test_records_classify_confidence_cap_and_entity_truncation():
    classes = {"c": ["a1", "a2", "a3", "a4", "a5"]}
    out, _ = call("records_classify", "a1 a2 a3 a4 a5 vendor: " + "V" * 120, classes=classes)
    assert out["confidence"] == 0.95 and len(out["entity"]) == 80


@pytest.mark.parametrize("doc_type,text,expected", [
    ("claim_form", "Policy No: ABC-2026-000001\nClaimant: Asha Rao\nDate of loss: 2026-10-01\nClaim amount: INR 8,000",
     {"policy_number": "ABC-2026-000001", "claimant_name": "Asha Rao", "loss_date": "2026-10-01", "claim_amount": "8000"}),
    ("invoice", "Invoice No: INV-9\nVendor: Foo & Co\nTotal amount: Rs. 1,25,000",
     {"invoice_number": "INV-9", "vendor": "Foo & Co", "total_amount": "125000"}),
    ("repair_estimate", "Estimate No: E-1\nTotal: ₹ 4,500", {"estimate_number": "E-1", "total_amount": "4500"}),
    ("medical_report", "Patient name: Ravi K\nDiagnosis: Fracture of tibia", {"patient_name": "Ravi K", "diagnosis": "Fracture of tibia"}),
    ("kyc_id", "Name: Meera Das\nAadhaar", {"name": "Meera Das", "id_type": "Aadhaar"}),
    ("payment_proof", "UTR: AB12CD\nAmount: INR 900", {"utr": "AB12CD", "amount": "900"}),
])
def test_extract_per_doc_type(doc_type, text, expected):
    out, _ = call("extract", text, doc_type=doc_type)
    assert {k: v["value"] for k, v in out["fields"].items()} == expected
    for v in out["fields"].values():
        assert v["evidence"] and v["evidence"].lower() in text.lower()


def test_extract_amount_commas_stripped_only_for_amount_fields():
    out, _ = call("extract", "Invoice No: A-1,2\nTotal amount: 1,000", doc_type="invoice")
    assert out["fields"]["total_amount"]["value"] == "1000"
    assert out["fields"]["invoice_number"]["value"] == "A-1"          # regex stops at the comma; not stripped


def test_extract_unknown_doc_type_and_missing_fields():
    assert call("extract", "anything", doc_type="other")[0] == {"fields": {}}
    assert call("extract", "nothing here", doc_type="invoice")[0] == {"fields": {}}


def test_find_returns_value_and_evidence_or_none_pair():
    assert M._find(r"x=(\d+)", "  X=42 ") == ("42", "X=42")
    assert M._find(r"x=(\d+)", "X=42", flags=0) == (None, None)


TOOLS = [ToolSpec(n, n, {}) for n in ("get_policy", "get_fraud_signals", "search_policy_clauses")]
CTX = json.dumps({"claim_number": "CLM-2026-000001", "policy_number": "POL-2026-000001", "claim_amount": 8000})


def agent(extra, tools=TOOLS, ctx=CTX):
    return run(M.complete(model="m", messages=[{"role": "user", "content": ctx}, *extra], tools=tools, meta={"task": "agent"}))


def tool_msg(name, payload, i="c"):
    return {"role": "tool", "tool_call_id": i, "content": json.dumps({"_tool": name, **payload})}


def test_agent_calls_tools_in_order_with_extracted_args():
    r1 = agent([])
    assert [(c.name, c.arguments) for c in r1.tool_calls] == [("get_policy", {"policy_number": "POL-2026-000001"})]
    assert r1.text == "" and (r1.input_tokens, r1.output_tokens, r1.model) == (50, 10, "mock")
    r2 = agent([tool_msg("get_policy", {"status": "active"}, "a")])
    assert [(c.name, c.arguments) for c in r2.tool_calls] == [("get_fraud_signals", {"claim_number": "CLM-2026-000001"})]
    r3 = agent([tool_msg("get_policy", {}, "a"), tool_msg("get_fraud_signals", {"fraud_score": 0}, "b")])
    assert [(c.name, c.arguments["query"]) for c in r3.tool_calls] == [("search_policy_clauses", "claim exclusions and documentation")]
    assert r3.tool_calls[0].id.startswith("call_")


def test_agent_missing_ids_default_to_empty_strings():
    r = agent([], ctx="no ids")
    assert r.tool_calls[0].arguments == {"policy_number": ""}


def test_agent_skips_tools_not_offered():
    r = agent([], tools=[TOOLS[2]])
    assert r.tool_calls[0].name == "search_policy_clauses"


def test_agent_non_json_tool_result_is_ignored_for_done_set():
    r = agent([{"role": "tool", "tool_call_id": "z", "content": "plain text"}])
    assert r.tool_calls[0].name == "get_policy"


def final(fraud, amount, clauses=None):
    msgs = [tool_msg("get_policy", {}, "a"), tool_msg("get_fraud_signals", {"fraud_score": fraud}, "b"),
            tool_msg("search_policy_clauses", {"results": clauses or []}, "c")]
    ctx = json.dumps({"claim_number": "CLM-2026-000001", "policy_number": "POL-2026-000001", "claim_amount": amount})
    r = agent(msgs, ctx=ctx)
    assert r.tool_calls == [] and (r.input_tokens, r.output_tokens) == (80, 40)
    return json.loads(r.text)


@pytest.mark.parametrize("fraud,amount,route", [
    (70, 100, "siu"), (69, 100, "adjuster"), (29, 10000, "fast_track"), (30, 10000, "adjuster"),
    (29, 10001, "adjuster"), (0, 250000, "adjuster"), (100, 999999, "siu"), (0, 0, "fast_track")])
def test_agent_route_boundaries(fraud, amount, route):
    assert final(fraud, amount)["route"] == route


def test_agent_output_fields_and_citation_cap():
    out = final(10, 500, clauses=[{"id": "C1"}, {"id": "C2"}, {"id": "C3"}])
    assert out == {"route": "fast_track", "confidence": 0.7, "rationale": "fraud=10, amount=500", "citations": ["C1", "C2"]}
