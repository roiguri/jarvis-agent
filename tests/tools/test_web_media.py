"""fetch_url's video path, exercised with yt-dlp and the model faked — no network.

What it guards: which URLs may reach the local downloader at all, that the
download runs bounded and contained, that the poster and caption come from
metadata, and that every way of not seeing the video is said plainly.
"""

import json
import os
import subprocess

import pytest
from langchain_core.messages import AIMessage

import turn_budget
from observability import store, telemetry
from tools.web import fetch_url, media

REEL = "https://www.instagram.com/reel/De1X6FFP_tt/?igsh=abc"
TIKTOK = "https://www.tiktok.com/@reviskitchenn/video/7699583492484582670"

IG_INFO = {
    "id": "De1X6FFP_tt", "channel": "homemade_style_recipe", "uploader": "Homemade Recipe",
    "upload_date": "20261023", "duration": None,
    "webpage_url": "https://www.instagram.com/reel/De1X6FFP_tt/",
    "description": "SPICY PEANUT BUTTER RAMEN 🍜 weeknight dinner",
}
TT_INFO = {
    "id": "7699583492484582670", "uploader": "reviskitchenn", "channel": "Revi",
    "upload_date": "20261022", "duration": 88,
    "webpage_url": TIKTOK, "description": "French onion meatballs. Ingredients: 1 lb ground beef",
}
RECORD = "Said: this is my peanut butter ramen\nOn-screen text: ...\nShown: ..."


class FakeRun:
    """Stands in for subprocess.run: records the call, writes what yt-dlp would."""

    def __init__(self, info=None, video=True, ext="mp4", returncode=0, stderr="", stdout="",
                 timeout=False):
        self.info, self.video, self.ext = info, video, ext
        self.returncode, self.stderr, self.stdout, self.timeout = returncode, stderr, stdout, timeout
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        work = cmd[cmd.index("-P") + 1]
        if self.info is not None:
            with open(os.path.join(work, f"{self.info['id']}.info.json"), "w") as f:
                json.dump(self.info, f)
            if self.video:
                with open(os.path.join(work, f"{self.info['id']}.{self.ext}"), "wb") as f:
                    f.write(b"\x00\x00\x00\x18ftypmp42 fake video")
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


class FakeVideoLLM:
    """Stands in for ChatGoogleGenerativeAI in the side call."""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.messages = None
        FakeVideoLLM.instances.append(self)

    def invoke(self, messages):
        self.messages = messages
        behaviour = FakeVideoLLM.behaviour
        if isinstance(behaviour, Exception):
            raise behaviour
        text, finish = behaviour
        return AIMessage(
            content=text,
            usage_metadata={"input_tokens": 3500, "output_tokens": 700, "total_tokens": 4200},
            response_metadata={"finish_reason": finish, "model_name": "fake-model"},
        )


@pytest.fixture
def fake_run(monkeypatch):
    def install(run):
        monkeypatch.setattr(media.subprocess, "run", run)
        return run
    return install


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    import langchain_google_genai

    FakeVideoLLM.instances = []
    FakeVideoLLM.behaviour = (RECORD, "STOP")
    monkeypatch.setattr(langchain_google_genai, "ChatGoogleGenerativeAI", FakeVideoLLM)
    return FakeVideoLLM


def read(url):
    return fetch_url.invoke({"url": url})


def tmp_entries():
    return os.listdir(media._TMP_DIR) if os.path.isdir(media._TMP_DIR) else []


# --- Which URLs reach the downloader ---------------------------------------------

