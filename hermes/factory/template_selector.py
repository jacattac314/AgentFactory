"""Deterministic keyword-based template selection and tool mapping."""

from __future__ import annotations

from typing import Dict, List, Tuple

# Keywords that signal each template type
_BRIEFING_KEYWORDS = {
    "gmail", "email", "calendar", "daily", "morning", "summary", "brief",
    "digest", "newsletter", "inbox",
}
_MONITOR_KEYWORDS = {
    "monitor", "watch", "alert", "risk", "notify", "channel", "trigger",
    "detect", "incident",
}
_RESEARCH_KEYWORDS = {
    "research", "web", "report", "sources", "summarize", "document",
    "paper", "search", "crawl", "scrape",
}

# Slack keywords are signal for tool grants but not template selection on their own
_SLACK_KEYWORDS = {"slack", "post", "message", "dm"}


def _tokens(request: str) -> set:
    return set(request.lower().split())


def select_template(request: str) -> str:
    tokens = _tokens(request)

    briefing_score = len(tokens & _BRIEFING_KEYWORDS)
    monitor_score = len(tokens & _MONITOR_KEYWORDS)
    research_score = len(tokens & _RESEARCH_KEYWORDS)

    if briefing_score >= monitor_score and briefing_score >= research_score:
        return "briefing_agent"
    if monitor_score >= research_score:
        return "monitor_agent"
    return "research_agent"


def map_tools(request: str) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Return (allowed_tools, denied_tools) based on keywords in the request."""
    tokens = _tokens(request)
    allowed: Dict[str, List[str]] = {}
    denied: Dict[str, List[str]] = {}

    # Gmail / email
    if tokens & {"gmail", "email", "inbox"}:
        allowed["gmail"] = ["search", "read"]
        denied["gmail"] = ["send", "delete", "modify_labels", "create_draft"]

    # Slack
    if tokens & _SLACK_KEYWORDS:
        allowed["slack"] = ["post_message", "read_channel"]
        denied["slack"] = ["delete_message", "invite_users", "create_channel"]

    # Calendar
    if tokens & {"calendar", "events", "schedule"}:
        allowed["calendar"] = ["read_events"]
        denied["calendar"] = ["create_event", "delete_event", "update_event"]

    # Notion
    if "notion" in tokens:
        allowed["notion"] = ["read_page", "read_database"]
        denied["notion"] = ["create_page", "update_page", "delete_page", "create_database"]

    # Jira
    if "jira" in tokens:
        allowed["jira"] = ["read_issue", "list_issues"]
        denied["jira"] = ["create_issue", "update_issue", "delete_issue", "transition_issue"]

    # GitHub
    if "github" in tokens:
        allowed["github"] = ["read_repo", "list_issues", "list_prs"]
        denied["github"] = ["push_code", "create_pr", "delete_branch", "merge_pr"]

    # Web / research
    if tokens & {"web", "research", "search", "crawl", "scrape"}:
        allowed["web"] = ["fetch_url", "search"]
        denied["web"] = []

    # Always deny dangerous capabilities
    denied["shell"] = ["execute", "run_command", "eval"]
    denied["filesystem"] = ["delete", "write_sensitive", "unlink", "rmtree"]
    denied["package_manager"] = ["install", "uninstall", "upgrade"]
    denied["cloud"] = ["deploy", "provision", "destroy"]

    return allowed, denied


def default_approval_gates() -> List[str]:
    return [
        "sending_email",
        "deleting_files",
        "modifying_code",
        "installing_packages",
        "cloud_deployment",
        "posting_to_external_services",
    ]
