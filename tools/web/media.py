"""Read a video link — an Instagram reel or post, a TikTok, a YouTube video — as text.

One side model call writes a record of what is shown, said and written; code
puts who posted it on top, never the model, because misattributing a link is the
failure this skill was built against. For YouTube that comes from the page as
Tavily reads it, and the model watches the video from its URL. Instagram and
TikTok are downloaded here with yt-dlp, and the poster, date and caption come
from its metadata.

Local fetching is bounded to the extractors in ``_allowed()``: a URL reaches yt-dlp
only when one of them claims it (a regex, no network), and yt-dlp is told it may
use no others, so its generic extractor — which would fetch any address, home
network included — is unreachable. Nothing is kept: the download lives in a
temporary directory for the length of the call.
"""
import base64
import glob
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time

import config
from files import records

logger = logging.getLogger(__name__)

_TMP_DIR = os.path.join(config.DATA_DIR, "web", "tmp")
_STALE_S = 3600

MAX_BYTES = 50 * 1024 * 1024
DOWNLOAD_MAX_S = 45
DESCRIBE_MAX_S = 90
# Each step needs at least this long to be worth starting.
MIN_STEP_S = 15
# Left for the model to answer after the tool returns.
_ANSWER_RESERVE_S = 30
DESCRIBE_MAX_TOKENS = records.VIDEO_MAX_TOKENS
CAPTION_MAX_CHARS = 2500
# YouTube is watched from its URL, never downloaded. Only the opening stretch,
# at a low frame rate: that caps the cost of any length (about 40k input tokens).
YOUTUBE_CLIP_S = 600
YOUTUBE_FPS = 0.5
# The page carries YouTube's own transcript, a better record of speech than the
# model's (which misses voice-over mixed with music), so the YouTube record leaves
# speech out. Page plus a ~400-word record stays under fetch_url's 8,000 chars.
YOUTUBE_PAGE_MAX_CHARS = 5000

# A stream known to carry both picture and sound; else Instagram's progressive
# mp4, which lists its audio as unknown but carries it. Never a DASH stream:
# without ffmpeg those are picture-only or sound-only.
_FORMAT_SORT = "res:540,+size"
_FORMAT = "b[vcodec!=none][acodec!=none][protocol^=http]/b[protocol^=http][format_id!*=dash]"
_MIMES = {"mp4": "video/mp4", "webm": "video/webm"}

_PROMPT = records.VIDEO_PROMPT
_YOUTUBE_PROMPT = (
    records.VIDEO_INTRO + "What is said is covered by a separate transcript, so leave speech out.\n"
    + records.VIDEO_ON_SCREEN + records.VIDEO_SHOWN
    + "Keep the record under about 400 words. Do not say who posted it. Output only the two "
    "sections, with no introduction or closing line."
)

_DONT_GUESS = (
    "Tell the user you could not read it and record the bare URL. Do not guess its "
    "content, and do not search for it."
)


def _allowed():
    """(platform, extractor class, yt-dlp extractor name) for every host read here."""
    from yt_dlp.extractor.instagram import InstagramIE
    from yt_dlp.extractor.tiktok import TikTokIE, TikTokVMIE

    return (
        ("Instagram", InstagramIE, "Instagram"),
        ("TikTok", TikTokIE, "TikTok"),
        ("TikTok", TikTokVMIE, "vm.tiktok"),
    )


def _youtube_ie():
    from yt_dlp.extractor.youtube import YoutubeIE

    return YoutubeIE


def match(url: str) -> str | None:
    """The platform whose extractor claims this URL, or None. No network."""
    try:
        allowed = _allowed()
        youtube = _youtube_ie()
    except ImportError:
        logger.warning("yt-dlp is not installed; video links fall through to page reading")
        return None
    for platform, ie, _ in allowed:
        if ie.suitable(url):
            return platform
    if youtube.suitable(url):
        return "YouTube"
    return None


def youtube_url(url: str) -> str:
    """The canonical watch URL for any YouTube video link (short, mobile, Shorts)."""
    return f"https://www.youtube.com/watch?v={_youtube_ie()._match_id(url)}"


