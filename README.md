# Open-source insurance workflow: BPMN/DMN orchestration with a GenAI layer

An open-source equivalent of the orchestration layer of a commercial BPM suite (Bizagi, Appian, Pega, Nintex, Creatio, Guidewire), applied to insurance. BPMN 2.0 / DMN 1.3 models run on [Operaton](https://operaton.org) (Apache-2.0 Camunda 7 fork); Python external-task workers hold the business logic and write a SQLite ledger; an async FastAPI service adds document AI, a tool-calling claims-triage agent, guardrails, routing, tracing and regression evals. It also contains customer-data, portfolio-risk and actuarial modules and a records-migration pipeline. It produces policies, claims and payments in the ledger, triage recommendations, portfolio metrics and records manifests/readiness reports.

> Status: active, demo-grade. Synthetic and illustrative data only (including the mortality table and the records file plan); not production insurance software and not actuarial advice.

## What it does
- Runs three core processes: `policy_issuance`, `premium_collection` (payment tracking), `claim_handling` (coverage, fraud score, triage DMN, settlement, payout SLA).
- Adds `customer_onboarding`, `portfolio_risk_review` and `records_migration` processes.
- Exposes a GenAI service on `:8090`: document classification/extraction/summary, claims triage agent, prompt-injection and PII guardrails, model routing with fallback, trace store, eval gate.
- Computes term-life premiums by the equivalence principle, plus reserving (chain ladder, Bornhuetter-Ferguson), GLM, credibility, proxy-free pricing, portfolio VaR/concentration.
- Classifies a document library against a file plan (ROT analysis, PII labels, target-safe paths, audited review, readiness report).
- Ships an engineering system: backlog-as-code with traceability, unit/property/BDD/contract tests, AI evals, mutation smoke test.

## Quick start
```bash
git clone https://github.com/<owner>/agentic-insurance-agent.git
cd agentic-insurance-agent
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                      # unit, property, BDD, contract; no containers needed
AI_PROVIDER=mock .venv/bin/python -m ai.evals.run_evals   # offline AI regression gate (exit 1 on regression)
```
Full stack (engine, workers, AI service, Postgres): `DUE_IN=PT2M GRACE_IN=PT2M PAYOUT_SLA=PT2M docker compose up --build` (or `podman compose`), then `python3 scripts/demo.py` to deploy the models and run a policy and a claim. Cockpit/Tasklist: http://localhost:8080/camunda/app/ (create the first admin user on first visit; refer/SIU cases appear as tasks for groups `underwriters`, `adjusters`, `siu`).

## How it works
```
BPMN/DMN (models/) --deployed to--> Operaton engine --external tasks--> workers/runner.py
                                                                          |  topic -> HANDLERS
        workers/handlers_policy.py   policy issue, premium, payments      v
        workers/handlers_claims.py   coverage, fraud score, settle   SQLite ledger (workers/ledger.py)
        workers/handlers_data.py     customers, portfolio, records
        workers/handlers.py          registry + AI handlers --HTTP--> ai/service.py (FastAPI :8090)
```
- `tools/build_models.py` generates the BPMN/DMN files; CI fails if the committed `models/` drift from it.
- Handlers are `(con, vars) -> dict`. `workers/http_json.py` is the single stdlib JSON POST used for the engine REST API and the AI service. The AI handlers take an optional injectable `ai` client and `records_analyse` an optional `llm`, so tests need no network.
- LLM recommends, humans and rules decide (ADR 0002). `ai/client.py` routes each task over providers (Azure OpenAI, OpenAI, Anthropic, deterministic mock) with fallback, one schema-repair retry and tracing; `default_routes(env)` / `build_providers(env)` and each provider's `transport=` argument are injectable.
- Payments live in the `payments` table (`kind` = premium | payout). Bank/PSP webhooks should call `POST /engine-rest/message` with `PaymentReceived` (key `policyNumber`) or `PayoutConfirmed` (key `claimNumber`).

## Repository layout
| Path | Purpose |
|---|---|
| `models/` | Generated BPMN 2.0 and DMN 1.3 files (open in bpmn.io / Camunda Modeler) |
| `workers/` | External-task runner, ledger, handlers split by domain, `http_json.py` |
| `ai/` | GenAI service: `client.py` routing, `providers.py`, `docai.py`, `agent.py`, `guardrails.py`, `retrieval.py`, `observe.py`, `governance.py`, `fairness.py`, `evals/` |
| `insurance/` | Customers (ingest, resolve, profile), portfolio risk, actuarial maths, underwriting rating |
| `records/` | Records classification, ROT, PII, review/audit, readiness report; run with `python -m records.run <library> <out> --apply-recommended` |
| `tests/`, `ai/tests/`, `insurance/tests/`, `records/tests/` | pytest suites (unit, property, BDD, contract, integration) |
| `features/` | Gherkin scenarios |
| `docs/` | Capability map, ADRs, backlog/traceability, threat model, test strategy, literature review, AI governance inventory |
| `scripts/`, `tools/` | Demos; model generator, traceability, AI inventory, mutation smoke test |
| `video/` | Demo-video generation scripts |

## Data sources
No external data is downloaded. Customer and portfolio data are synthetic (`insurance/portfolio/synth.py`, `records/synth.py`); the mortality table is a Gompertz-Makeham illustration; `ai/data/policy_clauses.md` and `records/fileplan.json` are illustrative examples.

## Configuration
| Variable | Default | Purpose |
|---|---|---|
| `ENGINE_URL` | `http://localhost:8080/engine-rest` | Operaton REST API used by the runner |
| `AI_SERVICE_URL` | `http://localhost:8090` | GenAI service used by the AI handlers |
| `LEDGER_DB` | `ledger.db` | SQLite ledger path |
| `DUE_IN`, `GRACE_IN`, `PAYOUT_SLA` | `P30D`, `P15D`, `P5D` | ISO-8601 timers (use `PT2M` for demos) |
| `VALUATION_RATE` | `0.06` | Term-life premium valuation rate |
| `AI_PROVIDER` | unset | `mock` forces the offline provider |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_DEPLOY_SMALL`, `AZURE_DEPLOY_LARGE`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | unset | Real model providers; set in your shell, never commit. The adapters are untested against live APIs |
| `AI_PRICES` | empty | `model=in:out,...` USD per 1M tokens for cost tracking |
| `RECORDS_FILE_PLAN`, `RECORDS_TARGET`, `RECORDS_PII_REGIONS`, `RECORDS_OUT` | see `records/` | Records pipeline configuration |

## Testing
```bash
.venv/bin/python -m pytest -q          # 103 passed, 1 skipped (live-stack test skips without the engine)
make ci                                # ruff, mypy, bandit, coverage gate (85%), evals, traceability
```
Backlog and acceptance criteria live in `docs/product/BACKLOG.yaml` and are checked against tests (`make trace`). See `docs/process/ENGINEERING_PROCESS.md` and `docs/TEST_STRATEGY.md`.

## Limitations and known issues
- Verified live (Operaton 2.1.5 via Podman, 2026-10-16): models deploy, DMN rules route correctly, policy issue, invoice, `PaymentReceived`, reconcile, and claim, triage, adjuster task, settle, `PayoutConfirmed` update the ledger. The timer/lapse and escalation branches have not been exercised.
- The `operaton/operaton` image runs on embedded H2; the `DB_*` variables in `docker-compose.yml` did not switch it to Postgres, so engine state is not durable.
- Single annual premium (no instalments), partial payment does not loop, the fraud score is a deterministic placeholder, there is no auth on the engine REST, documents/notifications are log stubs.
- The mock provider is a keyword heuristic: evals against it prove plumbing and regression gating, not model quality.
- `docker-compose.yml` contains throwaway local-development database passwords; change them for anything beyond a laptop.
- Alternatives to Operaton: Flowable (BPMN/CMMN/DMN, Apache-2.0), Camunda 8 self-managed (Zeebe, source-available), Temporal (code-first, no diagrams).

## Licence
No licence file yet; all rights reserved.
