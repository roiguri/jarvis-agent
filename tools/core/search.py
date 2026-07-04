import os
from langchain_core.tools import tool

from tools.registry import tool_register


@tool_register(namespace="core")
@tool
def web_search(query: str) -> str:
    """Search the web for current information using Tavily.

    Use this for questions about recent events, news, release dates, or anything
    that may have changed since the model's training cutoff.

    Args:
        query: The search query string.
    """
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
