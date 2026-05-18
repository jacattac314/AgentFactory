"""
Agent executor — runs a Hermes agent using Claude + real tool adapters.

Architecture:
  1. Load agent.yaml + prompt.md + tools.yaml
  2. Instantiate available tool adapters
  3. Send task to Claude claude-sonnet-4-6 with tool definitions
  4. Claude calls tools in a loop until done
  5. Return structured result + full execution log

Requires ANTHROPIC_API_KEY in environment.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class RunResult:
    agent_slug:   str
    status:       str          # "completed" | "error" | "dry_run"
    output:       str          # final answer / summary
    tool_calls:   List[Dict]   = field(default_factory=list)
    error:        Optional[str] = None
    started_at:   str          = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at:  Optional[str] = None
    model:        str          = "claude-sonnet-4-6"


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
    base_dir = base_dir or Path.cwd()
    agent_dir = base_dir / "agents" / "generated" / slug

    if not agent_dir.exists():
        return RunResult(slug, "error", "", error=f"Agent directory not found: {agent_dir}")

    # ── Load agent config ─────────────────────────────────────────────────────
    with open(agent_dir / "agent.yaml") as f:
        agent_cfg = yaml.safe_load(f)

    with open(agent_dir / "tools.yaml") as f:
        tools_cfg = yaml.safe_load(f)

    prompt_text = (agent_dir / "prompt.md").read_text()
    allowed_tools = tools_cfg.get("allowed_tools", {})
    task = task or agent_cfg.get("description", "Run your configured task.")

    if dry_run:
        return _dry_run(slug, task, allowed_tools)

    # ── Check API key ─────────────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return RunResult(
            slug, "error", "",
            error="ANTHROPIC_API_KEY not set. Add it to your .env file."
        )

    # ── Load tool adapters ────────────────────────────────────────────────────
    from hermes.tools.loader import dispatch_tool_call, load_tools, tool_definitions_for
    tools = load_tools(allowed_tools)
    tool_defs = tool_definitions_for(tools)

    if stream:
        _print_header(slug, task, tools)

    # ── Claude agent loop ─────────────────────────────────────────────────────
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    messages = [{"role": "user", "content": task}]
    tool_call_log: List[Dict] = []
    max_turns = 10

    for turn in range(max_turns):
        kwargs: Dict[str, Any] = {
            "model":      "claude-sonnet-4-6",
            "max_tokens": 4096,
            "system":     prompt_text,
            "messages":   messages,
        }
        if tool_defs:
            kwargs["tools"] = tool_defs

        response = client.messages.create(**kwargs)

        # Collect text content
        text_parts = [b.text for b in response.content if b.type == "text"]
        tool_uses  = [b for b in response.content if b.type == "tool_use"]

        if text_parts and stream:
            for t in text_parts:
                print(f"\n  [claude] {t[:500]}" + ("…" if len(t) > 500 else ""))

        # Done — no more tool calls
        if response.stop_reason in ("end_turn", "max_tokens") and not tool_uses:
            final_text = "\n".join(text_parts) or "(no text output)"
            result = RunResult(
                agent_slug=slug,
                status="completed",
                output=final_text,
                tool_calls=tool_call_log,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            if stream:
                _print_footer(result)
            _save_run(agent_dir, result)
            return result

        # Execute tool calls
        tool_results = []
        for tu in tool_uses:
            if stream:
                print(f"\n  [tool]   {tu.name}({json.dumps(tu.input, separators=(',',':'))[:120]})")

            # Safety check — never call denied tools
            prefix = tu.name.split("_")[0]
            denied = tools_cfg.get("denied_tools", {}).get(prefix, [])
            op     = "_".join(tu.name.split("_")[1:])
            if op in (denied or []):
                result_content = {"error": f"Denied: {tu.name} is in the denied_tools list for this agent."}
            elif prefix not in tools:
                result_content = {"error": f"Tool '{prefix}' is not configured. Set credentials first."}
            else:
                try:
                    result_content = dispatch_tool_call(tools, tu.name, tu.input)
                except Exception as e:
                    result_content = {"error": str(e)}

            if stream:
                preview = str(result_content)[:200]
                print(f"  [result] {preview}" + ("…" if len(str(result_content)) > 200 else ""))

            tool_call_log.append({"tool": tu.name, "input": tu.input, "result": result_content})
            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": tu.id,
                "content":     json.dumps(result_content),
            })

        # Add assistant + tool results to conversation
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user",      "content": tool_results})

    return RunResult(slug, "error", "", error=f"Exceeded {max_turns} turns without finishing.")


def _dry_run(slug: str, task: str, allowed_tools: Dict) -> RunResult:
    planned = []
    for service, ops in allowed_tools.items():
        for op in (ops or []):
            planned.append(f"{service}.{op}")
    output = (
        f"DRY RUN — task: {task}\n"
        f"Would use tools: {', '.join(planned) or 'none configured'}\n"
        "No external actions were performed."
    )
    return RunResult(slug, "dry_run", output, tool_calls=[])


def _print_header(slug: str, task: str, tools: Dict) -> None:
    print(f"\n{'─'*60}")
    print(f"  Running agent: {slug}")
    print(f"  Task:          {textwrap.shorten(task, 80)}")
    print(f"  Tools loaded:  {', '.join(tools.keys()) or 'none'}")
    print(f"{'─'*60}")


def _print_footer(result: RunResult) -> None:
    print(f"\n{'─'*60}")
    print(f"  Status:    {result.status}")
    print(f"  Tool calls:{len(result.tool_calls)}")
    print(f"  Output:    {textwrap.shorten(result.output, 200)}")
    print(f"{'─'*60}\n")


def _save_run(agent_dir: Path, result: RunResult) -> None:
    runs_dir = agent_dir / "runs"
    runs_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_file = runs_dir / f"{ts}.json"
    with open(run_file, "w") as f:
        json.dump({
            "status":     result.status,
            "output":     result.output,
            "tool_calls": result.tool_calls,
            "started_at": result.started_at,
            "finished_at":result.finished_at,
            "model":      result.model,
        }, f, indent=2)
