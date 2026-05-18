"""Generates agent files from Jinja2 templates."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .agent_spec import AgentSpec

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_FILES = [
    ("agent.yaml.j2", "agent.yaml"),
    ("prompt.md.j2", "prompt.md"),
    ("tools.yaml.j2", "tools.yaml"),
    ("workflow.py.j2", "workflow.py"),
    ("README.md.j2", "README.md"),
    ("tests/test_agent_permissions.py.j2", "tests/test_agent_permissions.py"),
]


def generate_files(spec: AgentSpec, output_dir: Path) -> None:
    """Render all templates for the given spec into output_dir."""
    template_dir = _TEMPLATES_DIR / spec.template_name
    if not template_dir.exists():
        raise FileNotFoundError(
            f"Template '{spec.template_name}' not found at {template_dir}"
        )

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        keep_trailing_newline=True,
        autoescape=False,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "tests").mkdir(exist_ok=True)

    ctx = spec.to_dict()

    for template_rel, output_rel in _FILES:
        tmpl = env.get_template(template_rel)
        rendered = tmpl.render(**ctx)
        out_path = output_dir / output_rel
        out_path.write_text(rendered)
        print(f"  [generated] {out_path.relative_to(output_dir.parent.parent.parent)}")