@pytest.mark.parametrize("url,platform", [
    ("https://www.instagram.com/reel/De1X6FFP_tt/?igsh=abc", "Instagram"),
    ("https://www.instagram.com/p/aye83DjauH/", "Instagram"),
    ("https://instagram.com/reel/De1X6FFP_tt/", "Instagram"),
    ("https://www.tiktok.com/@u/video/7699583492484582670", "TikTok"),
    ("https://vm.tiktok.com/ZMabc123/", "TikTok"),
    ("https://instagram.com.evil.lan/reel/De1X6FFP_tt/", None),
    ("https://evil.lan/www.instagram.com/reel/x/", None),
    ("http://192.168.1.1/", None),
    ("https://example.com/article", None),
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "YouTube"),
    ("https://youtu.be/jNQXAC9IVRw?si=abc", "YouTube"),
    ("https://www.youtube.com/shorts/uoXhLoND1q4", "YouTube"),
    ("https://www.youtube.com/@JKenjiLopezAlt", None),
    ("https://youtube.com.evil.lan/watch?v=jNQXAC9IVRw", None),
    ("https://x.com/i/status/2085023484913012992", None),
])
def test_match_claims_only_allowed_sites(url, platform):
    assert media.match(url) == platform


def test_other_urls_never_reach_the_downloader(fake_run, monkeypatch):
    run = fake_run(FakeRun(info=IG_INFO))
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    for url in ("http://192.168.1.1/", "https://instagram.com.evil.lan/reel/x/",
                "https://example.com/article"):
        out = read(url)
        assert "TAVILY_API_KEY is not configured" in out, "went the page-reading way"
    assert run.calls == []


def test_download_is_contained(fake_run):
    run = fake_run(FakeRun(info=IG_INFO))
    read(REEL)
    [(cmd, kw)] = run.calls
    assert cmd[cmd.index("--use-extractors") + 1] == "Instagram,TikTok,vm.tiktok"
    assert "--ignore-config" in cmd and "--no-cache-dir" in cmd
    assert not any(c.startswith(("--cookies", "--exec", "--force-generic")) for c in cmd)
    assert cmd[-2:] == ["--", REEL], "the URL can never be read as an option"
    assert set(kw["env"]) == {"PATH", "HOME", "LANG"}, "no API keys passed to the child"
    assert kw["timeout"] == media.DOWNLOAD_MAX_S
    assert tmp_entries() == [], "nothing kept"


# --- What comes back ---------------------------------------------------------------

def test_reel_comes_back_labelled_from_metadata(fake_run, fake_model):
    fake_run(FakeRun(info=IG_INFO))
    out = read(REEL)
    assert "Instagram · @homemade_style_recipe (Homemade Recipe) · posted 2026-10-23" in out
    assert "Caption: SPICY PEANUT BUTTER RAMEN" in out
    assert "the video itself is not kept" in out
    assert RECORD in out
    [llm] = fake_model.instances
    assert llm.kwargs["thinking_level"] == "low"
    assert llm.kwargs["max_output_tokens"] == media.DESCRIBE_MAX_TOKENS
    media_part = llm.messages[0].content[0]
    assert (media_part["type"], media_part["mime_type"]) == ("media", "video/mp4")
    assert "Do not say who posted it" in llm.messages[0].content[1]["text"]


def test_tiktok_handle_and_duration(fake_run):
    fake_run(FakeRun(info=TT_INFO))
    out = read(TIKTOK)
    assert "TikTok · @reviskitchenn (Revi) · posted 2026-10-22 · 1:28" in out


def test_long_caption_is_truncated(fake_run):
    fake_run(FakeRun(info={**IG_INFO, "description": "x" * (media.CAPTION_MAX_CHARS + 500)}))
    assert "[…caption truncated]" in read(REEL)


# --- Every way of not seeing the video is said -------------------------------------

def test_private_post(fake_run, fake_model):
    fake_run(FakeRun(returncode=1, stderr=(
        "ERROR: [Instagram] De1X6FFP_tt: Instagram sent an empty media response. "
        "Check if this post is accessible in your browser without being logged-in.")))
    out = read(REEL)
    assert "private, removed, or needs a login" in out
    assert "Do not guess" in out
    assert fake_model.instances == []


