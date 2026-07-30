"""Reply layout for slash commands — one contract, two renderers.

A handler returns neutral markdown; whichever channel the command arrived on
renders it. The channels disagree about a bare newline:

| renderer   | how it reads `\\n` between two plain lines                     |
|------------|----------------------------------------------------------------|
| Telegram   | `markdown_to_html.convert()` is line-based — a newline is a newline |
| jarvis-app | CommonMark — a **soft break**: the two lines flow into one paragraph |

So a layout that looks right in the Telegram client can silently collapse on
the app. The shape that satisfies both is: **a bold header, a blank line, then
real `- ` list items.** Telegram rewrites `^[-*+]\\s` into `• `; CommonMark keeps
genuine list items on their own lines. Emphasis is `**bold**` — a single `*` is
*italic* in both, which is not what a header wants.

Handlers build replies from the helpers here instead of hand-rolling layout:
the same soft-break defect was fixed three times in isolation (`/help`,
`/usage`, ...) because the contract was written down nowhere. `check_reply` is
the executable half of it — `scripts/ci/check_command_replies.py` runs it over
every handler's output in CI, so a fourth recurrence fails on the PR rather than
on the owner's phone. Prose version: docs/architecture/GATEWAY.md § Reply
formatting.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

# An empty section says so on its own plain line rather than as a bullet — a
# lone "- (none)" reads as an entry in the list it is denying.
_EMPTY = "_(none)_"

_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+\S")


def _bullet(item: str) -> str:
    """`- item`, preserving any leading indent as a nesting level.

    Two leading spaces nest one level: that clears the parent's content column
    (`- ` is two chars), which is what CommonMark requires for a sub-list, and
    Telegram's converter indents by `len(indent) // 2` to match.
    """
    stripped = item.lstrip(" ")
    indent = " " * (len(item) - len(stripped))
    return f"{indent}- {stripped}"


def section(header: str, items: Iterable[str], *, empty: str = _EMPTY) -> str:
    """A bold header, a blank line, and one `- ` list item per entry.

    Items are plain text — the bullet marker is added here so no handler
    hand-rolls it (a hand-typed `•` is inert prose in CommonMark). Pass `empty`
    to override the placeholder shown when there are no items.
    """
    body = "\n".join(_bullet(i) for i in items)
    return f"**{header}**\n\n{body or empty}"


def kv_section(header: str, pairs: Sequence[tuple[str, object]], **kwargs) -> str:
    """`section` over key/value pairs, rendered `- **key**: value`.

    One convention for the field-list shape, which `/status`, `/skills` and
    `/usage` each used to spell differently.
    """
    return section(header, [f"**{k}**: {v}" for k, v in pairs], **kwargs)


def document(header: str, body: str) -> str:
    """A bold header above raw file content.

    The blank line is the entire point: without it the header runs into the
    file's first line on a CommonMark client.
    """
    return f"**{header}**\n\n{body.strip()}"


def join(*blocks: str) -> str:
    """Blank-line-separated blocks — the only separator both renderers agree on.
    Falsy blocks are dropped so callers can inline a conditional."""
    return "\n\n".join(b for b in blocks if b)


def check_reply(text: str) -> list[str]:
    """Contract violations in a reply string; empty list means it conforms.

    Two rules, both about what CommonMark does that Telegram does not:

    1. A literal `•` is not a list item. Telegram's converter only rewrites
       `^[-*+]\\s`, so a hand-typed bullet passes there and stays inert prose in
       the app.
    2. A plain (non-list, non-heading) line must be followed by a blank line, a
       code fence, or the end of the reply. A second plain line *or* a list item
       after it is a soft break in CommonMark — the lines flow together. (A list
       may legally interrupt a paragraph in CommonMark, but requiring the blank
       line keeps one shape instead of two and costs nothing in either renderer.)

    Fenced code blocks are exempt — both renderers keep them verbatim.

    Only `scripts/ci/check_command_replies.py` calls this; it lives here so the
    rule and the helpers that satisfy it stay in one file.
    """
    problems: list[str] = []
    lines = text.split("\n")
    in_fence = False

    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if "•" in line:
            problems.append(f"line {i + 1}: literal '•' — use a real '- ' list item")
        if not line.strip() or _LIST_ITEM_RE.match(line) or _HEADING_RE.match(line):
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if nxt.strip() and not nxt.lstrip().startswith("```"):
            problems.append(
                f"line {i + 1}: plain line followed by a non-blank line — "
                f"CommonMark soft-breaks these into one paragraph "
                f"({line.strip()[:40]!r} → {nxt.strip()[:40]!r})"
            )
    return problems
