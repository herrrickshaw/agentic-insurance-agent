"""Worker handlers for onboarding, portfolio, rating, records and AI steps (the live BPMN entry points)."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "workers"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import handlers as h  # noqa: E402
import runner  # noqa: E402
from ledger import connect  # noqa: E402

from insurance.portfolio import synth  # noqa: E402


@pytest.fixture
def con():
    return connect(":memory:")


@pytest.mark.story("POL-2")
def test_rate_risk_and_actuarial_premium_handlers(con):
    base = dict(product="term_life", age=35, sumInsured=10_000_000, smoker=False, occupation="office", annualIncome=1_500_000)
    r = h.rate_risk(con, base)
    assert r["ratingClass"] == "preferred" and r["mortalityMultiple"] == 1.0
    s = h.rate_risk(con, {**base, "smoker": True})
    assert s["ratingClass"] == "substandard" and s["mortalityMultiple"] == 1.8
    assert h.rate_risk(con, {**base, "product": "motor"})["ratingClass"] == "standard"
    p1 = h.calculate_premium(con, {**base, "mortalityMultiple": 1.0})["premium"]
    p2 = h.calculate_premium(con, {**base, "mortalityMultiple": 1.8})["premium"]
    assert 1.5 < p2 / p1 < 1.9
    assert "premiumBasis" in h.calculate_premium(con, base)
    assert h.calculate_premium(con, {**base, "product": "motor", "sumInsured": 1000})["premium"] == 35.0


@pytest.mark.story("CUS-1")
@pytest.mark.story("CUS-2")
@pytest.mark.story("CUS-3")
@pytest.mark.story("PRT-1")
def test_onboarding_and_portfolio_handlers_end_to_end(con, tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_SEED", "1")
    synth.make_customers_csv(tmp_path / "c.csv", n=300, seed=3)
    ing = h.ingest_customers(con, {"sourceFile": str(tmp_path / "c.csv"), "sourceChannel": "agent_app"})
    assert ing["rowsIn"] > 300 and ing["accepted"] + ing["rejected"] == ing["rowsIn"] and 0 <= ing["rejectRate"] < 0.2
    res = h.resolve_entities(con, {"batchId": ing["batchId"]})
    assert res["autoMerged"] > 0 and res["goldenCustomers"] < ing["accepted"]
    if res["reviewPending"]:
        assert res["reviewSample"] and "~" in res["reviewSample"]
        assert h.apply_steward_decisions(con, {"batchId": ing["batchId"], "mergeAll": True})["reviewPending"] == 0
    prof = h.build_profiles_h(con, {"batchId": ing["batchId"]})
    assert prof["profilesBuilt"] > 0 and 0 < prof["avgCompleteness"] <= 1
    m = h.portfolio_metrics(con, {"ownFunds": 1.0e9, "portfolioLabel": "t"})
    assert m["var995"] >= m["tvar99"] * 0 and m["var995PctOwnFunds"] > 0 and "VaR99.5" in m["metricsSummary"]
    rs = h.run_reserving(con, {})
    assert rs["chainLadderReserve"] > 0 and rs["bfReserve"] > 0
    assert h.update_guidelines(con, {"committeeDecision": "cap MH"})["guidelineVersion"].startswith("UW-GL-")
    assert h.notify(con, {"rejectRate": 0.5}) == {} and "reject rate 0.5" in con.execute("SELECT event FROM events ORDER BY id DESC").fetchone()[0]


@pytest.mark.story("REC-9")
@pytest.mark.story("REC-5")
def test_records_handlers_run_the_review_flow(con, tmp_path, monkeypatch):
    from records import synth as rsynth
    monkeypatch.setenv("RECORDS_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("AI_PROVIDER", "mock")
    rsynth.build(tmp_path / "lib", n=60)
    a = h.records_analyse(con, {"sourceDir": str(tmp_path / "lib"), "batchLabel": "b1"})
    assert a["filesAnalysed"] > 60 and a["reviewCount"] > 0 and "need review" in a["analysisSummary"]
    v = {**a, "batchLabel": "b1", "reviewer": "m"}
    assert h.records_apply_decisions(con, {**v, "applyRecommended": False})["reviewCount"] == a["reviewCount"]
    d = h.records_apply_decisions(con, {**v, "applyRecommended": True})
    assert d["decisionsApplied"] == a["reviewCount"] and d["reviewCount"] == 0
    f = h.records_finalize(con, v)
    assert f["migrationReady"] is True and Path(f["manifestPath"]).exists() and (Path(a["batchDir"]) / "readiness.md").exists()


@pytest.mark.story("CLM-2")
def test_ai_handlers_use_the_service_contract(con, monkeypatch):
    calls = []

    def fake(path, body):
        calls.append(path)
        return {"/v1/documents/process": {"doc_type": "claim_form", "issues": [], "injection_flags": []},
                "/v1/documents/summarize": {"summary": "s"},
                "/v1/claims/triage": {"route": "fast_track", "confidence": 0.7, "rationale": "r", "trace_id": "t"}}[path]
    monkeypatch.setattr(h, "_ai", fake)
    d = h.ai_process_documents(con, {"claimNumber": "C", "claimDocuments": json.dumps([{"text": "x"}])})
    assert d["docsComplete"] is True and d["claimSummary"] == "s"
    assert h.ai_process_documents(con, {"claimNumber": "C"})["docsComplete"] is False
    assert h.ai_triage(con, {"claimNumber": "C", "policyNumber": "P", "claimAmount": 5})["aiRoute"] == "fast_track"
    assert calls == ["/v1/documents/process", "/v1/documents/summarize", "/v1/claims/triage"]


def test_runner_typing_and_engine_call():
    assert runner.typed(True)["type"] == "Boolean" and runner.typed(3)["type"] == "Long"
    assert runner.typed(2.5)["type"] == "Double" and runner.typed("a")["type"] == "String"

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({"echo": body, "path": self.path}).encode())

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        runner.ENGINE = f"http://127.0.0.1:{srv.server_port}"
        r = runner.call("/x", {"a": 1})
        assert r == {"echo": {"a": 1}, "path": "/x"}
    finally:
        srv.shutdown()
        runner.ENGINE = "http://localhost:8080/engine-rest"
