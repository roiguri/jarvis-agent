"""open_file and text files: pages, removed files, and a file result that the next
model call sees once."""
import shutil

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import agent
import config
from files import index, model_input, store
from tests.fakes import FakeLLM, tool_call
from tools.core.files import open_file

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"0" * 64


@pytest.fixture(autouse=True)
def clean():
    for d in (config.FILES_DIR, index.INDEX_DIR):
        shutil.rmtree(d, ignore_errors=True)
    yield


def text_file(n_chars, name="notes.py"):
    return store.add((("x" * 99 + "\n") * (n_chars // 100)).encode(), source="t", filename=name)


def opened(file_id, **kw):
    return open_file.invoke({"file_id": file_id, **kw})


# --- text on arrival ----------------------------------------------------------

def test_a_short_text_file_arrives_whole():
    rec = store.add(b"print('hi')\n", source="t", filename="hi.py")
    (block,) = model_input.content_blocks([rec])
    assert block["text"] == f"\n{rec.marker()}\n<file content>\nprint('hi')\n\n</file content>"


def test_a_long_text_file_arrives_as_its_first_page_with_a_way_on():
    rec = text_file(20_000)
    (block,) = model_input.content_blocks([rec])
    page = block["text"].split("<file content>\n", 1)[1].split("\n</file content>", 1)[0]
    assert len(page) == model_input.PAGE_CHARS
    assert block["text"].endswith(f'Showing characters 0–8,000 of 20,000. If you need the rest: '
                                  f'open_file("{rec.id}", start=8000).')


# --- open_file ------------------------------------------------------------------

def test_open_file_pages_through_text():
    rec = text_file(20_000)
    last = opened(rec.id, start=16_000)
    assert last.startswith(rec.marker()) and "Showing characters 16,000–20,000 of 20,000." in last
    assert "If you need the rest" not in last
    assert "nothing from 30,000" in opened(rec.id, start=30_000)
    big = text_file(30_000)
    assert "Showing characters 0–20,000 of 30,000" in opened(big.id, length=50_000), "capped at a max page"


def test_open_file_returns_media_as_a_file_result():
    rec = store.add(JPEG, source="telegram", caption="what plant is this?")
    store.put_derived(rec.id, "description", "A monstera in a white pot.", producer="p", version="1")
    result = opened(rec.id)
    assert isinstance(result, model_input.ToolFiles) and result.files == [rec]
    assert "source: telegram" in result.text and "With the message: what plant is this?" in result.text
    assert "Record (description): A monstera in a white pot." in result.text


@pytest.mark.parametrize("remove, note", [
    (lambda r: store.delete(r.id, "owner"), "The original was deleted."),
    (lambda r: store.expire(r.id, "retention"), "The original is no longer stored (it expired)."),
])
def test_open_file_on_a_removed_file(remove, note):
    rec = store.add(JPEG, source="t")
    store.put_derived(rec.id, "description", "A monstera.", producer="p", version="1")
    remove(rec)
    result = opened(rec.id)
    assert isinstance(result, str) and result.endswith(note)
    assert "A monstera." in result or "deleted" in note


def test_open_file_on_an_unknown_id():
    assert opened("file:zzzzzzzz") == "No file with id 'file:zzzzzzzz'."


# --- the next model call sees the file once ------------------------------------------

def test_the_file_reaches_the_next_call_only(use_llm):
    rec = store.add(JPEG, source="t")
    llm = use_llm(FakeLLM([
        AIMessage(content="", tool_calls=[tool_call("open_file", {"file_id": rec.id}, 1)]),
        AIMessage(content="", tool_calls=[tool_call("list_memory", {}, 2)]),
        AIMessage(content="It's a monstera."),
    ]))
    agent.ask_jarvis("what was that plant?", "t_open_file")
    seen = [m for m in llm.sent[1] if isinstance(m, ToolMessage)][-1]
    assert any(b.get("type") == "image" and b.get("base64") for b in seen.content)
    after = [m for m in llm.sent[2] if isinstance(m, ToolMessage) and m.name == "open_file"][-1]
    assert isinstance(after.content, str) and after.content.endswith(rec.marker())
