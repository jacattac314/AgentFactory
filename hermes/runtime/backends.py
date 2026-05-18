"""
LLM backend abstraction — supports Anthropic and OpenAI-compatible APIs.

Select backend via env vars:
  HERMES_BACKEND=openai        # use OpenAI-compatible API (LM Studio, Ollama, vLLM…)
  HERMES_API_BASE=http://localhost:1234/v1
  HERMES_MODEL=llama3.2        # model name to pass to the API
  HERMES_API_KEY=lm-studio     # dummy key for LM Studio (it ignores the value)

  ANTHROPIC_API_KEY=sk-ant-... # if set and HERMES_BACKEND not set → Anthropic

Auto-detection order:
  1. HERMES_BACKEND env var (explicit override)
  2. HERMES_API_BASE set → openai-compat
  3. ANTHROPIC_API_KEY set → anthropic
  4. Error
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    id: str
    name: str
    input: Dict[str, Any]


@dataclass
class TurnResult:
    text_parts: List[str]
    tool_calls: List[ToolCall]
    stop_reason: str       # "end_turn" | "tool_use" | "max_tokens"
    _raw: Any = field(repr=False, default=None)


# ── Anthropic backend ─────────────────────────────────────────────────────────

class AnthropicBackend:
    """Wraps the Anthropic client."""

    def __init__(self, api_key: str, model: str):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def chat(
        self,
        messages: List[Dict],
        tool_defs: List[Dict],
        system: str,
    ) -> TurnResult:
        kwargs: Dict[str, Any] = {
            "model":      self.model,
            "max_tokens": 4096,
            "system":     system,
            "messages":   messages,
        }
        if tool_defs:
            kwargs["tools"] = tool_defs

        resp = self.client.messages.create(**kwargs)
        text_parts = [b.text for b in resp.content if b.type == "text"]
        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=b.input)
            for b in resp.content if b.type == "tool_use"
        ]
        stop = "tool_use" if tool_calls else resp.stop_reason or "end_turn"
        return TurnResult(text_parts, tool_calls, stop, _raw=resp.content)

    def build_assistant_message(self, turn: TurnResult) -> Dict:
        return {"role": "assistant", "content": turn._raw}

    def build_tool_results_message(self, turn: TurnResult, results: List[Any]) -> Dict:
        """results: list of result_content dicts in the same order as turn.tool_calls."""
        return {
            "role": "user",
            "content": [
                {
                    "type":        "tool_result",
                    "tool_use_id": tc.id,
                    "content":     json.dumps(r),
                }
                for tc, r in zip(turn.tool_calls, results)
            ],
        }


# ── OpenAI-compatible backend ─────────────────────────────────────────────────

class OpenAICompatBackend:
    """Wraps the OpenAI client pointed at a local server (LM Studio, Ollama, vLLM)."""

    def __init__(self, api_base: str, model: str, api_key: str = "lm-studio"):
        from openai import OpenAI
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model

    def _convert_tool_defs(self, tool_defs: List[Dict]) -> List[Dict]:
        """Convert Anthropic tool format → OpenAI function-calling format."""
        out = []
        for t in tool_defs:
            out.append({
                "type": "function",
                "function": {
                    "name":        t["name"],
                    "description": t.get("description", ""),
                    "parameters":  t.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
        return out

    def chat(
        self,
        messages: List[Dict],
        tool_defs: List[Dict],
        system: str,
    ) -> TurnResult:
        oai_messages = [{"role": "system", "content": system}] + messages

        kwargs: Dict[str, Any] = {
            "model":      self.model,
            "max_tokens": 4096,
            "messages":   oai_messages,
        }
        if tool_defs:
            kwargs["tools"] = self._convert_tool_defs(tool_defs)
            kwargs["tool_choice"] = "auto"

        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message

        text_parts = [msg.content] if msg.content else []
        tool_calls: List[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    inp = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    inp = {"_raw": tc.function.arguments}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=inp))

        stop = "tool_use" if tool_calls else (
            "max_tokens" if choice.finish_reason == "length" else "end_turn"
        )
        return TurnResult(text_parts, tool_calls, stop, _raw=(msg, tool_calls))

    def build_assistant_message(self, turn: TurnResult) -> Dict:
        msg, tcs = turn._raw
        d: Dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if tcs:
            d["tool_calls"] = [
                {
                    "id":       tc.id,
                    "type":     "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                }
                for tc in tcs
            ]
        return d

    def build_tool_results_message(self, turn: TurnResult, results: List[Any]) -> Dict:
        raise NotImplementedError  # OpenAI uses one message per tool result

    def build_tool_result_messages(self, turn: TurnResult, results: List[Any]) -> List[Dict]:
        return [
            {
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      json.dumps(r),
            }
            for tc, r in zip(turn.tool_calls, results)
        ]


# ── Factory ───────────────────────────────────────────────────────────────────

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL    = "llama3.2"


def load_backend() -> "tuple[Any, str]":
    """
    Return (backend_instance, model_name) based on environment.

    Raises RuntimeError if no usable backend is configured.
    """
    explicit    = os.environ.get("HERMES_BACKEND", "").lower()
    api_base    = os.environ.get("HERMES_API_BASE", "")
    model_override = os.environ.get("HERMES_MODEL", "")
    anthropic_key  = os.environ.get("ANTHROPIC_API_KEY", "")

    use_openai = (explicit == "openai") or (not explicit and bool(api_base))
    use_anthropic = (explicit == "anthropic") or (not explicit and not api_base and bool(anthropic_key))

    if use_openai:
        base  = api_base or "http://localhost:1234/v1"
        model = model_override or DEFAULT_OPENAI_MODEL
        key   = os.environ.get("HERMES_API_KEY", "lm-studio")
        return OpenAICompatBackend(api_base=base, model=model, api_key=key), model

    if use_anthropic:
        model = model_override or DEFAULT_ANTHROPIC_MODEL
        return AnthropicBackend(api_key=anthropic_key, model=model), model

    raise RuntimeError(
        "No LLM backend configured.\n"
        "  • For LM Studio:  set HERMES_API_BASE=http://localhost:1234/v1 and HERMES_MODEL=<model>\n"
        "  • For Anthropic:  set ANTHROPIC_API_KEY=sk-ant-...\n"
        "  See .env.example for details."
    )
