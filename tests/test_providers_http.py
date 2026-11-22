"""Provider adapters (ai/providers.py): the HTTP request each adapter builds and how it parses replies. No network:
httpx.AsyncClient is replaced by a recorder that returns canned JSON."""
import asyncio
import json

import httpx
import pytest

from ai import providers as P
from ai.types import ToolCall, ToolSpec

run = asyncio.run


class FakeClient:
    """Stands in for httpx.AsyncClient; class attributes carry the canned reply and the captured request."""
    reply = {}
    status = 200
    seen = {}

    def __init__(self, timeout=None):
        FakeClient.seen = {"timeout": timeout}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        FakeClient.seen.update(url=url, body=json, headers=headers)
        req = httpx.Request("POST", url)
        return httpx.Response(FakeClient.status, json=FakeClient.reply, request=req)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(P.httpx, "AsyncClient", FakeClient)
    FakeClient.status = 200
    FakeClient.reply = {}
    return FakeClient


TOOL = ToolSpec("get_policy", "Look up a policy", {"type": "object", "properties": {}})
MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]


def test_oa_messages_rewrites_only_assistant_tool_calls():
    plain = {"role": "user", "content": "x"}
    asst_no_calls = {"role": "assistant", "content": "ok", "tool_calls": []}
    with_calls = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "f", "arguments": {"a": 1}}]}
    out = P._oa_messages([plain, asst_no_calls, with_calls])
    assert out[0] is plain and out[1] is asst_no_calls
    assert out[2] == {"role": "assistant", "content": None,
                      "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}}]}
    keep = P._oa_messages([{**with_calls, "content": "thinking"}])
    assert keep[0]["content"] == "thinking"


def test_oa_tools_shape():
    assert P._oa_tools([TOOL]) == [{"type": "function", "function": {
        "name": "get_policy", "description": "Look up a policy", "parameters": {"type": "object", "properties": {}}}}]


def test_openai_defaults_and_env_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    p = P.OpenAICompatProvider()
    assert (p.name, p.base_url, p.api_key, p.timeout) == ("openai", "https://api.openai.com/v1", "env-key", 60.0)
    p2 = P.OpenAICompatProvider("x", "http://h/v1/", "explicit", "2024-1", 5)
    assert (p2.base_url, p2.api_key, p2.azure_api_version, p2.timeout) == ("http://h/v1", "explicit", "2024-1", 5)
    monkeypatch.delenv("OPENAI_API_KEY")
    assert P.OpenAICompatProvider().api_key == ""


def test_openai_request_shape_and_response_parsing(fake):
    fake.reply = {"choices": [{"message": {"content": "hello", "tool_calls": [
        {"id": "t1", "function": {"name": "get_policy", "arguments": '{"policy_number": "P1"}'}},
        {"id": "t2", "function": {"name": "noargs", "arguments": ""}}]}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
    p = P.OpenAICompatProvider(api_key="k", timeout=9)
    r = run(p.complete(model="gpt-x", messages=MSGS, tools=[TOOL], json_schema={"type": "object"},
                       temperature=0.3, max_tokens=55))
    s = fake.seen
    assert s["url"] == "https://api.openai.com/v1/chat/completions"
    assert s["headers"] == {"Authorization": "Bearer k"}
    assert s["timeout"] == 9
    assert s["body"]["model"] == "gpt-x"
    assert s["body"]["temperature"] == 0.3 and s["body"]["max_completion_tokens"] == 55
    assert s["body"]["tools"] == P._oa_tools([TOOL])
    assert s["body"]["response_format"] == {"type": "json_schema",
                                            "json_schema": {"name": "result", "schema": {"type": "object"}}}
    assert r.text == "hello" and r.model == "gpt-x"
    assert (r.input_tokens, r.output_tokens) == (11, 7)
    assert r.tool_calls == [ToolCall("t1", "get_policy", {"policy_number": "P1"}), ToolCall("t2", "noargs", {})]


def test_openai_omits_optional_body_parts_and_tolerates_missing_fields(fake):
    fake.reply = {"choices": [{"message": {"content": None}}]}
    r = run(P.OpenAICompatProvider(api_key="k").complete(model="m", messages=MSGS))
    body = fake.seen["body"]
    assert "tools" not in body and "response_format" not in body
    assert body["temperature"] == 0.0 and body["max_completion_tokens"] == 1024
    assert (r.text, r.tool_calls, r.input_tokens, r.output_tokens) == ("", [], 0, 0)


def test_azure_uses_deployment_path_and_api_key_header(fake):
    fake.reply = {"choices": [{"message": {"content": "x"}}]}
    p = P.OpenAICompatProvider("azure", "https://res.openai.azure.com/", "azkey", "2024-10-21")
    run(p.complete(model="dep1", messages=MSGS))
    assert fake.seen["url"] == "https://res.openai.azure.com/openai/deployments/dep1/chat/completions?api-version=2024-10-21"
    assert fake.seen["headers"] == {"api-key": "azkey"}
    assert "model" not in fake.seen["body"]


def test_openai_http_error_propagates(fake):
    fake.status, fake.reply = 429, {"error": "slow down"}
    with pytest.raises(httpx.HTTPStatusError):
        run(P.OpenAICompatProvider(api_key="k").complete(model="m", messages=MSGS))


def test_anthropic_convert_roles():
    msgs = [{"role": "system", "content": "a"}, {"role": "system", "content": "b"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "thinking", "tool_calls": [{"id": "c1", "name": "f", "arguments": {"x": 1}}]},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "name": "g", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "res"},
            {"role": "assistant", "content": "plain"}]
    system, out = P.AnthropicProvider._convert(msgs)
    assert system == "a\n\nb"
    assert out[0] == {"role": "user", "content": "q"}
    assert out[1] == {"role": "assistant", "content": [{"type": "text", "text": "thinking"},
                                                       {"type": "tool_use", "id": "c1", "name": "f", "input": {"x": 1}}]}
    assert out[2]["content"] == [{"type": "tool_use", "id": "c2", "name": "g", "input": {}}]
    assert out[3] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "res"}]}
    assert out[4] == {"role": "assistant", "content": "plain"}
    assert P.AnthropicProvider._convert([]) == ("", [])


