"""LLMClient: model routing with fallbacks, structured outputs with repair, tracing."""
import json
import os
import time
import uuid
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ValidationError

from .observe import TraceStore, cost
from .providers import AnthropicProvider, MockProvider, OpenAICompatProvider


@dataclass
class Route:
    provider: str
    model: str
    quality: float = 0.8      # latest eval score for this task (see evals/); refreshed by run_evals
    max_latency_ms: float = 30000


def default_routes() -> dict[str, list[Route]]:
    """Task -> ordered candidates. Cheap/fast model first for easy tasks, strong model first for hard ones;
    each list ends with a different provider for reliability. Deployment names come from the environment."""
    az_small, az_large = os.environ.get("AZURE_DEPLOY_SMALL"), os.environ.get("AZURE_DEPLOY_LARGE")
    claude = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    if os.environ.get("AZURE_OPENAI_ENDPOINT") and az_small and az_large:
        r = {"classify": [Route("azure", az_small), Route("azure", az_large)],
             "summarize": [Route("azure", az_small), Route("azure", az_large)],
             "extract": [Route("azure", az_large), Route("anthropic", claude)],
             "agent": [Route("azure", az_large), Route("anthropic", claude)]}
        if not os.environ.get("ANTHROPIC_API_KEY"):
            r = {k: [x for x in v if x.provider != "anthropic"] for k, v in r.items()}
        return r
    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("AI_PROVIDER") != "mock":
        return {t: [Route("anthropic", claude)] for t in ("classify", "summarize", "extract", "agent")}
    return {t: [Route("mock", "mock-heuristic-1")] for t in ("classify", "summarize", "extract", "agent")}


def build_providers():
    p = {"mock": MockProvider()}
    if os.environ.get("AZURE_OPENAI_ENDPOINT"):
        p["azure"] = OpenAICompatProvider("azure", os.environ["AZURE_OPENAI_ENDPOINT"],
                                          os.environ.get("AZURE_OPENAI_API_KEY"),
                                          os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"))
    if os.environ.get("OPENAI_API_KEY"):
        p["openai"] = OpenAICompatProvider("openai")
    if os.environ.get("ANTHROPIC_API_KEY"):
        p["anthropic"] = AnthropicProvider()
    return p


class LLMClient:
    def __init__(self, providers=None, routes=None, store: TraceStore | None = None):
        self.providers = providers or build_providers()
        self.routes = routes or default_routes()
        self.store = store or TraceStore()

    def order(self, task, weights=(0.5, 0.3, 0.2)):
        """Rank candidates by weighted quality / observed p95 latency / cost from traces. With <5 calls of
        history a model keeps its configured order (stable until there's evidence)."""
        routes = self.routes[task]
        stats = {(s["task"], s["model"]): s for s in self.store.summary()}
        if any(stats.get((task, r.model), {}).get("calls", 0) < 5 for r in routes):
            return routes
        wq, wl, wc = weights
        def score(r):
            s = stats[(task, r.model)]
            lat = 1 - min(s["p95_ms"] / r.max_latency_ms, 1)
            cheap = 1 - min(s["cost_usd"] / max(s["calls"], 1) * 100, 1)
            return wq * r.quality * (1 - s["error_rate"]) + wl * lat + wc * cheap
        return sorted(routes, key=score, reverse=True)

    async def _call(self, task, route, trace_id, attempt, **kw):
        p = self.providers[route.provider]
        t0, status, err, resp = time.perf_counter(), "ok", None, None
        try:
            resp = await p.complete(model=route.model, meta={"task": task, **kw.pop("meta", {})}, **kw)
        except (httpx.HTTPError, TimeoutError, KeyError, json.JSONDecodeError) as e:
            status, err = "provider_error", f"{type(e).__name__}: {e}"
        ms = (time.perf_counter() - t0) * 1000
        tin, tout = (resp.input_tokens, resp.output_tokens) if resp else (0, 0)
        self.store.record(ts=time.time(), trace_id=trace_id, task=task, provider=route.provider,
                          model=route.model, status=status, attempt=attempt, latency_ms=ms,
                          input_tokens=tin, output_tokens=tout, cost_usd=cost(route.model, tin, tout), error=err)
        return resp

    async def structured(self, task: str, messages: list[dict], schema: type[BaseModel], meta=None,
                         max_tokens=1024) -> tuple[BaseModel, dict]:
        """Call routes in order; validate against the Pydantic schema; one repair retry per route."""
        trace_id, last_err = uuid.uuid4().hex[:12], "no route"
        js = schema.model_json_schema()
        for route in self.order(task):
            msgs, attempt = list(messages), 0
            for attempt in (1, 2):
                resp = await self._call(task, route, trace_id, attempt, messages=msgs, json_schema=js,
                                        max_tokens=max_tokens, meta=meta or {})
                if resp is None:
                    last_err = "provider_error"
                    break
                try:
                    txt = resp.text.strip().removeprefix("```json").removesuffix("```").strip()
                    return schema.model_validate_json(txt), {"trace_id": trace_id, "model": route.model,
                                                            "provider": route.provider, "attempts": attempt}
                except ValidationError as e:
                    last_err = f"schema: {e.errors()[:2]}"
                    self.store.record(ts=time.time(), trace_id=trace_id, task=task, provider=route.provider,
                                      model=route.model, status="schema_invalid", attempt=attempt, latency_ms=0,
                                      input_tokens=0, output_tokens=0, cost_usd=0, error=last_err[:200])
                    msgs = msgs + [{"role": "assistant", "content": resp.text},
                                   {"role": "user", "content": f"Invalid output: {last_err}. Return corrected JSON only."}]
        raise RuntimeError(f"all routes failed for {task}: {last_err}")
