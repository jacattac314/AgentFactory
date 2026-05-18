"""Hermes CLI entry point."""

import sys
from pathlib import Path

import click

from hermes.factory.factory_service import (
    FactoryError,
    create_agent,
    dry_run_agent,
    enable_agent,
    list_agents,
    validate_agent,
)


@click.group()
def cli():
    """Hermes — Controlled agent generation framework."""


@cli.group()
def factory():
    """Agent Factory commands."""


@factory.command("create")
@click.argument("request")
@click.option("--force", is_flag=True, default=False, help="Overwrite existing agent.")
@click.option("--dry-run-only", is_flag=True, default=False,
              help="Plan only — do not write files.")
@click.option("--template",
              type=click.Choice(["briefing_agent", "monitor_agent", "research_agent"]),
              default=None, help="Override template selection.")
def factory_create(request: str, force: bool, dry_run_only: bool, template: str):
    """Generate a new agent from a natural language REQUEST."""
    try:
        create_agent(
            request=request,
            force=force,
            dry_run_only=dry_run_only,
            template_override=template,
            base_dir=Path.cwd(),
        )
    except FactoryError as e:
        click.echo(f"\n[FACTORY ERROR] {e}", err=True)
        sys.exit(1)


@factory.command("validate")
@click.argument("agent_slug")
def factory_validate(agent_slug: str):
    """Validate a generated agent's safety and structure."""
    ok = validate_agent(agent_slug, base_dir=Path.cwd())
    sys.exit(0 if ok else 1)


@factory.command("dry-run")
@click.argument("agent_slug")
def factory_dry_run(agent_slug: str):
    """Execute an agent workflow in dry-run mode (no external actions)."""
    dry_run_agent(agent_slug, base_dir=Path.cwd())


@factory.command("enable")
@click.argument("agent_slug")
def factory_enable(agent_slug: str):
    """Enable an agent after passing validation."""
    ok = enable_agent(agent_slug, base_dir=Path.cwd())
    sys.exit(0 if ok else 1)


@factory.command("list")
def factory_list():
    """List all generated agents."""
    list_agents(base_dir=Path.cwd())


if __name__ == "__main__":
    cli()
