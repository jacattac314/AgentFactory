"""Gmail tool adapter.

Setup (one-time):
  1. Go to https://console.cloud.google.com
  2. Create a project → Enable Gmail API
  3. OAuth consent screen → Add yourself as test user
  4. Create credentials → OAuth 2.0 Client ID (Desktop app)
  5. Download JSON → save as ~/.hermes/gmail_client_secret.json
  6. Run: hermes tools auth gmail
     (opens browser, completes OAuth, saves token to ~/.hermes/gmail_token.json)

Or set env vars:
  GMAIL_CLIENT_SECRET_PATH=~/.hermes/gmail_client_secret.json
  GMAIL_TOKEN_PATH=~/.hermes/gmail_token.json
"""

from __future__ import annotations

import base64
import email as email_lib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",   # allowed: search, read
    # NOT including gmail.modify or gmail.send — least privilege
]

_DEFAULT_SECRET = Path.home() / ".hermes" / "gmail_client_secret.json"
_DEFAULT_TOKEN  = Path.home() / ".hermes" / "gmail_token.json"


class GmailAdapter:
    """Wraps the Gmail API with read-only access."""

    ALLOWED_OPS = {"search", "read"}

    def __init__(
        self,
        client_secret_path: Optional[str] = None,
        token_path: Optional[str] = None,
    ):
        secret = Path(client_secret_path or os.environ.get("GMAIL_CLIENT_SECRET_PATH", str(_DEFAULT_SECRET)))
        token  = Path(token_path         or os.environ.get("GMAIL_TOKEN_PATH",          str(_DEFAULT_TOKEN)))

        if not secret.exists():
            raise RuntimeError(
                f"Gmail client secret not found at {secret}.\n"
                "See hermes/tools/gmail.py docstring for setup instructions.\n"
                "Quick start: https://console.cloud.google.com → Gmail API → Desktop OAuth credentials"
            )

        self._service = self._build_service(secret, token)

    def _build_service(self, secret_path: Path, token_path: Path):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), _SCOPES)
                creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())

        return build("gmail", "v1", credentials=creds)

    # ── Allowed operations ────────────────────────────────────────────────────

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """Search Gmail messages. Returns list of {id, subject, from, date, snippet}."""
        try:
            result = self._service.users().messages().list(
                userId="me", q=query, maxResults=max_results
            ).execute()
            messages = result.get("messages", [])
            out = []
            for m in messages[:max_results]:
                meta = self._get_metadata(m["id"])
                out.append(meta)
            return out
        except Exception as e:
            return [{"error": str(e)}]

    def read(self, message_id: str) -> Dict[str, Any]:
        """Read a Gmail message by ID. Returns {id, subject, from, date, body}."""
        try:
            msg = self._service.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()
            return self._parse_message(msg)
        except Exception as e:
            return {"error": str(e)}

    # ── Denied operations ─────────────────────────────────────────────────────

    def send(self, *_, **__):
        raise PermissionError("gmail.send is not permitted. Requires explicit human approval gate.")

    def delete(self, *_, **__):
        raise PermissionError("gmail.delete is not permitted for generated agents.")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_metadata(self, message_id: str) -> Dict[str, Any]:
        msg = self._service.users().messages().get(
            userId="me", id=message_id, format="metadata",
            metadataHeaders=["Subject", "From", "Date"]
        ).execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        return {
            "id":      message_id,
            "subject": headers.get("Subject", "(no subject)"),
            "from":    headers.get("From", ""),
            "date":    headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
        }

    def _parse_message(self, msg: dict) -> Dict[str, Any]:
        payload = msg.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        body = self._extract_body(payload)
        return {
            "id":      msg["id"],
            "subject": headers.get("Subject", "(no subject)"),
            "from":    headers.get("From", ""),
            "date":    headers.get("Date", ""),
            "body":    body[:4000],  # cap to avoid token overflow
        }

    def _extract_body(self, payload: dict) -> str:
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return ""

    # ── Metadata ──────────────────────────────────────────────────────────────

    @staticmethod
    def tool_definitions() -> List[Dict[str, Any]]:
        return [
            {
                "name": "gmail_search",
                "description": "Search Gmail messages using a query string (Gmail search syntax supported).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query":       {"type": "string", "description": "Gmail search query, e.g. 'from:recruiter@company.com newer_than:1d'"},
                        "max_results": {"type": "integer", "description": "Max messages to return (default 10)", "default": 10},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "gmail_read",
                "description": "Read the full content of a Gmail message by its ID.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string", "description": "Gmail message ID from a search result"},
                    },
                    "required": ["message_id"],
                },
            },
        ]

    def call(self, tool_name: str, inputs: Dict[str, Any]) -> Any:
        if tool_name == "gmail_search":
            return self.search(**inputs)
        if tool_name == "gmail_read":
            return self.read(**inputs)
        raise ValueError(f"Unknown Gmail tool: {tool_name}")
