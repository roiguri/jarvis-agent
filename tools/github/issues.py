"""GitHub issue tools — reads (autonomous) and writes (confirmation-gated)."""

import asyncio

import httpx
from langchain_core.tools import tool

from tools.registry import tool_register
from tools.core.memory import _fenced
from tools.github._shared import (
    _gh_get,
    _gh_patch,
    _gh_post,
    _gh_token,
    _NETWORK_ERRORS,
)

_NOT_CONFIGURED = "GitHub is not configured (missing GITHUB_TOKEN)."

# Attribution footer appended to every issue/comment Jarvis authors. The GitHub
# author is still the token owner (see #41 for a true bot identity); this makes
# it visually unambiguous the content was written by Jarvis. The image is a
# GitHub user-attachments asset (globally public, durable, served regardless of
# repo privacy) — a repo raw URL cannot be used because this is a private repo
# and GitHub's image proxy fetches anonymously. Source artifact lives at
# docs/assets/jarvis-signature.jpg.
_SIG_IMG = (
    "https://github.com/user-attachments/assets/"
    "ae9ed3b2-8f81-4099-a87f-a6dcd9056d5e"
)
_SIGNATURE = (
    "\n\n---\n"
    f'<img src="{_SIG_IMG}" width="72" alt="Jarvis" /><br>'
    "<sub>Written by <b>Jarvis</b> on Roi's behalf</sub>"
)


def _sign(body: str) -> str:
    """Append the Jarvis attribution footer to an issue/comment body."""
    return f"{body.rstrip()}{_SIGNATURE}"


def _issue_title(token: str, repo_name: str, issue_number: int) -> str:
    """Best-effort issue title, for confirmation context. '' on any failure."""
    try:
        issue = _gh_get(token, f"/repos/{repo_name}/issues/{issue_number}")
        return (issue.get("title") or "").strip()
    except Exception:  # noqa: BLE001 — confirmation context is best-effort
        return ""


# --- Reads (autonomous) ----------------------------------------------------


@tool_register(namespace="github")
@tool
def list_repo_issues(repo_name: str, state: str = "open") -> str:
    """List issues for a repo. `repo_name` must be the full `owner/repo` form
    (e.g. 'owner/repo'). `state` is 'open', 'closed', or 'all'. Pull
    requests are excluded."""
    token = _gh_token()
    if not token:
        return _NOT_CONFIGURED
    try:
        items = _gh_get(
            token, f"/repos/{repo_name}/issues", {"state": state, "per_page": 50}
        )
        # GitHub's issues endpoint also returns PRs — skip those.
        issues = [i for i in items if "pull_request" not in i]
        if not issues:
            return f"No {state} issues in {repo_name}."
        lines = []
        for i in issues:
            labels = ", ".join(lbl.get("name", "") for lbl in i.get("labels", []))
            label_str = f" [{labels}]" if labels else ""
            lines.append(
                f"- #{i.get('number')} {i.get('title')} "
                f"({i.get('state')}){label_str}"
            )
        return f"{state.capitalize()} issues in {repo_name}:\n" + "\n".join(lines)
    except _NETWORK_ERRORS:
        return "Error: GitHub is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: GitHub returned {e.response.status_code} for {repo_name}."


@tool_register(namespace="github")
@tool
def read_issue_details(repo_name: str, issue_number: int) -> str:
    """Read the full description and context of one issue. `repo_name` is the
    full `owner/repo` form."""
    token = _gh_token()
    if not token:
        return _NOT_CONFIGURED
    try:
        i = _gh_get(token, f"/repos/{repo_name}/issues/{issue_number}")
        if "pull_request" in i:
            return (
                f"#{issue_number} in {repo_name} is a pull request, not an "
                f"issue — use read_pr_details instead."
            )
        labels = ", ".join(lbl.get("name", "") for lbl in i.get("labels", []))
        body = i.get("body") or "(no description)"
        return (
            f"Issue #{i.get('number')} in {repo_name}: {i.get('title')}\n"
            f"State: {i.get('state')} | Labels: {labels or '(none)'} | "
            f"Comments: {i.get('comments', 0)}\n"
            f"Author: {i.get('user', {}).get('login', '?')}\n\n"
            f"{body}"
        )
    except _NETWORK_ERRORS:
        return "Error: GitHub is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: GitHub returned {e.response.status_code} for issue #{issue_number}."


# --- Writes (confirmation-gated) -------------------------------------------


