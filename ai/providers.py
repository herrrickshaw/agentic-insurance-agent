"""Provider adapters over plain HTTP: Azure OpenAI, OpenAI, Anthropic, plus a deterministic offline mock."""
import json
import os
import re
import uuid
from typing import Protocol

import httpx

from .types import LLMResponse, Message, ToolCall, ToolSpec


class Provider(Protocol):
    name: str

    async def complete(self, *, model: str, messages: list[Message], tools: list[ToolSpec] | None = None,
                       json_schema: dict | None = None, temperature: float = 0.0, max_tokens: int = 1024,
                       meta: dict | None = None) -> LLMResponse: ...


def _oa_messages(messages):
    out = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": [
                {"id": t["id"], "type": "function",
                 "function": {"name": t["name"], "arguments": json.dumps(t["arguments"])}} for t in m["tool_calls"]]})
        else:
            out.append(m)
    return out


def _oa_tools(tools):
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.parameters}} for t in tools]


class OpenAICompatProvider:
    """OpenAI and Azure OpenAI (Azure AI Foundry deployments use the same chat-completions shape)."""

    def __init__(self, name="openai", base_url=None, api_key=None, azure_api_version=None, timeout=60.0):
        self.name = name
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.azure_api_version = azure_api_version
        self.timeout = timeout

    async def complete(self, *, model, messages, tools=None, json_schema=None, temperature=0.0,
                       max_tokens=1024, meta=None) -> LLMResponse:
        body = {"messages": _oa_messages(messages), "temperature": temperature, "max_completion_tokens": max_tokens}
        if tools:
            body["tools"] = _oa_tools(tools)
        if json_schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "result", "schema": json_schema}}
        if self.azure_api_version:  # Azure: deployment name in the path, api-key header
            url = f"{self.base_url}/openai/deployments/{model}/chat/completions?api-version={self.azure_api_version}"
            headers = {"api-key": self.api_key}
        else:
            url, headers = f"{self.base_url}/chat/completions", {"Authorization": f"Bearer {self.api_key}"}
            body["model"] = model
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(url, json=body, headers=headers)
            r.raise_for_status()
            d = r.json()
        msg = d["choices"][0]["message"]
        calls = [ToolCall(tc["id"], tc["function"]["name"], json.loads(tc["function"]["arguments"] or "{}"))
                 for tc in msg.get("tool_calls") or []]
        u = d.get("usage", {})
        return LLMResponse(msg.get("content") or "", calls, u.get("prompt_tokens", 0),
                           u.get("completion_tokens", 0), model, d)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key=None, base_url="https://api.anthropic.com", timeout=60.0):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    @staticmethod
    def _convert(messages):
        system, out = [], []
        for m in messages:
            if m["role"] == "system":
                system.append(m["content"])
            elif m["role"] == "tool":
                out.append({"role": "user", "content": [{"type": "tool_result",
                                                         "tool_use_id": m["tool_call_id"], "content": m["content"]}]})
            elif m["role"] == "assistant" and m.get("tool_calls"):
                blocks = ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                    {"type": "tool_use", "id": t["id"], "name": t["name"], "input": t["arguments"]}
                    for t in m["tool_calls"]]
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return "\n\n".join(system), out

    async def complete(self, *, model, messages, tools=None, json_schema=None, temperature=0.0,
                       max_tokens=1024, meta=None) -> LLMResponse:
        system, msgs = self._convert(messages)
        if json_schema:
            system += "\n\nReply with ONLY a JSON object matching this JSON Schema, no prose:\n" + json.dumps(json_schema)
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature, "messages": msgs}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                             for t in tools]
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{self.base_url}/v1/messages", json=body,
                             headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            d = r.json()
        text = "".join(b["text"] for b in d["content"] if b["type"] == "text")
        calls = [ToolCall(b["id"], b["name"], b["input"]) for b in d["content"] if b["type"] == "tool_use"]
        u = d.get("usage", {})
        return LLMResponse(text, calls, u.get("input_tokens", 0), u.get("output_tokens", 0), model, d)


