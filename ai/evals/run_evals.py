"""Regression eval: classification, field extraction, adversarial/guardrails, agent triage.

Usage: python -m ai.evals.run_evals   (AI_PROVIDER/credentials select the models under test)
Exits 1 if any metric is below evals/thresholds.json, so CI blocks prompt/model/routing regressions.
"""
import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from .. import docai
from ..agent import claims_tools, triage
from ..client import LLMClient
from ..observe import TraceStore
from ..retrieval import MemoryStore, load_clauses

HERE = Path(__file__).parent


def jl(name):
    return [json.loads(x) for x in (HERE / name).read_text().splitlines() if x.strip()]


def norm(v):
    return str(v).replace(",", "").strip().lower()


async def run(llm: LLMClient) -> dict:
    gold, adv = jl("golden_docs.jsonl"), jl("adversarial.jsonl")
    cls_ok = f_ok = f_tot = 0
    failures = []
    for g in gold:
        r = await docai.process_document(llm, g["text"])
        cls_ok += r["doc_type"] == g["doc_type"]
        if r["doc_type"] != g["doc_type"]:
            failures.append((g["id"], "doc_type", r["doc_type"], g["doc_type"]))
        for k, v in g["fields"].items():
            f_tot += 1
            ok = norm(r["fields"].get(k, "")) == norm(v)
            f_ok += ok
            if not ok:
                failures.append((g["id"], k, r["fields"].get(k), v))
    inj = [a for a in adv if a.get("expect_injection")]
    iss = [a for a in adv if a.get("expect_issue")]
    inj_hit = iss_hit = 0
    for a in inj:
        inj_hit += bool((await docai.process_document(llm, a["text"]))["injection_flags"])
    for a in iss:
        r = await docai.process_document(llm, a["text"])
        iss_hit += any(a["expect_issue"] in i for i in r["issues"])
    # agent: seeded ledger with known expected routes
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("CREATE TABLE policies(policy_number,status,sum_insured,product);"
                      "CREATE TABLE claims(claim_number,policy_number,amount);"
                      "INSERT INTO policies VALUES('POL-2026-000001','active',5000000,'health');"
                      "INSERT INTO claims VALUES('CLM-2026-000001','POL-2026-000001',8000);")
    clauses = MemoryStore()
    for cid, t in load_clauses(HERE.parent / "data" / "policy_clauses.md"):
        clauses.add(cid, t)
    cases = [({"claim_number": "CLM-2026-000001", "policy_number": "POL-2026-000001", "claim_amount": 8000}, "fast_track"),
             ({"claim_number": "CLM-2026-000001", "policy_number": "POL-2026-000001", "claim_amount": 250000}, "adjuster")]
    tri_ok = 0
    for claim, want in cases:
        tri_ok += (await triage(llm, claims_tools(con, clauses), claim))["route"] == want
    return {"classification_accuracy": cls_ok / len(gold), "field_exact_match": f_ok / max(f_tot, 1),
            "injection_recall": inj_hit / max(len(inj), 1), "adversarial_issue_recall": iss_hit / max(len(iss), 1),
            "triage_route_accuracy": tri_ok / len(cases), "_failures": failures}


def main():
    llm = LLMClient(store=TraceStore(str(Path(tempfile.mkdtemp()) / "traces.db")))
    scores = asyncio.run(run(llm))
    failures = scores.pop("_failures")
    thresholds = json.loads((HERE / "thresholds.json").read_text())
    bad = {k: (round(scores[k], 3), t) for k, t in thresholds.items() if scores[k] < t}
    print(json.dumps({"scores": {k: round(v, 3) for k, v in scores.items()}, "below_threshold": bad,
                      "failures": failures[:10], "latency_summary": llm.store.summary()}, indent=1))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
