"""Validates AgentSpec permissions before any files are generated."""

from __future__ import annotations

from typing import List

from ..agent_spec import AgentSpec


def validate_spec(spec: AgentSpec) -> List[str]:
    """Return a list of error messages. Empty list = valid."""
    errors: List[str] = []

    if spec.enabled:
        errors.append("agent.enabled must be False at creation time.")

    # Shell execute must be denied
    shell_allowed = spec.allowed_tools.get("shell", [])
    if any(op in shell_allowed for op in ["execute", "run_command", "eval"]):
        errors.append("shell.execute/run_command/eval must not be in allowed_tools.")

    # Gmail send/delete must not be allowed
    gmail_allowed = spec.allowed_tools.get("gmail", [])
    if "send" in gmail_allowed:
        errors.append("gmail.send must not be in allowed_tools (requires explicit human approval).")
    if "delete" in gmail_allowed:
        errors.append("gmail.delete must not be in allowed_tools.")

    # Filesystem delete must be denied
    fs_allowed = spec.allowed_tools.get("filesystem", [])
    if any(op in fs_allowed for op in ["delete", "unlink", "rmtree", "write_sensitive"]):
        errors.append("filesystem destructive ops (delete/unlink/rmtree) must not be allowed.")

    # Package installation must be denied
    pkg_allowed = spec.allowed_tools.get("package_manager", [])
    if any(op in pkg_allowed for op in ["install", "uninstall", "upgrade"]):
        errors.append("package_manager.install/uninstall/upgrade must not be in allowed_tools.")

    # Cloud deployment must not be allowed
    cloud_allowed = spec.allowed_tools.get("cloud", [])
    if any(op in cloud_allowed for op in ["deploy", "provision", "destroy"]):
        errors.append("cloud.deploy/provision/destroy must not be in allowed_tools.")

    # No wildcard permissions
    for service, ops in spec.allowed_tools.items():
        if "*" in ops or "all" in ops:
            errors.append(f"Wildcard permission '*' or 'all' is not allowed for '{service}'.")

    # Must have approval gates
    if not spec.approval_required_for:
        errors.append("approval_required_for must list at least one gate.")

    return errors
