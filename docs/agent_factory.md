# Hermes Agent Factory

## Overview

The Hermes Agent Factory is a meta-agent subsystem that generates new Hermes agents from natural language requests. It enforces a strict safety model: every generated agent is **disabled by default**, operates with **least-privilege permissions**, and must pass **validation before activation**.

The factory does NOT create self-replicating or uncontrolled agents. Generated agents are inert packages that require explicit human review and a deliberate enable step.

---

## Architecture

```
hermes/
  cli.py                          # CLI entry point (click)
  factory/
    __init__.py
    agent_spec.py                 # AgentSpec dataclass
    factory_service.py            # Orchestration (create/validate/enable/dry-run)
    template_selector.py          # Deterministic keyword → template + tool mapping
    file_generator.py             # Jinja2 template rendering
    registry_updater.py           # registry.yaml read/write
    validators/
      permission_validator.py     # Validates AgentSpec before file generation
      generated_agent_validator.py # Validates generated file content
    templates/
      briefing_agent/             # Gmail/email/calendar → brief → Slack
      monitor_agent/              # Watch channels → classify → alert
      research_agent/             # Web/documents → synthesise → report

agents/
  generated/
    <slug>/                       # Output of each factory run
      agent.yaml
      prompt.md
      tools.yaml
      workflow.py
      README.md
      tests/test_agent_permissions.py

registry.yaml                     # Catalogue of all generated agents
```

---

## CLI Usage

### Create an agent
```bash
hermes factory create "Create an agent that summarizes my Gmail and posts a morning brief to Slack"

# Override template
hermes factory create "..." --template briefing_agent

# Plan only (no files written)
hermes factory create "..." --dry-run-only

# Overwrite existing agent with same slug
hermes factory create "..." --force
```

### Validate a generated agent
```bash
hermes factory validate <agent_slug>
```
Checks:
- All required files exist
- `agent.yaml` has `enabled: false` and required fields
- `tools.yaml` has no wildcard permissions and no shell.execute
- `workflow.py` contains no forbidden imports (os, subprocess, shutil, pathlib)

### Dry-run a generated agent
```bash
hermes factory dry-run <agent_slug>
```
Loads `workflow.py` and calls `run({"dry_run": True, "tools": {}})`.
Prints the list of planned actions without executing anything external.

### Enable an agent
```bash
hermes factory enable <agent_slug>
```
1. Runs full validation first.
2. If validation fails → blocked, agent stays disabled.
3. If validation passes → sets `enabled: true` in `agent.yaml` and `registry.yaml`.

### List all generated agents
```bash
hermes factory list
```

---

## Safety Model

| Rule | Enforcement |
|------|-------------|
| Agent disabled at creation | `permission_validator` + `generated_agent_validator` |
| No shell execute | `permission_validator` + `tools.yaml` check |
| No Gmail send/delete | `permission_validator` + `tools.yaml` check |
| No filesystem delete | `permission_validator` + `tools.yaml` check |
| No package installation | `permission_validator` |
| No cloud deployment | `permission_validator` |
| No wildcard permissions | `permission_validator` + `tools.yaml` check |
| No direct os/subprocess/shutil imports | `generated_agent_validator` (regex + AST) |
| Approval gates required | `permission_validator` |
| Enable blocked if validation fails | `factory_service.enable_agent` |

---

## Permission Model

Tool permissions are derived deterministically from the user request using keyword matching.

### Allowed (read-only by default)
- `gmail`: `search`, `read`
- `slack`: `post_message`, `read_channel`
- `calendar`: `read_events`
- `notion`: `read_page`, `read_database`
- `jira`: `read_issue`, `list_issues`
- `github`: `read_repo`, `list_issues`, `list_prs`
- `web`: `fetch_url`, `search`

### Always Denied
- `shell`: `execute`, `run_command`, `eval`
- `filesystem`: `delete`, `write_sensitive`, `unlink`, `rmtree`
- `package_manager`: `install`, `uninstall`, `upgrade`
- `cloud`: `deploy`, `provision`, `destroy`
- `gmail`: `send`, `delete`
- `slack`: `delete_message`, `invite_users`, `create_channel`

---

## Generated Folder Structure

```
agents/generated/<slug>/
├── agent.yaml                    # Metadata, schedule, approval gates
├── prompt.md                     # System prompt with role, allowed/denied actions
├── tools.yaml                    # Explicit permission lists
├── workflow.py                   # Safe run(context) function, dry-run aware
├── README.md                     # Human-readable guide
└── tests/
    └── test_agent_permissions.py # Pytest tests for safety invariants
```

---

## Example: Gmail + Slack Morning Brief Agent

```bash
hermes factory create "Create an agent that summarizes my Gmail and posts a morning brief to Slack"
```

**Selected template**: `briefing_agent`

**Allowed tools**:
- `gmail`: search, read
- `slack`: post_message, read_channel

**Denied tools**:
- `gmail`: send, delete, modify_labels, create_draft
- `slack`: delete_message, invite_users, create_channel
- `shell`: execute, run_command, eval
- `filesystem`: delete, write_sensitive, unlink, rmtree
- `package_manager`: install, uninstall, upgrade
- `cloud`: deploy, provision, destroy

**Approval gates**:
- sending_email
- deleting_files
- modifying_code
- installing_packages
- cloud_deployment
- posting_to_external_services

**Generated output**:
```
agents/generated/gmail_summarizes_my_gmail_and_posts_a_morning_brief_to_slack_agent/
```

---

## Template Selection (Keyword Routing)

| Keywords | Template |
|----------|----------|
| gmail, email, calendar, daily, morning, summary, brief, digest | `briefing_agent` |
| monitor, watch, alert, risk, notify, channel, trigger, detect, incident | `monitor_agent` |
| research, web, report, sources, summarize, document, paper, search, crawl | `research_agent` |

Scores are counted; the highest-scoring template wins.

---

## Future Improvements

- LLM-powered spec generation for nuanced requests
- Per-agent runtime tool adapter injection
- Scheduled agent execution (cron / event-driven)
- Agent versioning and rollback
- Approval workflow integration (Slack DM, GitHub PR review)
- Multi-agent composition (factory creates pipelines)
- Generated agent test execution in CI
