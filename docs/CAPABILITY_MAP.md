# Capability map: what this repo covers and how well it is verified

Status key: **Verified** = run in this repo with evidence; **Written** = implemented, not exercised against the real service; **Gap** = not done.

| Capability | Where | Status |
|---|---|---|
| GenAI for underwriting / claims / servicing | `ai/` service called from BPMN external tasks (`ai-process-documents`, `ai-triage`) in the claims process | Verified end to end on the mock provider (Operaton + Podman run) |
| Document AI: classify, extract, summarise | `ai/docai.py` (grounded extraction: every value needs a verbatim evidence quote) | Verified (mock); real-model quality unmeasured |
| Agents, tool/function calling, multi-step | `ai/agent.py` provider-agnostic tool loop, max steps, human-lane fallback, action authorisation | Verified (mock); OpenAI/Anthropic tool formats **Written** |
| Prompting, structured outputs, guardrails | Pydantic schemas + repair retry (`ai/client.py`); PII redaction, injection screen, grounding and business-rule checks (`ai/guardrails.py`) | Verified (unit tests) |
| Evaluation and regression testing | `ai/evals/run_evals.py` + golden/adversarial sets + thresholds; CI gate | Verified, but see caveat below |
| Observability, latency/token optimisation, RCA | `ai/observe.py`: per-call traces (SQLite or Postgres), p50/p95, tokens, cost, `root_causes()`; `GET /v1/ai/metrics` | Verified incl. live Postgres |
| Model selection / routing (quality, latency, reliability, cost) | `ai/client.py` `Route`, `order()` (weighted by observed latency/error/cost), fallback chain across providers | Logic verified with fakes; real routing needs real traces |
| Azure AI / Azure OpenAI / Foundry | Azure OpenAI adapter (`OpenAICompatProvider(azure_api_version=…)`), env-driven deployments | **Written, never called** (no Azure credentials used). Foundry-specific features: Gap |
| Agent frameworks (Microsoft Agent Framework, LangGraph, Semantic Kernel) | Own minimal loop. Process-level orchestration is BPMN | **Gap**: no framework used; see "Adopt" below |
| Foundation models (OpenAI, Claude) | OpenAI-compatible and Anthropic adapters | **Written**, not called live |
| REST, async, PostgreSQL (+vector) | FastAPI async service; Postgres trace store; pgvector clause retrieval (`ai/retrieval.py`) | Verified live against `pgvector/pgvector:pg16` |
| Git, Docker, CI/CD, Azure DevOps / GitHub Actions | `.github/workflows/ci.yml`, `azure-pipelines.yml`, `ai/Dockerfile`, compose | Compose/Docker Verified; pipelines **not run** |
| Prototype → production | Compose stack, health check, restart policy, metrics endpoint | Partial: no auth, no secrets manager, no autoscaling |
| Insurance domain / enterprise workflow | Policy, premium collection, claims BPMN + DMN + ledger | Verified |
| Leadership / standards | This doc, eval gates, guardrail rules as code | Narrative only |

**Caveat on the evals:** the mock provider is a keyword/regex stand-in I tuned against my own golden set, so its 100% scores prove the harness, gating and plumbing, not model quality. The numbers that matter come from running `run_evals` with real deployments and a larger, realistic (anonymised) document set.

## Open-source repos to adopt next (GitHub)
These cover capabilities implemented minimally by hand here; swapping them in is the natural next step.
- `langchain-ai/langgraph`: replace the `agent.py` loop with a graph (state, checkpoints, human-in-the-loop interrupts).
- `microsoft/semantic-kernel` and `microsoft/agent-framework`: same agent on Microsoft's stack; natural fit with Azure OpenAI.
- `docling-project/docling`: layout-aware PDF/scan → text before `docai` (today input is plain text; OCR is a Gap).
- `langfuse/langfuse` or OpenTelemetry GenAI conventions: replace the homegrown trace table with a UI.
- `promptfoo/promptfoo`: richer eval matrices and red-teaming on top of `evals/`.
- `pgvector/pgvector`: already used.
(Repo names from memory; check current status and licences before adopting.)

## Design rationale
1. BPMN owns the process and the LLM only recommends (auditability; DMN stays authoritative; agent output cannot move money).
2. Grounded extraction: evidence-quote validation as a cheap hallucination check.
3. Failure design: provider fallback, schema-repair retry, forced human lane when the agent is unavailable.
4. Quality measurement: golden and adversarial sets, per-field exact match, thresholds in CI, nightly run against real models.
5. Cost and latency: traces by task and model, route ordering from observed p95, error rate and cost.
