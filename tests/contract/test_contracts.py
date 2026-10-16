"""Contract tests: the AI service API shape (snapshot) and what its consumers (workers) rely on."""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.contract
SNAP = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"


@pytest.fixture(scope="module")
def client():
    os.environ["AI_PROVIDER"] = "mock"
    from ai import service
    return TestClient(service.app)


def shape(spec):
    """Paths, methods and request-body schema names: stable under cosmetic changes, fails on breaking ones."""
    return {p: {m: (op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {}).get("$ref") or "")
                for m, op in ms.items()} for p, ms in spec["paths"].items()}


@pytest.mark.story("AIP-3")
def test_openapi_shape_matches_committed_snapshot(client):
    current = shape(client.get("/openapi.json").json())
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAP.write_text(json.dumps(current, indent=1, sort_keys=True))
    assert current == json.loads(SNAP.read_text()), "API shape changed: review for breaking changes, then UPDATE_SNAPSHOTS=1 pytest"


@pytest.mark.story("CLM-2")
def test_workers_can_rely_on_document_endpoints(client):
    """Keys read by workers/handlers.py ai_process_documents: doc_type, issues, injection_flags, summary."""
    doc = "CLAIM FORM\nPolicy No: POL-2026-000004\nClaimant: A\nDate of loss: 2026-10-01\nClaim amount: INR 8,000"
    r = client.post("/v1/documents/process", json={"text": doc}).json()
    assert {"doc_type", "confidence", "fields", "issues", "injection_flags", "needs_review"} <= set(r)
    s = client.post("/v1/documents/summarize", json={"documents": [{"text": doc}]}).json()
    assert {"summary", "key_points", "open_questions"} <= set(s)


@pytest.mark.story("CLM-4")
def test_workers_can_rely_on_triage_endpoint(client, tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "l.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE policies(policy_number,status,sum_insured,product);CREATE TABLE claims(claim_number,policy_number,amount);")
    c.commit(); c.close()
    monkeypatch.setenv("LEDGER_DB", str(db))
    r = client.post("/v1/claims/triage", json={"claim_number": "CLM-2026-000001", "policy_number": "POL-2026-000001", "claim_amount": 500}).json()
    assert {"route", "confidence", "rationale", "trace_id"} <= set(r) and r["route"] in {"fast_track", "adjuster", "siu"}


@pytest.mark.story("REC-9")
def test_manifest_columns_are_a_stable_contract(tmp_path):
    from records import pipeline
    rows = [{"source_path": "a", "size": 1, "sha256": "x", "status": "migrate", "reasons": "", "record_class": "invoice", "confidence": 0.9,
             "document_date": "", "entity": "", "retention_label": "l", "disposition_date": "", "sensitivity": "Internal", "pii": "",
             "target_path": "a", "model": "m"}]
    pipeline.write_manifest(rows, tmp_path)
    header = (tmp_path / "manifest.csv").read_text().splitlines()[0].split(",")
    assert header == ["source_path", "size", "sha256", "status", "reasons", "record_class", "confidence", "document_date", "entity",
                      "retention_label", "disposition_date", "sensitivity", "pii", "target_path", "model"]
