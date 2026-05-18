"""
Agent executor — runs a Hermes agent using a configurable LLM backend + real tool adapters.

Architecture:
  1. Load agent.yaml + prompt.md + tools.yaml
  2. Instantiate available tool adapters
  3. Send task to the configured LLM with tool definitions
  4. LLM calls tools in a loop until done
  5. Return structured result + full execution log

Backend selection (env vars):
  HERMES_BACKEND=openai        LM Studio / Ollama / vLLM (OpenAI-compatible)
  HERMES_API_BASE=http://localhost:1234/v1
  HERMES_MODEL=llama3.2
  -- or --
  ANTHROPIC_API_KEY=sk-ant-... Use Claude (default when no other config present)
"""

from __future__ import annotations

import json
import os
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class RunResult:
    agent_slug:  str
    status:      str           # "completed" | "error" | "dry_run"
    output:      str
    tool_calls:  List[Dict]    = field(default_factory=list)
    error:       Optional[str] = None
    started_at:  str           = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: Optional[str] = None
    model:       str           = ""


def run_agent(
    slug: str,
    task: Optional[str] = None,
    dry_run: bool = False,
    base_dir: Optional[Path] = None,
    stream: bool = True,
) -> RunResult:
    """
    Execute a Hermes agent.

    Args:
        slug:     Agent slug (must exist in agents/generated/)
        task:     Optional override task. Defaults to agent description.
        dry_run:  If True, skip all real tool calls and return planned actions.
        base_dir: Project root. Defaults to cwd.
        stream:   Print progress to stdout as it runs.
    """
    base_dir  = base_dir or Path.cwd()
    agent_dir = base_dir / "agents" / "generated" / slug

    if not agent_dir.exists():
        return RunResult(slug, "error", "", error=f"Agent directory not found: {agent_dir}")

    with open(agent_dir / "agent.yaml") as f:
        agent_cfg = yaml.safe_load(f)
    with open(agent_dir / "tools.yaml") as f:
        tools_cfg = yaml.safe_load(f)

    prompt_text   = (agent_dir / "prompt.md").read_text()
    allowed_tools = tools_cfg.get("allowed_tools", {})
    task          = task or agent_cfg.get("description", "Run your configured task.")

    if dry_run:
        return _dry_run(slug, task, allowed_tools)

    # ── Load backend ──────────────────────────────────────────────────────────
    try:
        from hermes.runtime.backends import load_backend
        backend, model_name = load_backend()
    except RuntimeError as e:
        return RunResult(slug, "error", "", error=str(e))
    except ImportError as e:
        return RunResult(slug, "error", "", error=f"Missing dependency: {e}")

    # ── Load tool adapters ────────────────────────────────────────────────────
    from hermes.tools.loader import dispatch_tool_call, load_tools, tool_definitions_for
    tools    = load_tools(allowed_tools)
    tool_defs = tool_definitions_for(tools)

    if stream:
        _print_header(slug, task, tools, model_name)

    # ── Agent loop ────────────────────────────────────────────────────────────
    messages: List[Dict[str, Any]] = [{"role": "user", "content": task}]
    tool_call_log: List[Dict] = []
    max_turns = 10

    for _turn in range(max_turns):
        try:
            turn_result = backend.chat(messages, tool_defs, prompt_text)
        except Exception as e:
            return RunResult(slug, "error", "", error=f"LLM call failed: {e}",
                             model=model_name)

        if turn_result.text_parts and stream:
            for t in turn_result.text_parts:
                print(f"\n  [llm]    {t[:500]}" + ("…" if len(t) > 500 else ""))

        # Done — no more tool calls
        if turn_result.stop_reason in ("end_turn", "max_tokens") and not turn_result.tool_calls:
            final_text = "\n".join(turn_result.text_parts) or "(no text output)"
            result = RunResult(
                agent_slug=slug,
                status="completed",
                output=final_text,
                tool_calls=tool_call_log,
                finished_at=datetime.now(timezone.utc).isoformat(),
                model=model_name,
            )
            if stream:
                _print_footer(result)
            _save_run(agent_dir, result)
            return result

        # Execute tool calls
        results: List[Any] = []
        for tc in turn_result.tool_calls:
            if stream:
                print(f"\n  [tool]   {tc.name}({json.dumps(tc.input, separators=(',',':'))[:120]})")

            prefix = tc.name.split("_")[0]
            denied = tools_cfg.get("denied_tools", {}).get(prefix, [])
            op     = "_".join(tc.name.split("_")[1:])

            if op in (denied or []):
                result_content: Any = {"error": f"Denied: {tc.name} is in denied_tools for this agent."}
            elif prefix not in tools:
                result_content = {"error": f"Tool '{prefix}' not configured. Set credentials first."}
            else:
                try:
                    result_content = dispatch_tool_call(tools, tc.name, tc.input)
                except Exception as e:
                    result_content = {"error": str(e)}

            if stream:
                preview = str(result_content)[:200]
                print(f"  [result] {preview}" + ("…" if len(str(result_content)) > 200 else ""))

            tool_call_log.append({"tool": tc.name, "input": tc.input, "result": result_content})
            results.append(result_content)

        # Append assistant turn + tool results (format differs per backend)
        messages.append(backend.build_assistant_message(turn_result))
        if hasattr(backend, "build_tool_result_messages"):
            messages.extend(backend.build_tool_result_messages(turn_result, results))
        else:
            messages.append(backend.build_tool_results_message(turn_result, results))

    return RunResult(slug, "error", "", error=f"Exceeded {max_turns} turns without finishing.",
                     model=model_name)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dry_run(slug: str, task: str, allowed_tools: Dict) -> RunResult:
    planned = [f"{svc}.{op}" for svc, ops in allowed_tools.items() for op in (ops or [])]
    output = (
        f"DRY RUN — task: {task}\n"
        f"Would use tools: {', '.join(planned) or 'none configured'}\n"
        "No external actions were performed."
    )
    return RunResult(slug, "dry_run", output, tool_calls=[])


def _print_header(slug: str, task: str, tools: Dict, model: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  Agent:   {slug}")
    print(f"  Model:   {model}")
    print(f"  Task:    {textwrap.shorten(task, 80)}")
    print(f"  Tools:   {', '.join(tools.keys()) or 'none'}")
    print(f"{'─'*60}")


def _print_footer(result: RunResult) -> None:
    print(f"\n{'─'*60}")
    print(f"  Status:     {result.status}")
    print(f"  Tool calls: {len(result.tool_calls)}")
    print(f"  Output:     {textwrap.shorten(result.output, 200)}")
    print(f"{'─'*60}\n")


def _save_run(agent_dir: Path, result: RunResult) -> None:
    runs_dir = agent_dir / "runs"
    runs_dir.mkdir(exist_ok=True)
    ts       = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_file = runs_dir / f"{ts}.json"
    with open(run_file, "w") as f:
        json.dump({
            "status":      result.status,
            "output":      result.output,
            "tool_calls":  result.tool_calls,
            "started_at":  result.started_at,
            "finished_at": result.finished_at,
            "model":       result.model,
        }, f, indent=2)
