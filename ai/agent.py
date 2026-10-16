"""Tool-calling agent loop (provider-agnostic). The agent RECOMMENDS a route; BPMN/DMN and humans decide."""
import json
import time
import uuid
from typing import Callable

from pydantic import BaseModel

from .client import LLMClient
from .guardrails import authorize_agent_action
from .types import ToolSpec

MAX_STEPS = 6


class Tool:
    def __init__(self, spec: ToolSpec, fn: Callable[[dict], dict]):
        self.spec, self.fn = spec, fn


class TriageRecommendation(BaseModel):
    route: str  # fast_track | adjuster | siu
    confidence: float
    rationale: str
    citations: list[str] = []


def claims_tools(ledger_con, clauses) -> list[Tool]:
    def get_policy(a):
        r = ledger_con.execute("SELECT policy_number,status,sum_insured,product FROM policies WHERE policy_number=?",
                               (a.get("policy_number"),)).fetchone()
        return dict(r) if r else {"error": "policy not found"}

    def fraud_signals(a):
        r = ledger_con.execute("SELECT COUNT(*) FROM claims WHERE policy_number=(SELECT policy_number FROM claims "
                               "WHERE claim_number=?)", (a.get("claim_number"),)).fetchone()
        n = r[0] if r else 0
        return {"prior_claims_on_policy": n, "fraud_score": min(100, 20 * max(n - 1, 0))}

    obj = lambda **p: {"type": "object", "properties": {k: {"type": "string"} for k in p}, "required": list(p)}
    return [
        Tool(ToolSpec("get_policy", "Look up policy status and sum insured.", obj(policy_number=1)), get_policy),
        Tool(ToolSpec("get_fraud_signals", "Fraud indicators for a claim.", obj(claim_number=1)), fraud_signals),
        Tool(ToolSpec("search_policy_clauses", "Search policy wording for relevant clauses.", obj(query=1)),
             lambda a: {"results": clauses.search(a.get("query", ""), 3)}),
    ]


async def triage(llm: LLMClient, tools: list[Tool], claim: dict) -> dict:
    by_name = {t.spec.name: t for t in tools}
    trace_id = uuid.uuid4().hex[:12]
    msgs = [{"role": "system", "content": "You triage insurance claims. Use tools to gather facts, then answer with "
                                          "JSON {route: fast_track|adjuster|siu, confidence, rationale, citations}. "
                                          "Cite clause ids. When unsure choose adjuster."},
            {"role": "user", "content": json.dumps(claim)}]
    steps = []
    for route in llm.order("agent"):
        provider = llm.providers[route.provider]
        for step in range(MAX_STEPS):
            t0 = time.perf_counter()
            try:
                resp = await provider.complete(model=route.model, messages=msgs, tools=[t.spec for t in tools],
                                               meta={"task": "agent"})
            except Exception as e:  # provider failure: next route
                llm.store.record(ts=time.time(), trace_id=trace_id, task="agent", provider=route.provider,
                                 model=route.model, status="provider_error", attempt=step, latency_ms=0,
                                 input_tokens=0, output_tokens=0, cost_usd=0, error=str(e)[:200])
                break
            llm.store.record(ts=time.time(), trace_id=trace_id, task="agent", provider=route.provider,
                             model=route.model, status="ok", attempt=step,
                             latency_ms=(time.perf_counter() - t0) * 1000, input_tokens=resp.input_tokens,
                             output_tokens=resp.output_tokens, cost_usd=0, error=None)
            if not resp.tool_calls:
                try:
                    rec = TriageRecommendation.model_validate_json(resp.text)
                except Exception:
                    msgs.append({"role": "user", "content": "Return valid JSON only."})
                    continue
                verdict = authorize_agent_action(rec.route, int(claim.get("claim_amount", 0)))
                out = rec.model_dump() | {"trace_id": trace_id, "steps": steps, "model": route.model}
                if not verdict.ok:
                    out |= {"route": "adjuster", "guardrail": verdict.issues}
                return out
            msgs.append({"role": "assistant", "content": resp.text,
                         "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in resp.tool_calls]})
            for c in resp.tool_calls:
                tool = by_name.get(c.name)
                result = tool.fn(c.arguments) if tool else {"error": f"unknown tool {c.name}"}
                steps.append({"tool": c.name, "args": c.arguments})
                msgs.append({"role": "tool", "tool_call_id": c.id,
                             "content": json.dumps({"_tool": c.name, **result})})
    # no route produced a decision: safe default is a human
    return {"route": "adjuster", "confidence": 0.0, "rationale": "agent unavailable or inconclusive",
            "citations": [], "trace_id": trace_id, "steps": steps, "guardrail": ["fallback_to_human"]}