def read_youtube(url: str, page: str | None) -> str:
    """Label a YouTube video from its page and describe it from its URL."""
    if page:
        cut = len(page) > YOUTUBE_PAGE_MAX_CHARS
        head = page[:YOUTUBE_PAGE_MAX_CHARS] + (" […page truncated]" if cut else "")
        header = f"Source: {url}\nYouTube page as published (its transcript is what is said):\n{head}"
    else:
        header = f"Source: {url}\nYouTube · the page could not be read, so who posted it is unknown"

    describe_s = _step_budget(DESCRIBE_MAX_S, _remaining_s(), reserve=_ANSWER_RESERVE_S)
    if describe_s is None:
        return f"{header}\n[not enough time left in this turn to watch the video]"
    part = {
        "type": "media", "file_uri": url, "mime_type": "video/*",
        "video_metadata": {"end_offset": f"{YOUTUBE_CLIP_S}s", "fps": YOUTUBE_FPS},
    }
    record, note = _describe(part, describe_s, prompt=_YOUTUBE_PROMPT, unwatched=(
        "the video itself could not be watched (it may be private, unlisted or age-restricted)"))
    if record is None:
        return f"{header}\n[{note}]"
    marker = (f"[Video described by a model from its first {YOUTUBE_CLIP_S // 60} minutes at most — "
              "what is said is in the page's transcript above; the video itself is not kept]")
    if note:
        marker += f"\n[{note}]"
    return f"{header}\n{marker}\n{record}"


def read(url: str, platform: str) -> str:
    """Download, describe and label one video link. Always returns text for the model."""
    remaining = _remaining_s()
    download_s = _step_budget(DOWNLOAD_MAX_S, remaining, reserve=_ANSWER_RESERVE_S + 60)
    if download_s is None:
        return f"Could not read {url} — not enough time left in this turn to watch it. {_DONT_GUESS}"

    _sweep_stale()
    os.makedirs(_TMP_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=_TMP_DIR) as work:
        problem, stdout = _download(url, work, download_s)
        if problem:
            return f"Could not read {url} — {problem}. {_DONT_GUESS}"
        info = _load_info(work)
        if info is None:
            return f"Could not read {url} — the post's details could not be read. {_DONT_GUESS}"

        header = _header(url, platform, info)
        video = _video_file(work)
        if video is None:
            if "larger than max-filesize" in stdout:
                note = "the video is too large to watch here, so only the caption was read"
            else:
                note = "the post has no video, so only the caption was read"
            return f"{header}\n[{note}]"

        mime = _MIMES.get(video.rsplit(".", 1)[-1].lower())
        if mime is None:
            return f"{header}\n[the video's format can't be watched here, so only the caption was read]"

        describe_s = _step_budget(DESCRIBE_MAX_S, _remaining_s(), reserve=_ANSWER_RESERVE_S)
        if describe_s is None:
            return f"{header}\n[not enough time left in this turn to watch the video — only the caption was read]"

        with open(video, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        part = {"type": "media", "mime_type": mime, "data": data}
        record, note = _describe(part, describe_s, prompt=_PROMPT, unwatched=(
            "the video itself could not be watched — only the caption was read"))
    if record is None:
        return f"{header}\n[{note}]"
    marker = "[Video described by a model — the video itself is not kept]"
    if note:
        marker += f"\n[{note}]"
    return f"{header}\n{marker}\n{record}"


def _remaining_s() -> float | None:
    """Seconds left in the running turn, or None outside one."""
    import turn_budget

    try:
        return turn_budget.TRACKER.get().remaining_s()
    except LookupError:
        return None


def _step_budget(cap: float, remaining: float | None, *, reserve: float) -> float | None:
    """How long a step may take: its cap, cut to what the turn can spare. None = skip it."""
    if remaining is None:
        return cap
    budget = min(cap, remaining - reserve)
    return budget if budget >= MIN_STEP_S else None


def _sweep_stale() -> None:
    """Remove downloads a killed process left behind."""
    cutoff = time.time() - _STALE_S
    for path in glob.glob(os.path.join(_TMP_DIR, "*")):
        try:
            if os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def _download(url: str, work: str, timeout_s: float) -> tuple[str | None, str]:
    """Run yt-dlp in a child process. Returns (problem or None, its stdout)."""
    extractors = ",".join(name for _, _, name in _allowed())
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--ignore-config", "--no-cache-dir", "--no-playlist", "--no-progress", "--no-warnings",
        "--use-extractors", extractors,
        "--socket-timeout", "15",
        "-S", _FORMAT_SORT, "-f", _FORMAT,
        "--max-filesize", str(MAX_BYTES),
        "--ignore-no-formats-error",
        "--write-info-json",
        "-P", work, "-o", "%(id)s.%(ext)s",
        "--", url,
    ]
    # A bare environment: the child needs no API keys, and no inherited config.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": work, "LANG": "C.UTF-8"}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=env)
    except subprocess.TimeoutExpired:
        return "the download timed out", ""
    if proc.returncode == 0:
        return None, proc.stdout
    return _classify(proc.stderr), proc.stdout


