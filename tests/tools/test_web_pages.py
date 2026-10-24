"""fetch_url's page path: a page that fits comes back whole; a longer one is kept
as a text file and shown as its first page, with how to read on."""
import sys
import types

import pytest

from files import ids, index, store
from tools.core.files import open_file
from tools.web import fetch, fetch_url

URL = "https://example.com/blog/the-long-read"


@pytest.fixture
def tavily(monkeypatch):
    """Stands in for the Tavily client: `page` is what the next extract returns."""
    state = {"page": ""}

    class Client:
        def __init__(self, api_key):
            pass

        def extract(self, urls, format):
            return {"results": [{"url": urls[0], "raw_content": state["page"]}]}

    errors = types.SimpleNamespace(UsageLimitExceededError=type("U", (Exception,), {}),
                                   InvalidAPIKeyError=type("I", (Exception,), {}),
                                   MissingAPIKeyError=type("M", (Exception,), {}))
    monkeypatch.setitem(sys.modules, "tavily", types.SimpleNamespace(TavilyClient=Client))
    monkeypatch.setitem(sys.modules, "tavily.errors", errors)
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    return state


def test_a_short_page_comes_back_whole_and_is_not_kept(tavily):
    tavily["page"] = "A short page."
    before = len(index.rows("SELECT id FROM files"))
    assert fetch_url.invoke({"url": URL}) == f"Source: {URL}\n\nA short page."
    assert len(index.rows("SELECT id FROM files")) == before


def test_a_long_page_is_kept_and_read_on(tavily):
    text = "".join(f"Paragraph {i}. " + "words " * 30 + "\n" for i in range(400))
    tavily["page"] = text
    out = fetch_url.invoke({"url": URL})
    (rec,) = [store.get(i) for i in ids.find_ids(out)]
    assert (rec.kind, rec.source, rec.filename, rec.origin) == \
        ("text", "web", "example.com-blog-the-long-read.md", {"url": URL})
    assert f"The full text is saved as {rec.marker()}." in out
    assert f"of {len(text.strip()):,}. If you need the rest: open_file(\"{rec.id}\", start=8000)" in out
    assert "truncated" not in out
    rest = open_file.invoke({"file_id": rec.id, "start": 8000})
    assert text[8000:8100] in rest


def test_a_page_that_cannot_be_kept_falls_back_to_head_and_tail(tavily, monkeypatch):
    from files import attachments

    monkeypatch.setattr(attachments, "save_fetched", lambda *a, **k: None)
    tavily["page"] = "x" * 20000
    assert "this is all that was read" in fetch_url.invoke({"url": URL})


def test_page_filenames():
    assert fetch._page_filename("https://news.site/a/b?c=1") == "news.site-a-b-c-1.md"
    assert fetch._page_filename("https://www.youtube.com/watch?v=AODAwMANijw") == \
        "www.youtube.com-watch-v-AODAwMANijw.md"
    assert fetch._page_filename("https://x.com/") == "x.com.md"
