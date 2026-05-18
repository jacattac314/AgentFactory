"""Manages registry.yaml for generated agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from .agent_spec import AgentSpec


def load_registry(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"generated_agents": {}}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("generated_agents", {})
    return data


def save_registry(path: Path, spec: AgentSpec) -> None:
    registry = load_registry(path)
    registry["generated_agents"][spec.slug] = {
        "name": spec.name,
        "path": f"agents/generated/{spec.slug}",
        "enabled": spec.enabled,
        "template_name": spec.template_name,
        "version": spec.version,
        "created_at": spec.created_at,
    }
    with open(path, "w") as f:
        yaml.dump(registry, f, default_flow_style=False, sort_keys=False)
    print(f"  [registry] Updated registry.yaml → {spec.slug}")
