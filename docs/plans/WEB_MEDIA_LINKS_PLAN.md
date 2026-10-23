# Web media links — let Jarvis read a shared reel or video

**Date:** 2026-10-23 · **Status:** planned, not started · **Branch:** `feat/web-media-links`
**Goal:** a link shared into Jarvis from Instagram, TikTok or YouTube is *read* — who posted it,
the caption, what is shown and said — and filed where it belongs, instead of failing or being
guessed at.

---

## Slices

- [ ] **S0 — Measure.** No repo code. Answer the open facts below against the live APIs and
      record the numbers here before S1 starts.
- [ ] **S1 — Instagram and TikTok.** `fetch_url` routes their links to a new media path: yt-dlp
      downloads the video, one side model call describes it, code adds the author and caption.
- [ ] **S2 — YouTube.** `fetch_url` hands a YouTube link to Gemini directly (no download),
      clipped and at low frame rate so any length costs a bounded amount.
- [ ] **S3 — Shared links.** The `web` skill rule for a link shared with no text: read it,
      decide where it belongs, file it, reply in one line. Evals for the behaviour.

S1 and S2 are independent of each other; both need S0. S3 is independent of both: on its own it
already applies to articles, and an unreadable reel simply takes the existing "could not read,
record the bare URL" path until S1 lands.

---

## 1. Where things stand

- **Sharing already reaches Jarvis.** The Telegram share sheet sends a plain text message holding
  the URL (Instagram adds `?igsh=…`). No channel work is needed.
- **`fetch_url` is text-only** (`tools/web/fetch.py`). Tavily `extract` returns markdown, capped at
  8,000 chars head+tail. It reads articles and x.com posts well (measured in
  [URL_FETCH_PLAN](archive/URL_FETCH_PLAN.md)). A video yields nothing; Instagram is probably
  login-walled for Tavily (S0 confirms).
- **Gemini already reads video.** `agent.py` inlines inbound video attachments; a 42 MB video was
  measured to read correctly ([MEDIA_INGESTION_PLAN](archive/MEDIA_INGESTION_PLAN.md)).
- **yt-dlp works without login on public Instagram posts.** Measured 2026-10-23 with yt-dlp
  2026.10.21 in a scratch venv: `instagram.com/p/aye83DjauH/` downloaded (1 MB mp4) with
  uploader and caption. Extractor matching (`suitable()`, a regex, no network) accepts
  `?igsh=` share links, `vm.tiktok.com` short links and `tiktok.com/@u/video/…`, and rejects
  `instagram.com.evil.lan/…` and `http://192.168.1.1/`.
- **The host has no ffmpeg.** yt-dlp cannot merge separate video and audio streams, so format
  selection must require one stream carrying both (S1).
- **`google-genai` 1.68** has `types.VideoMetadata(start_offset, end_offset, fps)` and
  `MediaResolution.MEDIA_RESOLUTION_LOW`.

## 2. How other agents do it

| Agent | Approach | Taken here |
|---|---|---|
| OpenClaw | One CLI per site wrapped as a skill: `summarize` (YouTube/podcasts → text), a yt-dlp skill, `bird` (X via browser cookies) | The shape: a side step that turns media into text |
| hermes-agent | Generic browser toolset (Browserbase, Browser Use, local Camofox with persistent logins) | Not taken: slowest, costliest, and a logged-in browser driven by a model is the largest injection surface |
| Karakeep (bookmark manager) | Doesn't pull from platforms; you share in, it crawls and AI-tags | The share-in model for S3 |

## 3. Design decisions

**One tool.** `fetch_url` stays the single entry point and routes internally. A second tool would
re-open the wrong-tool failure that the URL-fetch incidents came from.

**Text out, not video out.** The media path makes its own model call and returns text. Tool
results are re-sent on every later LLM call in the turn and on following turns until trimmed, so a
text description is paid once where an injected video would be paid repeatedly. (A `ToolMessage`
also cannot carry media blocks today.)

**Author and caption come from code.** The original x.com incident was misattribution. Who posted
it, when, the caption and the duration are copied verbatim from yt-dlp's metadata (S1) or the
provider's page data (S2). The side model writes only what is shown, said and written on screen.

**This reverses two earlier decisions, deliberately.**
- *URL_FETCH Q1, provider-only extraction* — "Jarvis never dereferences a model-supplied URL
  itself." S1 does dereference: Tavily cannot return video, and no hosted provider was found that
  does for Instagram/TikTok at this scale. The risk is bounded by matching only the Instagram and
  TikTok extractors (§S1 Security), never yt-dlp's generic one. S2 keeps the provider-only shape
  (Gemini fetches YouTube itself).
