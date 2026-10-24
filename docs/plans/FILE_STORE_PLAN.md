# File store — one place for every file Jarvis receives, fetches or sends

**Date:** 2026-10-23 · **Status:** 📝 design agreed, not started · **Branch:** `feat/file-store`
**Goal:** every file that reaches Jarvis gets a stable id, a written record and a home that
outlives the conversation — so Jarvis can look at it again, read it in pages, find it later,
send it back and attach it to things, and so later features (a browse screen, embeddings,
retention) plug in without reworking what came before.

Groups issue #81 (overflow store, #60 text-file ingestion, #61 media re-read, kept media from
shared links).

---

## Slices

- [x] **S0 — Prototype.** Done 2026-10-23 — the tool-result path works (see S0 results). No repo code. A throwaway script (run by the owner — it needs the API
      key) proves media in a tool result works with our library and model, and that stripping
      it from history afterwards does not break the next call. Records sizes and costs.
- [x] **S1 — Deploy backups.** Independent of everything else. Prune deploy-tagged snapshots
      (keep the last few), and document that stored files are out of deploy snapshots and
      covered by the Proxmox container backup.
- [x] **S2 — Store core.** New top-level `files/` package: ids, on-disk layout, the index,
      add / get / remove with a safe write order, file states, the reference interface with a
      dry-run report, and a `verify` script. No callers yet.
- [x] **S3a — Channel switch-over.** New attachment shape (channels hand bytes), saving in one
      place in core, file markers in history, automatic migration of both channel caches at
      startup, the channels stop writing their caches. Both channels move together.
- [ ] **S3b — Outbound saved.** The Outbox saves media Jarvis sends (through a save step passed
      in by `main.py`), and the sent message's history entry carries the file marker.
- [ ] **S4 — `open_file` (core tool).** Text in pages (text and code files become readable);
      images, audio and video shown again through a media tool result; clear answers for
      expired, deleted and missing files.
- [ ] **S5 — Background records.** A restart-safe job queue that writes a description,
      transcript or extracted text for each file after the turn, indexed for search in chunks.
- [ ] **S6 — `files` skill.** `find_files`, `send_file`, `delete_file`, and a `file` field type
      for collections.
- [ ] **S7 — `fetch_url` keeps what it fetched.** Overflow page text saved as a temporary file
      the result points to (with a cleanup for temporary files only); link videos saved with
      their id and their description in the result.
- [ ] **S8 — Remove the old caches.** Delete both `media_cache.py` modules and the cache dirs,
      once rollback to pre-S3a code is no longer wanted.

