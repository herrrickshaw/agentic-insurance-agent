"""ai/observe.py (trace store, pricing, timer) and ai/retrieval.py (embedding, clause loading, memory + pgvector stores)."""
import importlib
import math
import sys
import types

import pytest

from ai import observe, retrieval
from ai.observe import TraceStore, cost, timer


def rec(st, **kw):
    base = dict(ts=1.0, trace_id="t", task="classify", provider="p", model="m", status="ok", attempt=1, latency_ms=10.0,
                input_tokens=1, output_tokens=2, cost_usd=0.5, error=None)
    st.record(**{**base, **kw})


# ---- pricing ------------------------------------------------------------------------------------

def test_cost_known_and_unknown_model(monkeypatch):
    monkeypatch.setitem(observe.PRICES, "m1", (3.0, 15.0))
    assert cost("m1", 1_000_000, 0) == 3.0
    assert cost("m1", 0, 1_000_000) == 15.0
    assert cost("m1", 500_000, 100_000) == pytest.approx(3.0)
    assert cost("unknown", 10**9, 10**9) == 0.0


def test_prices_parsed_from_env_at_import(monkeypatch):
    monkeypatch.setenv("AI_PRICES", "a=1:2,b=0.5:3.5,,")
    saved = sys.modules["ai.observe"]
    try:
        fresh = importlib.reload(observe)
        assert fresh.PRICES == {"a": (1.0, 2.0), "b": (0.5, 3.5)}
    finally:
        monkeypatch.delenv("AI_PRICES")
        importlib.reload(saved)
    assert observe.PRICES == {}


# ---- TraceStore (sqlite) ---------------------------------------------------------------------

def test_store_roundtrip_and_since_filter():
    st = TraceStore(":memory:")
    rec(st, ts=1.0, trace_id="a")
    rec(st, ts=5.0, trace_id="b")
    rows = st.rows()
    assert [r["trace_id"] for r in rows] == ["a", "b"]
    assert set(rows[0]) == set(observe.COLS.split(","))
    assert [r["trace_id"] for r in st.rows(since=5.0)] == ["b"]        # >= is inclusive
    assert st.rows(since=5.1) == []


def test_store_orders_rows_by_ts():
    st = TraceStore(":memory:")
    rec(st, ts=9.0, trace_id="late")
    rec(st, ts=2.0, trace_id="early")
    assert [r["trace_id"] for r in st.rows()] == ["early", "late"]


def test_default_dsn_comes_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_DB_DSN", "")
    monkeypatch.setenv("AI_TRACE_DB", str(tmp_path / "x.db"))
    st = TraceStore()
    assert not st.pg and st.dsn == ""
    rec(st)
    assert (tmp_path / "x.db").exists()
    # an explicit empty dsn also means sqlite, not 'unset'
    monkeypatch.setenv("AI_DB_DSN", "ignored-because-explicit")
    assert TraceStore("").dsn == ""


def test_summary_aggregates_per_task_model():
    st = TraceStore(":memory:")
    for i, lat in enumerate([10.0, 20.0, 30.0, 40.0]):
        rec(st, ts=float(i), latency_ms=lat, input_tokens=10, output_tokens=5, cost_usd=0.25)
    rec(st, ts=9.0, latency_ms=50.0, status="provider_error", input_tokens=None, output_tokens=None, cost_usd=None)
    rec(st, task="agent", model="z", ts=3.0)
    s = st.summary()
    assert [(x["task"], x["model"]) for x in s] == [("agent", "z"), ("classify", "m")]
    c = s[1]
    assert c["calls"] == 5 and c["error_rate"] == 0.2
    assert c["p50_ms"] == 30.0                         # median of 10,20,30,40,50
    assert c["p95_ms"] == 50.0
    assert (c["input_tokens"], c["output_tokens"]) == (40, 20) and c["cost_usd"] == 1.0
    assert st.summary(since=3.5)[0]["calls"] == 1


