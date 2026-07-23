"""
Convert a Markdown-flavoured string to Telegram-compatible HTML.

Telegram HTML supports: <b>, <i>, <u>, <s>, <code>, <pre>, <a href>, <tg-spoiler>
It does NOT support block-level elements like <ul>/<li> — bullets become • text.
"""

import html
import re


def convert(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    in_code_block = False
    code_lang = ""
    code_lines: list[str] = []

    for line in lines:
        # --- fenced code blocks ---
        fence = re.match(r"^```(\w*)\s*$", line)
        if fence:
            if not in_code_block:
                in_code_block = True
                code_lang = fence.group(1)
                code_lines = []
            else:
                block = html.escape("\n".join(code_lines))
                if code_lang:
                    out.append(f'<pre><code class="language-{html.escape(code_lang)}">{block}</code></pre>')
                else:
                    out.append(f"<pre>{block}</pre>")
                in_code_block = False
                code_lang = ""
                code_lines = []
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # --- headings → bold ---
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading:
            content = _inline(heading.group(2))
            out.append(f"<b>{content}</b>")
            continue

        # --- bullet lists ---
        bullet = re.match(r"^(\s*)([-*+])\s+(.*)", line)
        if bullet:
            indent = len(bullet.group(1)) // 2
            prefix = "  " * indent + "•"
            out.append(f"{prefix} {_inline(bullet.group(3))}")
            continue

        # --- numbered lists ---
        numbered = re.match(r"^(\s*)(\d+)[.)]\s+(.*)", line)
        if numbered:
            indent = len(numbered.group(1)) // 2
            prefix = "  " * indent + f"{numbered.group(2)}."
            out.append(f"{prefix} {_inline(numbered.group(3))}")
            continue

        # --- horizontal rule ---
        if re.match(r"^(-{3,}|\*{3,}|_{3,})\s*$", line):
            out.append("─────────────────")
            continue

        out.append(_inline(line))

    # unclosed fence — dump as-is
    if in_code_block:
        out.extend(code_lines)

    return "\n".join(out)


def _inline(text: str) -> str:
    """Apply inline Markdown → HTML transforms to a single line."""
    # Escape HTML special chars first so our tags aren't double-escaped
    text = html.escape(text)

    # Protect inline-code spans before emphasis transforms so underscores in
    # file names (e.g. chat_history.jsonl) do not get interpreted as italics.
    code_spans: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        code_spans.append(match.group(1))
        return f"@@CODESPAN{len(code_spans) - 1}@@"

    text = re.sub(r"`([^`]+)`", _stash_code, text)

    # bold+italic (***text*** or ___text___)
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text)
    text = re.sub(r"___(.+?)___", r"<b><i>\1</i></b>", text)

    # bold (**text** or __text__)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # italic (*text* or _text_) — guard against running into bold leftovers
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>\1</i>", text)

    # strikethrough (~~text~~)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # links [text](url)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r'<a href="\2">\1</a>', text)

    # Restore protected inline-code spans.
    for idx, code in enumerate(code_spans):
        text = text.replace(f"@@CODESPAN{idx}@@", f"<code>{code}</code>")

    return text
