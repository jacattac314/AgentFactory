"""
Tests for the Hermes Agent Factory.

Run with:  pytest tests/test_factory.py -v
"""

import importlib.util
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from hermes.factory.agent_spec import AgentSpec, name_from_request, slugify
from hermes.factory.factory_service import (
    FactoryError,
    create_agent,
    dry_run_agent,
    enable_agent,
    validate_agent,
)
from hermes.factory.registry_updater import load_registry, save_registry
from hermes.factory.template_selector import map_tools, select_template
from hermes.factory.validators.generated_agent_validator import validate_generated_agent
from hermes.factory.validators.permission_validator import validate_spec


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    d = Path(tempfile.mkdtemp())
    (d / "agents" / "generated").mkdir(parents=True)
    yield d
    shutil.rmtree(d)


GMAIL_SLACK_REQUEST = (
    "Create an agent that summarizes my Gmail and posts a morning brief to Slack"
)


# ─── 1. Create briefing agent from Gmail + Slack request ─────────────────────

def test_create_briefing_agent(tmp_dir):
    spec = create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)
    assert spec.template_name == "briefing_agent"
    agent_dir = tmp_dir / "agents" / "generated" / spec.slug
    assert agent_dir.exists()


# ─── 2. Generated agent is disabled by default ───────────────────────────────

def test_agent_disabled_by_default(tmp_dir):
    spec = create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)
    assert spec.enabled is False

    agent_yaml_path = tmp_dir / "agents" / "generated" / spec.slug / "agent.yaml"
    with open(agent_yaml_path) as f:
        data = yaml.safe_load(f)
    assert data["enabled"] is False


# ─── 3. Shell execute is denied ──────────────────────────────────────────────

def test_shell_execute_denied(tmp_dir):
    spec = create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)
    shell_allowed = spec.allowed_tools.get("shell", [])
    assert "execute" not in shell_allowed
    assert "run_command" not in shell_allowed


def test_shell_execute_denied_in_tools_yaml(tmp_dir):
    spec = create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)
    tools_path = tmp_dir / "agents" / "generated" / spec.slug / "tools.yaml"
    with open(tools_path) as f:
        data = yaml.safe_load(f)
    shell_allowed = data.get("allowed_tools", {}).get("shell", [])
    assert "execute" not in (shell_allowed or [])


# ─── 4. Gmail send/delete are denied ─────────────────────────────────────────

def test_gmail_send_denied():
    allowed, denied = map_tools("Create an agent for Gmail")
    assert "send" not in allowed.get("gmail", [])
    assert "send" in denied.get("gmail", [])


def test_gmail_delete_denied():
    allowed, denied = map_tools("Create an agent for Gmail")
    assert "delete" not in allowed.get("gmail", [])
    assert "delete" in denied.get("gmail", [])


# ─── 5. Registry updates correctly ───────────────────────────────────────────

def test_registry_updated(tmp_dir):
    spec = create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)
    registry = load_registry(tmp_dir / "registry.yaml")
    assert spec.slug in registry["generated_agents"]
    entry = registry["generated_agents"][spec.slug]
    assert entry["enabled"] is False
    assert entry["template_name"] == "briefing_agent"


# ─── 6. Duplicate slug fails unless --force ───────────────────────────────────

def test_duplicate_slug_fails(tmp_dir):
    create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)
    with pytest.raises(FactoryError, match="already exists"):
        create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)


def test_duplicate_slug_with_force(tmp_dir):
    create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)
    spec2 = create_agent(GMAIL_SLACK_REQUEST, force=True, base_dir=tmp_dir)
    assert spec2.slug is not None


# ─── 7. Validation blocks unsafe workflow imports ─────────────────────────────

def test_validation_blocks_unsafe_import(tmp_dir):
    spec = create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)
    agent_dir = tmp_dir / "agents" / "generated" / spec.slug
    workflow = agent_dir / "workflow.py"
    # Inject a forbidden import
    original = workflow.read_text()
    workflow.write_text("import os\n" + original)

    errors = validate_generated_agent(agent_dir)
    assert any("os" in e for e in errors)

    # Restore
    workflow.write_text(original)


