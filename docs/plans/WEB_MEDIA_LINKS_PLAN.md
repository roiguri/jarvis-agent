# Web media links — let Jarvis read a shared reel or video

**Date:** 2026-10-23 · **Status:** S0 and S1 done; S2 next · **Branch:** `feat/web-media-links`
**Goal:** a link shared into Jarvis from Instagram, TikTok or YouTube is *read* — who posted it,
the caption, what is shown and said — and filed where it belongs, instead of failing or being
guessed at.

---

## Slices

- [x] **S0 — Measure.** No repo code. Answer the open facts below against the live APIs and
      record the numbers here before S1 starts.
- [x] **S1 — Instagram and TikTok.** `fetch_url` routes their links to a new media path: yt-dlp
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

### S0 results (2026-10-23, yt-dlp 2026.10.21)

| Check | Result |
|---|---|
| Allowlist (`--use-extractors TikTok,vm.tiktok,Instagram`) | `example.com`, `192.168.1.1`, `instagram.com.evil.lan` all refused — generic unreachable |
| TikTok, no login | Works **only with `curl_cffi`** (impersonation); without it "Unexpected response". 88 s recipe: uploader, full ingredient caption, audio. Progressive h264 720p ≈ 16 MB; h265 540p ≈ 7 MB also carries audio |
| Instagram recipe reel, no login | Works: uploader, channel handle, upload date, full caption. Progressive mp4 (h264, 720p, 7.3 MB) carries audio though metadata says "unknown"; DASH streams are split video/audio |
| Instagram older test reels | Progressive formats silent, no audio stream — those posts simply have no sound |
| Instagram duration | Not in metadata (`NA`) — size cap is the only pre-download bound |
| Gemini + YouTube URL (`gemini-3-flash-preview`) | Works for watch and `/shorts/` URLs. Clipping applies to URLs: 22-min video 122,259 → 22,955 prompt tokens with `end_offset=600s, fps=0.2` (36 s vs 43 s latency). Short: 2,230 → 976 |
| Tavily on YouTube | Returns title, channel ("J. Kenji López-Alt"), post date, description — S2 stays provider-only, no oEmbed |
| Tavily on Instagram | Login-wall page (1,391 chars) that still contains the handle and caption — non-empty, so it would pass today's "no text" check. Confirms: no Tavily fallback for Instagram |
| Inline reel cost (low res) | Instagram 7.3 MB reel: 3,494 prompt tokens, 9.7 s. TikTok 88 s: 8,050 tokens, ~14 s, for both the h265 7.5 MB and h264 16 MB files — h265 reads fine, so prefer the smaller file |
| Output completeness | First run used a "short parts" prompt and a 1,500-token cap; two answers stopped at ~56 output tokens — thinking tokens count toward the cap. With `thinking_level=LOW` and a 4,096 cap both reels finished `STOP` (728 and 992 output tokens, 6–8 s) with a full transcript and on-screen text |
| **Invented specifics** | The ramen reel's "Shown" section listed "1 tablespoon of red Thai curry paste", "1/4 can of coconut milk", "1–2 cloves" — none said, written, or in the caption. The model filled gaps. Every real quantity in both reels was in the transcript or on-screen text. Fix: a domain-neutral prompt that forbids unstated specifics (S1 "Side call"); re-measure on the ramen reel |

Prior art found: OpenClaw's `clawdbites` skill does recipe extraction from public reels the same
way (yt-dlp, no login): caption first, then Whisper on the audio, then frames via ffmpeg. Gemini
reading the video directly replaces the Whisper and frame steps.

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
- Format (measured in S0): sort `-S "res:540,+size"` (smallest near 540p — TikTok's 7 MB h265
  rather than its 16 MB h264; Gemini reads both), select
  `b[vcodec!=none][acodec!=none][protocol^=http]/b[protocol^=http][format_id!*=dash]` — a stream
  known to carry both, else Instagram's progressive mp4, which lists its audio as unknown but
  carries it. Never a DASH stream (video-only without ffmpeg). Whether sound is present is not
  always known from metadata, so the side call is told to say when the video has none.
- `curl_cffi` is required alongside yt-dlp: TikTok fails without browser impersonation.
- `--max-filesize 50M`; `--write-info-json` for the metadata; mp4/webm only.
- Temp dir: `tempfile.TemporaryDirectory` under `DATA_DIR/web/tmp/`, removed after use; anything
  older than an hour there is swept before each use (a killed subprocess leaves `.part` files).
- A post with no video (photo carousel) returns metadata only (`--ignore-no-formats-error`).

**Time bounds.** A blocking tool is bounded only by its own timeouts (RUNTIME.md). Both steps take
their limit from the turn's remaining time (`turn_budget.TRACKER.get().remaining_s()`):
download ≤ min(45 s, remaining − 90 s), side call ≤ min(90 s, remaining − 30 s). Under ~60 s
remaining, the media path is skipped with a "not enough time left to watch it" result. This also
covers heartbeat runs (120 s deadline) without a scope rule.

**Side call.** One Gemini call with the video inline at low media resolution,
`thinking_level=LOW` and `max_output_tokens=4096` (thinking tokens count toward the cap; at 1,500
answers were cut off in S0). A `finish_reason` other than `STOP` marks the record as cut off
rather than presenting it as complete. Its own lazily built client, on the same model as the agent
via a shared model-name constant (so `tools/` does not import `agent`).

