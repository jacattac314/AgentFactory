"""FactoryService: orchestrates agent generation from a user request."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from .agent_spec import AgentSpec, name_from_request, slugify
from .file_generator import generate_files
from .llm_spec_builder import build_spec_with_llm
from .registry_updater import load_registry, save_registry
from .template_selector import default_approval_gates, map_tools, select_template
from .validators.generated_agent_validator import validate_generated_agent
from .validators.permission_validator import validate_spec


class FactoryError(Exception):
    pass


def create_agent(
    request: str,
    force: bool = False,
    dry_run_only: bool = False,
    template_override: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> AgentSpec:
    """
    Full pipeline: request → spec → validate → generate files → register.

    Returns the AgentSpec that was created (or would be created in dry_run_only).
    Raises FactoryError on unrecoverable problems.
    """
    base_dir = base_dir or Path.cwd()

    # 1. Build spec — try LLM first, fall back to keyword heuristic
    llm_result = None if template_override else build_spec_with_llm(request)

    if llm_result:
        name, description, template_name, allowed, denied, approval_gates = llm_result
        slug = slugify(name)
        print("  [factory] Using LLM-generated spec.")
    else:
        if not template_override:
            print("  [factory] LLM unavailable — using keyword heuristic.")
        template_name = template_override or select_template(request)
        allowed, denied = map_tools(request)
        approval_gates = default_approval_gates()
        name = name_from_request(request)
        slug = slugify(name)
        description = f"Auto-generated agent: {request[:120]}"

    spec = AgentSpec(
        name=name,
        slug=slug,
        description=description,
        agent_type=template_name,
        user_request=request,
        template_name=template_name,
        allowed_tools=allowed,
        denied_tools=denied,
        approval_required_for=approval_gates,
        enabled=False,
    )

    # 2. Validate spec permissions before touching disk
    errors = validate_spec(spec)
    if errors:
        _print_section("PERMISSION VALIDATION ERRORS", errors, error=True)
        raise FactoryError("Spec failed permission validation. Agent not created.")

    # 3. Check registry for duplicates
    registry_path = base_dir / "registry.yaml"
    registry = load_registry(registry_path)
    existing = registry.get("generated_agents", {})
    if slug in existing and not force:
        raise FactoryError(
            f"Agent '{slug}' already exists in registry. Use --force to overwrite."
        )

    agent_dir = base_dir / "agents" / "generated" / slug

    # 4. Print plan
    _print_plan(spec, agent_dir)

    if dry_run_only:
        print("\n[DRY RUN] No files written. Remove --dry-run-only to generate.")
        return spec

    # 5. Generate files
    generate_files(spec, agent_dir)

    # 6. Validate generated files
    file_errors = validate_generated_agent(agent_dir)
    if file_errors:
        _print_section("GENERATED FILE VALIDATION WARNINGS", file_errors, error=False)

    # 7. Update registry
    save_registry(registry_path, spec)

    _print_success(spec, agent_dir)
    return spec


def validate_agent(slug: str, base_dir: Optional[Path] = None) -> bool:
    base_dir = base_dir or Path.cwd()
    agent_dir = base_dir / "agents" / "generated" / slug

    if not agent_dir.exists():
        print(f"[ERROR] Agent directory not found: {agent_dir}", file=sys.stderr)
        return False

    registry_path = base_dir / "registry.yaml"
    registry = load_registry(registry_path)
    entry = registry.get("generated_agents", {}).get(slug)
    if not entry:
        print(f"[ERROR] Agent '{slug}' not found in registry.", file=sys.stderr)
        return False

    errors = validate_generated_agent(agent_dir)
    if errors:
        print(f"\n[VALIDATION FAILED] {slug}")
        for e in errors:
            print(f"  ✗ {e}")
        return False

    print(f"\n[VALIDATION PASSED] {slug}")
    print("  All safety checks passed.")
    return True


def dry_run_agent(slug: str, base_dir: Optional[Path] = None) -> None:
    base_dir = base_dir or Path.cwd()
    agent_dir = base_dir / "agents" / "generated" / slug
    workflow_path = agent_dir / "workflow.py"

    if not workflow_path.exists():
        print(f"[ERROR] workflow.py not found for agent '{slug}'", file=sys.stderr)
        return

    import importlib.util
    spec_mod = importlib.util.spec_from_file_location(f"{slug}.workflow", workflow_path)
    mod = importlib.util.module_from_spec(spec_mod)
    spec_mod.loader.exec_module(mod)

    context = {"dry_run": True, "tools": {}}
    result = mod.run(context)

    print(f"\n[DRY RUN] {slug}")
    print(f"  status          : {result.get('status')}")
    print(f"  message         : {result.get('message')}")
    planned = result.get("planned_actions", [])
    if planned:
        print("  planned_actions :")
        for action in planned:
            print(f"    - {action}")
    else:
        print("  planned_actions : (none — no real actions performed)")


def enable_agent(slug: str, base_dir: Optional[Path] = None) -> bool:
    base_dir = base_dir or Path.cwd()
    agent_dir = base_dir / "agents" / "generated" / slug

    print(f"Running validation before enabling '{slug}'...")
    if not validate_agent(slug, base_dir):
        print("[BLOCKED] Agent not enabled. Fix validation errors first.")
        return False

    # Update agent.yaml
    import yaml
    agent_yaml_path = agent_dir / "agent.yaml"
    with open(agent_yaml_path) as f:
        data = yaml.safe_load(f)
    data["enabled"] = True
    with open(agent_yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    # Update registry
    registry_path = base_dir / "registry.yaml"
    registry = load_registry(registry_path)
    if slug in registry.get("generated_agents", {}):
        registry["generated_agents"][slug]["enabled"] = True
        with open(registry_path, "w") as f:
            import yaml as _yaml
            _yaml.dump(registry, f, default_flow_style=False, sort_keys=False)

    print(f"\n[ENABLED] Agent '{slug}' is now active.")
    return True


def list_agents(base_dir: Optional[Path] = None) -> None:
    base_dir = base_dir or Path.cwd()
    registry_path = base_dir / "registry.yaml"
    registry = load_registry(registry_path)
    agents = registry.get("generated_agents", {})

    if not agents:
        print("No generated agents found.")
        return

    print(f"\n{'SLUG':<40} {'TEMPLATE':<20} {'ENABLED':<8} {'VERSION'}")
    print("-" * 80)
    for slug, entry in agents.items():
        print(
            f"{slug:<40} {entry.get('template_name',''):<20} "
            f"{str(entry.get('enabled', False)):<8} {entry.get('version','')}"
        )


# ─── helpers ──────────────────────────────────────────────────────────────────

def _print_section(title: str, items: list, error: bool = False) -> None:
    prefix = "[ERROR]" if error else "[WARN]"
    print(f"\n{prefix} {title}:")
    for item in items:
        print(f"  • {item}")


def _print_plan(spec: AgentSpec, agent_dir: Path) -> None:
    print(f"\n{'='*60}")
    print(f"  Hermes Agent Factory — Generation Plan")
    print(f"{'='*60}")
    print(f"  Name            : {spec.name}")
    print(f"  Slug            : {spec.slug}")
    print(f"  Template        : {spec.template_name}")
    print(f"  Output dir      : {agent_dir}")
    print(f"  Enabled         : {spec.enabled}  ← disabled by default")
    print(f"\n  Allowed tools:")
    for service, ops in spec.allowed_tools.items():
        print(f"    {service}: {', '.join(ops)}")
    if not spec.allowed_tools:
        print("    (none)")
    print(f"\n  Denied tools:")
    for service, ops in spec.denied_tools.items():
        print(f"    {service}: {', '.join(ops)}")
    print(f"\n  Approval gates:")
    for gate in spec.approval_required_for:
        print(f"    • {gate}")
    print(f"\n  Files to be generated:")
    for fname in ["agent.yaml", "prompt.md", "tools.yaml", "workflow.py", "README.md",
                  "tests/test_agent_permissions.py"]:
        print(f"    {agent_dir}/{fname}")
    print(f"\n  Dry-run after creation:")
    print(f"    hermes factory dry-run {spec.slug}")
    print(f"  Enable after review:")
    print(f"    hermes factory enable {spec.slug}")


def _print_success(spec: AgentSpec, agent_dir: Path) -> None:
    print(f"\n{'='*60}")
    print(f"  Agent generated successfully!")
    print(f"{'='*60}")
    print(f"  slug    : {spec.slug}")
    print(f"  dir     : {agent_dir}")
    print(f"  enabled : {spec.enabled}  ← requires explicit enable step")
    print(f"\n  Next steps:")
    print(f"    hermes factory validate {spec.slug}")
    print(f"    hermes factory dry-run  {spec.slug}")
    print(f"    hermes factory enable   {spec.slug}")
