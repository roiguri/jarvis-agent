# Inbound media ingestion — file-kind attachments + inline size guard

**Status:** ✅ **SHIPPED** — slices 1–5 landed 2026-07-30; archived.
**Verified:** slice 1 live (a 42 MB video from the app read correctly, which also measured away this
plan's central premise — see below). Slices 2–5 have **synthetic coverage only** and have not been
exercised against the live API; the untested bets are the mime alias normalization
(`video/quicktime`→`video/mov`, `audio/mpeg`→`audio/mp3`) and PDF ingestion end to end.
**Left open, by decision:** text and code files → **#60**; multi-turn access to media already sent →
**#61**. Telegram still has no handler for music files, animations or video notes — the same
missing-handler defect this plan fixed for documents, recorded in slice 5 and not fixed.

**Issue:** #51 — "Ingest inbound file-kind attachments (PDF + video) from the app channel".
**Date:** started 2026-07-29, shipped 2026-07-30.
**Touches:** `agent.py` (model boundary, channel-agnostic), `gateway/channels/jarvis_app/` and
`gateway/channels/telegram/` (kind mapping, channel-owned), `docs/architecture/GATEWAY.md`
(neutral vocabulary).
**Companion:** [../app-plans/EXECUTION_PLAN.md](../app-plans/EXECUTION_PLAN.md) — Stage C shipped the
transport this plan builds on (C4 inbound media); `file`-kind ingestion was deferred out of it.

---

## Manager summary

**Problem.** A video or PDF sent from the app reaches Jarvis but never reaches the model. The hub's
`_kind_for_mime` maps `image/*`→`image`, `audio/*`→`audio`, **everything else→`file`**, so PDFs and
videos arrive tagged `file`. `agent.py`'s media loop dispatches on the `kind` string and only
handles `image`/`audio`/`video`, so a `file` falls into the honest-note branch: the blob is
downloaded, cached, and then described as *"[Received a file (video/mp4) attachment I can't read
yet.]"*. No silent drop, but no ingestion — and `agent.py` already contains a working `video`
branch that this path can never reach.

**The edge that was assumed, and measured away.** This plan originally held that Gemini caps the
whole request at ~20 MB, and that base64 (+33%) put the real raw-bytes ceiling at ~15 MB — which
would have made size the dominant constraint. **Both halves are wrong.** The API documents inline
media at **<100 MB**; the ~20 MB figure is its recommendation for *preferring* the Files API, not a
limit. Measured on 2026-07-30 against `gemini-3-flash-preview`: a **42 MB** mp4 sent from the app
inlined and was read correctly (specific detail returned — banner text, Hebrew on-screen text,
scene) with no error, at ~56 MB on the wire after base64.

So no current channel can produce media the model will refuse: the hub caps uploads at 50 MB and
Telegram's Bot API at 20 MB, both under the inline ceiling. Size stops being a design driver and
becomes a backstop (slice 2). What the byte count *cannot* see is duration — the API pairs the
100 MB inline limit with a **<1 min** guideline for video, an axis no size guard detects; unmeasured,
and out of scope here.

Capping the hub was never the right fix regardless: it pushes a model constraint into transport, so
a model swap would need a hub deploy plus an app-version skew window, and an oversized send would
fail as a composer upload error instead of something Jarvis can answer conversationally.

