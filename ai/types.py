from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    raw: Any = None


# Messages use the OpenAI-style neutral shape: {"role": system|user|assistant|tool, "content": str,
# "tool_calls": [...], "tool_call_id": str}. Provider adapters translate.
Message = dict