class MockProvider:
    """Deterministic heuristic baseline: regex/keyword 'model' so the pipeline, evals and CI run offline.

    NOT a quality proxy for a real LLM. Evals against it prove plumbing and regression-gating only.
    """
    name = "mock"
    KEYWORDS = {
        "claim_form": ["claim form", "date of loss", "claimant", "loss date"],
        "repair_estimate": ["repair estimate", "estimate no", "labour", "spare parts"],
        "medical_report": ["medical report", "diagnosis", "discharge summary", "patient"],
        "invoice": ["invoice", "invoice no", "bill to", "gst"],
        "kyc_id": ["aadhaar", "passport", "driving licence", "pan card", "identity"],
        "policy_application": ["proposal form", "application for", "sum assured", "nominee"],
        "payment_proof": ["utr", "transaction id", "payment confirmation", "neft", "upi ref"],
    }

    async def complete(self, *, model, messages, tools=None, json_schema=None, temperature=0.0,
                       max_tokens=1024, meta=None) -> LLMResponse:
        meta = meta or {}
        task = meta.get("task")
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if meta.get("force_error"):
            raise httpx.ConnectError("mock failure")
        if task == "classify":
            low, best, score = user.lower(), "other", 0
            for t, kws in self.KEYWORDS.items():
                s = sum(k in low for k in kws)
                if s > score:
                    best, score = t, s
            out = {"doc_type": best, "confidence": min(0.5 + 0.15 * score, 0.97) if score else 0.3,
                   "rationale": f"{score} keyword hits"}
        elif task == "extract":
            out = self._extract(meta["doc_type"], user)
        elif task == "summarize":
            sents = [s.strip() for s in re.split(r"(?<=[.\n])\s+", user) if len(s.strip()) > 20][:3]
            out = {"summary": " ".join(sents)[:600], "key_points": sents, "open_questions": []}
        elif task == "agent":
            return self._agent(messages, tools or [])
        else:
            out = {}
        txt = json.dumps(out)
        return LLMResponse(txt, [], len(user) // 4, len(txt) // 4, model)

    @staticmethod
    def _find(pattern, text, flags=re.I):
        m = re.search(pattern, text, flags)
        return (m.group(1).strip(), m.group(0).strip()) if m else (None, None)

    def _extract(self, doc_type, text):
        pats = {
            "claim_form": {"policy_number": r"policy (?:no\.?|number)[:\s]+([A-Z]{3}-\d{4}-\d{6})",
                           "claimant_name": r"claimant(?: name)?[:\s]+([A-Za-z .]+?)(?:\n|,|$)",
                           "loss_date": r"(?:date of loss|loss date)[:\s]+(\d{4}-\d{2}-\d{2})",
                           "claim_amount": r"(?:claim(?:ed)? amount|amount claimed)[:\s]+(?:INR|Rs\.?|₹)?\s*([\d,]+)"},
            "invoice": {"invoice_number": r"invoice (?:no\.?|number)[:\s]+([A-Z0-9-]+)",
                        "vendor": r"vendor[:\s]+([A-Za-z0-9 .&]+?)(?:\n|$)",
                        "total_amount": r"total(?: amount)?[:\s]+(?:INR|Rs\.?|₹)?\s*([\d,]+)"},
            "repair_estimate": {"estimate_number": r"estimate (?:no\.?|number)[:\s]+([A-Z0-9-]+)",
                                "total_amount": r"total(?: amount)?[:\s]+(?:INR|Rs\.?|₹)?\s*([\d,]+)"},
            "medical_report": {"patient_name": r"patient(?: name)?[:\s]+([A-Za-z .]+?)(?:\n|,|$)",
                               "diagnosis": r"diagnosis[:\s]+([^\n]+)"},
            "kyc_id": {"name": r"name[:\s]+([A-Za-z .]+?)(?:\n|$)", "id_type": r"(aadhaar|passport|pan card|driving licence)"},
            "payment_proof": {"utr": r"(?:utr|transaction id)[:\s]+([A-Z0-9]+)",
                              "amount": r"amount[:\s]+(?:INR|Rs\.?|₹)?\s*([\d,]+)"},
        }.get(doc_type, {})
        fields = {}
        for name, pat in pats.items():
            v, ev = self._find(pat, text)
            if v:
                fields[name] = {"value": v.replace(",", "") if "amount" in name else v, "evidence": ev}
        return {"fields": fields}

    def _agent(self, messages, tools):
        results = {m["tool_call_id"]: m for m in messages if m["role"] == "tool"}
        ctx = next(m["content"] for m in messages if m["role"] == "user")
        names = {t.name for t in tools}
        done = {json.loads(m["content"]).get("_tool") for m in results.values() if m["content"].startswith("{")}
        for tool in ("get_policy", "get_fraud_signals", "search_policy_clauses"):
            if tool in names and tool not in done:
                args = {"get_policy": {"policy_number": self._find(r"(POL-\d{4}-\d{6})", ctx)[0] or ""},
                        "get_fraud_signals": {"claim_number": self._find(r"(CLM-\d{4}-\d{6})", ctx)[0] or ""},
                        "search_policy_clauses": {"query": "claim exclusions and documentation"}}[tool]
                return LLMResponse("", [ToolCall(f"call_{uuid.uuid4().hex[:8]}", tool, args)], 50, 10, "mock")
        data = {json.loads(m["content"]).get("_tool"): json.loads(m["content"]) for m in results.values()
                if m["content"].startswith("{")}
        fraud = data.get("get_fraud_signals", {}).get("fraud_score", 0)
        amt = int(self._find(r"claim_amount\D+(\d+)", ctx)[0] or 0)
        route = "siu" if fraud >= 70 else "fast_track" if amt <= 10000 and fraud < 30 else "adjuster"
        out = {"route": route, "confidence": 0.7, "rationale": f"fraud={fraud}, amount={amt}",
               "citations": [c["id"] for c in data.get("search_policy_clauses", {}).get("results", [])][:2]}
        return LLMResponse(json.dumps(out), [], 80, 40, "mock")
