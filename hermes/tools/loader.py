"""Loads available tool adapters based on configured credentials."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_tools(allowed_tools: Dict[str, List[str]]) -> Dict[str, Any]:
    """
    Instantiate adapters for every service listed in allowed_tools,
    but only if credentials are present. Missing creds → skipped with a warning.

    Returns a dict of service_name → adapter instance.
    """
    tools: Dict[str, Any] = {}
    missing: List[str] = []

    for service in allowed_tools:
        adapter = _try_load(service)
        if adapter is not None:
            tools[service] = adapter
        else:
            missing.append(service)

    if missing:
        print(f"\n  [tools] Not configured (skipped): {', '.join(missing)}")
        print("  Run 'hermes tools status' to see what's needed.\n")

    return tools


def _try_load(service: str) -> Optional[Any]:
    try:
        if service == "slack":
            if not os.environ.get("SLACK_BOT_TOKEN"):
                return None
            from hermes.tools.slack import SlackAdapter
            return SlackAdapter()

        if service == "gmail":
            secret = Path(os.environ.get("GMAIL_CLIENT_SECRET_PATH",
                          str(Path.home() / ".hermes" / "gmail_client_secret.json")))
            if not secret.exists():
                return None
            from hermes.tools.gmail import GmailAdapter
            return GmailAdapter()

        if service == "web":
            from hermes.tools.web import WebAdapter
            return WebAdapter()          # works without an API key

        if service == "calendar":
            return None                  # not yet implemented

    except Exception as e:
        print(f"  [tools] Failed to load '{service}': {e}")
        return None

    return None


def tool_definitions_for(tools: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect Claude tool definitions from all loaded adapters."""
    defs = []
    for adapter in tools.values():
        if hasattr(adapter, "tool_definitions"):
            defs.extend(adapter.tool_definitions())
    return defs


def dispatch_tool_call(tools: Dict[str, Any], tool_name: str, inputs: Dict[str, Any]) -> Any:
    """Route a Claude tool call to the right adapter."""
    prefix = tool_name.split("_")[0]   # e.g. "slack_post_message" → "slack"
    adapter = tools.get(prefix)
    if adapter is None:
        return {"error": f"Tool '{tool_name}' is not available — '{prefix}' adapter not loaded."}
    return adapter.call(tool_name, inputs)


def tools_status() -> Dict[str, Dict[str, Any]]:
    """Return the configuration status for all supported services."""
    from pathlib import Path as P

    slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
    gmail_secret = P(os.environ.get("GMAIL_CLIENT_SECRET_PATH",
                     str(P.home() / ".hermes" / "gmail_client_secret.json")))
    gmail_token  = P(os.environ.get("GMAIL_TOKEN_PATH",
                     str(P.home() / ".hermes" / "gmail_token.json")))
    serper_key   = os.environ.get("SERPER_API_KEY", "")
    anthropic_key= os.environ.get("ANTHROPIC_API_KEY", "")

    return {
        "slack": {
            "ready":  bool(slack_token),
            "detail": "SLACK_BOT_TOKEN set" if slack_token else
                      "Set SLACK_BOT_TOKEN=xoxb-... in .env\n"
                      "  Get one: https://api.slack.com/apps → OAuth & Permissions",
        },
        "gmail": {
            "ready":  gmail_secret.exists() and gmail_token.exists(),
            "detail": f"client_secret: {'✓' if gmail_secret.exists() else '✗ not found'}, "
                      f"token: {'✓' if gmail_token.exists() else '✗ run: hermes tools auth gmail'}",
        },
        "web": {
            "ready":  True,
            "detail": f"DuckDuckGo (no key needed)"
                      + (f" + Serper (key set)" if serper_key else
                         " — set SERPER_API_KEY for Google results"),
        },
        "claude": {
            "ready":  bool(anthropic_key),
            "detail": "ANTHROPIC_API_KEY set" if anthropic_key else
                      "Set ANTHROPIC_API_KEY=sk-ant-... in .env — required for real execution",
        },
    }
