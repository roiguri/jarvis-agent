import os
import re

from langchain_core.tools import tool

from tools.registry import tool_register

# A query that is an address rather than a description. Searching for one cannot
# work: a search engine matches text, and a bare post id matches whatever else
# contains those digits. The incident behind #7 searched `2084703057267286118`
# fourteen times; Tavily returned ASCII code tables, scored 1.0. A relevance
# threshold was measured and rejected for exactly that reason — the score was
# maximal. Guard the shape of the input instead.
_URL_LIKE = re.compile(r"^\s*(https?://|www\.)\S+\s*$", re.I)
_BARE_ID = re.compile(r"^\s*\d{7,}\s*$")


def _looks_like_an_address(query: str) -> bool:
    return bool(_URL_LIKE.match(query) or _BARE_ID.match(query))


@tool_register(namespace="web")
@tool
def web_search(query: str) -> str:
    """Search the web for current information using Tavily.

    Use this for questions about recent events, news, release dates, or anything
    that may have changed since the model's training cutoff.

    Use this only when you do NOT already have a URL. If you have a link, read it
    with fetch_url — searching for a URL or a post id cannot find it.

    Args:
        query: The search query string.
    """
    if _looks_like_an_address(query):
        return (
            f"{query.strip()!r} is an address, not a search query — searching cannot "
            "find it. Use fetch_url to read it directly. If fetch_url has already "
            "failed on it, say you could not read it and record the bare URL; do not "
            "try to identify it by searching."
        )

    try:
        from tavily import TavilyClient
        from tavily.errors import UsageLimitExceededError, InvalidAPIKeyError, MissingAPIKeyError
        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            return "Web search is unavailable: TAVILY_API_KEY is not configured. Answer from training knowledge and note that results may be outdated."
        client = TavilyClient(api_key=api_key)
        response = client.search(query, max_results=5)
        results = response.get("results", [])
        if not results:
            return "No results found."
        lines = []
        for r in results:
            lines.append(f"**{r.get('title', 'No title')}**")
            lines.append(r.get("url", ""))
            lines.append(r.get("content", ""))
            lines.append("")
        return "\n".join(lines).strip()
    except UsageLimitExceededError:
        return "Web search quota exhausted for this month. Answer from training knowledge and let the user know results may be outdated."
    except (InvalidAPIKeyError, MissingAPIKeyError):
        return "Web search is unavailable: invalid or missing API key. Answer from training knowledge and note that results may be outdated."
    except Exception as e:
        return f"Web search failed ({type(e).__name__}): {e}. Answer from training knowledge if possible."
