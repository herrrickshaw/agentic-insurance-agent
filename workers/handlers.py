"""Worker topic registry plus the GenAI-service handlers.

Business logic lives in the ``handlers_*`` modules; this module re-exports it so ``import handlers`` keeps working.
Variables arrive as plain Python values; integers are returned as ints and typed by the runner.
"""
import json
import os

from handlers_claims import (  # noqa: F401  (re-exports)
    PAYOUT_SLA, close_claim, escalate_payout, fraud_score, reject_claim, settle_claim, verify_coverage)
from handlers_data import (  # noqa: F401  (re-exports)
    apply_steward_decisions, build_profiles_h, ingest_customers, notify, portfolio_metrics, records_analyse,
    records_apply_decisions, records_finalize, resolve_entities, run_reserving, update_guidelines)
from handlers_policy import (  # noqa: F401  (re-exports)
    BASE_RATE, DUE_IN, GRACE_IN, calculate_premium, calculate_premium_legacy, create_invoice, issue_policy,
    lapse_policy, notify_applicant, rate_risk, reconcile_payment, send_documents, send_reminder, validate_application)
from http_json import post_json
from ledger import log

AI_URL = os.environ.get("AI_SERVICE_URL", "http://localhost:8090")


def _ai(path, body):
    """POST ``body`` to the GenAI service and return the decoded JSON reply (the default AI-service client)."""
    return post_json(AI_URL, path, body, timeout=90, env_name="AI_SERVICE_URL", allow_empty=False)


def ai_process_documents(con, v, ai=None):
    """Classify/extract each submitted document, flag gaps, summarise for the adjuster (GenAI service).

    ``ai`` is an injectable ``(path, body) -> dict`` client; it defaults to the HTTP client ``_ai``.
    """
    ai = ai or _ai
    docs = json.loads(v.get("claimDocuments") or "[]")
    if not docs:
        return {"docsComplete": False, "docIssues": "no documents submitted", "claimSummary": "No documents."}
    results = [ai("/v1/documents/process", {"text": d["text"]}) for d in docs]
    types = {r["doc_type"] for r in results}
    issues = [i for r in results for i in r["issues"]] + [f"injection flags: {r['injection_flags']}" for r in results if r["injection_flags"]]
    summary = ai("/v1/documents/summarize", {"documents": [{"text": d["text"]} for d in docs]})
    log(con, v["claimNumber"], f"AI processed {len(docs)} docs types={sorted(types)} issues={len(issues)}")
    return {"docsComplete": "claim_form" in types and not issues, "docIssues": "; ".join(issues)[:900],
            "docTypes": ",".join(sorted(types)), "claimSummary": summary["summary"][:900]}


def ai_triage(con, v, ai=None):
    """Ask the GenAI triage agent for a claim route (``ai``: injectable client, default ``_ai``)."""
    r = (ai or _ai)("/v1/claims/triage", {"claim_number": v["claimNumber"], "policy_number": v["policyNumber"],
                                   "claim_amount": int(v["claimAmount"]), "description": v.get("description", "")})
    log(con, v["claimNumber"], f"AI triage recommends {r['route']} (conf {r['confidence']}, trace {r['trace_id']})")
    return {"aiRoute": r["route"], "aiRationale": r["rationale"][:500], "aiTraceId": r["trace_id"]}

HANDLERS = {
    "records-analyse": records_analyse, "records-apply-decisions": records_apply_decisions, "records-finalize": records_finalize,
    "rate-risk": rate_risk, "ingest-customers": ingest_customers, "resolve-entities": resolve_entities,
    "apply-steward-decisions": apply_steward_decisions, "build-profiles": build_profiles_h,
    "portfolio-metrics": portfolio_metrics, "run-reserving": run_reserving,
    "update-guidelines": update_guidelines, "notify-owner": notify,
    "ai-process-documents": ai_process_documents, "ai-triage": ai_triage,
    "validate-application": validate_application, "calculate-premium": calculate_premium,
    "issue-policy": issue_policy, "send-documents": send_documents, "notify-applicant": notify_applicant,
    "create-invoice": create_invoice, "send-reminder": send_reminder, "reconcile-payment": reconcile_payment,
    "lapse-policy": lapse_policy, "verify-coverage": verify_coverage, "fraud-score": fraud_score,
    "settle-claim": settle_claim, "escalate-payout": escalate_payout, "close-claim": close_claim,
    "reject-claim": reject_claim,
}
