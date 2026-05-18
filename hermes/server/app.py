"""Hermes chat server — lets you talk to generated agents via a browser UI."""

from __future__ import annotations

import importlib.util
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent.parent.parent
REGISTRY_PATH = BASE_DIR / "registry.yaml"
AGENTS_DIR = BASE_DIR / "agents" / "generated"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Hermes Agent Factory", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── Models ──────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str           # "user" | "agent"
    content: str
    planned_actions: List[str] = []
    status: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    dry_run: bool = True


class ChatResponse(BaseModel):
    role: str = "agent"
    content: str
    planned_actions: List[str] = []
    status: str
    agent_slug: str


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _load_registry() -> Dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {}
    with open(REGISTRY_PATH) as f:
        data = yaml.safe_load(f) or {}
    return data.get("generated_agents", {})


def _load_agent_meta(slug: str) -> Dict[str, Any]:
    agent_dir = AGENTS_DIR / slug
    yaml_path = agent_dir / "agent.yaml"
    tools_path = agent_dir / "tools.yaml"

    meta: Dict[str, Any] = {}
    if yaml_path.exists():
        with open(yaml_path) as f:
            meta.update(yaml.safe_load(f) or {})
    if tools_path.exists():
        with open(tools_path) as f:
            meta["tools_config"] = yaml.safe_load(f) or {}

    prompt_path = agent_dir / "prompt.md"
    if prompt_path.exists():
        meta["prompt"] = prompt_path.read_text()

    return meta


def _run_workflow(slug: str, message: str, dry_run: bool) -> Dict[str, Any]:
    workflow_path = AGENTS_DIR / slug / "workflow.py"
    if not workflow_path.exists():
        raise FileNotFoundError(f"workflow.py not found for {slug}")

    spec = importlib.util.spec_from_file_location(f"agent_{slug}", workflow_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    context = {
        "dry_run": dry_run,
        "tools": {},
        "user_message": message,
    }
    return mod.run(context)


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    index = STATIC_DIR / "index.html"
    return FileResponse(str(index))


@app.get("/api/agents")
async def list_agents():
    registry = _load_registry()
    agents = []
    for slug, entry in registry.items():
        meta = _load_agent_meta(slug)
        agents.append({
            "slug": slug,
            "name": entry.get("name", slug),
            "enabled": entry.get("enabled", False),
            "template_name": entry.get("template_name", ""),
            "version": entry.get("version", ""),
            "description": meta.get("description", ""),
            "allowed_tools": meta.get("tools_config", {}).get("allowed_tools", {}),
            "denied_tools": meta.get("tools_config", {}).get("denied_tools", {}),
            "approval_required_for": meta.get("approval_required_for", []),
        })
    return {"agents": agents}


@app.get("/api/agents/{slug}")
async def get_agent(slug: str):
    registry = _load_registry()
    if slug not in registry:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    meta = _load_agent_meta(slug)
    entry = registry[slug]
    return {
        "slug": slug,
        **entry,
        **meta,
    }


@app.post("/api/agents/{slug}/chat", response_model=ChatResponse)
async def chat(slug: str, req: ChatRequest):
    registry = _load_registry()
    if slug not in registry:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")

    try:
        result = _run_workflow(slug, req.message, req.dry_run)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    status = result.get("status", "unknown")
    planned = result.get("planned_actions", [])
    message_out = result.get("message", "")

    if status == "dry_run":
        if planned:
            content = (
                f"**Dry run** — here's what I would do for: _{req.message}_\n\n"
                + "\n".join(f"• {a}" for a in planned)
                + f"\n\n_{message_out}_"
            )
        else:
            content = f"**Dry run** — no actions planned yet.\n\n_{message_out}_"
    elif status == "skipped":
        content = (
            f"I'm ready but need real tool adapters wired up to act on: _{req.message}_\n\n"
            f"_{message_out}_"
        )
    else:
        content = message_out or f"Status: {status}"

    return ChatResponse(
        content=content,
        planned_actions=planned,
        status=status,
        agent_slug=slug,
    )