The prompt is the same for every video and names no domain — no "ingredients", no "steps". It
asks for a faithful record, not a summary:
- **Said** — everything spoken, word for word, in the original language.
- **On-screen text** — all text that appears, verbatim, in order.
- **Shown** — what happens, in order, with a specific detail (number, amount, name, place, price,
  time) only if it is said or written; never estimated, and a judgement from the picture alone is
  marked as a visual guess.
- Say if there is no sound; never say who posted it (code supplies that).

Shaping the record for a purpose — a recipe into a recipes collection, a place into travel — is
the main model's job, as it already is for an article. An optional `focus` argument (the main
model passing "the recipe") is a possible later addition; it does not help a bare share, where the
purpose is decided after reading.

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
side-call failure → metadata-only result · non-`STOP` finish → marked cut off · telemetry lands
in a job row and the turn's count is unchanged · low remaining time skips the media path.

**Real-model check:** the ramen reel's record contains no amounts that are not said or written
(the S0 failure). Run by hand with the S0 script, not as a committed eval — `evals/cases/` hold
synthetic data only and a real video can't be committed. The S3 filing evals check the main
model does not add amounts either.

### S1 staging checklist

Run on `feat/web-media-links` with the staging bot. A bare link may not be read until S3 changes
the skill rule, so cases 2–6 send the link *with* a question.

**Before**
- [ ] Ramen reel through the S0 script with the shipped prompt — the record lists ingredients with
      no amounts the video doesn't say or show:
      `venv/bin/python <scratchpad>/s0_measure.py --full --video <scratchpad>/dl3/ramen_1.mp4`
- [ ] `venv/bin/python -m pytest -q` passes.
- [ ] Restart: `pct exec 106 -- /app/jarvis_staging/code/scripts/jrestart-staging.sh` — the boot
      block shows `root : /app/jarvis_staging` and branch `feat/web-media-links`.
- [ ] In another terminal: `journalctl -u jarvis-staging -f`.

**Cases** (send to the staging bot)

| # | Send | Expect |
|---|---|---|
| 1 | "what's in this reel?" + `https://www.instagram.com/reel/De1X6FFP_tt/` | Names **@homemade_style_recipe**; describes the peanut-butter ramen; ingredients with **no invented amounts** |
| 2 | Follow-up: "how much peanut butter does it use?" | Says the video doesn't give an amount — no guess |
| 3 | "what's in this?" + `https://www.tiktok.com/@reviskitchenn/video/7699583492484582670` | Names **@reviskitchenn (Revi)**; quantities match the caption (1 lb ground beef, 1/2 cup Parmesan) |
| 4 | "what's this?" + `https://www.instagram.com/reel/ZZZZZZZZZZZ/` | Says it couldn't read it; **no description**; offers to keep the bare URL or asks what it was |
| 5 | "summarize this" + `https://en.wikipedia.org/wiki/Ramen` | Article read as before — the page path still works |
| 6 | "what's this video?" + a YouTube link | Not handled until S2: reads the page (title, channel, description), no video record. Note what it says |
| 7 | The ramen reel link alone, shared from the Instagram app | **Baseline for S3** — note whether it reads, files, or ignores it. No pass/fail |

**After**
- [ ] Every turn in 1–6 shows a `fetch_url` call:
      `JARVIS_ROOT=/app/jarvis_staging venv/bin/python3 scripts/trace.py --last 8`.
- [ ] Side calls landed as job rows, one per video watched (cases 1 and 3), about 3.5k–8k input
      tokens each:
      `JARVIS_ROOT=/app/jarvis_staging venv/bin/python -c "from observability import store; [print(r['ts'][:19], r['llm_calls'], r['input_tokens'], r['outcome']) for r in store.rows(\"SELECT * FROM turns WHERE job='web_media' ORDER BY ts DESC LIMIT 5\")]"`
- [ ] Nothing kept: `ls -A /app/jarvis_staging/jarvis_data/web/tmp/` is empty.
- [ ] No unexpected errors in the journal (a "video description failed" line means case 1 or 3
      fell back to caption only — check which).
- [ ] Each case took well under a minute; note any that came close to the turn deadline.
- [ ] Stop staging: `pct exec 106 -- systemctl stop jarvis-staging.service`.

### S1 staging results (2026-10-23, channel jarvis-app)

| # | Result |
|---|---|
| 1+7 | ✅ The bare share was read even before S3 — the model activated `web` on its own. Named @homemade_style_recipe; record clean, no invented amounts. It asked whether to save rather than filing (S3 baseline) |
| 2 | ⚠️ Said the video gives no amount, then searched the web and offered estimates, crediting "the generous spoonful shown in the video" — a phrase from a *different* video in the search results. Main-model behaviour, not the video path; a skill rule against it was considered and not taken for now |
| 3 | ✅ @reviskitchenn (Revi); every quantity matches the caption |
| 4 | ✅ "private, removed, or requires a login"; nothing described |
| 5 | ✅ Page reading unchanged |
| 6 | ✅ YouTube read as a page (title, channel, description), as expected before S2 |

Side calls: one `web_media` job row per video — reel 3.6k input / 564 output tokens, 8 s;
TikTok 8.1k / 2.0k, 11 s. `fetch_url` took 10–13 s end to end. `web/tmp/` empty afterwards.
The record opened with "Certainly! Here's a record…" and closed with "I hope this helps!", so the
prompt now asks for the three sections only.

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

None. Decided in S1: the shared model name is `config.MODEL`, read by `agent.py` and
`tools/web/media.py`.
