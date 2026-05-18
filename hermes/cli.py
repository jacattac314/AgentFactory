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


def _load_dotenv():
    """Load .env if present."""
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except ImportError:
            pass


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


@cli.command("run")
@click.argument("agent_slug")
@click.option("--task", default=None, help="Override the agent's default task.")
@click.option("--dry-run", is_flag=True, default=False, help="Plan only — no real tool calls.")
def run_cmd(agent_slug: str, task: str, dry_run: bool):
    """Run an agent with its configured tools."""
    _load_dotenv()
    from hermes.runtime.executor import run_agent
    result = run_agent(agent_slug, task=task, dry_run=dry_run, base_dir=Path.cwd())
    if result.status == "error":
        click.echo(f"\n[ERROR] {result.error}", err=True)
        sys.exit(1)


@cli.group("tools")
def tools_group():
    """Manage tool credentials and configuration."""


@tools_group.command("status")
def tools_status_cmd():
    """Show configuration status for all tool integrations."""
    _load_dotenv()
    from hermes.tools.loader import tools_status
    status = tools_status()
    click.echo()
    for service, info in status.items():
        icon = "✓" if info["ready"] else "✗"
        click.echo(f"  [{icon}] {service:<10} {info['detail']}")
    click.echo()


@tools_group.command("auth")
@click.argument("service", type=click.Choice(["gmail"]))
def tools_auth(service: str):
    """Authenticate a tool integration (currently: gmail)."""
    _load_dotenv()
    if service == "gmail":
        _auth_gmail()


def _auth_gmail():
    """Run the Gmail OAuth2 flow and save token to ~/.hermes/gmail_token.json."""
    import os
    from pathlib import Path as P

    secret_path = P(os.environ.get(
        "GMAIL_CLIENT_SECRET_PATH",
        str(P.home() / ".hermes" / "gmail_client_secret.json"),
    ))
    token_path = P(os.environ.get(
        "GMAIL_TOKEN_PATH",
        str(P.home() / ".hermes" / "gmail_token.json"),
    ))

    if not secret_path.exists():
        click.echo(
            f"\n[ERROR] Client secret not found: {secret_path}\n"
            "  1. Go to console.cloud.google.com → APIs & Services → Credentials\n"
            "  2. Create an OAuth 2.0 Client ID (Desktop app)\n"
            "  3. Download JSON and save it to that path\n",
            err=True,
        )
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
        flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
        click.echo("\n  Opening browser for Gmail authorization...")
        creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
        click.echo(f"  Token saved → {token_path}\n")
    except ImportError:
        click.echo("[ERROR] google-auth-oauthlib not installed. Run: pip install google-auth-oauthlib", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"[ERROR] Auth failed: {e}", err=True)
        sys.exit(1)


@cli.command("serve")
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.option("--reload", is_flag=True, default=False, help="Auto-reload on code changes.")
def serve(host: str, port: int, reload: bool):
    """Start the Hermes chat UI server."""
    try:
        import uvicorn
    except ImportError:
        click.echo("[ERROR] uvicorn not installed. Run: pip install uvicorn fastapi", err=True)
        sys.exit(1)
    click.echo(f"  Hermes UI → http://{host}:{port}")
    uvicorn.run(
        "hermes.server.app:app",
        host=host, port=port, reload=reload,
    )


if __name__ == "__main__":
    cli()