- *"Resist a second special case"* (no per-host adapters). This is not a per-host text adapter;
  it is a content-type tier — video — that the text provider cannot serve at all. Routing goes by
  extractor match, not a hand-kept host list. If more video sites are wanted later, they are an
  entry in the allowlist, not new code.

The `fetch.py` module docstring is rewritten to say what is now fetched locally and why.

---

## S0 — Measure

Scratch scripts only (scratchpad venv); nothing committed except the results recorded here.

1. **Gemini + YouTube URL on `gemini-3-flash-preview`**: does a `file_data` YouTube URL work on
   this preview model? Do `video_metadata` (`end_offset`, `fps`) and low media resolution apply
   to a URL (the docs only show them for uploaded video)? Do `/shorts/` and `youtu.be` URLs work?
   Record input tokens for a ~1 min and a ~20 min video, clipped and unclipped.
2. **Tavily `extract` on a YouTube page**: does it return the title and channel name? If yes, S2
   takes the author from Tavily and stays provider-only; if not, from YouTube oEmbed
   (`youtube.com/oembed?url=…`, a fixed host).
3. **Tavily `extract` on an Instagram reel**: confirm it fails or returns a login wall (this is why
   S1 never falls back to Tavily for Instagram — a login-wall page is non-empty text and would
   pass as content).
4. **TikTok without login**: a public video downloads with uploader and caption.
5. **Side-call cost**: input tokens and latency for a 30 s and a 90 s reel at low resolution.

## S1 — Instagram and TikTok

**Files:** new `tools/web/media.py` (private helpers, no `@tool_register`), `tools/web/fetch.py`
(routing), `requirements.txt` (`yt-dlp`, pinned), `CLAUDE.md` (data tree line), tests.

**Routing.** `fetch_url` asks `media.match(url)` first. It returns the platform when one of the
allowed extractor classes (`InstagramIE`, `TikTokIE`, `TikTokVMIE` — and in S2 `YoutubeIE`)
reports `suitable(url)`; otherwise the URL goes to Tavily exactly as today. yt-dlp is imported
lazily.

**Download.** yt-dlp runs as a subprocess (`sys.executable -m yt_dlp`), so a hung download can be
killed and its memory stays out of the main process.
- `--use-extractors` set to the allowed list (generic is unreachable), no cookies, no
  post-processors, no `--exec`, fixed output template `%(id)s.%(ext)s`.
- Format: one stream with both video and audio, http protocol, ≤480p:
  `b[vcodec!=none][acodec!=none][protocol^=http][height<=480]/b[vcodec!=none][acodec!=none][protocol^=http]`.
- `--max-filesize 50M`; `--write-info-json` for the metadata; mp4/webm only.
- Temp dir: `tempfile.TemporaryDirectory` under `DATA_DIR/web/tmp/`, removed after use; anything
  older than an hour there is swept before each use (a killed subprocess leaves `.part` files).
- A post with no video (photo carousel) returns metadata only (`--ignore-no-formats-error`).

**Time bounds.** A blocking tool is bounded only by its own timeouts (RUNTIME.md). Both steps take
their limit from the turn's remaining time (`turn_budget.TRACKER.get().remaining_s()`):
download ≤ min(45 s, remaining − 90 s), side call ≤ min(90 s, remaining − 30 s). Under ~60 s
remaining, the media path is skipped with a "not enough time left to watch it" result. This also
covers heartbeat runs (120 s deadline) without a scope rule.

**Side call.** One Gemini call with the video inline at low media resolution and
`max_output_tokens` ≈ 1,500. The prompt asks for three parts — what is shown, the speech
transcript, on-screen text — in the video's own language, and forbids naming the poster (code
supplies that). Its own lazily built client, on the same model as the agent via a shared model-name
constant (so `tools/` does not import `agent`).

**Telemetry.** The side call runs inside `telemetry.job("web_media")` and records via
`record_llm_call`, so it lands on its own `scope=job` row and the user turn's `llm_calls` and
`llm_call_index` stay correct. Linking the job row to its parent turn is a possible follow-up, not
part of this slice.

**Result shape** (same 8,000-char cap):

```
Source: https://www.instagram.com/reel/…
Instagram · @handle (Display Name) · posted 2026-10-20 · 0:42
Caption: …
[Video described by a model — the video itself is not kept]
Shown: …
Said: …
On-screen text: …
```

Partial results say what was missed: no audio stream → "the video has no sound"; side call
failed → caption and author only, "the video itself could not be watched"; carousel → "photos
were not viewed".

