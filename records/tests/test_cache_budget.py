import tempfile
from pathlib import Path

import pytest

from ai.client import LLMClient
from ai.observe import TraceStore
from records import pipeline, synth
from records.cache import ClassificationCache


def llm_and_store():
    store = TraceStore(":memory:")
    return LLMClient(store=store), store


def lib(n=60):
    d = Path(tempfile.mkdtemp()); synth.build(d / "lib", n=n)
    return d / "lib"


def n_calls(store):
    return len(store.rows())


@pytest.mark.story("REC-7")
def test_rerun_uses_cache_and_makes_no_llm_calls(tmp_path):
    src, cache = lib(), ClassificationCache(tmp_path / "c.db")
    llm, store = llm_and_store()
    rows1 = pipeline.analyse(src, llm, cache=cache)
    first = n_calls(store)
    assert first > 0
    rows2 = pipeline.analyse(src, llm, cache=cache)
    assert n_calls(store) == first                                   # zero additional model calls
    assert [r["record_class"] for r in rows1] == [r["record_class"] for r in rows2]


@pytest.mark.story("REC-7")
def test_cache_is_invalidated_when_file_plan_changes(tmp_path):
    cache = ClassificationCache(tmp_path / "c.db", plan_version="v1")
    cache.put("abc", {"record_class": "invoice"})
    assert cache.get("abc") == {"record_class": "invoice"}
    assert ClassificationCache(tmp_path / "c.db", plan_version="v2").get("abc") is None


@pytest.mark.story("REC-8")
def test_budget_guard_routes_overflow_to_review():
    src = lib()
    llm, store = llm_and_store()
    rows = pipeline.analyse(src, llm, max_llm_calls=10)
    assert n_calls(store) <= 10
    over = [r for r in rows if "AI budget exhausted" in r["reasons"]]
    assert over and all(r["status"] == "review" for r in over)       # never silently guess or silently drop
