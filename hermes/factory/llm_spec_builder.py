"""
LLM-powered agent spec builder.

Uses the configured backend (LM Studio, Anthropic, etc.) to interpret a natural
language request and return a structured AgentSpec.  Falls back to the keyword
heuristic if no backend is configured or the LLM call fails.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .agent_spec import AgentSpec, slugify

_SYSTEM_PROMPT = """You are an agent factory assistant. Given a natural language request,
return a JSON object describing the agent to build. Be precise and conservative with permissions.

Rules:
- enabled must always be false
- Only grant tools that are explicitly needed
- Always deny dangerous operations (shell execute, file delete, package install, cloud deploy)
- Use one of these templates: briefing_agent, monitor_agent, research_agent

Respond with ONLY valid JSON, no markdown fences, no explanation. Schema:
{
  "name": "short human-readable name (max 8 words)",
  "description": "one sentence describing what this agent does",
  "template": "briefing_agent | monitor_agent | research_agent",
  "allowed_tools": {
    "service_name": ["op1", "op2"]
  },
  "denied_tools": {
    "service_name": ["op1", "op2"],
    "shell": ["execute", "run_command", "eval"],
    "filesystem": ["delete", "write_sensitive", "unlink", "rmtree"],
    "package_manager": ["install", "uninstall", "upgrade"],
    "cloud": ["deploy", "provision", "destroy"]
  },
  "approval_required_for": ["sending_email", "posting_to_external_services"]
}

Available services and their safe operations:
- gmail: search, read  (never grant: send, delete, modify_labels)
- slack: post_message, read_channel  (never grant: delete_message, create_channel, invite_users)
- web: fetch_url, search
- calendar: read_events  (never grant: create_event, delete_event, update_event)
- github: read_repo, list_issues, list_prs  (never grant: push_code, merge_pr, delete_branch)
- notion: read_page, read_database  (never grant: create_page, delete_page)
"""


def build_spec_with_llm(request: str) -> "Optional[Tuple[str, str, str, Dict, Dict, List]]":
    """
    Ask the LLM to interpret the request and return agent parameters.

    Returns (name, description, template, allowed_tools, denied_tools, approval_gates)
    or None if the LLM is unavailable or returns unparseable output.
    """
    try:
        from hermes.runtime.backends import load_backend
        backend, model_name = load_backend()
    except (RuntimeError, ImportError):
        return None

    messages = [{"role": "user", "content": f"Build an agent for this request: {request}"}]

    try:
        result = backend.chat(messages, [], _SYSTEM_PROMPT)
    except Exception:
        return None

    raw = "\n".join(result.text_parts).strip()
    if not raw:
        return None

    # Strip markdown code fences if the model added them anyway
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    raw = raw.strip()

    try:
        data: Dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        # Try extracting a JSON object from the middle of the response
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return None

    name     = str(data.get("name", ""))[:80] or None
    desc     = str(data.get("description", ""))[:300] or None
    template = data.get("template", "")
    if template not in ("briefing_agent", "monitor_agent", "research_agent"):
        template = "research_agent"

    allowed  = data.get("allowed_tools", {})
    denied   = data.get("denied_tools", {})
    gates    = data.get("approval_required_for", [])

    # Enforce hard denials regardless of what the LLM said
    for svc, ops in [
        ("shell",           ["execute", "run_command", "eval"]),
        ("filesystem",      ["delete", "write_sensitive", "unlink", "rmtree"]),
        ("package_manager", ["install", "uninstall", "upgrade"]),
        ("cloud",           ["deploy", "provision", "destroy"]),
    ]:
        denied.setdefault(svc, [])
        for op in ops:
            if op not in denied[svc]:
                denied[svc].append(op)

    if not name or not desc:
        return None

    return name, desc, template, allowed, denied, gates
