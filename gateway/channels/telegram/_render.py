"""Telegram-specific rendering for confirmation prompts.

This module emits Telegram-flavored HTML, so it lives inside the Telegram
channel package — the channel-agnostic gateway (contract, store, factory) and
tools never import it. Producers pass a single neutral string; only this layer
turns it into Telegram HTML.

`render_fences_only` deliberately interprets ONLY triple-backtick fenced
blocks (-> <pre> / <pre><code class="language-X">). Every other line is
HTML-escaped verbatim — no headings/rules/emphasis/links — so opaque
confirmation descriptions that legitimately contain '#', '---', '*', '_'
(e.g. GitHub issue references) are shown literally and never mangled. The
fence loop intentionally mirrors gateway/markdown_to_html.convert() rather
than importing it, to keep the Telegram channel self-owned (see issue #48).
"""

import html
import re

_FENCE = re.compile(r"^```(\w*)\s*$")


def _emit_fence(lang: str, lines: list[str]) -> str:
    block = html.escape("\n".join(lines))
    if lang:
        return f'<pre><code class="language-{html.escape(lang)}">{block}</code></pre>'
    return f"<pre>{block}</pre>"


def render_fences_only(text: str) -> str:
    """Render `text` to Telegram HTML, interpreting only ``` fenced blocks.

    Fenced blocks become monospace <pre> (with a language class when given,
    e.g. ```diff). All non-fenced lines are HTML-escaped as-is. An unclosed
    fence at EOF degrades to escaped plain lines (never raises).
    """
    out: list[str] = []
    in_block = False
    lang = ""
    buf: list[str] = []

    for line in text.split("\n"):
        fence = _FENCE.match(line)
        if fence:
            if not in_block:
                in_block, lang, buf = True, fence.group(1), []
            else:
                out.append(_emit_fence(lang, buf))
                in_block, lang, buf = False, "", []
            continue
        if in_block:
            buf.append(line)
        else:
            out.append(html.escape(line))

    # Unclosed fence — emit what we buffered, escaped, so nothing is lost.
    if in_block:
        out.extend(html.escape(b) for b in buf)

    return "\n".join(out)