Order: S0 first (it decides S4's mechanism). S1 any time. S2 → S3a → S4. S3b, S5 and S6 need
S3a; S7 needs S4 (its results point to `open_file`). S6's `find_files` is only useful once S5
has written records. S8 last.

---

## 1. Where things stand

- **Each channel keeps its own cache.** Telegram (`gateway/channels/telegram/media_cache.py`) and
  jarvis-app (`gateway/channels/jarvis_app/media_cache.py`) save inbound files under the channel's
  gateway dir and pass `{kind, path, mime_type, source}` in `InboundMessage.attachments`
  (`gateway/base.py`). Both delete files untouched for 90 days, on module import. Prod holds
  97 MB (96 MB from the app), staging 152 MB.
- **The caches are not backed up.** They live in the code dir; `deploy/backup_state.sh` snapshots
  only `jarvis_memory/` and `jarvis_data/`.
- **Media reaches one model call.** `ask_jarvis` inlines the bytes (cap
  `INLINE_MEDIA_MAX_BYTES`, 100 MB). The state reducer `_add_and_trim` strips media from every
  existing message on each write, so the blobs are gone at the first write after the first model
  call — if that call uses a tool, the next call no longer has the file (DEVELOPMENT.md, "Media
  blob stripping"). An image becomes `[image attached]` with no path; audio, video and documents
  keep a text line with the path. Nothing can show the file to the model again.
- **History points at files that vanish.** The episode store keeps every message forever and
  records attachments as `[attached: <path>]`; the file behind the path is gone after 90 days.
- **Text and code files can't be read.** Telegram maps any non-PDF, non-media document to kind
  `file`, which the agent answers with "can't read yet". The original filename is dropped.
- **Long pages are cut.** `fetch_url` caps its result at 8,000 chars (head 70% / tail 20%); the
  middle is lost.
- **Link videos are thrown away.** `tools/web/media.py` downloads a reel to `web/tmp/`, describes
  it, then deletes it.
- **Tool results are strings by our choice, not the library's.** `langchain-google-genai` 4.2.6
  converts media blocks in a `ToolMessage` into Gemini parts next to the function response
  (`_convert_tool_message_to_parts`); `_tool_node` in `agent.py` coerces every result to `str`.
  Only standard data blocks (`image_url`, or `{"type": "image"|"audio"|"video"|"file",
  "base64": …}`) are lifted into parts. The `{"type": "media", …}` blocks `ask_jarvis` uses for
  audio, video and PDF are not — inside a tool result they would become one huge text string.
  The standard-block path also never applies `video_metadata`, so a video in a tool result
  cannot be clipped or slowed.
- **Outbound is already bytes.** `send_media` / `send_to_owner_media` take bytes, a kind and a
  caption; the Outbox is the one send seam.
- **Backups grow without limit.** Prod has 46 state tarballs (117 MB), ~6 MB each and growing
  with the episode store. `--prune` keeps every tarball whose deploy tag still exists, and all
  46 are deploy-tagged, so in practice nothing is pruned.
- **Disk:** LXC 106, 16 GB, 6.4 GB free. Proxmox backs up the whole container.

## 2. Research

| Approach | What it does | Taken here |
|---|---|---|
| OpenClaw | Describes inbound media once into a text block, plus a `media://inbound/*` reference tools can open later | Both halves: a record made once, and an id that can be opened again |
| hermes-agent | `read_file` with offset/limit; storing big outputs is opt-in | Text read in pages |
| Letta | Folders attached to an agent, open/closed files, grep and semantic search; moving to git-backed storage | Search over records; chunks with positions |
| ChatGPT Library | Account-level file store; files outlive the chat that brought them | Files outlive the conversation |
| Immich / Paperless-ngx / Karakeep | Self-hosted photo, document and link archives with community MCP servers | Not now: heavy (Immich needs ~4 GB RAM and its own LXC) and only worth it if the owner wants that archive for itself. Stays an option behind the store |
| Gemini Files API | Upload once, reference by URI | Not as storage: URIs last 48 h. Possibly for one large look-again (S0) |
| jarvis-app hub's attachment storage | The hub already stores app attachments | Rejected: core would depend on one channel |

## 3. Design decisions

### The store is its own layer

A top-level `files/` package, like `episodes/`. One narrow interface: channels' files enter
through core, tools read / send / attach through it, `agent.py` turns a file into what the model
sees, a future browse surface reads the index. Nothing outside it reads or writes the store's
disk layout. The gateway never imports it: the Outbox gets its save step injected by `main.py`,
the same way it gets its log step.

### Ids, not paths

- `file:` + 8 random characters (Crockford base32, ~10¹² values), e.g. `file:k7m2q9xd`.
- Uniqueness is enforced by the index (a unique constraint); a clash draws again. So a
  collision cannot happen — the worst case is a retry.
- Random rather than sequential: a mistyped id almost always lands on nothing ("no such file")
  instead of on a different real file, and two stores can be merged.
- The id format is unique enough for a text scan to find (needed by references, below): the
  scan requires exactly 8 Crockford characters after `file:` with word boundaries (so
  `file:///` never matches) and confirms each hit against the index. Ids the model types are
  normalised (I/L → 1, O → 0) before lookup.
- Every place stores ids, never paths. This is what lets the bytes move — a bigger disk, a
  mounted volume, object storage — without breaking any reference.

### Layout

- **Index:** `jarvis_data/files/files.sqlite` — small, inside deploy snapshots (online-backup
  copy, like the episode store).
- **Bytes:** a separate root, `$ROOT/jarvis_files/`, laid out `ab/cd/<sha256>` with no
  extension (the type is in the index). The root is derived in `config.derive()` like every
  other state path — never set in `.env`, which is copied between instances and would silently
  point staging at prod's files. Staging and prod are separate by root. A Proxmox volume is
  mounted at that path when more space is needed (it needs backup enabled in its mount
  options, or Proxmox skips it). `scripts/ci/check_paths.py` gains `/app/jarvis_files` as a
  prod marker.
- **Bytes and index are separate**, with storage behind a small interface (put / get / delete),
  so the backend can change without touching callers.
- **One record per arrival**, bytes shared by hash. The same photo sent twice is two records
  (each with its own date, source and caption) and one copy on disk. Dedupe is bytes-only:
  every add makes a record, except sending an already-stored file by its id, which makes none.
- **Write order:** add = write bytes (temp file in the same root, fsync, rename, fsync the
  directory), then the record. Remove = mark or remove the record; the bytes are never deleted
  on the spot.
- **Unused bytes are collected later.** A collector deletes bytes only after no record has used
  them for a while (minutes, not seconds). This rules out the race where a remove deletes bytes
  that a concurrent add of the same file is about to point at.
- **Missing is a state, not a crash.** A crash can only leave unused bytes. But restoring an
  older index (`backup_state.sh --restore`, which restores `jarvis_data` only) can bring back a
  record whose bytes were since collected. `open_file` and `find_files` report such a file as
  missing, with its record. `verify` runs after any restore (a line in DEPLOY.md).

### Index tables (shape, not final schema)

- `files` — id, hash, kind, mime type (detected from the content, not trusted from the sender),
  size, filename, source (`telegram`, `jarvis-app`, `web`, `jellyfin`, …), origin (URL,
  uploader, channel's own id), the arrival turn id, created, state, removed-at and reason.
  Source values are data only; no code or docstring outside the channels names a channel.
- `derived` — file id, kind (description, transcript, text, later thumbnail / frame / OCR /
  embedding), producer and version (model + prompt), created, content. New capabilities are
  new kinds, not schema changes; the producer makes old records re-processable and traceable.
- `chunks` + FTS — derived text split into chunks with their position, so a search hit points
  into `open_file`.
- `jobs` — background work (S5).
- `legacy_paths` — old cache path → file id, from the migration. Many-to-one: old Telegram
  cache names are truncated ids with a shared prefix, so several arrivals can share one path.

### File states

| State | Bytes | Records | Minimal record |
|---|---|---|---|
| Available | ✓ | ✓ | ✓ |
| In trash (automatic cleanup only, restorable ~30 days) | ✓ | ✓ | ✓ |
| Expired (a future retention policy) | ✗ | ✓ | ✓ |
| Deleted by the owner | ✗ | ✗ | ✓ |
| Missing (bytes lost, e.g. after an index restore) | ✗ | ✓ | ✓ |

Expiry only frees disk, so the description stays useful. An owner's delete means "I don't want
this" and takes the records (and embeddings) with it. Either way the minimal record stays —
id, kind, date, source, when and why — so a collection item or note pointing at it shows
"removed on X" instead of breaking. Chat history is not edited by a delete: past messages that
mention the file stay, and the confirmation says so.

### Retention: keep forever, ready for more

- **Default:** keep everything forever. No retention sweep runs in v1 — only the collector for
  unused bytes and the cleanup of temporary files (below).
- **References are computed, never registered.** Each place that can use a file registers one
  function, "which file ids do I use?" (the way tools register with `@tool_register`):
  collections (their `file` fields), memory (scan the markdown for ids), the trigger store
  (a reminder or wake can hold an id), and the episode store (history; the live conversation
  is a subset of it, so it isn't scanned separately). A new tool joins by adding its function.
  For one file's where-used, the episode store is queried through its full-text index, not
  scanned.
- **Strong and weak.** Collections, reminders and owner-curated memory keep a file alive.
  History, daily logs (`daily/`) and heartbeat notes only mention it — daily logs are written
  from the day's chat and kept forever, so as strong references they would keep every file
  mentioned that day alive forever. Without the distinction nothing could ever be cleaned up.
- **Fail closed.** If any reference function errors, a sweep deletes nothing.
- **Grace period.** Files younger than a few days are never swept.
- **Trash first** for automatic cleanup; the owner's own delete is immediate after confirmation.
- **Where-used.** The same functions answer "where is this used?", shown before a delete.
- **v1 builds:** the interface, collections / memory / history implementations, and a dry-run
  report of what a policy would remove. The sweep itself waits for a setting.
- **Disk quota** raises an alert to the owner as the store nears it; it never deletes kept
  files to make room.

### Retention classes

Everything starts kept (inbound, link videos, tool-obtained, outbound). A tool may mark an
obvious scratch file temporary — overflow page text (S7) is the first. Temporary files get the
one cleanup v1 runs: after their time limit, with the grace period, and only if nothing holds a
strong reference. The source is recorded, so a future policy can treat sources differently
(e.g. posters) without touching any tool.

### Channel interface

- **Channels hand bytes; core saves.** Attachments become `{kind, mime_type, data, filename,
  source, origin_id}`. `process_inbound_message` in `main.py` saves them first — off the event
  loop (`asyncio.to_thread`) and before waiting for the turn lock, so the file is kept even if
  the queued turn later fails. It mints the turn id there and passes it to `ask_jarvis`
  (`turn_id=` is already supported), so each file record names the turn it arrived in. The
  turn gets file ids, not bytes. A channel only translates its protocol; a new channel cannot
  skip saving because there is nothing to skip. Failures (disk full, over the size cap) are
  handled once, in core, with an honest reply.
- **Bytes in memory is no new cost:** channels already download into memory, and the agent
  already reads the whole file to inline it. A future channel with very large uploads can hand a
  temporary path that core saves from instead.
- **No placeholder text:** channels used to write their own placeholders (`[IMAGE attachment]`,
  `[attachments: …]`) when a message had no caption. They are dropped: the file's marker says
  what arrived. A file that can't be saved (too large, disk full) becomes a note in the message
  and the turn still runs.
- **Outbound unchanged:** the Outbox accepts a file id (read from the store) or bytes; bytes are
  saved through the injected save step, off the event loop, so everything Jarvis sends has an
  id. The id goes into the notification log, so the sent message's history entry and the
  pending-mirror drain show the marker.
- **Contract change:** `gateway/base.py` attachment shape, GATEWAY.md, and the code-review
  skill's G8 rule ("path is absolute and channel-produced") are rewritten together.
- **Deferred:** delivery receipts (`send_media` returning the channel's id for what it sent, for
  re-send without upload and recognising replies to a file).

### Three ways in, one call

| How a file arrives | Who saves it |
|---|---|
| The owner sends it | Core, as the message enters (S3a) |
| A tool obtains it | The tool (link videos, a fetched PDF, a generated chart, a poster) |
| Jarvis sends it | The Outbox, if not already stored |

All through `add(bytes, source, origin)`. Identical bytes are stored once; each arrival still
gets its own record.

### Record once, look again

- **Record:** every file gets a written record (description for images and video, transcript
  for audio, extracted text for documents), made in the background after the turn — the model
  already saw the file inline that turn, so the reply doesn't wait. Same kind of side call as
  `tools/web/media.py` (faithful-record prompt, telemetry job, `config.MODEL`). Records are
  written in English; quoted speech and on-screen text stay in the original language.
- **Look again:** the model decides, by calling `open_file`. The tool result carries the file
  itself, and like inbound media it is seen by the next model call only: the reducer strips it
  at the following write, back to a plain-string tool result with the marker. If the model
  needs the file again later in the turn, it calls `open_file` again. Automatic re-showing
  (e.g. on a reply to a file) is added later only if a need appears.
- **Long videos:** a tool result cannot clip a video (see section 1), so a video over the clip
  length goes into the next model call's input as a clipped, low-frame-rate `media` block
  instead (S0 confirms the exact mechanism).
- **Link videos are not described twice.** `fetch_url` already describes a reel during the
  turn; S7 stores that text as the file's description (producer `web_media`) instead of
  queueing a second paid call.

### History format

Every file in history, from any source and images included:

```
[file:k7m2q9xd · image · photo.jpg · 1.2 MB]
```

No description in the marker — the record doesn't exist yet when it's written, and descriptions
would bloat every later turn. The filename is untrusted text going into the prompt, so it is
cleaned (no `]` or newlines, length capped). The same marker replaces the chat log's `media`
field, the episode store's `[attached: <path>]` and the path hints `ask_jarvis` writes, and the
compaction prompt keeps file ids in its record so compacted turns keep their handles.

### Tools

| Need | Tool | Where |
|---|---|---|
| Look again | `open_file(id)` — record plus the file inline | core |
| Read text / code | `open_file(id, start, length)` — a page at a time | core |
| Too big for context | `fetch_url` saves the full text and points to it | web (S7) |
| Keep media from links | `fetch_url` saves the video, gives its id | web (S7) |
| Send back | `send_file(id, caption)` via the Outbox | `files` skill |
| Attach | collections `file` field type, holding ids | collections |
| Find | `find_files(query, kind, source, since)` | `files` skill |
| Delete | `delete_file(id)` — destructive, shows where-used, confirms | `files` skill |

`open_file` is core because markers appear in ordinary turns. `open_file` also accepts an old
cache path recorded by the migration, so old history stays openable.

### Search

- **Separate `find_files`**, not merged into `search_history`: "what happened" and "what do I
  have" return different shapes (a file needs a state, kind and id). They link through ids:
  history holds the markers, and each file result names the message it arrived with.
- **Indexed:** filename, arrival message, source, and the records — long text in chunks with
  positions, so a hit gives a snippet and a position for `open_file(id, start)`.
- **States respected:** default results are available files; expired files match by their
  record and are marked; deleted files never match by content.
- **Ready for embeddings:** the tool takes a plain-language query, never search syntax.
  Embeddings later become a `derived` kind per chunk in a vector index that supports delete by
  id (`sqlite-vec` qualifies); search mixes both behind the same tool. Their main payoff here is
  Hebrew, where attached prefixes defeat keyword matching.
- **Indexes are derived and rebuildable** from the tables; every removal goes through the store,
  which updates every index.

### Background work

A `jobs` table in the index: file id, record kind, attempts, next attempt, last error. Retries
back off; every attempt shows in telemetry. Until a file's record exists it is found by name,
caption and source only. A changed model or prompt version queues re-processing for files
whose records came from the old one.

Concurrency: the worker runs outside the owner turn lock, in a fresh `contextvars.Context()`,
so it never inherits a finished turn's turn id or budget tracker (which would charge its model
calls to that turn). Every store connection is short-lived, WAL with a busy timeout (as in
`episodes/store.py`), and no write transaction is held across a model call or a byte write.

### Safety

Size caps on add; type detected from the content; files are never executed; the store is
outside the memory tools' reach; a long video shown again is clipped and low-frame-rate like
YouTube in `tools/web/media.py` (through the next call's input, not the tool result).

## 4. Slice details

### S0 — Prototype

A scratch script, never committed, run by the owner from the code root. Answers:

1. Does Gemini 3 (`config.MODEL`) accept an image, an audio clip, a video and a PDF inside a
   `ToolMessage` as standard data blocks (`{"type": "image"|"audio"|"video"|"file",
   "base64": …}`) through `langchain-google-genai` 4.2.6, and describe them correctly?
2. After replacing that media with a plain-string result and the marker, does the next call
   still succeed (thought signatures on the tool call are untouched)?
3. How large can an inline video in a tool result be before it fails or needs a Files API
   upload?
4. Can a video in a tool result carry `fps` / `end_offset`? Expected no in 4.2.6; if so, a long
   video uses the fallback below.
5. Token and time cost of a background record for a typical photo, voice note and short video.

The fallback, for (1)/(2) failing or a long video: put the file in the next model call's input
as a `media` block rather than in the tool result (inbound media already works that way).

### S0 results (2026-10-23, `gemini-3-flash-preview`, `langchain-google-genai` 4.2.6)

Run by the owner from a scratch script: a dummy `open_file` tool bound the way the agent binds
tools; the model asked for the file, the file came back as a standard data block, then the same
history with the file stripped to the plain-string marker was sent again.

| Case | Size | B: seen in tool result | Input tokens | C1 same turn stripped | C2 next turn stripped |
|---|---|---|---|---|---|
| Image (generated, known text) | <0.1 MB | ✓ exact text read | 473 | ok | ok |
| PDF (generated, known text) | <0.1 MB | ✓ exact code read | 720 | ok | ok |
| Audio (voice-over of a recipe) | 0.3 MB | ✓ recipe followed | 1,160 | ok | ok |
| Video (reel) | 0.9 MB | ✓ | 536 | ok | ok |
| Video (same ~1-min recipe at 3 encodings) | 7.5 / 16.1 / 24.6 MB | ✓ all three | ~8,200 each | ok | ok |

1. **Q1 — yes.** Image, audio, video and PDF are all seen inside a tool result as standard data
   blocks; known details came back exactly. Per-part `media_resolution` (low) is accepted.
2. **Q2 — yes.** Stripping the file afterwards breaks nothing, in the same turn or the next; no
   thought-signature errors. After stripping, the model answers the follow-up from its own
   earlier reply.
3. **Q3 — 24.6 MB works** (11 s); the limit was not reached. 25–50 MB (the link-video cap) is
   untested in a tool result; inbound inlining has read 42 MB before. Cost follows duration, not
   file size: three encodings of one video cost the same.
4. **Q4 — no clipping** (checked offline against the library's converter): no block shape
   carries `video_metadata` into a tool result, and the legacy `{"type": "media"}` block turns
   into base64 text inside the function response. Long videos take the fallback.
5. **Q5 — background records are cheap.**

   | Kind | Time | Input / output tokens | Cost |
   |---|---|---|---|
   | Image | 1.1 s | 334 / 42 | $0.0003 |
   | PDF (one page) | 1.8 s | 327 / 31 | $0.0003 |
   | Voice clip (0.3 MB) | 2.2 s | 1,013 / 200 | $0.0011 |
   | Reel (0.9 MB) | 3.1 s | 391 / 82 | $0.0004 |
   | ~1-min video (7.5 MB) | 8.3 s | 8,076 / 1,526 | $0.0086 |

   A hundred photos and a few videos a month is a few cents.

**Decision for S4:** the tool result is the main path for every kind. The fallback (next call's
input) is used only for videos longer than the clip length, which need clipping.

### S1 — Deploy backups

`deploy/backup_state.sh --prune` keeps the last N deploy-tagged snapshots instead of every one
whose tag exists. DEPLOY.md states the split: deploy snapshots undo the last few deploys; the
Proxmox container backup covers loss of the disk, and covers stored files (never in deploy
snapshots).

### S2 — Store core

`files/` package with: id generation, storage backend interface + local implementation, the
index and its tables, `add` / `get` / `remove` / `set_state` in the write order above, the
reference-function registry with a dry-run report, the unused-bytes collector, a quota check,
`scripts/` `verify` (hashes, missing bytes, unused bytes). `config.derive()` gains the files
root; `check_paths.py` gains it as a prod marker. `backup_state.sh` adds
`jarvis_data/files/files.sqlite` to its online-backup stores list (otherwise it is tarred live,
mid-write). CLAUDE.md trees and MEMORY.md placement gain the new homes. Unit tests only;
nothing calls it yet.

### S3a — Channel switch-over

- `gateway/base.py`: new attachment shape; Telegram keeps the document's filename.
- `main.py` `process_inbound_message` saves attachments before the turn lock (off the loop),
  mints the turn id and passes it to `ask_jarvis`; `ask_jarvis` takes file ids and reads bytes
  from the store for inlining.
- History markers everywhere a path is written today: `_strip_media_blobs` (keeps the marker
  for every kind, images included), the path hints in `ask_jarvis`, the episode record's
  `[attached: …]`, the chat log's `media` field. The channels' placeholders are dropped. The
  compaction prompt keeps file ids.
- **Migration runs automatically at startup**, before the channels start: idempotent, keyed on
  the legacy path, so it also picks up files the old code wrote after the deploy and before
  the restart. It copies, never moves — the cache dirs stay so a rollback to pre-S3a code still
  finds them (S8 removes them). Each record's arrival comes from the episode row that mentions
  its path, not from file times. An old path that no longer has a file gets "removed before the
  file store existed", not "no such file".
- Channels stop writing their caches and their import-time trim stops; the modules stay until S8.
- A shared contract test every channel's attachments must pass.
- Tests and guards that encode the old shape: the episode `[attached: …]` assertion, the hint
  and strip tests in `tests/test_agent_messages.py`, `check_timezone_anchors.py`'s
  `media_cache` skip, `.gitignore`.
- Docs: GATEWAY.md (attachment shape, media sections), code-review skill G8, CLAUDE.md
  placement principle and trees, MEMORY.md, DEVELOPMENT.md ("Media blob stripping").

### S3a staging checklist

1. **Startup import.** Restart staging. The startup log has `File store: imported N old files,
   collected 0 unused, … MB stored, … GB free`, with N at least the number of files in both old
   caches. `JARVIS_ROOT=/app/jarvis_staging venv/bin/python scripts/files_verify.py` reports 0
   missing and 0 corrupt.
2. **Photo with a caption** ("what is this?"): answered from the photo.
3. **Photo with no caption:** Jarvis responds to the photo; no `[IMAGE attachment]` text anywhere.
4. **Album** (2–3 photos, one caption): all photos seen.
5. **Voice note:** understood.
6. **Short video:** described.
7. **PDF document:** read; its filename shows in the marker.
8. **Text or code file** (`.txt`, `.py`): Jarvis says it can't read a text file yet (expected
   until S4), naming the file.
9. **Follow-up, next turn:** "what was in the first photo?" is answered from the earlier reply.
10. **jarvis-app:** an image with text, and one without.
11. **History:** each new owner row in the episode store ends with its file markers, and the
    store has one record per file sent, with the right turn id.
12. **Restart again:** `imported 0 old files`.

### S3b — Outbound saved

The Outbox gets a save step injected by `main.py` (like its log step) and runs it off the loop;
it accepts a file id or bytes. The file id goes into the notification log, so the sent
message's history entry and the pending-mirror drain carry the marker. Today's only caller of
outbound media is the webhook notifier's posters.

### S4 — `open_file`

- Text kinds: pages by character offset and length. Text and code files get their kind from
  their content, so they no longer arrive as "can't read yet" (the small-text-on-arrival
  question is decided here).
- Media kinds: `_tool_node` accepts one media result type produced by the store (the one
  documented exception to "tools return plain strings"), built as standard data blocks.
  `_strip_media_blobs` handles tool results and standard blocks too, turning them back into a
  plain-string result with the marker. Long videos take the fallback (next call's input).
- Expired / deleted / missing: the record (if any) and a clear note that the original is gone.
- `_tool_node` telemetry measures list content properly (today `result_size` would count blocks).
- Docs: RUNTIME.md (tool result rule), AGENTS.md only if a rule is needed (docstrings first);
  golden snapshots.

### S5 — Background records

Job queue and worker; producers per kind (image / video description, audio transcript, document
text); producer + version on every record; chunking into FTS; telemetry job per record;
re-process command for a changed version.

### S6 — `files` skill

`tools/files/` with `find_files`, `send_file`, `delete_file` (confirmation via
`get_confirmation()`, where-used from the reference functions, including other arrivals of the
same bytes — deleting one arrival keeps bytes another still uses). Collections gain a `file`
field type (validates the id exists, shows state) and register their reference function.

### S7 — `fetch_url`

Overflow text saved as a temporary file; the result ends with its id and how to read on with
`open_file`. The cleanup for temporary files only. Link videos saved (source `web`, origin the
URL), their in-turn description stored as their record, their id included in the result.
YouTube is not downloaded, so it has no file.

### S8 — Remove the old caches

Delete both `media_cache.py` modules and the cache dirs once the S3a code has run long enough
that rolling back past it is no longer wanted.

## Out of scope (extension points)

- **Retention sweep and trash** — built on the reference functions when a policy is wanted.
- **Embeddings** — a `derived` kind and a vector index behind `find_files`.
- **Browse surface** — a read-only app surface over the index; thumbnails as a `derived` kind.
- **Delivery receipts** — `send_media` returning the channel's id.
- **Frames from video** — a `derived` kind (needs PyAV; the host has no ffmpeg).
- **Automatic look-again** — on replies to a file, if a need appears.
- **External archives** (Immich, Paperless-ngx) — behind the store, if the owner wants one.

## Open questions

- Document text extraction: a local library (e.g. `pypdf`) or a Gemini side call? Scanned PDFs
  need the model either way.
- Should small text files be inlined on arrival, with only larger ones read through `open_file`?
- Quota value and alert threshold.
- Link-video volume (~5–15 MB each, all kept): watch growth after S7.
- How long unused bytes wait before the collector deletes them, and how long temporary files
  live.
- Default page size for `open_file` text.