def test_summary_p95_index_for_small_and_large_samples():
    st = TraceStore(":memory:")
    for i in range(20):
        rec(st, ts=float(i), latency_ms=float(i + 1))
    assert st.summary()[0]["p95_ms"] == 20.0           # int(0.95*20)=19 -> last element
    st2 = TraceStore(":memory:")
    rec(st2, latency_ms=7.0)
    assert st2.summary()[0]["p95_ms"] == 7.0           # single sample clamps to index 0
    st3 = TraceStore(":memory:")
    for i in range(10):
        rec(st3, ts=float(i), latency_ms=float(i + 1))
    assert st3.summary()[0]["p95_ms"] == 10.0          # int(9.5)=9
    st4 = TraceStore(":memory:")
    for i in range(4):
        rec(st4, ts=float(i), latency_ms=float(i + 1))
    assert st4.summary()[0]["p95_ms"] == 4.0           # int(3.8)=3
    assert TraceStore(":memory:").summary() == []


def test_summary_rounds_values():
    st = TraceStore(":memory:")
    rec(st, ts=1.0, latency_ms=1.26, cost_usd=0.123456)
    s = st.summary()[0]
    assert s["p50_ms"] == 1.3 and s["cost_usd"] == 0.1235


def test_root_causes_rank_and_truncate():
    st = TraceStore(":memory:")
    rec(st, ts=0.0)                                                                  # ok: ignored
    for i in range(3):
        rec(st, ts=float(i), status="provider_error", error="ConnectError: down")
    rec(st, ts=5.0, status="schema_invalid", error="E" * 200)
    rec(st, ts=6.0, status="schema_invalid", error=None)
    rc = st.root_causes()
    assert rc[0] == {"task": "classify", "model": "m", "status": "provider_error", "error": "ConnectError: down", "count": 3}
    long = next(r for r in rc if r["error"].startswith("E"))
    assert len(long["error"]) == 80 and long["count"] == 1
    assert any(r["error"] == "" and r["count"] == 1 for r in rc)
    assert len(rc) == 3
    assert len(st.root_causes(top=1)) == 1
    assert st.root_causes(since=5.5)[0]["status"] == "schema_invalid"


def test_timer_measures_elapsed_ms(monkeypatch):
    ticks = iter([100.0, 100.25])
    monkeypatch.setattr(observe.time, "perf_counter", lambda: next(ticks))
    with timer() as t:
        assert "ms" not in t
    assert t["ms"] == pytest.approx(250.0)


# ---- TraceStore (postgres path, fake psycopg) ------------------------------------------------

class FakeCon:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return [(1.0, "tr", "classify", "p", "m", "ok", 1, 5.0, 1, 2, 0.1, None)]

    def commit(self):
        raise AssertionError("postgres connection is autocommit")


@pytest.fixture
def fake_psycopg(monkeypatch):
    mod = types.ModuleType("psycopg")
    holder = {}

    def connect(dsn, autocommit=False):
        holder.update(dsn=dsn, autocommit=autocommit, con=FakeCon())
        return holder["con"]
    mod.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", mod)
    return holder


def test_postgres_store_uses_psycopg_placeholders_and_no_commit(fake_psycopg):
    st = TraceStore("postgresql://u@h/db")
    assert st.pg and fake_psycopg["dsn"] == "postgresql://u@h/db" and fake_psycopg["autocommit"] is True
    ddl = fake_psycopg["con"].calls[0][0]
    assert "BIGSERIAL PRIMARY KEY" in ddl
    rec(st)                                            # would hit FakeCon.commit -> AssertionError if called
    sql, vals = fake_psycopg["con"].calls[-1]
    assert sql.count("%s") == 12 and "?" not in sql and len(vals) == 12
    rows = st.rows(since=2.0)
    sql, params = fake_psycopg["con"].calls[-1]
    assert "ts>=%s" in sql and params == (2.0,)
    assert rows[0]["trace_id"] == "tr" and rows[0]["status"] == "ok"


def test_postgres_prefix_detection():
    assert TraceStore("").pg is False
    assert TraceStore(":memory:").pg is False


def test_sqlite_ddl_uses_autoincrement_and_q_keeps_question_marks():
    st = TraceStore(":memory:")
    assert st._q("a=? b=?") == "a=? b=?"
    assert st.con.execute("SELECT sql FROM sqlite_master WHERE name='llm_calls'").fetchone()[0].count("AUTOINCREMENT") == 1


# ---- retrieval ---------------------------------------------------------------------------------

