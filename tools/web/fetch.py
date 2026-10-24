"""Read the page at a URL.

Pages are read provider-side: the URL goes to Tavily and the markdown comes
back, so a URL naming a host on the home network is fetched from Tavily's
infrastructure, which cannot reach it.

Video links get a record of the video as well (``media.py``). A YouTube link
is watched by the model provider from its URL, with its page read through
Tavily. Tavily cannot return video, so an Instagram or TikTok link is
downloaded and watched here; that local fetch is bounded to those sites'
extractors — any other URL, a home-network address included, still goes only
to providers.
"""
import os
import re
from urllib.parse import urlparse

from langchain_core.tools import tool

from files import attachments, model_input
from tools.registry import tool_register
from tools.web import media

# Below the references (OpenClaw 20k, hermes-agent 15k) on purpose: tool results
# are re-sent verbatim on every following turn inside agent.py's 50-message
# window, so every fetched character is paid many times over.
MAX_CHARS = 8_000
_HEAD_CHARS = int(MAX_CHARS * 0.70)
_TAIL_CHARS = int(MAX_CHARS * 0.20)


def _long_page(text: str, url: str) -> str:
    """A page that fits, whole. A longer one is kept as a text file and shown as
    its first page, with how to read on; if it can't be kept, head and tail."""
    if len(text) <= MAX_CHARS:
        return text
    rec = attachments.save_fetched(text.encode("utf-8"), source="web",
                                   filename=_page_filename(url), origin={"url": url},
                                   mime_hint="text/markdown")
    if rec is None:
        return _truncate(text)
    return (f"The full text is saved as {rec.marker()}.\n"
            f"{model_input.page(text, rec.id, 0, MAX_CHARS)}")


def _page_filename(url: str) -> str:
    """A readable name from the address: host, path and query, as a .md file."""
    parsed = urlparse(url)
    slug = re.sub(r"[^\w.-]+", "-", f"{parsed.netloc}{parsed.path}?{parsed.query}").strip("-.")
    return f"{slug[:100] or 'page'}.md"


def _truncate(text: str) -> str:
    """Head + tail, so an article's conclusion and a thread's replies survive.

    Used where there is no fuller copy (a video's record, or a long page that
    could not be kept), so the marker says "this is all that was read" rather
    than implying the rest is retrievable.
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

    Use this whenever you are given a link — an article, a post, a repo, a PDF,
    or a video such as an Instagram reel, a TikTok or a YouTube video, which comes
    back as who posted it, its caption, and a record of what is shown, said and
    written.
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

    url = url.strip()
    platform = media.match(url)
    try:
        if platform == "YouTube":
            canonical = media.youtube_url(url)
            page, _ = _read_page(canonical, keep=False)
            return _truncate(media.read_youtube(canonical, page))
        if platform:
            return _truncate(media.read(url, platform))
    except Exception as e:
        return (
            f"Could not read {url} ({type(e).__name__}: {e}). Tell the user you could not "
            "read it and record the bare URL. Do not guess its content, and do not "
            "search for it."
        )
    return _read_page(url)[1]


def _read_page(url: str, *, keep: bool = True) -> tuple[str | None, str]:
    """Read a page through Tavily: (its text or None, the result for the model).
    `keep`: store a long page as a file (False when only its text is wanted)."""
    try:
        from tavily import TavilyClient
        from tavily.errors import (
            UsageLimitExceededError,
            InvalidAPIKeyError,
            MissingAPIKeyError,
        )

        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            return None, (
                "Page reading is unavailable: TAVILY_API_KEY is not configured. Tell the "
                "user you could not read the page — do not guess what it contained."
            )

        client = TavilyClient(api_key=api_key)
        response = client.extract(urls=[url], format="markdown")
        results = response.get("results", [])
        if not results:
            return None, (
                f"Could not read {url} — the page could not be extracted (it may be "
                "login-walled, removed, or fully script-rendered). Tell the user you "
                "could not read it and record the bare URL. Do not guess its content, "
                "and do not search for it."
            )

        result = results[0]
        text = (result.get("raw_content") or "").strip()
        if not text:
            return None, (
                f"Read {url} but it contained no extractable text. Tell the user you "
                "could not read it. Do not guess its content."
            )

        final_url = result.get("url") or url
        body = _long_page(text, final_url) if keep else _truncate(text)
        return text, f"Source: {final_url}\n\n{body}"

    except UsageLimitExceededError:
        return None, (
            "Page reading quota exhausted for this month. Tell the user you could not "
            "read the page — do not guess what it contained."
        )
    except (InvalidAPIKeyError, MissingAPIKeyError):
        return None, (
            "Page reading is unavailable: invalid or missing API key. Tell the user you "
            "could not read the page — do not guess what it contained."
        )
    except Exception as e:
        return None, (
            f"Could not read {url} ({type(e).__name__}: {e}). Tell the user you could not "
            "read it and record the bare URL. Do not guess its content, and do not "
            "search for it."
        )
