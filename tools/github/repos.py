"""GitHub repository & pull-request tools (read-only / autonomous)."""

import httpx
from langchain_core.tools import tool

from tools.registry import tool_register
from tools.github._shared import _gh_get, _gh_token, _NETWORK_ERRORS

_NOT_CONFIGURED = "GitHub is not configured (missing GITHUB_TOKEN)."


@tool_register(namespace="github")
@tool
def list_github_repositories() -> str:
    """List the GitHub repositories the authenticated user can access, most
    recently updated first. Call this FIRST whenever you don't already know the
    exact `owner/repo` name you need — never guess a repository name."""
    token = _gh_token()
    if not token:
        return _NOT_CONFIGURED
    try:
        repos = _gh_get(token, "/user/repos", {"sort": "updated", "per_page": 100})
        names = [r.get("full_name", "?") for r in repos]
        if not names:
            return "No repositories found for this account."
        return "Accessible repositories (most recently updated first):\n" + "\n".join(
            f"- {n}" for n in names
        )
    except _NETWORK_ERRORS:
        return "Error: GitHub is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: GitHub returned {e.response.status_code} — check GITHUB_TOKEN."


@tool_register(namespace="github")
@tool
def list_repo_pulls(repo_name: str, state: str = "open") -> str:
    """List pull requests for a repo. `repo_name` must be the full `owner/repo`
    form (e.g. 'owner/repo'). `state` is 'open', 'closed', or 'all'."""
    token = _gh_token()
    if not token:
        return _NOT_CONFIGURED
    try:
        pulls = _gh_get(
            token, f"/repos/{repo_name}/pulls", {"state": state, "per_page": 50}
        )
        if not pulls:
            return f"No {state} pull requests in {repo_name}."
        lines = []
        for p in pulls:
            draft = " (draft)" if p.get("draft") else ""
            head = p.get("head", {}).get("ref", "?")
            base = p.get("base", {}).get("ref", "?")
            lines.append(
                f"- #{p.get('number')} {p.get('title')}{draft} "
                f"[{p.get('state')}] {head} -> {base}"
            )
        return f"{state.capitalize()} PRs in {repo_name}:\n" + "\n".join(lines)
    except _NETWORK_ERRORS:
        return "Error: GitHub is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: GitHub returned {e.response.status_code} for {repo_name}."


@tool_register(namespace="github")
@tool
def read_pr_details(repo_name: str, pr_number: int) -> str:
    """Read the full details of one pull request (title, state, body, branches,
    draft/mergeable flags). `repo_name` is the full `owner/repo` form."""
    token = _gh_token()
    if not token:
        return _NOT_CONFIGURED
    try:
        p = _gh_get(token, f"/repos/{repo_name}/pulls/{pr_number}")
        head = p.get("head", {}).get("ref", "?")
        base = p.get("base", {}).get("ref", "?")
        body = p.get("body") or "(no description)"
        return (
            f"PR #{p.get('number')} in {repo_name}: {p.get('title')}\n"
            f"State: {p.get('state')}"
            f"{' (draft)' if p.get('draft') else ''}"
            f" | mergeable: {p.get('mergeable')}\n"
            f"Branches: {head} -> {base}\n"
            f"Author: {p.get('user', {}).get('login', '?')}\n\n"
            f"{body}"
        )
    except _NETWORK_ERRORS:
        return "Error: GitHub is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: GitHub returned {e.response.status_code} for PR #{pr_number}."
