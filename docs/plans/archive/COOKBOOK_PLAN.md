# Cookbook — Jarvis reads and saves recipes in the family cookbook

**Date:** 2026-10-24 · **Status:** ✅ complete, archived 2026-10-24 — C1–C4 shipped; prod needs the owner's cookbook token · **Branch:** `feat/cookbook`
**Goal:** Jarvis can find and read recipes in the family cookbook (the `roiguri/cookbook` site)
and, after talking a recipe through with the owner, save it there: from a reel, a page, a photo,
a voice note or plain chat, with its photos and step video.

The cookbook side is built: its Recipe API (`docs/architecture/recipe-api.md` in that repo,
PR #436 there). This plan is the Jarvis side only.

---

## Slices

- [x] **C1 — Reads.** The `cookbook` skill with its client, settings and errors; `find_recipes`
      and `get_recipe`. Checked against the cookbook's staging project.
- [x] **C2 — Save a recipe.** `save_recipe` (JSON, no files): the owner's final confirmation,
      an id from the source so a save is never duplicated, Hebrew content, estimates marked, the
      skill's rules for talking a recipe through first.
- [x] **C3 — Photos and step video.** `save_recipe` takes stored files by id: photos (resized
      to the API's limits when needed) and one step video.
- [x] **C4 — A reel's cover picture.** `fetch_url` keeps an Instagram or TikTok video's cover
      image as its own stored file and names its id, so it can become a recipe's main photo
      (or anything else's). Independent of the cookbook.

Order: C1 → C2 → C3. C4 any time; it is a web change and needs nothing from C1–C3.

## 1. Where things stand

- **The API** (cookbook repo, `functions/api/`): reads (`/v1/me`, `/v1/categories`,
  `/v1/recipes/mine?stage=`, `/v1/recipes?category=&q=`, `/v1/recipes/{id}`) and create
  (`POST /v1/recipes`, JSON or multipart with files). It acts as the token's owner, under the
  app's own rules. Created recipes are always private; no update, delete or publish. A create is
  safe to retry with a caller-chosen `id` (same body → `200 created: false`, nothing changed).
  Recipes must be complete (`name`, `category`, `difficulty`, `prepTime`, `servings`,
  ingredients, instructions); difficulty is Hebrew (`קלה` / `בינונית` / `קשה`); unknown fields
  are errors. Photos JPEG / PNG / WebP ≤ 5 MB (≤ 5, one primary); step media image or
  MP4 / WebM / QuickTime ≤ 50 MB; one request ≤ 30 MB. 1,000 requests per token per day; 1–3 s
  per call. Incomplete private recipes are cookbook issue #434.
- **Jarvis** has no recipe store and no recipe collection. What a recipe is made from already
  exists: `fetch_url` returns a reel's poster, caption and record (said / on-screen text /
  shown) and keeps the video in the file store with its URL and poster; long pages are kept as
  text files; the owner's photos, voice notes and documents are stored by id with records.

## 2. Design decisions

### One owner, one token

Jarvis has one owner, so one token: the owner's, scope `read+write`. Everything saved goes into
the owner's library (private); reads see what the owner sees (own, published, shared). The
owner's cookbook role must be approved or manager. Saving for other family members is not
planned (it would need a token per person and a way to choose).

Settings per instance in its `.env`, added by the owner: `COOKBOOK_API_URL` (the function's
base URL) and `COOKBOOK_API_TOKEN`. Staging Jarvis talks to the cookbook's staging project with
a staging token; prod to prod. A missing setting makes the tools say the cookbook isn't set up,
nothing else. The token never appears in logs, errors or tool results.

### Tools stand alone

The cookbook tools know nothing of where a recipe came from. Jarvis gathers the content however
it arrived (a link through `fetch_url`, a photo or voice note in chat, a stored page, the
owner's own words), and `save_recipe` takes the recipe as structured fields plus file ids. No
cookbook tool calls or imports another tool; `fetch_url` knows nothing of the cookbook. The file
store is the only thing they share, through ids.

### Talk it through, then confirm

The skill's rules make saving a conversation: Jarvis drafts the recipe in chat (in Hebrew),
says which details it estimated and which it couldn't find, and adjusts it as the owner asks.
It calls `save_recipe` only once the owner agrees. `save_recipe` then asks for a final
confirmation (Confirm / Cancel, the gateway's confirmation flow) showing the name, category,
stage, the estimated fields and the files; the request is sent only on Confirm.

### Missing details: estimated and marked

Sources rarely state prep time, servings or difficulty, and the API refuses a recipe without
them. Jarvis estimates what the source doesn't say, shows the estimates as such in the draft,
and the saved recipe gets a comment saying so, about the recipe rather than about Jarvis
(e.g. "זמן ההכנה וכמות המנות הם הערכה בלבד."). A
value the source states is never marked. Never invented: amounts, ingredients or steps that
aren't in the source.

### Hebrew

Recipes are saved in Hebrew, translated when the source is in another language; the original
name goes in `description`. Ingredient units in Hebrew as the site uses them.

### Stage and approval

`stage` is `to-try` unless the owner says they've made it (`tested`). `submit` (send to the
managers for approval, the step before publishing) only when the owner asks.

### Never twice

The recipe's id comes from its source: `jarvis-` plus a short hash of the source URL, or of
the file id(s) or the draft when there is no URL. Saving the same reel again answers "already
in your cookbook" (the API's `created: false`) instead of a copy; a retry after a timeout is
safe.

### Attribution

`attribution` gets who the recipe is from and where: the poster and URL a link video's file
records, a page's URL, or the person the owner names.

### Files

`save_recipe(photo_file_ids, video_file_id)`. Photos: the first is the main one; HEIC or a photo
over 5 MB is converted / resized to JPEG (Pillow, already a dependency); at most 5. Step video:
one, MP4 / WebM / QuickTime; if the whole request would pass 30 MB the video is left out and
the result says so. A file that is gone or of the wrong kind is refused with a clear message
before anything is sent.

### Reads

`find_recipes(query, mine, category, stage)` returns summaries with ids and site links;
`get_recipe(id)` returns the full recipe (ingredients, steps, photos' links). The API searches
by name only; Jarvis can list the owner's recipes and open the likely ones.

### Errors

Each API error code becomes one plain sentence for the model (`401`/`403`: the cookbook token
isn't valid or lacks access, tell the owner; `400` with `details`: the fields to fix; `429`:
the daily limit; `5xx`: try again later, safe with the same id). Non-JSON platform errors are
treated as `bad-request` / `payload-too-large`, as the API docs say.

## 3. Slice details

### C1 — Reads

`tools/cookbook/`: `SKILL.md`, `_client.py` (settings, one `httpx` call with a timeout, error
mapping, the categories list cached for the process), `recipes.py` (`find_recipes`,
`get_recipe`). Tests against a fake HTTP transport. Staging: the owner creates a staging token
(read+write) in the cookbook's staging dashboard and adds the two settings to staging's
`.env`.

### C2 — Save a recipe

`save_recipe` (JSON): builds the body (the recipe as drafted; the cookbook validates it, and
its field-by-field refusal comes back as the outcome, so Jarvis fixes it and asks again;
nothing of the cookbook's rules is repeated in Jarvis); the id from the source; the
confirmation; the comment for estimates. The skill's rules for drafting and discussing.
Tests: body shapes, retries (`created: false`), a refusal shown as a failure, each error
code.

### C3 — Photos and step video

Multipart: the JSON in `request`, one part per file; photo conversion; the 30 MB budget.
Tests with stored files of each kind.

### C4 — A reel's cover picture

`tools/web/media.py` asks yt-dlp for the thumbnail with the video (`--write-thumbnail`), keeps
it as an image file (source `web`, origin the post's URL, the video's id), and names it in the
result next to the video. Also gives the app a picture to show for link videos (#186, in part).

## As built

`tools/cookbook/`: `_client.py` (settings, the call, error sentences, categories cached an
hour), `recipes.py` (`find_recipes` filters the owner's list by name and category here, since
`/recipes/mine` takes only `stage`; `get_recipe` renders the recipe inside `<recipe>` tags),
`save.py` (`save_recipe` with a typed `Recipe` schema; local checks; `recipe_id()`; photos
re-encoded to JPEG when over 5 MB or not JPEG / PNG / WebP; one step video within the 30 MB
request). `tools/web/media.py`: `--write-thumbnail` under its own name (`<id>.cover.<ext>`),
kept when it is an image, named in the result as "Cover picture kept as [file:…]" (for a post
without a video too). Always-on cost: the skill's line in the skill list; active, about 1.4k
tokens.

## Staging checklist

1. **Reads:** "what recipes do I have in the cookbook?" lists them with ids; "show me <one>"
   opens it in full. A published search by name works.
2. **Talk it through:** send a recipe reel ("save this to the cookbook"). Jarvis drafts it in
   Hebrew in chat, names its estimates, and waits. Ask for a change; it adjusts.
3. **Confirm:** after "yes, save it", the Confirm / Cancel prompt shows name, category,
   stage, estimates, photo and video. Cancel once: nothing saved. Then confirm: the recipe is
   in the staging cookbook as private / to try, with the reel's cover as its main photo, the
   estimate note in its comments, and the attribution.
4. **Never twice:** ask to save the same reel again: "already in the cookbook".
5. **Other sources:** a recipe from a photo you send, and one dictated in chat (no files).
6. **Tried / approval:** "I made it, save it as tried" saves as tested; approval only when
   asked.
7. **Cover picture:** the reel's result names "Cover picture kept as [file:…]"; `send_file`
   of it shows the picture.

**Result (2026-10-24, staging):** reads listed the 64 staging recipes; a Turkish reel was
drafted in Hebrew in chat (two ingredient sections, seven steps, estimates named) and, after
"yes" and Confirm, saved private / to try with the reel's cover as main photo, the reel as a
step video and the estimate note. Fixed after: no `attribution` was set (a rule now asks for
it), and the id was made from the link as given, tracking code included (`?srtk=…` changes
per share), so the same reel shared again would have been saved twice; tracking parameters
are now dropped before the id is made. Built in one go (C1–C4), so C1–C3 are one commit.

**Review before merge (2026-10-24):** a failed save after Confirm now raises, so the owner's
prompt shows "❌ Action failed" instead of "✅"; the id includes the recipe's name, so a second
recipe from the same reel or page gets its own; timestamps and `youtu.be` links are one link;
and, at the owner's request, Jarvis no longer checks the recipe itself (the cookbook does;
repeating its rules would drift) and a range becomes one chosen number, never text. The
estimate note reads as a statement about the recipe ("זמן ההכנה וכמות
המנות הם הערכה בלבד."), at the owner's request.

## Decided

- No separate record of saved recipes in Jarvis (no collection, no memory entry): the cookbook
  and chat history are enough.
- No eval scenario for drafting.
