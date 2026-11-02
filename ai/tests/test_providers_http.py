"""Provider adapters and route/provider construction, exercised against an in-process httpx transport (no network)."""
import asyncio
import json

import httpx
import pytest

from ai import client as client_mod
from ai.providers import AnthropicProvider, MockProvider, OpenAICompatProvider
from ai.types import ToolSpec

REAL_ASYNC_CLIENT = httpx.AsyncClient


def fake_http(monkeypatch, handler):
    """Route every httpx.AsyncClient through a MockTransport; return the list of captured requests."""
    seen = []

    def transport(request):
        seen.append(request)
        return handler(request)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(transport), **kw))
    return seen


TOOL = ToolSpec("get_policy", "look up", {"type": "object"})
HISTORY = [
    {"role": "system", "content": "be brief"},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "get_policy", "arguments": {"a": 1}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "{\"x\": 1}"},
]


def test_openai_request_and_response_mapping(monkeypatch):
    seen = fake_http(monkeypatch, lambda r: httpx.Response(200, json={
        "choices": [{"message": {"content": "ok", "tool_calls": [
            {"id": "t1", "function": {"name": "get_policy", "arguments": "{\"policy_number\": \"P\"}"}}]}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3}}))
    p = OpenAICompatProvider(base_url="https://x.test/v1/", api_key="k")
    r = asyncio.run(p.complete(model="m", messages=HISTORY, tools=[TOOL], json_schema={"type": "object"}, max_tokens=50))
    req = seen[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://x.test/v1/chat/completions" and req.headers["authorization"] == "Bearer k"
    assert body["model"] == "m" and body["max_completion_tokens"] == 50 and body["temperature"] == 0.0
    assert body["tools"][0]["function"]["name"] == "get_policy"
    assert body["response_format"]["json_schema"]["name"] == "result"
    assistant = body["messages"][2]
    assert assistant["content"] is None and assistant["tool_calls"][0]["function"]["arguments"] == "{\"a\": 1}"
    assert (r.text, r.input_tokens, r.output_tokens, r.model) == ("ok", 7, 3, "m")
    assert (r.tool_calls[0].id, r.tool_calls[0].name, r.tool_calls[0].arguments) == ("t1", "get_policy", {"policy_number": "P"})


def test_azure_uses_deployment_path_and_api_key_header(monkeypatch):
    seen = fake_http(monkeypatch, lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "z"}}]}))
    p = OpenAICompatProvider("azure", "https://az.test", "secret", "2024-10-21")
    r = asyncio.run(p.complete(model="dep1", messages=[{"role": "user", "content": "q"}]))
    req = seen[0]
    assert str(req.url) == "https://az.test/openai/deployments/dep1/chat/completions?api-version=2024-10-21"
    assert req.headers["api-key"] == "secret" and "model" not in json.loads(req.content)
    assert r.text == "z" and r.tool_calls == [] and r.input_tokens == 0


def test_http_error_propagates(monkeypatch):
    fake_http(monkeypatch, lambda r: httpx.Response(500, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(OpenAICompatProvider(api_key="k").complete(model="m", messages=[{"role": "user", "content": "q"}]))


def test_anthropic_request_and_response_mapping(monkeypatch):
    seen = fake_http(monkeypatch, lambda r: httpx.Response(200, json={
        "content": [{"type": "text", "text": "hello"}, {"type": "tool_use", "id": "u1", "name": "get_policy", "input": {"k": 2}}],
        "usage": {"input_tokens": 11, "output_tokens": 5}}))
    r = asyncio.run(AnthropicProvider(api_key="ak").complete(model="claude-x", messages=HISTORY, tools=[TOOL],
                                                             json_schema={"type": "object"}))
    req = seen[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "ak" and req.headers["anthropic-version"] == "2023-06-01"
    assert body["system"].startswith("be brief\n\nReply with ONLY a JSON object")
    assert body["tools"] == [{"name": "get_policy", "description": "look up", "input_schema": {"type": "object"}}]
    assert body["messages"][1] == {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "get_policy", "input": {"a": 1}}]}
    assert body["messages"][2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "{\"x\": 1}"}]}
    assert (r.text, r.input_tokens, r.output_tokens) == ("hello", 11, 5)
    assert (r.tool_calls[0].id, r.tool_calls[0].arguments) == ("u1", {"k": 2})


