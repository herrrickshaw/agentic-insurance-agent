"""ai/client.py: route selection from the environment, provider construction, ranking and the structured() retry/fallback loop."""
import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from ai import client as C
from ai import observe
from ai.observe import TraceStore
from ai.providers import AnthropicProvider, MockProvider, OpenAICompatProvider
from ai.types import LLMResponse

run = asyncio.run
ENV = ("AZURE_DEPLOY_SMALL", "AZURE_DEPLOY_LARGE", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_API_VERSION",
       "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "AI_PROVIDER", "OPENAI_API_KEY")
TASKS = {"classify", "summarize", "extract", "agent", "records_classify"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)


def azure(monkeypatch, anthropic=False):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://az.example")
    monkeypatch.setenv("AZURE_DEPLOY_SMALL", "small")
    monkeypatch.setenv("AZURE_DEPLOY_LARGE", "large")
    if anthropic:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")


def test_default_routes_fall_back_to_mock():
    r = C.default_routes()
    assert set(r) == TASKS
    assert all(v == [C.Route("mock", "mock-heuristic-1")] for v in r.values())


def test_default_routes_anthropic_only(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    r = C.default_routes()
    assert set(r) == TASKS and all(v == [C.Route("anthropic", "claude-sonnet-5-5")] for v in r.values())
    monkeypatch.setenv("ANTHROPIC_MODEL", "my-claude")
    assert C.default_routes()["agent"][0].model == "my-claude"


def test_ai_provider_mock_overrides_anthropic_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    assert C.default_routes()["classify"] == [C.Route("mock", "mock-heuristic-1")]
    monkeypatch.setenv("AI_PROVIDER", "other")
    assert C.default_routes()["classify"][0].provider == "anthropic"


def test_default_routes_azure_with_anthropic_fallback(monkeypatch):
    azure(monkeypatch, anthropic=True)
    r = C.default_routes()
    assert [(x.provider, x.model) for x in r["classify"]] == [("azure", "small"), ("azure", "large")]
    assert [(x.provider, x.model) for x in r["summarize"]] == [("azure", "small"), ("azure", "large")]
    assert [(x.provider, x.model) for x in r["extract"]] == [("azure", "large"), ("anthropic", "claude-sonnet-5-5")]
    assert [(x.provider, x.model) for x in r["agent"]] == [("azure", "large"), ("anthropic", "claude-sonnet-5-5")]
    assert [(x.provider, x.model) for x in r["records_classify"]] == [("azure", "small"), ("azure", "large")]


def test_default_routes_azure_drops_anthropic_without_key(monkeypatch):
    azure(monkeypatch)
    r = C.default_routes()
    assert [(x.provider, x.model) for x in r["extract"]] == [("azure", "large")]
    assert all(x.provider == "azure" for v in r.values() for x in v)


@pytest.mark.parametrize("missing", ["AZURE_DEPLOY_SMALL", "AZURE_DEPLOY_LARGE", "AZURE_OPENAI_ENDPOINT"])
def test_azure_needs_endpoint_and_both_deployments(monkeypatch, missing):
    azure(monkeypatch)
    monkeypatch.delenv(missing)
    assert all(x.provider == "mock" for v in C.default_routes().values() for x in v)


def test_route_defaults():
    r = C.Route("p", "m")
    assert (r.quality, r.max_latency_ms) == (0.8, 30000)


def test_build_providers_is_mock_only_by_default():
    p = C.build_providers()
    assert list(p) == ["mock"] and isinstance(p["mock"], MockProvider)


def test_build_providers_from_env(monkeypatch):
    azure(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azk")
    monkeypatch.setenv("OPENAI_API_KEY", "oak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ank")
    p = C.build_providers()
    assert set(p) == {"mock", "azure", "openai", "anthropic"}
    az = p["azure"]
    assert isinstance(az, OpenAICompatProvider)
    assert (az.name, az.base_url, az.api_key, az.azure_api_version) == ("azure", "https://az.example", "azk", "2024-10-21")
    assert p["openai"].base_url == "https://api.openai.com/v1" and p["openai"].api_key == "oak"
    assert isinstance(p["anthropic"], AnthropicProvider) and p["anthropic"].api_key == "ank"
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-01-01")
    assert C.build_providers()["azure"].azure_api_version == "2025-01-01"


def test_client_constructor_defaults_use_environment():
    c = C.LLMClient(store=TraceStore(":memory:"))
    assert list(c.providers) == ["mock"] and set(c.routes) == TASKS


# ---- order() ---------------------------------------------------------------------------------

def seed(store, model, n, *, task="t", status="ok", latency=100.0, tokens=0):
    for i in range(n):
        store.record(ts=float(i), trace_id="x", task=task, provider="p", model=model, status=status, attempt=1,
                     latency_ms=latency, input_tokens=tokens, output_tokens=0, cost_usd=0.0, error=None)


def test_order_keeps_configured_order_with_thin_history():
    st = TraceStore(":memory:")
    a, b = C.Route("p", "a"), C.Route("p", "b")
    seed(st, "a", 5)
    seed(st, "b", 4)                       # one candidate under 5 calls -> stable
    c = C.LLMClient({"p": MockProvider()}, {"t": [a, b]}, st)
    assert c.order("t") == [a, b]


def test_order_needs_five_calls_on_every_candidate_before_reranking():
    a, b = C.Route("p", "a", quality=0.9), C.Route("p", "b", quality=0.9)
    st = TraceStore(":memory:")
    seed(st, "a", 5, latency=29000.0)                       # slow, enough history
    seed(st, "b", 4, latency=10.0)                          # fast but only 4 calls: ordering must stay as configured
    c = C.LLMClient({"p": MockProvider()}, {"t": [a, b]}, st)
    assert c.order("t") == [a, b]
    seed(st, "b", 1, latency=10.0)                          # now 5 calls: fast model wins
    assert c.order("t") == [b, a]


def test_order_ranks_by_quality_latency_and_errors():
    st = TraceStore(":memory:")
    slow, fast = C.Route("p", "slow", quality=0.9), C.Route("p", "fast", quality=0.9)
    seed(st, "slow", 5, latency=29000.0)
    seed(st, "fast", 5, latency=100.0)
    c = C.LLMClient({"p": MockProvider()}, {"t": [slow, fast]}, st)
    assert c.order("t") == [fast, slow]
    assert c.order("t", weights=(0, 1, 0)) == [fast, slow]


def test_order_penalises_error_rate_and_respects_quality_weight():
    st = TraceStore(":memory:")
    flaky, steady = C.Route("p", "flaky", quality=0.9), C.Route("p", "steady", quality=0.5)
    seed(st, "flaky", 5, status="provider_error", latency=100.0)
    seed(st, "steady", 5, latency=100.0)
    c = C.LLMClient({"p": MockProvider()}, {"t": [flaky, steady]}, st)
    assert c.order("t") == [steady, flaky]                 # error_rate 1 zeros the flaky quality term
    healthy = C.Route("p", "flaky", quality=0.9)
    st2 = TraceStore(":memory:")
    seed(st2, "flaky", 5); seed(st2, "steady", 5)
    c2 = C.LLMClient({"p": MockProvider()}, {"t": [steady, healthy]}, st2)
    assert c2.order("t") == [healthy, steady]              # equal latency/cost, higher quality first
    assert c2.order("t", weights=(0, 0, 1)) == [steady, healthy]   # ties keep configured order (stable sort)


def test_order_latency_is_capped_at_max_latency():
    st = TraceStore(":memory:")
    a = C.Route("p", "a", max_latency_ms=1000)
    b = C.Route("p", "b", max_latency_ms=1000)
    seed(st, "a", 5, latency=5000.0)       # beyond max -> lat term 0, not negative
    seed(st, "b", 5, latency=5000.0)
    c = C.LLMClient({"p": MockProvider()}, {"t": [a, b]}, st)
    assert c.order("t") == [a, b]


def test_order_cost_term_prefers_cheaper_model(monkeypatch):
    monkeypatch.setitem(observe.PRICES, "pricey", (1000.0, 0.0))
    st = TraceStore(":memory:")
    pricey, cheap = C.Route("p", "pricey"), C.Route("p", "cheap")
    for m in ("pricey", "cheap"):
        for i in range(5):
            st.record(ts=float(i), trace_id="x", task="t", provider="p", model=m, status="ok", attempt=1, latency_ms=100.0,
                      input_tokens=1000, output_tokens=0, cost_usd=C.cost(m, 1000, 0), error=None)
    c = C.LLMClient({"p": MockProvider()}, {"t": [pricey, cheap]}, st)
    assert c.order("t") == [cheap, pricey]


# ---- _call / structured ---------------------------------------------------------------------

class Out(BaseModel):
    doc_type: str


class Scripted:
    """Provider returning scripted items (LLMResponse or Exception) in order; records the messages it saw."""
    def __init__(self, *items):
        self.items, self.seen = list(items), []

    async def complete(self, **kw):
        self.seen.append(kw)
        it = self.items.pop(0)
        if isinstance(it, Exception):
            raise it
        return it


def mkclient(*providers_routes):
    provs = {name: p for name, p, _ in providers_routes}
    routes = [C.Route(name, model) for name, _, model in providers_routes]
    st = TraceStore(":memory:")
    return C.LLMClient(provs, {"t": routes}, st), st


def test_structured_success_strips_json_fence_and_records_trace():
    p = Scripted(LLMResponse('```json\n{"doc_type": "invoice"}\n```', [], 12, 4, "m"))
    c, st = mkclient(("p", p, "m"))
    out, meta = run(c.structured("t", [{"role": "user", "content": "x"}], Out, meta={"k": 1}, max_tokens=77))
    assert out.doc_type == "invoice"
    assert meta["provider"] == "p" and meta["model"] == "m" and meta["attempts"] == 1 and len(meta["trace_id"]) == 12
    kw = p.seen[0]
    assert kw["max_tokens"] == 77 and kw["json_schema"] == Out.model_json_schema()
    assert kw["meta"] == {"task": "t", "k": 1}
    (row,) = st.rows()
    assert (row["status"], row["input_tokens"], row["output_tokens"], row["attempt"], row["provider"]) == ("ok", 12, 4, 1, "p")
    assert row["latency_ms"] >= 0


def test_structured_repairs_once_with_conversation_context():
    p = Scripted(LLMResponse('{"nope": 1}'), LLMResponse('{"doc_type": "kyc"}'))
    c, st = mkclient(("p", p, "m"))
    out, meta = run(c.structured("t", [{"role": "user", "content": "x"}], Out))
    assert out.doc_type == "kyc" and meta["attempts"] == 2
    second = p.seen[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"] == '{"nope": 1}' and second[2]["content"].startswith("Invalid output: schema:")
    assert second[2]["content"].endswith("Return corrected JSON only.")
    statuses = [r["status"] for r in st.rows()]
    assert sorted(statuses) == ["ok", "ok", "schema_invalid"]
    bad = next(r for r in st.rows() if r["status"] == "schema_invalid")
    assert bad["error"].startswith("schema:") and len(bad["error"]) <= 200 and bad["cost_usd"] == 0


def test_structured_original_messages_are_not_mutated():
    msgs = [{"role": "user", "content": "x"}]
    p = Scripted(LLMResponse("garbage"), LLMResponse('{"doc_type": "a"}'))
    c, _ = mkclient(("p", p, "m"))
    run(c.structured("t", msgs, Out))
    assert msgs == [{"role": "user", "content": "x"}]


def test_structured_two_invalid_replies_falls_to_next_route():
    bad = Scripted(LLMResponse("not json"), LLMResponse("still not"))
    good = Scripted(LLMResponse('{"doc_type": "z"}'))
    c, _ = mkclient(("a", bad, "m1"), ("b", good, "m2"))
    out, meta = run(c.structured("t", [{"role": "user", "content": "x"}], Out))
    assert out.doc_type == "z" and meta["provider"] == "b" and meta["attempts"] == 1
    assert len(bad.seen) == 2                      # exactly one repair retry


@pytest.mark.parametrize("exc", [httpx.ConnectError("boom"), TimeoutError("slow"), KeyError("k"),
                                 json.JSONDecodeError("m", "d", 0)])
def test_provider_errors_are_recorded_and_next_route_is_tried(exc):
    bad = Scripted(exc)
    good = Scripted(LLMResponse('{"doc_type": "ok"}'))
    c, st = mkclient(("a", bad, "m1"), ("b", good, "m2"))
    out, meta = run(c.structured("t", [{"role": "user", "content": "x"}], Out))
    assert out.doc_type == "ok" and meta["provider"] == "b"
    assert len(bad.seen) == 1                      # provider error: no repair retry on the same route
    err = next(r for r in st.rows() if r["status"] == "provider_error")
    assert err["error"].startswith(type(exc).__name__ + ":") and err["input_tokens"] == 0 and err["output_tokens"] == 0


def test_unexpected_exception_type_is_not_swallowed():
    c, _ = mkclient(("a", Scripted(RuntimeError("bug")), "m"))
    with pytest.raises(RuntimeError, match="bug"):
        run(c.structured("t", [{"role": "user", "content": "x"}], Out))


def test_all_routes_failing_raises_with_last_error():
    c, _ = mkclient(("a", Scripted(httpx.ConnectError("down")), "m"))
    with pytest.raises(RuntimeError, match="all routes failed for t: provider_error"):
        run(c.structured("t", [{"role": "user", "content": "x"}], Out))
    c2, _ = mkclient(("a", Scripted(LLMResponse("x"), LLMResponse("y")), "m"))
    with pytest.raises(RuntimeError, match="all routes failed for t: schema:"):
        run(c2.structured("t", [{"role": "user", "content": "x"}], Out))


def test_no_routes_raises_no_route():
    st = TraceStore(":memory:")
    c = C.LLMClient({"p": MockProvider()}, {"t": []}, st)
    with pytest.raises(RuntimeError, match="no route"):
        run(c.structured("t", [], Out))


def test_call_records_cost_from_price_table(monkeypatch):
    monkeypatch.setitem(observe.PRICES, "priced", (2.0, 4.0))
    p = Scripted(LLMResponse('{"doc_type": "a"}', [], 1_000_000, 500_000, "priced"))
    c, st = mkclient(("p", p, "priced"))
    run(c.structured("t", [{"role": "user", "content": "x"}], Out))
    assert st.rows()[0]["cost_usd"] == pytest.approx(4.0)
