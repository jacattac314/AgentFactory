"""Hermes Agent Factory server — three-panel dashboard + API."""

from __future__ import annotations

import importlib.util
import json
import queue
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
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


class CreateAgentRequest(BaseModel):
    request: str
    force: bool = False


class PreviewRequest(BaseModel):
    request: str


class EnableDisableResponse(BaseModel):
    slug: str
    enabled: bool
    ok: bool
    message: str = ""


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


def _set_agent_enabled(slug: str, enabled: bool) -> bool:
    """Update agent.yaml and registry.yaml enabled flag. Returns True on success."""
    agent_dir = AGENTS_DIR / slug
    agent_yaml_path = agent_dir / "agent.yaml"
    if not agent_yaml_path.exists():
        return False

    with open(agent_yaml_path) as f:
        data = yaml.safe_load(f) or {}
    data["enabled"] = enabled
    with open(agent_yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    if REGISTRY_PATH.exists():
        with open(REGISTRY_PATH) as f:
            registry = yaml.safe_load(f) or {}
        agents = registry.get("generated_agents", {})
        if slug in agents:
            agents[slug]["enabled"] = enabled
            registry["generated_agents"] = agents
            with open(REGISTRY_PATH, "w") as f:
                yaml.dump(registry, f, default_flow_style=False, sort_keys=False)

    return True


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    index = STATIC_DIR / "index.html"
    return FileResponse(str(index))


@app.get("/api/status")
async def get_status():
    """Return LLM readiness + tools status."""
    from hermes.tools.loader import tools_status
    status = tools_status()
    return status


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


@app.post("/api/agents")
async def create_agent_endpoint(req: CreateAgentRequest):
    """Create a new agent from a natural language request."""
    from hermes.factory.factory_service import FactoryError, create_agent
    try:
        spec = create_agent(req.request, force=req.force, base_dir=BASE_DIR)
        return {
            "ok": True,
            "slug": spec.slug,
            "name": spec.name,
            "template_name": spec.template_name,
            "allowed_tools": spec.allowed_tools,
            "denied_tools": spec.denied_tools,
            "description": spec.description,
        }
    except FactoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/factory/preview")
async def preview_spec(req: PreviewRequest):
    """Preview agent spec without writing any files."""
    from hermes.factory.llm_spec_builder import build_spec_with_llm
    from hermes.factory.template_selector import default_approval_gates, map_tools, select_template
    from hermes.factory.agent_spec import name_from_request, slugify

    llm_result = build_spec_with_llm(req.request)
    if llm_result:
        name, description, template_name, allowed, denied, approval_gates = llm_result
    else:
        template_name = select_template(req.request)
        allowed, denied = map_tools(req.request)
        approval_gates = default_approval_gates()
        name = name_from_request(req.request)
        description = f"Auto-generated agent: {req.request[:120]}"

    slug = slugify(name)
    return {
        "name": name,
        "slug": slug,
        "description": description,
        "template_name": template_name,
        "allowed_tools": allowed,
        "denied_tools": denied,
        "approval_required_for": approval_gates,
    }


@app.post("/api/agents/{slug}/enable")
async def enable_agent(slug: str):
    registry = _load_registry()
    if slug not in registry:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    ok = _set_agent_enabled(slug, True)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to enable agent")
    return EnableDisableResponse(slug=slug, enabled=True, ok=True, message="Agent enabled.")


@app.post("/api/agents/{slug}/disable")
async def disable_agent(slug: str):
    registry = _load_registry()
    if slug not in registry:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    ok = _set_agent_enabled(slug, False)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to disable agent")
    return EnableDisableResponse(slug=slug, enabled=False, ok=True, message="Agent disabled.")


@app.get("/api/agents/{slug}/runs")
async def list_runs(slug: str):
    """List past run JSON files for the agent, newest first."""
    runs_dir = AGENTS_DIR / slug / "runs"
    if not runs_dir.exists():
        return {"runs": []}

    run_files = sorted(runs_dir.glob("*.json"), reverse=True)
    runs = []
    for f in run_files:
        try:
            with open(f) as fh:
                data = json.load(fh)
            data["id"] = f.name
            runs.append(data)
        except Exception:
            pass
    return {"runs": runs}


@app.get("/api/agents/{slug}/run/stream")
async def run_stream(slug: str, task: str = "", dry_run: bool = False):
    """SSE endpoint that streams run_agent log events."""
    registry = _load_registry()
    if slug not in registry:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")

    event_queue: queue.Queue = queue.Queue()

    def run_in_thread():
        from hermes.runtime.executor import run_agent

        def callback(event: dict):
            event_queue.put(event)

        try:
            run_agent(
                slug=slug,
                task=task or None,
                dry_run=dry_run,
                base_dir=BASE_DIR,
                stream=False,
                log_callback=callback,
            )
        except Exception as e:
            event_queue.put({"type": "error", "message": str(e)})
        finally:
            event_queue.put(None)  # sentinel

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()

    async def event_generator():
        while True:
            try:
                event = event_queue.get(timeout=60)
            except queue.Empty:
                yield "data: {\"type\": \"error\", \"message\": \"timeout\"}\n\n"
                break
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
