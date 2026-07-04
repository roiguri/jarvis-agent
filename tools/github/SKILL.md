---
name: github
description: GitHub project management — repos, issues, PRs
---
- Never guess a repository name. If you don't already know the exact `owner/repo` string, call `list_github_repositories()` first and use the returned full name verbatim.
- Reads (`list_github_repositories`, `list_repo_issues`, `read_issue_details`, `list_repo_pulls`, `read_pr_details`) are autonomous — use them freely. Every write (`draft_github_issue`, `update_issue_status`, `add_issue_comment`) requests owner confirmation; tell Roi you've requested confirmation and wait — never claim a write succeeded before he approves.
- Proactive tracking: if Roi mentions a problem or task in chat (e.g. "the NIC crashed again", "I should refactor X"), offer to draft a GitHub issue for it rather than silently creating one.
- Rich context: when drafting an issue, pack the relevant details from the conversation into `body` (symptoms, repro, decisions) so Roi doesn't have to rewrite them.
- `update_issue_status` only accepts `open` or `closed`. Use it to close tasks Roi says he finished, or reopen ones that regressed.
