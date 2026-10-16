"""LLM call tracing + root-cause queries. SQLite by default; Postgres when AI_DB_DSN is set."""
import os
import sqlite3
import statistics
import time
from contextlib import contextmanager

DDL = """CREATE TABLE IF NOT EXISTS llm_calls(
  id {pk}, ts DOUBLE PRECISION, trace_id TEXT, task TEXT, provider TEXT, model TEXT, status TEXT,
  attempt INTEGER, latency_ms DOUBLE PRECISION, input_tokens INTEGER, output_tokens INTEGER,
  cost_usd DOUBLE PRECISION, error TEXT)"""
COLS = "ts,trace_id,task,provider,model,status,attempt,latency_ms,input_tokens,output_tokens,cost_usd,error"
# USD per 1M tokens (input, output). Deliberately empty: set via AI_PRICES="model=in:out,..." from your contract.
PRICES = {}
for part in filter(None, os.environ.get("AI_PRICES", "").split(",")):
    k, v = part.split("=")
    PRICES[k] = tuple(float(x) for x in v.split(":"))


def cost(model, tin, tout):
    pi, po = PRICES.get(model, (0.0, 0.0))
    return (tin * pi + tout * po) / 1e6


class TraceStore:
    def __init__(self, dsn=None):
        self.dsn = dsn if dsn is not None else os.environ.get("AI_DB_DSN", "")
        self.pg = self.dsn.startswith("postgres")
        if self.pg:
            import psycopg
            self.con = psycopg.connect(self.dsn, autocommit=True)
            self.con.execute(DDL.format(pk="BIGSERIAL PRIMARY KEY"))
        else:
            self.con = sqlite3.connect(self.dsn or os.environ.get("AI_TRACE_DB", "ai_traces.db"),
                                       check_same_thread=False)
            self.con.execute(DDL.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT"))

    def _q(self, sql):
        return sql.replace("?", "%s") if self.pg else sql

    def record(self, **r):
        vals = [r.get(c) for c in COLS.split(",")]
        sql = f"INSERT INTO llm_calls({COLS}) VALUES({','.join('?' * 12)})"  # nosec B608 - COLS is a module constant
        self.con.execute(self._q(sql), vals)
        if not self.pg:
            self.con.commit()

    def rows(self, since=0.0):
        sql = f"SELECT {COLS} FROM llm_calls WHERE ts>=? ORDER BY ts"  # nosec B608 - COLS is a module constant
        cur = self.con.execute(self._q(sql), (since,))
        return [dict(zip(COLS.split(","), x)) for x in cur.fetchall()]

    def summary(self, since=0.0):
        """Per task+model: volume, error rate, latency p50/p95, token and cost totals."""
        groups = {}
        for r in self.rows(since):
            groups.setdefault((r["task"], r["model"]), []).append(r)
        out = []
        for (task, model), rs in sorted(groups.items()):
            lat = sorted(r["latency_ms"] for r in rs)
            p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
            out.append({"task": task, "model": model, "calls": len(rs),
                        "error_rate": round(sum(r["status"] != "ok" for r in rs) / len(rs), 3),
                        "p50_ms": round(statistics.median(lat), 1), "p95_ms": round(p95, 1),
                        "input_tokens": sum(r["input_tokens"] or 0 for r in rs),
                        "output_tokens": sum(r["output_tokens"] or 0 for r in rs),
                        "cost_usd": round(sum(r["cost_usd"] or 0 for r in rs), 4)})
        return out

    def root_causes(self, since=0.0, top=5):
        """Rank (task, model, status, error) buckets for non-ok calls: starting point for RCA."""
        buckets = {}
        for r in self.rows(since):
            if r["status"] != "ok":
                k = (r["task"], r["model"], r["status"], (r["error"] or "")[:80])
                buckets[k] = buckets.get(k, 0) + 1
        return sorted(({"task": k[0], "model": k[1], "status": k[2], "error": k[3], "count": n}
                       for k, n in buckets.items()), key=lambda x: -x["count"])[:top]


@contextmanager
def timer():
    t = {"t0": time.perf_counter()}
    yield t
    t["ms"] = (time.perf_counter() - t["t0"]) * 1000
