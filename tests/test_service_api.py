"""ai/service.py (FastAPI) and ai/evals/run_evals.py, fully offline: mock provider, tmp trace/ledger DBs."""
import importlib
import json
import sqlite3
import sys
import types

import pytest
from fastapi.testclient import TestClient

from ai.evals import run_evals


@pytest.fixture
def service(tmp_path, monkeypatch):
    """Import ai.service fresh with every file path redirected into tmp_path; restore sys.modules afterwards."""
    for k in ("AZURE_OPENAI_ENDPOINT", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AI_DB_DSN", "VECTOR_DSN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("AI_TRACE_DB", str(tmp_path / "traces.db"))
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.delitem(sys.modules, "ai.service", raising=False)
    mod = importlib.import_module("ai.service")
    yield mod
    sys.modules.pop("ai.service", None)


def seed_ledger(path):
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE policies(policy_number,status,sum_insured,product);"
                      "CREATE TABLE claims(claim_number,policy_number,amount);"
                      "INSERT INTO policies VALUES('POL-2026-000001','active',500000,'health');"
                      "INSERT INTO claims VALUES('CLM-2026-000001','POL-2026-000001',8000);")
    con.commit()
    con.close()


DOC = "CLAIM FORM\nPolicy No: POL-2026-000004\nClaimant: Asha Rao\nDate of loss: 2026-10-01\nClaim amount: INR 8,000"


def test_health_lists_providers_and_routes(service):
    body = TestClient(service.app).get("/health").json()
    assert body["status"] == "ok" and body["providers"] == ["mock"]
    assert set(body["routes"]) == {"classify", "summarize", "extract", "agent", "records_classify"}
    assert body["routes"]["classify"] == ["mock-heuristic-1"]


def test_process_document_endpoint(service):
    r = TestClient(service.app).post("/v1/documents/process", json={"text": DOC})
    assert r.status_code == 200
    body = r.json()
    assert body["doc_type"] == "claim_form" and body["fields"]["claim_amount"] == "8000" and body["needs_review"] is False


def test_summarize_endpoint_returns_model_and_honours_audience(service):
    r = TestClient(service.app).post("/v1/documents/summarize", json={"documents": [{"text": DOC}, {"text": DOC}], "audience": "auditor"})
    assert r.status_code == 200
    b = r.json()
    assert b["model"] == "mock-heuristic-1" and set(b) == {"summary", "key_points", "open_questions", "model"}


def test_summarize_passes_the_audience_through_and_defaults_to_claims_adjuster(service, monkeypatch):
    seen = []

    async def fake(llm, texts, audience="claims adjuster"):
        seen.append((texts, audience))
        return service.docai.Summary(summary="s"), {"model": "m"}
    monkeypatch.setattr(service.docai, "summarize", fake)
    c = TestClient(service.app)
    c.post("/v1/documents/summarize", json={"documents": [{"text": "a"}], "audience": "auditor"})
    c.post("/v1/documents/summarize", json={"documents": [{"text": "a"}, {"text": "b"}]})
    assert seen == [(["a"], "auditor"), (["a", "b"], "claims adjuster")]


def test_triage_endpoint_reads_the_ledger_and_returns_recommendation(service, tmp_path):
    seed_ledger(tmp_path / "ledger.db")
    payload = {"claim_number": "CLM-2026-000001", "policy_number": "POL-2026-000001", "claim_amount": 8000}
    out = TestClient(service.app).post("/v1/claims/triage", json=payload).json()
    assert out["route"] == "fast_track" and [s["tool"] for s in out["steps"]] == ["get_policy", "get_fraud_signals", "search_policy_clauses"]
    big = TestClient(service.app).post("/v1/claims/triage", json={**payload, "claim_amount": 250000}).json()
    assert big["route"] == "adjuster"


def test_validation_errors_are_422(service):
    c = TestClient(service.app)
    assert c.post("/v1/documents/process", json={}).status_code == 422
    assert c.post("/v1/claims/triage", json={"claim_number": "x"}).status_code == 422
    assert c.post("/v1/documents/summarize", json={"documents": "nope"}).status_code == 422
    assert c.get("/nope").status_code == 404


def test_metrics_endpoint_reflects_calls_made(service):
    c = TestClient(service.app)
    assert c.get("/v1/ai/metrics").json() == {"summary": [], "root_causes": []}
    c.post("/v1/documents/process", json={"text": DOC})
    m = c.get("/v1/ai/metrics").json()
    assert {s["task"] for s in m["summary"]} == {"classify", "extract"} and m["root_causes"] == []