@tool_register(namespace="github", destructive=True)
@tool
def draft_github_issue(
    repo_name: str, title: str, body: str, labels: list[str] | None = None
) -> str:
    """Create a new issue. Requests owner confirmation before executing.
    `repo_name` is the full `owner/repo` form. Pack the relevant context from
    the conversation into `body`."""
    token = _gh_token()
    if not token:
        return _NOT_CONFIGURED
    from gateway.factory import get_confirmation

    payload = {"title": title, "body": _sign(body), "labels": labels or []}

    async def _do_create() -> str:
        return await asyncio.to_thread(_exec_create_issue, token, repo_name, payload)

    label_line = ", ".join(labels) if labels else "(none)"
    detail = (
        f"Create a GitHub issue in {repo_name}\n"
        f"Title: {title}\n"
        f"Labels: {label_line}\n"
        f"Body:\n"
        + _fenced(body)
    )

    try:
        return get_confirmation().request_confirmation_sync(
            description=detail,
            action_fn=_do_create,
            result_ok_text=f"Issue created in {repo_name}: '{title}'.",
            result_cancel_text=f"Issue creation in {repo_name} cancelled.",
        )
    except Exception as e:
        return f"Error requesting issue-creation confirmation: {e}"


def _exec_create_issue(token: str, repo_name: str, payload: dict) -> str:
    try:
        issue = _gh_post(token, f"/repos/{repo_name}/issues", payload)
        return (
            f"Created issue #{issue.get('number')} in {repo_name}: "
            f"{issue.get('html_url')}"
        )
    except _NETWORK_ERRORS:
        return f"GitHub unreachable when creating the issue in {repo_name}."
    except httpx.HTTPStatusError as e:
        return f"Create failed: GitHub returned {e.response.status_code}."


@tool_register(namespace="github", destructive=True)
@tool
def update_issue_status(repo_name: str, issue_number: int, state: str) -> str:
    """Close (state='closed') or reopen (state='open') an issue. Requests
    owner confirmation before executing. `repo_name` is the full `owner/repo`
    form."""
    token = _gh_token()
    if not token:
        return _NOT_CONFIGURED
    if state not in ("open", "closed"):
        return "Error: state must be 'open' or 'closed'."
    from gateway.factory import get_confirmation

    async def _do_update() -> str:
        return await asyncio.to_thread(
            _exec_update_issue, token, repo_name, issue_number, state
        )

    title = _issue_title(token, repo_name, issue_number)
    verb = "Close" if state == "closed" else "Reopen"
    title_line = f': "{title}"' if title else ""
    detail = f"{verb} issue #{issue_number} in {repo_name}{title_line}"

    try:
        return get_confirmation().request_confirmation_sync(
            description=detail,
            action_fn=_do_update,
            result_ok_text=f"Issue #{issue_number} in {repo_name} set to {state}.",
            result_cancel_text=f"Status change for issue #{issue_number} cancelled.",
        )
    except Exception as e:
        return f"Error requesting status-change confirmation: {e}"


def _exec_update_issue(
    token: str, repo_name: str, issue_number: int, state: str
) -> str:
    try:
        _gh_patch(token, f"/repos/{repo_name}/issues/{issue_number}", {"state": state})
        return f"Issue #{issue_number} in {repo_name} is now {state}."
    except _NETWORK_ERRORS:
        return f"GitHub unreachable when updating issue #{issue_number}."
    except httpx.HTTPStatusError as e:
        return f"Update failed: GitHub returned {e.response.status_code}."


@tool_register(namespace="github", destructive=True)
@tool
def add_issue_comment(repo_name: str, issue_number: int, body: str) -> str:
    """Add a comment to an existing issue. Requests owner confirmation before
    executing. `repo_name` is the full `owner/repo` form."""
    token = _gh_token()
    if not token:
        return _NOT_CONFIGURED
    from gateway.factory import get_confirmation

    async def _do_comment() -> str:
        return await asyncio.to_thread(
            _exec_add_comment, token, repo_name, issue_number, body
        )

    title = _issue_title(token, repo_name, issue_number)
    title_line = f': "{title}"' if title else ""
    detail = (
        f"Comment on issue #{issue_number} in {repo_name}{title_line}\n"
        f"Body:\n"
        + _fenced(body)
    )

    try:
        return get_confirmation().request_confirmation_sync(
            description=detail,
            action_fn=_do_comment,
            result_ok_text=f"Comment added to issue #{issue_number} in {repo_name}.",
            result_cancel_text=f"Comment on issue #{issue_number} cancelled.",
        )
    except Exception as e:
        return f"Error requesting comment confirmation: {e}"


def _exec_add_comment(
    token: str, repo_name: str, issue_number: int, body: str
) -> str:
    try:
        c = _gh_post(
            token,
            f"/repos/{repo_name}/issues/{issue_number}/comments",
            {"body": _sign(body)},
        )
        return f"Comment posted: {c.get('html_url')}"
    except _NETWORK_ERRORS:
        return f"GitHub unreachable when commenting on issue #{issue_number}."
    except httpx.HTTPStatusError as e:
        return f"Comment failed: GitHub returned {e.response.status_code}."
