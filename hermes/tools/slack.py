"""Slack tool adapter.

Required env var:  SLACK_BOT_TOKEN=xoxb-...
Optional:          SLACK_DEFAULT_CHANNEL=#general

Bot needs scopes:  chat:write, channels:history, channels:read
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


class SlackAdapter:
    """Wraps the Slack Web API with only the operations agents are permitted to use."""

    ALLOWED_OPS = {"post_message", "read_channel"}

    def __init__(self, token: Optional[str] = None):
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError

        self._token = token or os.environ.get("SLACK_BOT_TOKEN", "")
        if not self._token:
            raise RuntimeError(
                "Slack bot token not found. Set SLACK_BOT_TOKEN in your environment "
                "or .env file.\n"
                "Get one at: https://api.slack.com/apps → OAuth & Permissions → Bot Token"
            )
        self._client = WebClient(token=self._token)
        self._SlackApiError = SlackApiError

    # ── Allowed operations ────────────────────────────────────────────────────

    def post_message(self, channel: str, text: str, blocks: Optional[list] = None) -> Dict[str, Any]:
        """Post a message to a Slack channel. Returns the API response."""
        try:
            kwargs: Dict[str, Any] = {"channel": channel, "text": text}
            if blocks:
                kwargs["blocks"] = blocks
            resp = self._client.chat_postMessage(**kwargs)
            return {"ok": True, "ts": resp["ts"], "channel": resp["channel"]}
        except self._SlackApiError as e:
            return {"ok": False, "error": str(e.response["error"])}

    def read_channel(self, channel: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Read recent messages from a channel."""
        try:
            resp = self._client.conversations_history(channel=channel, limit=limit)
            return [
                {
                    "ts": m.get("ts"),
                    "user": m.get("user", "unknown"),
                    "text": m.get("text", ""),
                    "type": m.get("type"),
                }
                for m in resp.get("messages", [])
            ]
        except self._SlackApiError as e:
            return [{"error": str(e.response["error"])}]

    # ── Denied operations (explicit guards) ───────────────────────────────────

    def delete_message(self, *_, **__):
        raise PermissionError("slack.delete_message is not permitted for generated agents.")

    def invite_users(self, *_, **__):
        raise PermissionError("slack.invite_users is not permitted for generated agents.")

    def create_channel(self, *_, **__):
        raise PermissionError("slack.create_channel is not permitted for generated agents.")

    # ── Metadata ──────────────────────────────────────────────────────────────

    @staticmethod
    def tool_definitions() -> List[Dict[str, Any]]:
        """Claude tool definitions for this adapter."""
        return [
            {
                "name": "slack_post_message",
                "description": "Post a message to a Slack channel.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "description": "Channel name or ID, e.g. #general"},
                        "text":    {"type": "string", "description": "Message text (markdown supported)"},
                    },
                    "required": ["channel", "text"],
                },
            },
            {
                "name": "slack_read_channel",
                "description": "Read recent messages from a Slack channel.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "description": "Channel name or ID"},
                        "limit":   {"type": "integer", "description": "Max messages to fetch (default 20)", "default": 20},
                    },
                    "required": ["channel"],
                },
            },
        ]

    def call(self, tool_name: str, inputs: Dict[str, Any]) -> Any:
        """Dispatch a Claude tool call by name."""
        if tool_name == "slack_post_message":
            return self.post_message(**inputs)
        if tool_name == "slack_read_channel":
            return self.read_channel(**inputs)
        raise ValueError(f"Unknown Slack tool: {tool_name}")