def test_ledger_helper_uses_row_factory_and_env_path(service, tmp_path):
    seed_ledger(tmp_path / "ledger.db")
    con = service.ledger()
    assert con.row_factory is sqlite3.Row
    assert con.execute("SELECT status FROM policies").fetchone()["status"] == "active"


def test_clauses_are_loaded_into_the_store_at_import(service):
    hits = service.clauses.search("lapsed policy premium not paid", 1)
    assert hits[0]["id"] == "CL-PAY-06"


def test_vector_dsn_switches_to_pgvector_store(tmp_path, monkeypatch):
    calls = []

    class Con:
        def execute(self, sql, params=None):
            calls.append((sql, params))
            return self

        def fetchall(self):
            return []
    mod = types.ModuleType("psycopg")
    mod.connect = lambda dsn, autocommit=False: Con()
    monkeypatch.setitem(sys.modules, "psycopg", mod)
    monkeypatch.setenv("VECTOR_DSN", "postgresql://v")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("AI_TRACE_DB", str(tmp_path / "t.db"))
    monkeypatch.delitem(sys.modules, "ai.service", raising=False)
    try:
        svc = importlib.import_module("ai.service")
        assert type(svc.clauses).__name__ == "PgVectorStore"
        assert sum("INSERT INTO policy_clauses" in c[0] for c in calls) > 5          # every clause was added through SQL
    finally:
        sys.modules.pop("ai.service", None)


# ---- eval runner ---------------------------------------------------------------------------------------------

def test_norm_strips_commas_whitespace_and_case():
    assert run_evals.norm(" 1,250 ") == "1250" and run_evals.norm("ASHA Rao") == "asha rao" and run_evals.norm(8000) == "8000"


def test_jl_skips_blank_lines():
    rows = run_evals.jl("golden_docs.jsonl")
    assert rows and all("text" in r and "doc_type" in r for r in rows)


def test_main_exits_zero_and_prints_scores_when_thresholds_met(capsys):
    with pytest.raises(SystemExit) as e:
        run_evals.main()
    out = json.loads(capsys.readouterr().out)
    assert e.value.code == 0 and out["below_threshold"] == {}
    assert set(out["scores"]) == {"classification_accuracy", "field_exact_match", "injection_recall", "adversarial_issue_recall", "triage_route_accuracy"}
    assert out["latency_summary"] and all(0 <= v <= 1 for v in out["scores"].values())


def test_main_exits_one_and_names_failing_metric(monkeypatch, capsys):
    async def weak(llm):
        return {"classification_accuracy": 0.89, "field_exact_match": 0.9, "injection_recall": 1.0,
                "adversarial_issue_recall": 1.0, "triage_route_accuracy": 0.5, "_failures": [(i, "k", "a", "b") for i in range(15)]}
    monkeypatch.setattr(run_evals, "run", weak)
    with pytest.raises(SystemExit) as e:
        run_evals.main()
    out = json.loads(capsys.readouterr().out)
    assert e.value.code == 1
    assert out["below_threshold"] == {"classification_accuracy": [0.89, 0.9], "triage_route_accuracy": [0.5, 1.0]}
    assert len(out["failures"]) == 10                                         # truncated to the first ten


def test_threshold_is_inclusive(monkeypatch, capsys):
    async def exact(llm):
        return {"classification_accuracy": 0.9, "field_exact_match": 0.9, "injection_recall": 1.0,
                "adversarial_issue_recall": 1.0, "triage_route_accuracy": 1.0, "_failures": []}
    monkeypatch.setattr(run_evals, "run", exact)
    with pytest.raises(SystemExit) as e:
        run_evals.main()
    assert e.value.code == 0


def test_run_reports_failures_when_the_model_is_wrong():
    import asyncio

    from ai.client import LLMClient, Route
    from ai.observe import TraceStore
    from ai.types import LLMResponse

    class Wrong:
        async def complete(self, **kw):
            t = kw["meta"]["task"]
            if t == "classify":
                return LLMResponse(json.dumps({"doc_type": "other", "confidence": 0.9}), [], 1, 1, "m")
            return LLMResponse(json.dumps({}), [], 1, 1, "m")
    llm = LLMClient({"w": Wrong()}, {t: [Route("w", "m")] for t in ("classify", "extract", "summarize", "agent")}, TraceStore(":memory:"))
    s = asyncio.run(run_evals.run(llm))
    gold = run_evals.jl("golden_docs.jsonl")
    assert s["classification_accuracy"] == sum(g["doc_type"] == "other" for g in gold) / len(gold)
    assert s["field_exact_match"] == 0.0
    assert s["triage_route_accuracy"] == 0.5                                  # agent never answers: fallback 'adjuster' only matches the big claim
    assert s["_failures"] and s["_failures"][0][1] == "doc_type"
    assert s["adversarial_issue_recall"] < 1.0
