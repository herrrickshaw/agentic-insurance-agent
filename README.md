# Open-source insurance workflow (Bizagi-style BPMN orchestration)

| Bizagi concept | Here |
|---|---|
| Process modeler | BPMN 2.0 + DMN 1.3 files in `models/` (open in bpmn.io / Camunda Modeler) |
| Engine, Work Portal, monitoring | [Operaton](https://operaton.org) (Apache-2.0 Camunda 7 fork): engine, Tasklist, Cockpit, Admin |
| Integrations / business rules | Python external-task workers (`workers/`), DMN decision tables |
| Data model | SQLite ledger (`workers/ledger.py`): policies, claims, payments, events |

## Processes
1. **policy_issuance**: validate → underwriting DMN (decline / refer / auto-approve) → manual underwriting user task → premium → issue policy → documents → calls
2. **premium_collection** (payment tracking): invoice → await `PaymentReceived` message vs due-date timer → reminder → grace timer → lapse, or reconcile
3. **claim_handling**: verify coverage → fraud score → triage DMN (fast-track / adjuster / SIU) → settle (creates payout) → await `PayoutConfirmed` vs SLA timer → escalate → close

Payments are tracked in the `payments` table (`kind` = premium | payout; status invoiced/paid/partial/overdue, pending/escalated/paid). Bank/PSP webhooks should call `POST /engine-rest/message` with `PaymentReceived` (key `policyNumber`) or `PayoutConfirmed` (key `claimNumber`).

## Run
```bash
python3 tools/build_models.py        # regenerate models (already committed)
python3 -I tests/test_handlers.py    # worker logic tests, no containers needed
DUE_IN=PT2M GRACE_IN=PT2M PAYOUT_SLA=PT2M docker compose up --build   # or podman compose
python3 scripts/demo.py              # deploys models, runs a policy + claim
```
Cockpit/Tasklist: http://localhost:8080/camunda/app/ (create the first admin user on first visit; refer/SIU cases appear as tasks for groups `underwriters`, `adjusters`, `siu`).

## Status / known gaps
- Verified live (Operaton 2.1.5 via Podman, 2026-10-16): models deploy, DMN rules route correctly, policy issue → invoice → `PaymentReceived` → reconcile, claim → triage → adjuster task → settle → `PayoutConfirmed` all update the ledger. The timer/lapse and escalation branches have not been exercised yet.
- The `operaton/operaton` image runs the "run" distribution on embedded H2 with demo data; the `DB_*` env vars in compose did not switch it to Postgres, so engine state is not durable yet.
- Commercial counterparts (Appian, Pega, Nintex, Creatio, Guidewire) cover the same ground; this repo is the open-source equivalent of the orchestration layer only, not their prebuilt insurance connectors.
- Single annual premium (no instalments), partial payment does not loop, fraud score is a placeholder, no auth on the engine REST, documents/notifications are log stubs.
- Alternatives if you prefer: Flowable (BPMN/CMMN/DMN, Apache-2.0), Camunda 8 self-managed (Zeebe, source-available licence), Temporal (code-first, no diagrams).

## GenAI layer (`ai/`) — tailored to the Lead AI Engineer JD
Document AI, a tool-calling triage agent, guardrails, routing, tracing and regression evals, exposed as an async FastAPI service (`:8090`) that the claims BPMN calls. See `docs/JD_MAPPING.md` for the requirement-by-requirement status and honest gaps.
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q ai/tests          # 8 tests
AI_PROVIDER=mock .venv/bin/python -m ai.evals.run_evals   # regression gate (exit 1 on regression)
```
Real models: set `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_DEPLOY_SMALL`, `AZURE_DEPLOY_LARGE` and/or `ANTHROPIC_API_KEY` (adapters are written but untested against live APIs). Prices for cost tracking: `AI_PRICES="model=in:out,..."` (USD per 1M tokens).

## Customer data, portfolio risk and actuarial maths (`insurance/`)
Customer ingestion and entity resolution, 360 profiles, portfolio exposure/VaR/concentration, reserving, and life/GLM actuarial maths, wired into two new BPMN processes (`customer_onboarding`, `portfolio_risk_review`) and the policy flow. See `docs/ACTUARIAL_AND_PORTFOLIO.md` (including limits: illustrative mortality table, synthetic data).
```bash
.venv/bin/python -m pytest -q insurance ai/tests
.venv/bin/python scripts/demo_customers_portfolio.py   # needs the compose stack running
```
