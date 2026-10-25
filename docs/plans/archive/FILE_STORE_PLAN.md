# File store — one place for every file Jarvis receives, fetches or sends

**Date:** 2026-10-23 · **Status:** ✅ complete, archived 2026-10-24 — S0–S7 shipped; S3b checked on prod after the deploy and S8 later (#185), both tracked in #187 · **Branch:** `feat/file-store`
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
- [x] **S4 — `open_file` (core tool).** Any tool can return stored files; inbound and tool
      results share one block shape; small text files read on arrival, longer ones in pages;
      images, audio, video and PDFs shown again; clear answers for removed files.
- [x] **S5 — Background records.** A restart-safe job queue that writes a description,
      transcript or extracted text for each file after the turn, indexed for search in chunks.
- [x] **S6 — `files` skill.** `find_files`, `send_file`, `delete_file`, and a `file` field type
      for collections.
- [x] **S7 — `fetch_url` keeps what it fetched.** Long page text saved as a file the result
      points to (kept, like everything else); link videos saved with
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
  unused bytes.
- **References are computed, never registered.** Each place that can use a file registers one
  function, "which file ids do I use?" (the way tools register with `@tool_register`):
  collections (their `file` fields), memory (scan the markdown for ids), the trigger store
  (a reminder or wake can hold an id), and the episode store (history; the live conversation
  is a subset of it, so it isn't scanned separately). A new tool joins by adding its function.
  For one file's where-used, the episode store is queried through its full-text index, not
  scanned.
- **Strong and weak.** Collections, reminders, owner-curated memory and heartbeat notes keep
  a file alive (a note is a task's working state: a file it names is one the task tracks, and
  the mention goes when the task drops it). History and daily logs (`daily/`) only mention it
  — daily logs are written from the day's chat and kept forever, so as strong references they
  would keep every file mentioned that day alive forever. Without the distinction nothing
  could ever be cleaned up.
- **Fail closed.** If any reference function errors, a sweep deletes nothing.
- **Grace period.** Files younger than a few days are never swept.
- **Trash first** for automatic cleanup; the owner's own delete is immediate after confirmation.
- **Where-used.** The same functions answer "where is this used?", shown before a delete.
- **v1 builds:** the interface, collections / memory / history implementations, and a dry-run
  report of what a policy would remove. The sweep itself waits for a setting.
- **Disk quota** raises an alert to the owner as the store nears it; it never deletes kept
  files to make room.

### Retention classes

Everything is kept (inbound, link videos, tool-obtained, outbound, long page text). The store
accepts a `temporary` retention for a future scratch file, but nothing uses it yet and no
cleanup runs for it; long page text (S7) was the candidate and is small enough to keep. The source is recorded, so a future policy can treat sources differently
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
- **Long videos:** a tool result cannot clip a video (see section 1). Videos are shown whole
  for now, like inbound ones; trimming is an open question for the end of the plan.
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

`open_file` is core because markers appear in ordinary turns. It takes file ids only: files
from before the store are imported with their date and caption, so `find_files` finds them.

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
outside the memory tools' reach; file contents reach the model as data (text inside
`<file content>` tags), never as instructions.

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

As built: the save step is `files.attachments.save_sent`, the same `save_attachments` path
inbound files take. The Outbox sends first and saves only what was delivered; a failed save
still logs the send, without a marker. Bytes only for now: sending a stored file by id comes
with its first caller, `send_file` (S6). The drain keeps markers when it cuts a long caption.
Staging runs without the webhook, so the live check is the first poster after a prod deploy
(`send_file` in a chat turn doesn't take this path; see S6).

### S4 — `open_file`

- **Any tool can return files.** A tool returns plain text, or text plus stored file records
  (`files.model_input.ToolFiles`). `_tool_node` turns the files into blocks through
  `content_blocks`, the same code inbound files use; the next model call sees them, then the
  reducer strips them back to a plain-string result with the markers. A tool that obtains a new
  file saves it first, so every file it returns has an id. `open_file` is the first user.
- **One block shape.** Inbound files move to the standard data blocks (`{"type": "image" |
  "audio" | "video" | "file", "base64", "mime_type"}`) that tool results need, so one function
  decides what the model can read, the size cap and the notes, and one strip rule covers both.
  The strip rule still drops the older shapes, which checkpoints written before may hold.
- **Text and code files.** Read on arrival up to one page (8,000 characters), inside
  `<file content>` tags. A longer file shows its first page and ends with "Showing characters
  0–8,000 of N. If you need the rest: `open_file(…, start=8000)`." `open_file` pages by
  character offset and length with the same function.
- **`open_file(file_id, start, length)`:** the marker, arrival time and message, state, and
  the file's written records (none until S5), then the file itself; a text file as a page.
  Expired, deleted or missing files return the record and a note that the original is gone.
  Old cache paths are not accepted (old files are in the store; `find_files` finds them).
- **No video trimming** (see Open questions).
- `_tool_node` telemetry counts a file result's text, not its blocks.
- Docs: RUNTIME.md (a tool may return files), the docstring carries the usage guidance ("if
  needed"); no AGENTS.md change; golden snapshots.

### S4 staging checklist

1. **Inbound, same as before** (the block shape changed): a photo, a voice note and a PDF are
   each understood.
2. **Short text file:** send a small `.py` or `.txt`; Jarvis reads it on arrival.
3. **Long text file:** send one over 8,000 characters; Jarvis answers from the first page and
   reads further only when the question needs it.
4. **Look again:** later, ask about a detail of an earlier photo that its first reply didn't
   cover; Jarvis calls `open_file` and answers from the photo.
5. **No needless opening:** a follow-up the earlier reply already answers gets no `open_file`
   call (check with `scripts/trace.py`).
6. **Video again:** ask about an earlier video; it is opened and described.
7. **Next call only:** after a turn with `open_file`, the checkpoint holds the tool result as
   plain text with the marker, no base64.

**Result (2026-10-24):** 1, 2, 4, 6, 7 passed on staging (photo, voice, PDF, a 3 KB CSV read
on arrival; an earlier photo and a 21 MB video opened again and described; checkpoint clean).
5 passed incidentally: the answer was already in an earlier reply, so the photo wasn't opened,
but the photo turn had been trimmed from history and Jarvis first opened the wrong file (the
trim and search behaviour predate S4). 3 not run live; paging is covered by tests.

### S5 — Background records

**One entry point, one function per file kind.** `files/records.py` holds a table from file kind
to its record function; `records.make(rec)` is the only way a record is made. Improving how a
kind is read (a local PDF text reader, OCR, frames from video) replaces that kind's function and
bumps its version; nothing else changes.

| Kind | Record kind | Made by | Version |
|---|---|---|---|
| image | description: what is shown, and all visible text verbatim | model | `image-1` |
| video | description: said / on-screen text / shown (the reel prompt, shared with `fetch_url`) | model, low resolution | `video-1` |
| audio | transcript: an English summary line, then everything said in the original language | model | `audio-1` |
| document | text: an English summary line, then the full text in reading order | model (reads scans; keeps Hebrew order) | `document-1` |
| text | text: the file's own text (capped) | no model | `text-1` |
| file | none: found by name and caption | — | — |

Model records use `config.MODEL` with low thinking, in English except quoted speech and text.
A record that hits the output cap is kept, with a note that it stops early.

- **Queue:** `store.add` queues a job in the same write as the file row, so every source gets
  records (owner files, files Jarvis sends, later `fetch_url`). Startup also queues every file
  of a recorded kind that has no record and no job, which back-fills the imported old files.
- **Worker:** one async task started by `main.py`; it wakes after each owner turn and once a
  minute, and does one job at a time, newest file first, outside the turn lock, in a fresh
  context, under `telemetry.job("file_record")`. A job's attempt is counted before the model
  call, so a crash mid-call retries later. Back-off 1 min, 10 min, 1 h, 6 h, 1 day; after five
  failures the job stays with its last error and is not retried. A file that is deleted,
  expired or missing has its job dropped.
- **Same bytes, one call:** if another file with the same hash already has this record at the
  current version, it is copied.
- **Redo:** `scripts/files_requeue.py --kind <kind>` queues files whose record is older than the
  current version (rows only, no model calls; the service does the work).
- **`open_file`:** while the original is stored, each record is shown cut to 1,500 characters
  (a text file's own-text record is skipped: the page is the content). Once the original is
  gone, the records are the content, paged with `start` / `length` like a text file.
- **Shared side call:** the model-call part of `tools/web/media.py` and the video prompt move to
  `files/records.py`; `fetch_url` and the worker use the same function.

**As built:** `files/records.py` (`make`, `RECORDERS`, `side_call`), `files/worker.py` (queue,
one job at a time on its own daemon thread, so a restart never waits for a model call),
`scripts/files_requeue.py`; `scripts/files_verify.py` also prints the queue and any jobs that
gave up. Every stored file gets a job; a kind with no record (`file`) has its job dropped on
the first try. `model_input.page()` pages both text files and records.

### S5 staging checklist

1. **Back-fill:** after the restart, the startup line says `queued N records`; the log shows
   `file record made for file:…` lines, one about every few seconds, then nothing.
   `files_verify.py` ends at `records queued: 0, given up: 0`.
2. **New photo:** send one; within about a minute its record is made (log line), and the turn
   itself is no slower.
3. **Voice note:** its transcript is in the original language with an English summary line.
4. **PDF:** its record is the full text, Hebrew in the right order.
5. **Look again uses the record:** `open_file` on an earlier photo shows `Record (description):`.
6. **Restart mid-queue:** restart while records are still being made; the restart is quick and
   the queue carries on after it.
7. **Telemetry:** each record is its own `file_record` job in `scripts/trace.py`, not part of a
   turn.

**Result (2026-10-24):** staging's 80 older files were skipped (jobs marked given up, so
`files_requeue.py` can revive them) and the newest file of each kind back-filled: 5 records
in 30 s. 1–5 and 7 passed: records were made a few seconds after each reply, turns no slower;
Hebrew transcripts with an English summary line; PDF text in reading order (a Hebrew/Chinese
table row came out partly jumbled); `open_file` showed the record with the photo. 6 not run
(retries are covered by tests). Seen: the video record opens with a filler line ("Sure,
here's a record of the video.") despite the prompt; fix with `fetch_url`'s shared prompt in S7.

### S6 — `files` skill

`tools/files/`, a skill (no per-turn cost until activated).

- **`find_files(query, kind, source, since, until, limit)`** — one entry per file: marker,
  arrival time (Israel) and source, the message it came with, and the best matching snippet;
  a text file's match also says where to `open_file(…, start=…)`. Dates as `search_history`
  takes them (`episodes.search.parse_bound`). No query lists the newest files. Expired files
  match by their record and are marked; deleted files never match.
- **Search index:** `chunks_fts` uses SQLite's trigram tokenizer, so a Hebrew word matches with
  a prefix attached ("חוזה" finds "והחוזה"); all terms first, then any term; terms under 3
  characters are dropped. An index built with the old tokenizer is rebuilt at first use (it
  is derived from `chunks`). History search keeps its own tokenizer for now.
- **`send_file(file_id, caption)`** — to the turn's origin channel, through the Outbox, as
  `send_form` does (tagged as a heartbeat send on a tick). The Outbox is handed the file's id
  and marker, so it is not stored a second time. Channels send every kind: the Channel
  methods gain optional `filename` and `mime_type`; Telegram sends video, audio (ogg as a
  voice note) and anything else as a document with its filename; the app sends images and
  audio as now and anything else as a `file` attachment.
- **`delete_file(file_ids)`** — destructive, confirmed. The confirmation lists each file,
  every other arrival of the same bytes (deleted with it: "delete that photo" means it is
  gone), and where each is used. If the where-used scan fails, nothing is deleted.
- **Bytes go within the hour:** the worker also collects unused bytes hourly, not only at
  startup.
- **Collections `file` field:** one file id per field; unknown or deleted ids are refused;
  shown as the file's marker (so a removed file reads as removed). Collections register as a
  strong reference holder; so do memory files (ids Jarvis wrote into them).
- **S3b's live check is not here:** a chat-turn send is already in the conversation as the tool
  result, so it is not logged as a notification (as with `send_form`); S3b waits for the first
  proactive poster on prod.

**As built:** `files/search.py` (the search, with a substring fallback for words under 3
characters, and the start of a file's record shown when there is no match snippet),
`tools/files/` (`library.py` + `SKILL.md`), the index's `PRAGMA user_version` (1 = trigram
built; an interrupted rebuild runs again), `Outbox.notify_owner_media(..., filename,
mime_type, kept)`, holders in `tools/collections/references.py` and `tools/core/memory.py`.
Always-on cost: the skill's one line in the skill list. Seen on staging: files sent to the app
had no length (voice notes, videos) and no preview picture (videos); the app channel now reads
length and size from the Ogg / MP4 headers (`jarvis_app/media_meta.py`); the preview is #186,
its fix (Jarvis or app side) undecided. Collections file fields in the app: jarvis-app#36.

### S6 staging checklist

1. **Upgrade:** after the restart, `find_files` works on the existing index (it is rebuilt
   with the new tokenizer on first open; the log shows no error).
2. **Find:** "find the cinema tickets" and a Hebrew word from a PDF (e.g. "הזמנה") each find
   the right file; "what voice notes did I send today?" lists them with their transcripts.
3. **Send back, app:** "send me the Cinematheque PDF" arrives as a file named
   `Cinematheque Tel Aviv.pdf`; a photo arrives as an image.
4. **Send back, Telegram:** the same from Telegram: the PDF as a document with its name, a
   voice note as a voice message, the video as a video.
5. **Same id:** after a send, the files table has no new row (`files_verify.py` record count
   unchanged).
6. **Collection field:** add a `photo` file field to a test collection and attach a photo by
   id; listing the item shows the file's marker.
7. **Delete:** delete a photo that is in that collection; the confirmation lists it and says
   "used in: collections"; cancel once (nothing changes), then confirm; the item now shows
   the marker as deleted, and `open_file` says it was deleted.

**Result (2026-10-24):** passed on both channels. Find: `find_files` found the newest image,
document, voice note and video by kind; before the skill was active, "find the cinema tickets"
went through `search_history` (it finds files by their markers), which also worked. Send back:
app and Telegram, PDF, voice note, photo, video, each under its own id (no new rows after 12
sends). The 21 MB video first timed out on the app's 10 s request limit; uploads now get a
size-based limit, and it then sent in about 19 s. Collection: a `file` field created, a photo
attached; after deleting the photo (the confirmation said "used in: collections") the item
shows it as deleted. Not run live: cancelling a delete (tested), the 21 MB video over
Telegram.

### S7 — `fetch_url`

- **Long pages kept:** a page over 8,000 characters is stored whole as a text file (source
  `web`, origin its URL), and the result is its first page with the same "Showing characters …
  If you need the rest: open_file(…, start=…)" tail a text file gets (`model_input.page`),
  replacing the head-and-tail cut. Kept like every other file: page text is small (prod reads
  15–20 pages a month), so there is no temporary class and no cleanup for it. It gets a record
  like any text file, so `find_files` finds pages read before.
- **Link videos kept:** an Instagram or TikTok video is stored (source `web`, origin the URL
  and poster, the post's caption as its message), and the result names its id. The in-turn
  description is written as its record in the same write as the file (`store.add(...,
  record=...)`), so the worker never describes it again; if the description failed, the
  worker makes one. YouTube is not downloaded, so it has no file.
- **The video record's opening filler line** ("Sure, here's a record of the video.") is left
  as it is: it is harmless, and telling it apart from content in code is guesswork.

**As built:** `files.attachments.save_fetched()` is how a tool keeps a file (tied to the running
turn; a failure logs and the answer goes on without the file); `store.add(..., record=...)`
writes a ready record in the same write and queues no job. `tools/web/fetch.py` `_long_page()`
(a page that can't be kept falls back to head and tail); `tools/web/media.py` `_keep()`. A link
video's origin holds its URL, poster, posting date and length; `FileRecord.origin_text()` shows
them in `open_file` and `find_files`. A YouTube page is read with `keep=False`: never stored.

### S7 staging checklist

1. **Long page:** send a long article link; the reply answers from it, and the result names
   a `[file:… · text · <host>-<path>.md …]`. Ask about something near its end; Jarvis reads on
   with `open_file(…, start=…)`.
2. **Short page:** a short page comes back whole and stores nothing.
3. **Reel kept:** send an Instagram reel or a TikTok; the reply is as before, the result says
   "kept as [file:…]", and the log shows no `file record made` line for it (no second call).
4. **Reel again:** later, "send me that reel" sends the video back; "what's in the reel?"
   answers from its record or opens it.
5. **Find:** `find_files` finds the reel by its poster or a caption word, and the article by a
   word from its text.
6. **YouTube:** a YouTube link reads as before and stores no file.

**Result (2026-10-24):** 1–3 and 6 passed. The Wikipedia article on placebo (119 KB) was kept,
and questions about its middle were answered by reading on with `open_file`; Tavily's text
opens with about 8,000 characters of site menus, so the first page is mostly navigation. A
short page stored nothing. The reel (7 MB) and the TikTok (16 MB) were kept with URL, poster,
caption and their record, with no second description call. The first YouTube run stored its
page as a file (`_read_page` saved long pages for every caller); fixed with `keep=False`, and
the re-run stored nothing. 4–5 (sending the reel back, finding it by poster) not run live:
sending back passed in S6, and the origin line and its search are tested.

### S8 — Remove the old caches

Delete both `media_cache.py` modules and the cache dirs once the S3a code has run long enough
that rolling back past it is no longer wanted. The record back-fill (`worker.queue_missing()` and its startup
step) goes with them. Each removal TODO naming #185 marks what to remove.

## Out of scope (extension points)

- **Retention sweep and trash** — built on the reference functions when a policy is wanted.
- **Embeddings** — a `derived` kind and a vector index behind `find_files`.
- **Browse surface** — a read-only app surface over the index; thumbnails as a `derived` kind.
- **Delivery receipts** — `send_media` returning the channel's id.
- **Frames from video** — a `derived` kind (needs PyAV; the host has no ffmpeg).
- **Automatic look-again** — on replies to a file, if a need appears.
- **External archives** (Immich, Paperless-ngx) — behind the store, if the owner wants one.

## Review before merge (2026-10-24)

`/code-review` with three parallel reviewers. Fixed: the collector removed bytes the index
had no row for, so restoring an older index (or losing it) would have deleted every newer
file at the next start; it now removes only bytes the index says were released, and
`files_verify.py` reports the rest as unknown. Also fixed: state checks moved inside the
write (a record finished after a delete is not written back; two state changes can't both
pass); Telegram waits for its reply as long as for the upload, and sends an image it can't
take as a photo (HEIC, over 10 MB) as a document; the app's image preview is computed off
the event loop; record previews in `open_file` are tagged as file content; daily logs no
longer count as using a file; `delete_file` refuses more than 20 ids rather than dropping
some, and takes expired copies too; the Outbox keeps a sent file's name and type; a file's
origin text is one flattened line. Left for #187: the legacy import can stop partway or
duplicate one arrival after a crash mid-file.

## Open questions

Tracked in #187 (checks after the deploy, growth, small decisions), with links to #185, #186
and jarvis-app#36.

- **Decided at the end of the plan: no video trimming for now.** A 2-minute, 21 MB video costs
  about 11k tokens per look, which is fine at reel lengths, and trimming needs the video's
  duration (a new dependency). Revisit through #187 if long videos or repeated looks become
  common; it shares the decoder decision in #186.
- Quota value and alert threshold; how long unused bytes wait before collection; link-video
  growth a month after the deploy: in #187.