def test_anthropic_omits_empty_system_and_tools(monkeypatch):
    seen = fake_http(monkeypatch, lambda r: httpx.Response(200, json={"content": []}))
    asyncio.run(AnthropicProvider(api_key="ak").complete(model="m", messages=[{"role": "user", "content": "q"}]))
    body = json.loads(seen[0].content)
    assert "system" not in body and "tools" not in body


def test_mock_provider_tasks():
    m = MockProvider()
    run = lambda **kw: asyncio.run(m.complete(model="mock", **kw))
    cls = json.loads(run(messages=[{"role": "user", "content": "Claim Form\nClaimant: A\nDate of loss: 2026-01-01"}], meta={"task": "classify"}).text)
    assert cls["doc_type"] == "claim_form" and cls["confidence"] == pytest.approx(0.97)
    assert json.loads(run(messages=[{"role": "user", "content": "nothing"}], meta={"task": "classify"}).text)["doc_type"] == "other"
    ext = json.loads(run(messages=[{"role": "user", "content": "Invoice No: INV-9\nTotal amount: Rs. 1,200"}],
                         meta={"task": "extract", "doc_type": "invoice"}).text)
    assert ext["fields"]["invoice_number"]["value"] == "INV-9" and ext["fields"]["total_amount"]["value"] == "1200"
    rec = json.loads(run(messages=[{"role": "user", "content": "loan agreement 2020-05-06 Borrower: Z"}],
                         meta={"task": "records_classify", "classes": {"loans": ["loan"], "hr": ["payroll"]}}).text)
    assert rec["record_class"] == "loans" and rec["document_date"] == "2020-05-06" and rec["entity"] == "Z"
    assert json.loads(run(messages=[{"role": "user", "content": "x"}], meta={"task": "other"}).text) == {}
    with pytest.raises(httpx.ConnectError):
        run(messages=[{"role": "user", "content": "x"}], meta={"force_error": True})


def test_default_routes_follow_environment(monkeypatch):
    for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_DEPLOY_SMALL", "AZURE_DEPLOY_LARGE", "ANTHROPIC_API_KEY", "AI_PROVIDER", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert {t: [(r.provider, r.model) for r in rs] for t, rs in client_mod.default_routes().items()}["classify"] == [("mock", "mock-heuristic-1")]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert [(r.provider, r.model) for r in client_mod.default_routes()["agent"]] == [("anthropic", "claude-sonnet-5-5")]
    monkeypatch.setenv("AI_PROVIDER", "mock")
    assert client_mod.default_routes()["agent"][0].provider == "mock"
    monkeypatch.delenv("AI_PROVIDER")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://az.test")
    monkeypatch.setenv("AZURE_DEPLOY_SMALL", "s")
    monkeypatch.setenv("AZURE_DEPLOY_LARGE", "l")
    r = client_mod.default_routes()
    assert [(x.provider, x.model) for x in r["classify"]] == [("azure", "s"), ("azure", "l")]
    assert [x.provider for x in r["extract"]] == ["azure", "anthropic"]
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert [x.provider for x in client_mod.default_routes()["extract"]] == ["azure"]


def test_build_providers_follow_environment(monkeypatch):
    for k in ("AZURE_OPENAI_ENDPOINT", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert set(client_mod.build_providers()) == {"mock"}
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://az.test")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    p = client_mod.build_providers()
    assert set(p) == {"mock", "azure", "openai", "anthropic"} and p["azure"].azure_api_version == "2024-10-21"
