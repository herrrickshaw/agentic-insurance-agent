# Sprint 4 plan (example of the cadence; capacity is an assumption)

**Goal:** make the platform safe to put in front of real users: authenticated, durable, and measured against real models.
**Capacity (assumption):** one engineer, 10 points.

| Order | Story | Pts | WSJF | Notes |
|---|---|---|---|---|
| 1 | SEC-1 Authenticate engine REST and AI service | 3 | 8.7 | Blocks any shared deployment; OIDC or API-key behind a gateway, secrets from a store |
| 2 | DATA-1 Durable engine state on Postgres | 3 | 7.7 | Operaton image ignored the `DB_*` variables; needs the right config path |
| 3 | AIP-4 Real-model nightly evals | 3 | 8.0 | **Blocked** on Azure OpenAI / Anthropic credentials; task is to obtain them, then enable `vars.REAL_MODEL_EVALS` |
| stretch | REC-10 PDF/Office parsing (OCR) | 8 | 2.1 | Does not fit; split into spike (parser choice) and build |

**Technical debt pulled in:** kill the remaining mutation survivors in `records/review.py` and `resolve.py`.
**Not in the sprint:** CUS-4 steward UI (Could).
**Exit demo:** unauthenticated call returns 401; restart the engine mid-process and the instance resumes; nightly eval report per model.