def test_broken_extractor_names_itself(fake_run):
    fake_run(FakeRun(returncode=1, stderr="ERROR: [TikTok] 769: Unexpected response from webpage request"))
    out = read(TIKTOK)
    assert "extractor error" in out and "Unexpected response" in out


def test_timeout(fake_run):
    fake_run(FakeRun(timeout=True))
    out = read(REEL)
    assert "timed out" in out and "Do not guess" in out
    assert tmp_entries() == []


def test_too_large_reads_caption_only(fake_run, fake_model):
    fake_run(FakeRun(info=IG_INFO, video=False,
                     stdout="[download] File is larger than max-filesize (60 > 50). Aborting."))
    out = read(REEL)
    assert "Caption: SPICY PEANUT BUTTER RAMEN" in out
    assert "too large" in out and "only the caption" in out
    assert fake_model.instances == []


def test_post_without_video_reads_caption_only(fake_run):
    fake_run(FakeRun(info=IG_INFO, video=False))
    assert "no video, so only the caption" in read(REEL)


def test_unwatchable_format(fake_run, fake_model):
    fake_run(FakeRun(info=IG_INFO, ext="flv"))
    assert "can't be watched here" in read(REEL)
    assert fake_model.instances == []


def test_side_call_failure_keeps_the_caption(fake_run, fake_model):
    fake_run(FakeRun(info=IG_INFO))
    fake_model.behaviour = TimeoutError("deadline")
    out = read(REEL)
    assert "Caption: SPICY PEANUT BUTTER RAMEN" in out
    assert "could not be watched" in out
    assert "not kept" not in out, "no record claimed"


def test_cut_off_record_is_marked(fake_run, fake_model):
    fake_run(FakeRun(info=IG_INFO))
    fake_model.behaviour = ("Said: this is my", "MAX_TOKENS")
    out = read(REEL)
    assert "Said: this is my" in out
    assert "stops early (MAX_TOKENS)" in out


# --- Bounded by the turn -----------------------------------------------------------

def _tracker(remaining_s):
    tracker = turn_budget.TurnTracker(turn_budget.POLICIES["user"])
    tracker.started -= tracker.policy.budget.deadline_s - remaining_s
    return tracker


def test_little_time_left_skips_the_download(fake_run):
    run = fake_run(FakeRun(info=IG_INFO))
    token = turn_budget.TRACKER.set(_tracker(remaining_s=40))
    try:
        out = read(REEL)
    finally:
        turn_budget.TRACKER.reset(token)
    assert "not enough time left" in out
    assert run.calls == []


def test_download_timeout_shrinks_with_the_turn(fake_run):
    run = fake_run(FakeRun(info=IG_INFO))
    token = turn_budget.TRACKER.set(_tracker(remaining_s=130))
    try:
        read(REEL)
    finally:
        turn_budget.TRACKER.reset(token)
    [(_, kw)] = run.calls
    assert media.MIN_STEP_S <= kw["timeout"] <= 130 - 90


def test_side_call_is_its_own_job_and_charges_the_turn(fake_run):
    fake_run(FakeRun(info=IG_INFO))
    tracker = _tracker(remaining_s=250)
    token = turn_budget.TRACKER.set(tracker)
    id_token = telemetry.TURN_ID.set("turn-web-media")
    telemetry.record_turn_start(thread_id="owner", scope="user")
    try:
        read(REEL)
    finally:
        telemetry.record_turn_end()
        telemetry.TURN_ID.reset(id_token)
        turn_budget.TRACKER.reset(token)
    rows = {r["scope"]: r for r in store.rows("SELECT * FROM turns")}
    assert rows["user"]["llm_calls"] == 0, "the turn's own call count is untouched"
    assert (rows["job"]["job"], rows["job"]["llm_calls"]) == ("web_media", 1)
    assert rows["job"]["input_tokens"] == 3500
    assert tracker.input_tokens == 3500, "the turn's token budget sees the side call"