**Plan.** Five shippable slices (a sixth was reframed and moved to #61), split along one principle: **channels translate
into the neutral vocabulary; the model boundary owns model limits.** Each slice is one topic — the
size backstop, the `document` branch and the mime allowlist all live in `agent.py`'s media loop, but
they answer different questions (*how much may I send?* / *what kinds can I read?* / *which formats
of that kind?*), so they ship and revert separately.

| # | Slice | Layer | Size | Effect |
|---|---|---|---|---|
| 1 | Neutral `document` kind + app-router kind mapping | channel-owned | small | `file`+mime resolves to a real kind at the boundary, as Telegram already does |
| 2 | Inline size backstop in `agent.py` | model boundary | small | Unreachable ceiling made honest instead of opaque — **for every channel** |
| 3 | `document` branch in `agent.py` | model boundary | small | PDFs ingested rather than described as unreadable |
| 4 | Per-kind mime allowlist in `agent.py` | model boundary | small | An unsupported format meets an honest note, not an opaque API error — **fixes `image/gif` too** |
| 5 | Telegram document handler | channel-owned | small | Closes a **silent drop**: a PDF sent over Telegram currently reaches nothing at all |
| 6 | ~~Files API for oversized media~~ | — | — | **Moved to #61** — reframed as multi-turn media access, not a size workaround |

**What slice 2 is, after the measurement.** It was scoped as an urgent hole: Telegram has *no* size
handling anywhere (`gateway/channels/telegram/router.py:133` downloads and stores unconditionally),
so a 20 MB Bot API download would have blown the assumed ~15 MB ceiling. With the real ceiling at
100 MB and both channels capped below it, **that hole does not exist** and the guard cannot fire
today. It is kept, at the documented ceiling, for two reasons: a future channel (or a raised hub cap
— see *Not in scope*) inherits it for free, and the failure it converts is an opaque API error into
a sentence Jarvis can say. It is no longer urgent, and no longer blocks anything.

**Risk posture.** Slices 1–5 are additive — new kinds, a backstop, two new branches, and a handler
on paths that currently terminate in a text note or in nothing. Each is independently revertable.
The one change carrying a real latent hazard is the `file_uri` work, which is why it left this plan
for #61 rather than sitting here as a slice someone might pick up.

**Ordering.** The original ordering constraint — *land the guard before anything that makes new
bytes reachable* — is void: slice 1 shipped, a 42 MB video went through it unguarded, and the model
read it. One soft dependency remains: slice 5 classifies broadly by mime prefix, so it wants slice 4
to have landed, or an unsupported format reaches the model as an opaque error instead of a note.

---

## Design: where each decision lives

The gateway's neutral vocabulary is `image | video | audio` (`docs/architecture/GATEWAY.md:346`).
PDFs have no neutral kind, so the vocabulary gains **`document`**, and **`file` keeps its meaning as
"opaque, unidentified"** — which already routes to the honest note. The catch-all stays honest
instead of quietly becoming a second dispatch axis inside the agent.

This preserves the existing invariant that `agent.py` branches on `kind` and never re-interprets a
channel's wire format. Mime lives on in the attachment dict, used for the data URL and the media
block's `mime_type`, as it is today.

| Decision | Owner | Rationale |
|---|---|---|
| Which wire format means which kind | channel router | Only the channel knows its source's vocabulary |
| Which kinds the model can read | `agent.py` | Model capability, not transport |
| Which *formats* of a kind it can read | `agent.py` | Same table the API publishes; one copy, not one per channel |
| How large an inline blob may be | `agent.py` | Model limit, not transport |
| How large an upload may be | hub | Storage/bandwidth policy, independent of any model |

---

## Slice 1 — Neutral vocabulary + app-router mapping (channel-owned)

**`gateway/channels/jarvis_app/router.py`** — in `_download_attachments` (`:174`), resolve the
neutral kind from the hub's kind + mime before building the attachment dict:

| hub kind | mime | neutral kind |
|---|---|---|
| `image` | `image/*` | `image` |
| `audio` | `audio/*` | `audio` |
| `file` | `video/mp4`, `video/webm` | `video` |
| `file` | `application/pdf` | `document` |
| `file` | anything else | `file` (unchanged → honest note) |

Mirrors `gateway/channels/telegram/router.py:95-102`, which already tags `kind="video"` at the
boundary. Pass the resolved kind to `media_cache.save` as well, so the cached filename reflects what
the blob actually is.

**`gateway/channels/jarvis_app/media_cache.py`** — add `"video": ".mp4"` and `"document": ".pdf"` to
`_EXT` (`:23`). Cosmetic (its docstring notes the agent branches on mime, not the filename), but it
keeps the cache dir legible.

**`docs/architecture/GATEWAY.md:346`** — vocabulary gains `document`; add a line stating that `file`
means "unidentified — surfaced to the agent as text, never fed to the model".

## Slice 2 — Inline size backstop (model boundary, channel-agnostic)

One question: *how much may I send the model?* Independent of which kinds are readable — it applies
to the image, audio and video branches that exist today, and to `document` whenever slice 3 lands.

In `agent.py`, media loop at `:604-670`: a size check between the kind check (`:618`) and the read
(`:628`). Use `os.path.getsize` *before* `open()`, so an oversized blob is never pulled into memory
only to be refused. Constant near `MAX_MESSAGES`, set to the API's documented inline ceiling rather
than to a derived figure:

```python
INLINE_MEDIA_MAX_BYTES = 100 * 1024 * 1024
```

Over the limit → the same honest-note shape as `:618`, sized and channel-neutral:
`[Received a video (128 MB) — too large for me to read.]` No channel, hub, or cap is named. The
article is chosen by vowel, so the note reads "an image", not "a image".

**This cannot fire on any channel that exists today** — that is the point of keeping it small and
never letting it grow a policy. If it ever *does* fire, the ceiling moved or a channel arrived
without an upload cap; either way the number here is the single place to change.

## Slice 3 — `document` branch (model boundary, channel-agnostic)

The other question: *what can I read?* Also in `agent.py`'s media loop, and deliberately not bundled
with the guard — a readable-kind regression and a size-ceiling regression have nothing in common,
and either should be revertable without the other.

1. **`document` branch** alongside `video` (`:657`): a `{"type": "media", "mime_type": …,
   "data": b64}` block plus the lightweight text hint, identical in shape to the audio and video
   branches. `_strip_media_blobs` (`:71`) already drops `media` blocks carrying `data`, so nothing
   new accumulates in thread state.

2. Add `"document"` to the readable-kinds tuple at `:618`.

## Slice 4 — Per-kind mime allowlist (model boundary, channel-agnostic)

**Why it exists.** `document` is the third kind whose readability depends on the *format*, not just
the kind — the API supports exactly one document mime (`application/pdf`), five image mimes, six
audio, nine video. Today `agent.py` checks the kind and never looks at the mime, so an unsupported
format is inlined and fails as an opaque API error. That defect is already live: `image/gif` is
allowlisted by the hub and would inline as an `image`, and the API does not accept GIF. It was
previously recorded under *Not in scope*; this slice absorbs it.

It is also what lets slice 5 classify broadly. If channels enumerated the model's supported formats
themselves, the model's capability table would be copied into every channel and go stale on every
model swap — the exact layering this plan exists to avoid. Channels answer *what kind of thing is
this?*; the model boundary answers *can I read this format?*

Two constants near `INLINE_MEDIA_MAX_BYTES`, then one check in the media loop after the size
backstop and before the branches:

- **`SUPPORTED_MEDIA_MIMES`** — `{kind: {mime, …}}`, transcribed from the API's format lists.
- **`MEDIA_MIME_ALIASES`** — the API names some formats differently from what clients emit (`.mov`
  arrives as `video/quicktime`, `.mp3` as `audio/mpeg`). Normalize to the documented spelling before
  the check *and* before the blob is sent, so the media block carries a mime the API recognizes.

An empty mime skips the check — the existing per-branch defaults (`image/jpeg` guessed from the
filename, `audio/ogg`, `video/mp4`) already handle that case and predate this slice. Outside the
allowlist → the honest note, naming the format: `[Received an image (image/gif) — I can't read that
format.]`

## Slice 5 — Telegram document handler (channel-owned)

**The defect.** `gateway/channels/telegram/host.py:60-63` registers exactly four handlers — `TEXT`,
`PHOTO`, `VIDEO`, `VOICE`. A PDF arrives as `msg.document`, matches none of them, and is **dropped
before any gateway code runs**: no `InboundMessage`, no turn, no reply. Worse than the app channel's
pre-slice-1 state, which at least produced an honest note. Same for a video the sender attached as a
file rather than as a video — Telegram delivers that as a document too.

**A document is a container, not a type.** It is how Telegram delivers anything that is not a
compressed photo, a video, or a voice note — so the handler's job is to look at the mime and let
most of it land on kinds that already work:

| Sent as a document | mime | kind |
|---|---|---|
| PDF | `application/pdf` | `document` |
| Video | `video/*` | `video` |
| Music, audio clip | `audio/*` | `audio` |
| Photo "sent as file" (original quality, HEIC off an iPhone) | `image/*` | `image` |
| Everything else — archives, Office formats, text | — | `file` (honest note) |

**`gateway/channels/telegram/host.py`** — register
`MessageHandler(filters.Document.ALL, self._router.handle_document)` alongside the other four.

**`gateway/channels/telegram/router.py`** — `handle_document`, mirroring `handle_video` (`:95`):
read `msg.document.mime_type`, resolve it by prefix per the table above, `_download_and_store`,
dispatch with `msg.caption or "[DOCUMENT attachment]"`. A missing mime falls back to
`mimetypes.guess_type` on the filename — the field is client-supplied and not guaranteed.

**Classification is by prefix, deliberately.** The channel does not know which `video/*` the model
accepts and must not — slice 4 owns that, once, for every channel.

**Each channel owns its own mapping.** Do **not** import the app router's `_neutral_kind`: channels
never import each other, and the inputs differ — PTB exposes a mime directly on the message, where
the hub supplies a coarse kind *plus* a mime. Shared vocabulary, separate translations. If a third
channel ever repeats it, promote the table to `gateway/base.py`; two is not yet duplication worth
centralizing.

**`gateway/channels/telegram/media_cache.py:20`** — `_EXT` gains `"document": ".pdf"`.

**Depends on slice 3** for PDFs to be *read* rather than noted, and wants slice 4 so a format the
model rejects becomes a note rather than an error. Independent of slice 2: the Bot API caps
downloads at 20 MB, comfortably under the inline ceiling.

**Still unhandled after this slice** (each a distinct defect, not folded in): `filters.AUDIO` (music
files, as opposed to voice notes), animations, and video notes. All are silently dropped today for
the same reason — a handler that was never registered.

## Slice 6 — Files API (gated → moved to #61)

**No known trigger, and no longer this plan's business.** This slice existed to rescue media above a
ceiling that turned out not to exist at the size any channel can produce. What survives is not a size
workaround but a capability — media is single-turn today, and a `file_uri` is one way to let the
model look at it again. That framing, the alternatives to it (re-reading the 90-day channel cache is
cheaper and has no expiry), and the hazards below now live in **issue #61**.

Nothing here is planned work. The analysis is kept because it is worth not repeating:
`google-genai==1.68.0` is already installed, and the LangChain adapter accepts `file_uri` in the
*same* media block shape the agent already emits (`chat_models.py:493-495` branches on `data` vs
`file_uri`). So it is a swap inside the existing branch, not a new path.

Two hard requirements before it ships:

- **`_strip_media_blobs` must be fixed in the same commit.** Its condition is `"data" in block`
  (`agent.py:71`), so a `file_uri` block *survives* into thread history — and Files API URIs expire
  after 48h. A stale URI re-sent on a later turn errors, and because the whole window is re-sent it
  can brick that thread for its 50-message life. Change to
  `"data" in block or "file_uri" in block`. Landing slice 6 without this is a latent thread-breaker.
- **Latency has no seam.** Upload plus Gemini's PROCESSING poll blocks the turn for tens of seconds
  with no intermediate ack in the current flow. Decide that UX before building.

**Recommendation:** ship 1–5. #51's size question is answered by measurement rather than by design:
a 40 MB video needs no special handling at all — it just works.

---

## Verification

Per slice, before moving on. Claude cannot restart the service — the owner restarts, Claude verifies
against logs and a live round-trip.

1. **Pre-restart, runnable immediately:** drive the kind mapping through the staging venv with
   synthetic inputs (each hub kind × mime pair). The backstop is not a pure function — exercise the
   real media loop with the compiled graph stubbed out and an isolated `JARVIS_ROOT`, asserting on
   the content blocks it builds (at the ceiling, one byte over, and a readable kind well under).
2. **After staging restart:**
   - PDF from the app → Jarvis answers about its contents, not a "can't read" note.
   - Video sent as a file from the app → Jarvis describes it. ✅ *done 2026-07-30: 5.5 MB and 42 MB,
     both read correctly.*
   - The backstop cannot be exercised live from any current channel — synthetic coverage only.
3. **Telegram regression** (slices 2 and 3 touch the shared path): photo, voice note, and a small
   video still ingest normally.
4. Cache filenames land as `video_att_….mp4` / `document_att_….pdf` in the app channel's
   `media_cache/`.
5. **Slice 4, synthetic:** each kind × a supported mime, an unsupported one (`image/gif`), an
   aliased one (`video/quicktime`, `audio/mpeg`), and an empty mime.
6. **Slice 5, after restart:** a PDF over Telegram → Jarvis answers about its contents (today: no
   reply at all). A `.mp4` sent as a file over Telegram → described as a video. A photo sent as a
   file → described. A `.zip` → the honest note, no traceback. Cache filename
   `document_<file_id>.pdf`.

## Not in scope

- **Outbound video/document.** `gateway/channels/jarvis_app/channel.py:_upload_meta` (`:49`) still
  raises `NotImplementedError` for any kind but image/audio, and the Outbox reports that as a failed
  send. No producer needs it — the notifier sends images.
- **Text and code files** (`.txt`, `.md`, `.csv`, `.json`, source). Worth supporting, but not as a
  `document`: the API extracts them as pure text with no layout understanding, so the better shape is
  to read the file and inline it as a text block with a length cap. Split out as **issue #60**.
- **Office formats** (`.docx`, `.xlsx`, `.pptx`). Not supported by the model at all; they would need
  a conversion step. They reach the honest note via the `file` catch-all.
- **Raising the hub's 50 MB cap.** Now known to have headroom under the model — the inline ceiling
  is 100 MB — but it is a storage/bandwidth policy on the hub's side, decided there, not here.
- **Inline video duration.** The API's <1 min guideline for inline video is unmeasured; the 42 MB
  clip that validated size was short. A long-but-small video is the untested corner.
