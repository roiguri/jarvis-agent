"""Read the page at a URL.

Extraction is provider-side: the URL goes to Tavily and the markdown comes
back. Jarvis never dereferences a model-supplied URL itself, so a URL naming a
host on the home network is fetched from Tavily's infrastructure, which cannot
reach it — there is no SSRF surface to guard.
"""
import os
from urllib.parse import urlparse

from langchain_core.tools import tool

from tools.registry import tool_register

# Below the references (OpenClaw 20k, hermes-agent 15k) on purpose: tool results
# are re-sent verbatim on every following turn inside agent.py's 50-message
# window, so every fetched character is paid many times over.
MAX_CHARS = 8_000
_HEAD_CHARS = int(MAX_CHARS * 0.70)
_TAIL_CHARS = int(MAX_CHARS * 0.20)


def _truncate(text: str) -> str:
    """Head + tail, so an article's conclusion and a thread's replies survive.

    The marker deliberately says "this is all that was read" rather than
    pointing at a fuller copy: nothing is written to disk here, so truncation
    is terminal and the text must not imply the rest is retrievable.
    """
    if len(text) <= MAX_CHARS:
        return text
    dropped = len(text) - _HEAD_CHARS - _TAIL_CHARS
    return (
        f"{text[:_HEAD_CHARS]}\n\n"
        f"[... truncated {dropped:,} chars — this is all that was read ...]\n\n"
        f"{text[-_TAIL_CHARS:]}"
    )


@tool_register(namespace="web")
@tool
def fetch_url(url: str) -> str:
    """Read the page at a URL and return its text.

    Use this whenever you are given a link — an article, a post, a repo, a PDF.
    Never try to identify a link by searching for it: a URL or a post id is an
    address, and web_search cannot look one up. Use web_search only when you do
    NOT have a URL and need to find one.

    If this returns an error, say you could not read the page and record the
    bare URL. Do not guess what it contained, do not describe it from the
    surrounding conversation, and do not fall back to web_search.

    Args:
        url: The full http(s) URL to read.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return (
            f"Not a fetchable URL: {url!r}. Only http and https addresses can be read. "
            "Tell the user you could not read it rather than guessing its content."
        )

    try:
        from tavily import TavilyClient
        from tavily.errors import (
            UsageLimitExceededError,
            InvalidAPIKeyError,
            MissingAPIKeyError,
        )

        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            return (
                "Page reading is unavailable: TAVILY_API_KEY is not configured. Tell the "
                "user you could not read the page — do not guess what it contained."
            )

        client = TavilyClient(api_key=api_key)
        response = client.extract(urls=[url], format="markdown")
        results = response.get("results", [])
        if not results:
            return (
                f"Could not read {url} — the page could not be extracted (it may be "
                "login-walled, removed, or fully script-rendered). Tell the user you "
                "could not read it and record the bare URL. Do not guess its content, "
                "and do not search for it."
            )

        result = results[0]
        text = (result.get("raw_content") or "").strip()
        if not text:
            return (
                f"Read {url} but it contained no extractable text. Tell the user you "
                "could not read it. Do not guess its content."
            )

        final_url = result.get("url") or url
        return f"Source: {final_url}\n\n{_truncate(text)}"

    except UsageLimitExceededError:
        return (
            "Page reading quota exhausted for this month. Tell the user you could not "
            "read the page — do not guess what it contained."
        )
    except (InvalidAPIKeyError, MissingAPIKeyError):
        return (
            "Page reading is unavailable: invalid or missing API key. Tell the user you "
            "could not read the page — do not guess what it contained."
        )
    except Exception as e:
        return (
            f"Could not read {url} ({type(e).__name__}: {e}). Tell the user you could not "
            "read it and record the bare URL. Do not guess its content, and do not "
            "search for it."
        )
