"""The agent's state helpers: media normalization, stripping seen media from
history, and the active-skills reducer. Trimming the window is covered by the
turn-lifecycle and context-mirror tests."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import agent
from files import model_input

IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
REMOTE_IMAGE = {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}
AUDIO = {"type": "media", "mime_type": "audio/ogg", "data": "AAAA"}
MARKER = {"type": "text", "text": "\n[file:k7m2q9xd · audio · 12 KB]"}


# --- media mime types -----------------------------------------------------------

@pytest.mark.parametrize("raw, want", [
    ("image/png", "image/png"),
    ("IMAGE/PNG", "image/png"),
    ("audio/ogg; codecs=opus", "audio/ogg"),
    ("video/quicktime", "video/mov"),
    ("audio/mpeg", "audio/mp3"),
    ("image/jpg", "image/jpeg"),
    ("application/x-unknown", "application/x-unknown"),
    ("", ""),
])
def test_normalize_media_mime(raw, want):
    assert model_input.normalize_mime(raw) == want


def test_every_alias_lands_on_a_supported_mime():
    supported = set().union(*model_input.SUPPORTED_MEDIA_MIMES.values())
    assert set(model_input.MEDIA_MIME_ALIASES.values()) <= supported


@pytest.mark.parametrize("word, article", [("image", "an"), ("audio", "an"), ("video", "a"),
                                           ("document", "a"), ("Upload", "an")])
def test_article(word, article):
    assert model_input.article(word) == article


# --- stripping seen media -------------------------------------------------------

def test_inline_image_dropped_marker_kept():
    image_marker = {"type": "text", "text": "\n[file:k7m2q9xd · image · 3 KB]"}
    msg = HumanMessage(content=[{"type": "text", "text": "look"}, IMAGE, image_marker], id="m1")
    out = agent._strip_media_blobs(msg)
    assert out.content == [{"type": "text", "text": "look"}, image_marker]
    assert out.id == "m1", "keeps its id, so the reducer replaces rather than appends"


def test_media_blob_dropped_marker_kept():
    out = agent._strip_media_blobs(HumanMessage(content=[AUDIO, MARKER]))
    assert out.content == [MARKER]


@pytest.mark.parametrize("msg", [
    HumanMessage(content="plain text"),
    HumanMessage(content=[{"type": "text", "text": "no media"}, REMOTE_IMAGE]),
    AIMessage(content=[IMAGE]),
])
def test_untouched_messages_returned_as_is(msg):
    assert agent._strip_media_blobs(msg) is msg


def test_reducer_strips_history_but_not_the_new_turn():
    seen = HumanMessage(content=[{"type": "text", "text": "earlier"}, IMAGE], id="old")
    new = HumanMessage(content=[{"type": "text", "text": "now"}, IMAGE], id="new")
    merged = agent._add_and_trim([seen, AIMessage(content="ok", id="a1")], [new])
    assert IMAGE not in merged[0].content, "already seen: stripped"
    assert IMAGE in merged[-1].content, "this turn's media reaches the model"


# --- active-skills reducer ------------------------------------------------------

@pytest.mark.parametrize("existing, new, want", [
    (None, None, set()),
    ({"travel"}, None, {"travel"}),
    ({"travel"}, {"fitness"}, {"fitness"}),
    ({"travel"}, set(), set()),
    (["travel"], ("travel", "web"), {"travel", "web"}),
])
def test_merge_skills(existing, new, want):
    assert agent._merge_skills(existing, new) == want