# ─── 8. Dry-run does not perform external actions ─────────────────────────────

def test_dry_run_returns_dry_run_status(tmp_dir):
    spec = create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)
    agent_dir = tmp_dir / "agents" / "generated" / spec.slug
    workflow_path = agent_dir / "workflow.py"

    mod_spec = importlib.util.spec_from_file_location("wf", workflow_path)
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)

    result = mod.run({"dry_run": True, "tools": {}})
    assert result["status"] == "dry_run"
    assert "planned_actions" in result


# ─── 9. Enable runs validation first ─────────────────────────────────────────

def test_enable_runs_validation(tmp_dir):
    spec = create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)
    agent_dir = tmp_dir / "agents" / "generated" / spec.slug

    # Corrupt agent.yaml to force validation failure
    agent_yaml = agent_dir / "agent.yaml"
    with open(agent_yaml) as f:
        data = yaml.safe_load(f)
    data.pop("entrypoint", None)  # remove required field
    with open(agent_yaml, "w") as f:
        yaml.dump(data, f)

    ok = enable_agent(spec.slug, base_dir=tmp_dir)
    assert ok is False  # blocked by validation


def test_enable_succeeds_after_validation_passes(tmp_dir):
    spec = create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)
    ok = enable_agent(spec.slug, base_dir=tmp_dir)
    assert ok is True

    agent_yaml_path = tmp_dir / "agents" / "generated" / spec.slug / "agent.yaml"
    with open(agent_yaml_path) as f:
        data = yaml.safe_load(f)
    assert data["enabled"] is True


# ─── Template selector ────────────────────────────────────────────────────────

def test_template_selector_briefing():
    assert select_template("Gmail summary morning brief") == "briefing_agent"


def test_template_selector_monitor():
    assert select_template("Watch Slack channel for alerts and risk") == "monitor_agent"


def test_template_selector_research():
    assert select_template("Research web sources and write a report") == "research_agent"


# ─── Permission validator ─────────────────────────────────────────────────────

def test_permission_validator_blocks_enabled():
    spec = AgentSpec(
        name="Bad Agent",
        slug="bad_agent",
        description="Test",
        agent_type="briefing_agent",
        user_request="test",
        template_name="briefing_agent",
        enabled=True,  # INVALID
        approval_required_for=["sending_email"],
    )
    errors = validate_spec(spec)
    assert any("enabled" in e for e in errors)


def test_permission_validator_blocks_shell_execute():
    spec = AgentSpec(
        name="Bad Agent",
        slug="bad_agent",
        description="Test",
        agent_type="briefing_agent",
        user_request="test",
        template_name="briefing_agent",
        enabled=False,
        allowed_tools={"shell": ["execute"]},  # INVALID
        approval_required_for=["sending_email"],
    )
    errors = validate_spec(spec)
    assert any("shell" in e for e in errors)


def test_permission_validator_blocks_wildcard():
    spec = AgentSpec(
        name="Bad Agent",
        slug="bad_agent",
        description="Test",
        agent_type="briefing_agent",
        user_request="test",
        template_name="briefing_agent",
        enabled=False,
        allowed_tools={"gmail": ["*"]},  # INVALID
        approval_required_for=["sending_email"],
    )
    errors = validate_spec(spec)
    assert any("wildcard" in e.lower() or "*" in e for e in errors)


# ─── All required files are generated ────────────────────────────────────────

def test_all_required_files_exist(tmp_dir):
    spec = create_agent(GMAIL_SLACK_REQUEST, base_dir=tmp_dir)
    agent_dir = tmp_dir / "agents" / "generated" / spec.slug
    for fname in [
        "agent.yaml",
        "prompt.md",
        "tools.yaml",
        "workflow.py",
        "README.md",
        "tests/test_agent_permissions.py",
    ]:
        assert (agent_dir / fname).exists(), f"Missing: {fname}"
