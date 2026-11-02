"""Customer-onboarding, portfolio-risk and records-migration handlers: (con, vars) -> dict of output variables."""
import json
import os
import time
from pathlib import Path

from ledger import log


def _ins(con):
    from insurance.db import ensure_schema
    ensure_schema(con)


def ingest_customers(con, v):
    from insurance.customers.ingest import ingest_file
    _ins(con)
    r = ingest_file(con, v["sourceFile"], v.get("sourceChannel", "unknown"))
    log(con, r["batch_id"], f"ingested {r['rows_in']} rows: {r['accepted']} ok, {r['rejected']} rejected")
    return {"batchId": r["batch_id"], "rowsIn": r["rows_in"], "accepted": r["accepted"], "rejected": r["rejected"], "rejectRate": r["reject_rate"]}


def resolve_entities(con, v):
    from insurance.customers.resolve import resolve_batch
    r = resolve_batch(con, v["batchId"])
    pend = con.execute("SELECT q.id,a.full_name,b.full_name,q.score FROM match_queue q JOIN customers_raw a ON a.raw_id=q.raw_a "
                       "JOIN customers_raw b ON b.raw_id=q.raw_b WHERE q.batch_id=? AND q.status='pending' ORDER BY q.score DESC", (v["batchId"],)).fetchall()
    sample = "; ".join(f"{p[1]} ~ {p[2]} ({p[3]:.2f})" for p in pend[:5])
    log(con, v["batchId"], f"entity resolution: {r['auto_merged']} auto-merged, {len(pend)} pending steward review")
    return {"autoMerged": r["auto_merged"], "reviewPending": len(pend), "reviewSample": sample[:900], "goldenCustomers": r["golden_customers"]}


def apply_steward_decisions(con, v):
    from insurance.customers.resolve import apply_steward_decision
    ids = [r[0] for r in con.execute("SELECT id FROM match_queue WHERE batch_id=? AND status='pending'", (v["batchId"],))]
    for i in ids:
        apply_steward_decision(con, i, bool(v.get("mergeAll")))
    log(con, v["batchId"], f"steward decided {len(ids)} pairs: {'merged' if v.get('mergeAll') else 'kept separate'}")
    return {"reviewPending": 0}


def build_profiles_h(con, v):
    from insurance.customers.profile import build_profiles
    if os.environ.get("DEMO_SEED") == "1" and con.execute("SELECT COUNT(*) FROM policy_book").fetchone()[0] == 0:
        from insurance.portfolio.synth import make_policy_book
        make_policy_book(con)                          # DEMO ONLY: stands in for the policy admin system feed
    r = build_profiles(con)
    log(con, v["batchId"], f"profiles built: {r['profiles']} (completeness {r['avg_completeness']})")
    return {"profilesBuilt": r["profiles"], "avgCompleteness": r["avg_completeness"]}


def portfolio_metrics(con, v):
    from insurance.portfolio.risk import snapshot
    m = snapshot(con, own_funds=float(v["ownFunds"]), label=v.get("portfolioLabel", "run"))
    log(con, "portfolio", f"VaR99.5 {m['var995']:.0f} = {m['var995_pct_own_funds']}% of own funds; LR {m['expected_loss_ratio']}")
    return {"var995": m["var995"], "tvar99": m["tvar99"], "var995PctOwnFunds": m["var995_pct_own_funds"],
            "maxStateSharePct": m["max_state_share_pct"], "maxState": m["max_state"], "expectedLossRatio": m["expected_loss_ratio"],
            "top10Pct": m["top10_customer_share_pct"], "totalSumInsured": m["total_sum_insured"], "portfolioPremium": m["premium"],
            "metricsSummary": f"VaR99.5={m['var995']:.3e} ({m['var995_pct_own_funds']}% of own funds); largest state {m['max_state']} {m['max_state_share_pct']}%; expected loss ratio {m['expected_loss_ratio']}"}


def run_reserving(con, v):
    from insurance.portfolio import risk as pr
    rows = [r for r in pr.policy_frame(con) if r["product"] != "term_life"]
    nl = float(pr.expected_loss(rows).sum()) if rows else 0.0
    r = pr.reserving_run(con, nl)
    return {"chainLadderReserve": r["chain_ladder_reserve"], "bfReserve": r["bf_reserve"]}


def update_guidelines(con, v):
    log(con, "portfolio", f"committee decision recorded: {v.get('committeeDecision', 'n/a')}")
    return {"guidelineVersion": f"UW-GL-{int(time.time())}"}


def notify(con, v):
    msg = v.get("notifyMessage") or (f"batch rejected: reject rate {v['rejectRate']}" if "rejectRate" in v else "attention required")
    log(con, v.get("batchId", "ops"), f"notified owner: {msg}")
    return {}


def _records_load(batch_dir):
    return json.loads((Path(batch_dir) / "rows.json").read_text())


def _records_save(batch_dir, rows):
    (Path(batch_dir) / "rows.json").write_text(json.dumps(rows))


def records_analyse(con, v, llm=None):
    """Classify a records library; ``llm`` is an injectable LLMClient (default: environment-configured)."""
    from ai.client import LLMClient
    from records import pipeline
    from records.audit import AuditLog
    out = Path(os.environ.get("RECORDS_OUT", "/data/out")) / v["batchLabel"]
    out.mkdir(parents=True, exist_ok=True)
    rows = pipeline.analyse(v["sourceDir"], llm or LLMClient())
    _records_save(out, rows)
    AuditLog(out / "audit.jsonl").append("system", "analyse", v["sourceDir"], {"files": len(rows)})
    cnt = {s: sum(r["status"] == s for r in rows) for s in ("migrate", "review", "exclude")}
    log(con, v["batchLabel"], f"records analysed {len(rows)} files: {cnt}")
    return {"batchDir": str(out), "filesAnalysed": len(rows), "reviewCount": cnt["review"], "migrateCount": cnt["migrate"],
            "excludeCount": cnt["exclude"], "analysisSummary": f"{len(rows)} files: {cnt['migrate']} ready, {cnt['review']} need review, {cnt['exclude']} excluded (ROT)"}


def records_apply_decisions(con, v):
    from records import review
    from records.audit import AuditLog
    rows, audit = _records_load(v["batchDir"]), AuditLog(Path(v["batchDir"]) / "audit.jsonl")
    n = 0
    if v.get("applyRecommended"):
        n = review.apply_decisions(rows, review.recommended_decisions(rows), v.get("reviewer") or "records-manager", audit)
    _records_save(v["batchDir"], rows)
    left = sum(r["status"] == "review" for r in rows)
    log(con, v["batchLabel"], f"records review: {n} decisions applied, {left} still open")
    return {"decisionsApplied": n, "reviewCount": left}


def records_finalize(con, v):
    from records import pipeline, report
    from records.audit import AuditLog
    rows, audit = _records_load(v["batchDir"]), AuditLog(Path(v["batchDir"]) / "audit.jsonl")
    pipeline.write_manifest(rows, v["batchDir"])
    text, ready = report.readiness(rows, audit)
    (Path(v["batchDir"]) / "readiness.md").write_text(text)
    audit.append("system", "finalize", v["batchLabel"], {"ready": ready})
    log(con, v["batchLabel"], f"records manifest finalised: {'READY' if ready else 'NOT READY'}")
    return {"migrationReady": ready, "manifestPath": str(Path(v["batchDir"]) / "manifest.csv")}
