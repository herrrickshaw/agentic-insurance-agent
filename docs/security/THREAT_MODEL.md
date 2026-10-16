# Threat model (STRIDE, per component)

Scope: customer onboarding (PII), claims AI, records migration, BPMN engine. Status: **implemented** = covered by code and tests; **gap** = not done.

| Component | Threat | Control | Status |
|---|---|---|---|
| Customer ingestion | Information disclosure of Aadhaar/PAN | Salted hashes + last four only; redaction before model calls (CLM-3, CUS-1) | implemented. Salt comes from `PII_SALT`; the default is for development only |
| Customer ingestion | Tampering via malformed rows | Pydantic validation, checksums, reject table with reasons | implemented |
| Documents to LLM | Prompt injection (instructions in a document) | Document treated as data; injection patterns flagged and routed to review; agent cannot act on money | implemented (pattern list is heuristic) |
| LLM output | Hallucinated values | Evidence-quote grounding; schema validation + repair; low confidence goes to review | implemented |
| Agent | Excessive agency | Recommend-only; amount limits; human fallback (CLM-4) | implemented |
| Records decisions | Repudiation / tampering | Hash-chained audit log, actor and reason recorded (REC-6) | implemented; head hash not anchored externally |
| Records disposal | Irreversible deletion | Disposal is a decision state, never an action; manifest only | implemented |
| Engine REST, AI service | Spoofing, unauthorised access | None | **gap (SEC-1)**: both are unauthenticated |
| Logs and traces | PII in prompts or traces | Trace store keeps metadata only, no prompt text | implemented; verify for new log lines |
| Secrets | Keys in repo | Environment variables, gitleaks pre-commit | implemented; no secrets manager (gap) |
| Dependencies | Vulnerable packages | bandit now; no dependency audit | **gap**: add pip-audit |
| Availability | Model outage / cost spike | Fallback routes, budget guard (REC-8), cache (REC-7) | implemented |
| Data at rest | Disclosure of the SQLite files | None (plain files) | **gap**: encryption at rest |