# --- YouTube: watched from its URL, labelled from its page --------------------------

KENJI = "https://youtu.be/AODAwMANijw?si=share"
KENJI_CANONICAL = "https://www.youtube.com/watch?v=AODAwMANijw"
KENJI_PAGE = ("# For Better Baked Ziti, Don't Boil Your Pasta: Soak It.\n"
              "## Channel: J. Kenji López-Alt\nPosted: 2026-01-21\n### Description\nSoak the pasta.")


@pytest.fixture
def fake_page(monkeypatch):
    from tools.web import fetch

    seen = []

    def install(page):
        def read_page(url):
            seen.append(url)
            return (page, f"Source: {url}\n\n{page}") if page else (None, "Could not read")
        monkeypatch.setattr(fetch, "_read_page", read_page)
        return seen
    return install


@pytest.mark.parametrize("url", [
    "https://youtu.be/AODAwMANijw?si=share",
    "https://m.youtube.com/watch?v=AODAwMANijw&t=30",
    "https://www.youtube.com/watch?v=AODAwMANijw&list=PL1",
])
def test_youtube_links_are_canonical(url):
    assert media.youtube_url(url) == KENJI_CANONICAL


def test_youtube_watched_from_url_and_labelled_from_page(fake_page, fake_run, fake_model):
    seen = fake_page(KENJI_PAGE)
    run = fake_run(FakeRun())
    out = read(KENJI)
    assert seen == [KENJI_CANONICAL]
    assert run.calls == [], "YouTube is never downloaded"
    assert "## Channel: J. Kenji López-Alt" in out
    assert "first 10 minutes at most" in out and RECORD in out
    [llm] = fake_model.instances
    part = llm.messages[0].content[0]
    assert part["file_uri"] == KENJI_CANONICAL
    assert part["video_metadata"] == {"end_offset": "600s", "fps": media.YOUTUBE_FPS}


def test_youtube_page_unreadable_still_watched(fake_page):
    fake_page(None)
    out = read(KENJI)
    assert "who posted it is unknown" in out
    assert RECORD in out


def test_youtube_unwatchable(fake_page, fake_model):
    fake_page(KENJI_PAGE)
    fake_model.behaviour = ValueError("400 INVALID_ARGUMENT")
    out = read(KENJI)
    assert "## Channel: J. Kenji López-Alt" in out
    assert "private, unlisted or age-restricted" in out
    assert "not kept" not in out


def test_youtube_long_page_is_cut(fake_page):
    fake_page("x" * (media.YOUTUBE_PAGE_MAX_CHARS + 100))
    assert "[…page truncated]" in read(KENJI)


def test_youtube_page_transcript_kept_whole_under_the_cap(fake_page):
    page = KENJI_PAGE + "\n### Transcript\n" + "soak the ziti " * 230
    assert len(page) < media.YOUTUBE_PAGE_MAX_CHARS
    fake_page(page)
    out = read(KENJI)
    assert page in out and "[…page truncated]" not in out


def test_youtube_record_leaves_speech_to_the_transcript(fake_page, fake_run, fake_model):
    fake_page(KENJI_PAGE)
    read(KENJI)
    fake_run(FakeRun(info=IG_INFO))
    read(REEL)
    youtube, reel = (llm.messages[0].content[1]["text"] for llm in fake_model.instances)
    assert "Said:" not in youtube and "leave speech out" in youtube
    assert "Said:" in reel
    for prompt in (youtube, reel):
        assert "Never estimate or fill in" in prompt and "Do not say who posted it" in prompt


def test_youtube_full_page_and_record_fit_without_a_middle_cut(fake_page, fake_model):
    fake_page("x" * (media.YOUTUBE_PAGE_MAX_CHARS + 3000))
    fake_model.behaviour = ("w" * 2400, "STOP")  # ~400 words
    out = read(KENJI)
    assert "truncated" not in out.replace("[…page truncated]", ""), "no head+tail cut"