def test_hash_embed_is_unit_norm_deterministic_and_case_insensitive():
    v = retrieval.hash_embed("Hello World hello")
    assert len(v) == retrieval.DIM
    assert math.sqrt(sum(x * x for x in v)) == pytest.approx(1.0)
    assert v == retrieval.hash_embed("hello world HELLO")
    assert retrieval.hash_embed("a b c", dim=16).__len__() == 16
    assert retrieval.hash_embed("") == [0.0] * retrieval.DIM
    assert retrieval.hash_embed("!!! ???") == [0.0] * retrieval.DIM    # no alphanumerics


def test_hash_embed_ignores_punctuation_tokenisation():
    assert retrieval.hash_embed("claim-form, amount.") == retrieval.hash_embed("claim form amount")


def test_load_clauses_parses_headings_and_bodies(tmp_path):
    f = tmp_path / "c.md"
    f.write_text("preamble ignored\n## C1 First clause\nbody one\nmore\n## C2 Second\nbody two\n", encoding="utf-8")
    out = retrieval.load_clauses(f)
    assert out == [("C1", "C1 First clause\nbody one\nmore"), ("C2", "C2 Second\nbody two")]


def test_load_clauses_skips_text_before_first_heading_and_handles_empty(tmp_path):
    f = tmp_path / "e.md"
    f.write_text("just text\n# not level two\n", encoding="utf-8")
    assert retrieval.load_clauses(f) == []
    g = tmp_path / "g.md"
    g.write_text("## X\n", encoding="utf-8")
    assert retrieval.load_clauses(g) == [("X", "X")]


def test_memory_store_ranks_by_similarity_and_limits_k():
    ms = retrieval.MemoryStore()
    ms.add("fire", "fire damage to the insured building is covered")
    ms.add("flood", "flood and storm surge exclusions apply to coastal property")
    ms.add("life", "death benefit payable to the nominee")
    hits = ms.search("storm flood exclusion", k=2)
    assert [h["id"] for h in hits][0] == "flood" and len(hits) == 2
    assert hits[0]["score"] >= hits[1]["score"]
    assert all(set(h) == {"id", "text", "score"} for h in hits)
    assert len(ms.search("anything")) == 3             # default k=3
    assert len(ms.search("anything", k=10)) == 3
    assert retrieval.MemoryStore().search("x") == []


def test_memory_store_custom_embedder_and_rounding():
    ms = retrieval.MemoryStore(embed=lambda t: [1.0, 0.0] if "a" in t else [0.0, 1.0])
    ms.add("A", "aaa"); ms.add("B", "bbb")
    hits = ms.search("a")
    assert [(h["id"], h["score"]) for h in hits] == [("A", 1.0), ("B", 0.0)]
    ms2 = retrieval.MemoryStore(embed=lambda t: [1 / 3])
    ms2.add("x", "t")
    assert ms2.search("t")[0]["score"] == 0.111


class PgCon:
    def __init__(self):
        self.calls, self.rows = [], [("c1", "body", 0.98765)]

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return self.rows


def test_pgvector_store_sql_and_serialisation(monkeypatch):
    con = PgCon()
    mod = types.ModuleType("psycopg")
    seen = {}

    def connect(dsn, autocommit=False):
        seen.update(dsn=dsn, autocommit=autocommit)
        return con
    mod.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", mod)
    st = retrieval.PgVectorStore("postgresql://x", embed=lambda t: [0.5, -0.25], dim=2)
    assert seen == {"dsn": "postgresql://x", "autocommit": True}
    assert con.calls[0][0] == "CREATE EXTENSION IF NOT EXISTS vector"
    assert "vector(2)" in con.calls[1][0]
    st.add("c1", "text")
    sql, params = con.calls[-1]
    assert "ON CONFLICT(id) DO UPDATE" in sql and params == ("c1", "text", "[0.500000,-0.250000]")
    out = st.search("q", k=4)
    sql, params = con.calls[-1]
    assert "ORDER BY embedding <=> %s::vector LIMIT %s" in sql and params == ("[0.500000,-0.250000]", "[0.500000,-0.250000]", 4)
    assert out == [{"id": "c1", "text": "body", "score": 0.988}]
    st.search("q")
    assert con.calls[-1][1][2] == 3
