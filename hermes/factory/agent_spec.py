"""AgentSpec: structured description of a generated agent."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class AgentSpec:
    name: str
    slug: str
    description: str
    agent_type: str
    user_request: str
    template_name: str
    allowed_tools: Dict[str, List[str]] = field(default_factory=dict)
    denied_tools: Dict[str, List[str]] = field(default_factory=dict)
    approval_required_for: List[str] = field(default_factory=list)
    enabled: bool = False
    schedule: Optional[str] = None
    version: str = "0.1.0"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "agent_type": self.agent_type,
            "user_request": self.user_request,
            "template_name": self.template_name,
            "enabled": self.enabled,
            "schedule": self.schedule,
            "version": self.version,
            "created_at": self.created_at,
            "allowed_tools": self.allowed_tools,
            "denied_tools": self.denied_tools,
            "approval_required_for": self.approval_required_for,
        }


def slugify(text: str) -> str:
    """Convert free text to a safe slug."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"_+", "_", text)
    return text[:60]


def name_from_request(request: str) -> str:
    """Derive a human-readable name from the user request."""
    words = request.split()
    # Drop leading verbs like Create/Make/Build
    if words and words[0].lower() in {"create", "make", "build", "generate", "write"}:
        words = words[1:]
    # Capitalise each word, keep first 8 words
    title = " ".join(w.capitalize() for w in words[:8])
    if not title.endswith("Agent"):
        title += " Agent"
    return title