**Failures** — a small fixed set, each ending with the existing "do not guess, record the bare
URL" instruction, and naming the yt-dlp error class so telemetry can tell a broken extractor from
a private post: private or removed · login required · too long or too large · timed out ·
extractor error (`<class>`).

**Security.** Covered by tests: a non-allowlisted URL (`http://192.168.1.1/`, a lookalike host,
an arbitrary article) never reaches the subprocess; the command line never carries `--cookies`,
`--exec` or a generic extractor.

**yt-dlp upkeep.** Instagram and TikTok extractors break every few weeks. The pin is bumped when
the "extractor error" failure shows up in telemetry. `deploy/deploy.sh` already runs
`pip install -r requirements.txt` whenever the file changed across the pull, so a bump ships with
an ordinary deploy.

**Tests** (offline; subprocess and Gemini mocked): routing matrix (share links, short links,
lookalike hosts, non-media URLs still go to Tavily) · security assertions above · subprocess
timeout → honest string and temp dir gone · size cap · video-only format → no transcript claimed ·
side-call failure → metadata-only result · telemetry lands in a job row and the turn's count is
unchanged · low remaining time skips the media path.

**Verify live (owner):** restart staging; share a public reel, a public TikTok, a photo carousel
and a private post into Telegram.

## S2 — YouTube

**Files:** `tools/web/media.py`, `tools/web/fetch.py`, tests.

- `YoutubeIE.suitable(url)` routes watch, `youtu.be` and `/shorts/` links (pure regex — yt-dlp is
  not used to fetch anything for YouTube).
- One Gemini call with the URL as `file_data`, low media resolution, low `fps`, and
  `end_offset` at 10 minutes (values from S0). No duration lookup is needed: the clip caps the cost
  for any length. The result says "watched the first 10 minutes" when the video may be longer.
- Title and channel from Tavily or oEmbed (per S0 item 2), copied into the header by code.
- Same telemetry, time bounds, result shape and failure set as S1 (plus "private or unlisted").

**Later, not in this slice:** the full transcript of a long video via captions.

**Verify live:** share a Short, a 5-minute video and a 1-hour talk.

## S3 — Shared links

**Files:** `tools/web/SKILL.md`, `tools/web/fetch.py` (docstring mentions reels and videos),
`tests/golden/` (regenerated, reviewed), `evals/cases/chat/` (new cases).

No routing in code: Jarvis decides where a link belongs using the tools it already has. Rule
text, added to the `web` skill body:

> - A link shared with no text is a request: read it, then decide what it is for. A shared link is
>   something the owner wants kept — filing it is the default, dropping it after the reply is the
>   exception.
> - File it where it fits: an existing collection or memory file whose purpose matches; otherwise
>   the reading list. Don't create a new collection unless asked.
> - Reply in one line: who posted it, what it is, and where you filed it — e.g.
>   "@chef_x — 15-min miso pasta · saved to Recipes."
> - If it couldn't be read, say so and still record the bare URL. Never file a guessed
>   description.
> - When the owner adds text ("is this place open Sundays?"), do what they asked; filing is then
>   optional.

This replaces the current "a link appearing in conversation is not by itself a reason to fetch
it", which would leave a bare share unread. That rule is about links *mentioned in passing* inside
a message about something else, and the new wording keeps that distinction.

One open behaviour point: the `web` skill must be active before `fetch_url` is bound, so a bare
share costs one activation round. The skill's description line should make clear that a bare link
is its business; evals show whether that is enough.

**Evals:** a bare recipe reel (fetched → filed in a matching place, one-line reply naming it); a
bare YouTube talk (filed to the reading list); a bare private post (no description, bare URL
recorded); a link with a question (answered, no forced filing).

---

## Out of scope

- **X video.** Tavily already reads X text; detecting a video needs a probe first. Deferred.
- **Logged-in content** (private accounts, the owner's saved posts/bookmarks) — needs account
  cookies or the X API; a separate plan.
- **A share target in the jarvis-app Android app** — lives in the jarvis-app repo.
- **Photo carousels' images** — caption only for now.
- **Keeping media from a link** — a representative frame (the model already watching the video
  can name the moment; PyAV extracts it without system ffmpeg; the creator's cover image is the
  fallback and the only option for YouTube), or the video itself. Both need a file store, a way
  to attach a stored file to a collection item, and a tool to send it back to the owner (channels
  already implement `send_media`; nothing exposes it to the model). That is the file-handling
  work tracked by #81, not a web-only store built here.
- **Re-reading a video later.** The description is terminal; a follow-up question re-fetches.
  Same home: #81.

## Open questions

1. Where does the shared model-name constant live (`config.py` or a small module both `agent.py`
   and `tools/web/media.py` import)? Decide in S1.
