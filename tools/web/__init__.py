"""Web skill — find a page, and read one.

`web_search` and `fetch_url` are two halves of one capability and share a
namespace deliberately: with search always bound and fetch missing, a turn
handed a URL reaches for the only web-shaped tool within reach and searches for
it, which is what produced the incidents in #7. Bound together, that state is
unreachable — either both are available or neither is.

Importing this package imports each module, running its ``@tool_register``
side-effects.
"""

from tools.web.fetch import fetch_url  # noqa: F401
from tools.web.search import web_search  # noqa: F401

__all__ = [
    "fetch_url",
    "web_search",
]
