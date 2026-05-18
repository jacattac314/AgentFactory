"""Validates the files inside a generated agent directory."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import List

import yaml

_FORBIDDEN_IMPORTS = {"os", "subprocess", "shutil", "pathlib"}
_FORBIDDEN_CALLS = {"unlink", "rmtree", "remove", "system", "popen", "call", "run"}
_FORBIDDEN_WORKFLOW_PATTERNS = [
    r"\bimport\s+os\b",
    r"\bimport\s+subprocess\b",
    r"\bimport\s+shutil\b",
    r"\bfrom\s+pathlib\b",
    r"\bos\.system\b",
    r"\bos\.popen\b",
    r"\bsubprocess\.",
    r"\bshutil\.rmtree\b",
    r"\bshutil\.move\b",
    r"\.unlink\(",
    r"open\s*\(.*['\"]w['\"]",
]


def validate_generated_agent(agent_dir: Path) -> List[str]:
    """
    Validate a generated agent directory.
    Returns list of error/warning strings. Empty = clean.
    """
    errors: List[str] = []
    errors += _check_required_files(agent_dir)
    errors += _check_agent_yaml(agent_dir)
    errors += _check_tools_yaml(agent_dir)
    errors += _check_workflow(agent_dir)
    return errors


def _check_required_files(agent_dir: Path) -> List[str]:
    required = [
        "agent.yaml",
        "prompt.md",
        "tools.yaml",
        "workflow.py",
        "README.md",
        "tests/test_agent_permissions.py",
    ]
    missing = [f for f in required if not (agent_dir / f).exists()]
    return [f"Missing required file: {f}" for f in missing]


def _check_agent_yaml(agent_dir: Path) -> List[str]:
    errors: List[str] = []
    path = agent_dir / "agent.yaml"
    if not path.exists():
        return errors

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    if data.get("enabled") is True:
        errors.append("agent.yaml: enabled must be false at creation.")

    for field in ["name", "slug", "approval_required_for", "entrypoint"]:
        if not data.get(field):
            errors.append(f"agent.yaml: missing required field '{field}'.")

    return errors


def _check_tools_yaml(agent_dir: Path) -> List[str]:
    errors: List[str] = []
    path = agent_dir / "tools.yaml"
    if not path.exists():
        return errors

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    allowed = data.get("allowed_tools", {})
    for service, ops in allowed.items():
        if isinstance(ops, list) and ("*" in ops or "all" in ops):
            errors.append(f"tools.yaml: wildcard permission in '{service}'.")

    shell_allowed = allowed.get("shell", [])
    if isinstance(shell_allowed, list) and any(
        op in shell_allowed for op in ["execute", "run_command", "eval"]
    ):
        errors.append("tools.yaml: shell execute is not permitted.")

    if not data.get("approval_required_for"):
        errors.append("tools.yaml: approval_required_for is empty.")

    return errors


def _strip_comments(source: str) -> str:
    """Return source with comment-only lines removed (for pattern matching)."""
    lines = [
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    ]
    return "\n".join(lines)


def _check_workflow(agent_dir: Path) -> List[str]:
    errors: List[str] = []
    path = agent_dir / "workflow.py"
    if not path.exists():
        return errors

    source = path.read_text()
    uncommented = _strip_comments(source)

    for pattern in _FORBIDDEN_WORKFLOW_PATTERNS:
        if re.search(pattern, uncommented):
            errors.append(f"workflow.py: forbidden pattern detected: {pattern}")

    # AST check: imports
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in _FORBIDDEN_IMPORTS:
                        errors.append(
                            f"workflow.py: forbidden import '{alias.name}'."
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module in _FORBIDDEN_IMPORTS:
                    errors.append(
                        f"workflow.py: forbidden 'from {node.module} import ...'."
                    )
    except SyntaxError as e:
        errors.append(f"workflow.py: syntax error — {e}")

    return errors
