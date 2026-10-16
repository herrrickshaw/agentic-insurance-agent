"""Async FastAPI service exposing the GenAI capabilities to BPMN workers and other systems."""
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from . import docai
from .agent import claims_tools, triage
from .client import LLMClient
from .retrieval import MemoryStore, PgVectorStore, load_clauses

app = FastAPI(title="Insurance GenAI service", version="0.1.0")
llm = LLMClient()
clauses = PgVectorStore(os.environ["VECTOR_DSN"]) if os.environ.get("VECTOR_DSN") else MemoryStore()
for cid, text in load_clauses(Path(__file__).parent / "data" / "policy_clauses.md"):
    clauses.add(cid, text)


def ledger():
    con = sqlite3.connect(os.environ.get("LEDGER_DB", "ledger.db"), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


class DocIn(BaseModel):
    text: str


class DocsIn(BaseModel):
    documents: list[DocIn]
    audience: str = "claims adjuster"


class TriageIn(BaseModel):
    claim_number: str
    policy_number: str
    claim_amount: int
    description: str = ""


@app.get("/health")
def health():
    return {"status": "ok", "providers": list(llm.providers), "routes": {k: [r.model for r in v] for k, v in llm.routes.items()}}


@app.post("/v1/documents/process")
async def process(doc: DocIn):
    return await docai.process_document(llm, doc.text)


@app.post("/v1/documents/summarize")
async def summarize(body: DocsIn):
    s, meta = await docai.summarize(llm, [d.text for d in body.documents], body.audience)
    return s.model_dump() | {"model": meta["model"]}


@app.post("/v1/claims/triage")
async def claim_triage(body: TriageIn):
    return await triage(llm, claims_tools(ledger(), clauses), body.model_dump())


@app.get("/v1/ai/metrics")
def metrics():
    return {"summary": llm.store.summary(), "root_causes": llm.store.root_causes()}
