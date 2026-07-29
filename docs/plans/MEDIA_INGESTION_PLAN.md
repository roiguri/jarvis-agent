# Inbound media ingestion — file-kind attachments + inline size guard

**Issue:** #51 — "Ingest inbound file-kind attachments (PDF + video) from the app channel".
**Date:** 2026-07-29.
**Touches:** `agent.py` (model boundary, channel-agnostic), `gateway/channels/jarvis_app/`
(kind mapping, channel-owned), `docs/architecture/GATEWAY.md` (neutral vocabulary).
**Companion:** [app-plans/EXECUTION_PLAN.md](app-plans/EXECUTION_PLAN.md) — Stage C shipped the
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

**The edge that shaped the design.** Gemini caps the whole request at ~20 MB while the hub allows
50 MB. Inline media is base64 in JSON (+33%), so the real raw-bytes ceiling is **~15 MB**, not 20 —
which also rules out "just cap the hub at 20 MB" as a fix, on the number alone. Capping the hub is
wrong on layering too: it pushes a model constraint into transport, so a model swap would need a
hub deploy plus an app-version skew window, and an oversized send would fail as a composer upload
error instead of something Jarvis can answer conversationally.

**Plan.** Two shippable slices plus a gated third, split along one principle: **channels translate
into the neutral vocabulary; the model boundary owns model limits.**

| # | Slice | Layer | Size | Effect |
|---|---|---|---|---|
| 1 | Neutral `document` kind + app-router kind mapping | channel-owned | small | `file`+mime resolves to a real kind at the boundary, as Telegram already does |
| 2 | Size guard + `document` branch in `agent.py` | model boundary | small | PDFs/videos ingested; oversized media refused honestly — **for every channel** |
| 3 | Files API for >15 MB media | model boundary | medium | **Gated** — only if oversized sends prove to matter |

**Why slice 2 is worth doing on its own merits.** The size guard is not app-channel work. Telegram
has *no* size handling anywhere (`gateway/channels/telegram/router.py:133` downloads and stores
unconditionally), and the Bot API permits downloads up to 20 MB — which base64s to ~25 MB and would
exceed the request cap today. The hole already exists on the older channel; it has never fired only
because the single video that has ever flowed through production was 1 MB. Slice 2 closes it once,
at the one place that knows what is being fed to the model, and every future channel inherits it.

**Risk posture.** Slices 1 and 2 are additive — new kinds and a new guard on a path that currently
terminates in a text note. Both are independently revertable. Slice 3 carries a real latent hazard
(see below) and is deliberately separated.

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

## Slice 2 — Model boundary (channel-agnostic)

All in `agent.py`, media loop at `:604-670`:

1. **Size guard**, between the kind check (`:618`) and the read (`:628`). Use `os.path.getsize`
   *before* `open()`, so a 50 MB blob is never pulled into memory only to be refused. Constant near
   `MAX_MESSAGES`:

   ```python
   # Gemini caps the whole request at ~20 MB. Inline media is base64 in JSON
   # (+33%), so the raw-bytes ceiling is ~15 MB, leaving headroom for the prompt.
   INLINE_MEDIA_MAX_BYTES = 15 * 1024 * 1024
   ```

   Over the limit → the same honest-note shape as `:618`, sized and channel-neutral:
   `[Received a video (38 MB) — too large for me to read.]` No channel, hub, or cap is named.

2. **`document` branch** alongside `video` (`:657`): a `{"type": "media", "mime_type": …,
   "data": b64}` block plus the lightweight text hint, identical in shape to the audio and video
   branches. `_strip_media_blobs` (`:71`) already drops `media` blocks carrying `data`, so nothing
   new accumulates in thread state.

3. Add `"document"` to the readable-kinds tuple at `:618`.

## Slice 3 — Files API (gated)

Only if oversized media proves to matter in practice. Cheaper than the issue assumed:
`google-genai==1.68.0` is already installed, and the LangChain adapter accepts `file_uri` in the
*same* media block shape the agent already emits (`chat_models.py:493-496` branches on `data` vs
`file_uri`). So it is a swap inside the existing branch, not a new path.

Two hard requirements before it ships:

- **`_strip_media_blobs` must be fixed in the same commit.** Its condition is `"data" in block`
  (`agent.py:71`), so a `file_uri` block *survives* into thread history — and Files API URIs expire
  after 48h. A stale URI re-sent on a later turn errors, and because the whole window is re-sent it
  can brick that thread for its 50-message life. Change to
  `"data" in block or "file_uri" in block`. Landing slice 3 without this is a latent thread-breaker.
- **Latency has no seam.** Upload plus Gemini's PROCESSING poll blocks the turn for tens of seconds
  with no intermediate ack in the current flow. Decide that UX before building.

**Recommendation:** ship 1+2 and leave #51's size question answered as **cap-and-note**. An honest
"too large to read" is a fine terminal state for a 40 MB video.

---

## Verification

Per slice, before moving on. Claude cannot restart the service — the owner restarts, Claude verifies
against logs and a live round-trip.

1. **Pre-restart, runnable immediately:** the kind mapping and the size guard are pure functions —
   drive both through the staging venv with synthetic inputs (each hub kind × mime pair; a file just
   under and just over the ceiling).
2. **After staging restart:**
   - PDF from the app → Jarvis answers about its contents, not a "can't read" note.
   - Video sent as a file from the app → Jarvis describes it.
   - Oversized video → the sized honest note; no traceback in `journalctl -u jarvis-staging`.
3. **Telegram regression** (slice 2 touches the shared path): photo, voice note, and a small video
   still ingest normally.
4. Cache filenames land as `video_att_….mp4` / `document_att_….pdf` in the app channel's
   `media_cache/`.

## Not in scope

- **Outbound video/document.** `gateway/channels/jarvis_app/channel.py:_upload_meta` (`:49`) still
  raises `NotImplementedError` for any kind but image/audio, and the Outbox reports that as a failed
  send. No producer needs it — the notifier sends images.
- **`image/gif`.** The hub allowlists it and this plan would inline it as an image, but Gemini's
  supported image set is png/jpeg/webp/heic/heif. Likely a one-line addition to the readable check,
  but it is a distinct defect from `file`-kind ingestion — recorded here rather than folded in.
- **Raising the hub's 50 MB cap.** Only becomes interesting if slice 3 ever lands.