def _classify(stderr: str) -> str:
    """One short reason from yt-dlp's error output. The extractor's own message is
    kept for the unrecognised case, so a broken extractor is visible in telemetry."""
    error = next((ln for ln in stderr.splitlines() if ln.startswith("ERROR:")), stderr.strip())
    low = error.lower()
    if "empty media response" in low or "login" in low or "cookies" in low:
        return "the post is private, removed, or needs a login"
    if "private" in low or "not available" in low or "unavailable" in low or "404" in low:
        return "the post is private or was removed"
    return f"the video site could not be read (extractor error: {error[:200]})"


def _load_info(work: str) -> dict | None:
    for path in glob.glob(os.path.join(work, "*.info.json")):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    return None


def _video_file(work: str) -> str | None:
    for path in glob.glob(os.path.join(work, "*")):
        if not path.endswith((".info.json", ".part", ".ytdl")) and os.path.isfile(path):
            return path
    return None


def _header(url: str, platform: str, info: dict) -> str:
    """Who posted it, when, and the caption — copied from the metadata."""
    if platform == "Instagram":
        handle, name = info.get("channel"), info.get("uploader")
    else:
        handle, name = info.get("uploader"), info.get("channel")
    who = f"@{handle}" if handle else (name or "unknown poster")
    if handle and name and name != handle:
        who += f" ({name})"
    parts = [platform, who]
    date = info.get("upload_date") or ""
    if len(date) == 8:
        parts.append(f"posted {date[:4]}-{date[4:6]}-{date[6:]}")
    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        parts.append(f"{int(duration) // 60}:{int(duration) % 60:02d}")
    caption = (info.get("description") or "").strip()
    if len(caption) > CAPTION_MAX_CHARS:
        caption = caption[:CAPTION_MAX_CHARS] + " […caption truncated]"
    lines = [f"Source: {info.get('webpage_url') or url}", " · ".join(parts)]
    lines.append(f"Caption: {caption}" if caption else "Caption: (none)")
    return "\n".join(lines)


def _describe(part: dict, timeout_s: float, *, prompt: str,
              unwatched: str) -> tuple[str | None, str | None]:
    """One side model call on a media part. Returns (record or None, a note on what
    was missed); ``unwatched`` is the note when there is no record at all."""
    text, finish, response = records.side_call(
        [part], prompt, job="web_media", timeout_s=timeout_s,
        max_output_tokens=DESCRIBE_MAX_TOKENS, low_resolution=True)
    if response is None:
        logger.warning("video description failed: %s", finish)
        return None, unwatched
    _charge_turn(response)
    if text is None:
        return None, unwatched
    if finish not in records.NORMAL_FINISH:
        return text, f"the record stops early ({finish}) — the end of the video may be missing"
    return text, None


def _charge_turn(response) -> None:
    """Count the side call's input against the turn's token budget."""
    import turn_budget

    try:
        tracker = turn_budget.TRACKER.get()
    except LookupError:
        return
    usage = getattr(response, "usage_metadata", None) or {}
    tracker.input_tokens += int(usage.get("input_tokens") or 0)