def test_anthropic_defaults_and_env_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "envk")
    p = P.AnthropicProvider(base_url="http://x/")
    assert (p.api_key, p.base_url, p.timeout, p.name) == ("envk", "http://x", 60.0, "anthropic")
    assert P.AnthropicProvider("explicit").api_key == "explicit"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert P.AnthropicProvider().api_key == ""


def test_anthropic_request_and_response(fake):
    fake.reply = {"content": [{"type": "text", "text": "a"}, {"type": "tool_use", "id": "u1", "name": "get_policy",
                                                              "input": {"p": 1}}, {"type": "text", "text": "b"}],
                  "usage": {"input_tokens": 5, "output_tokens": 3}}
    p = P.AnthropicProvider(api_key="ak")
    r = run(p.complete(model="claude-x", messages=MSGS, tools=[TOOL], json_schema={"type": "object"},
                       temperature=0.2, max_tokens=99))
    s = fake.seen
    assert s["url"] == "https://api.anthropic.com/v1/messages"
    assert s["headers"] == {"x-api-key": "ak", "anthropic-version": "2023-06-01"}
    b = s["body"]
    assert b["model"] == "claude-x" and b["max_tokens"] == 99 and b["temperature"] == 0.2
    assert b["messages"] == [{"role": "user", "content": "hi"}]
    assert b["system"].startswith("sys\n\nReply with ONLY a JSON object matching this JSON Schema")
    assert b["system"].endswith(json.dumps({"type": "object"}))
    assert b["tools"] == [{"name": "get_policy", "description": "Look up a policy",
                           "input_schema": {"type": "object", "properties": {}}}]
    assert r.text == "ab" and r.tool_calls == [ToolCall("u1", "get_policy", {"p": 1})]
    assert (r.input_tokens, r.output_tokens, r.model) == (5, 3, "claude-x")


def test_anthropic_without_system_tools_or_schema(fake):
    fake.reply = {"content": []}
    r = run(P.AnthropicProvider(api_key="k").complete(model="m", messages=[{"role": "user", "content": "q"}]))
    assert "system" not in fake.seen["body"] and "tools" not in fake.seen["body"]
    assert (r.text, r.tool_calls, r.input_tokens, r.output_tokens) == ("", [], 0, 0)


def test_anthropic_schema_without_system_message_starts_with_blank_prefix(fake):
    fake.reply = {"content": []}
    run(P.AnthropicProvider(api_key="k").complete(model="m", messages=[{"role": "user", "content": "q"}],
                                                  json_schema={"a": 1}))
    assert fake.seen["body"]["system"] == '\n\nReply with ONLY a JSON object matching this JSON Schema, no prose:\n{"a": 1}'


def test_anthropic_http_error_propagates(fake):
    fake.status = 500
    with pytest.raises(httpx.HTTPStatusError):
        run(P.AnthropicProvider(api_key="k").complete(model="m", messages=MSGS))
