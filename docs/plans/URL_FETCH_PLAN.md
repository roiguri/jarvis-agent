# URL fetch — give Jarvis a way to read a link

**Status:** planned, not started.
**Date:** 2026-08-06.
**Goal:** add a `fetch_url` core tool so a URL handed to Jarvis is *dereferenced* rather than
guessed at, and close the loop that let a single unreadable link cost 4m44s and ~$0.36 in one turn.

---

## Context

On 2026-08-06 the owner sent a bare `x.com` status URL with "add this as well" — the same request
shape that had already failed at least twice before (see "This is a recurrence" below). Turn
`b068e543` ran **283.7 s**, made **16 LLM calls** and **14 consecutive `web_search` calls**, burned
**494,962 input / 65,994 output tokens** (64,321 of them reasoning) — ~10× the median user turn
(~59k total) and the 3rd most expensive user turn in two weeks. It then wrote a **fabricated**
description into `reading_list.md` and reported success confidently. The owner corrected it in the
next turn.

The root cause is not prompt tuning. `tools/core/search.py` exposes `web_search(query: str)` —
Tavily *search* — and it is the only web capability in the tree. There is no way to read a URL.
Given an address, the only available move is to invent a query, and for a numeric status ID that
is structurally hopeless.

Two properties made it expensive rather than merely wrong:

1. **`web_search` cannot fail.** Every path in `search.py` returns a string; a search that finds
   nothing relevant still returns `status: ok` and five plausible results. Nothing tells the model
   to stop, so it reformulated 14 times, each result adding tokens and plausibility-noise.
2. **Nothing bounded the retry.** The 14 searches took ~60 s; the remaining **3m39s** was a single
   LLM call thinking in circles after the searches produced nothing usable.

Then it anchored: the previous link in the thread was a Matt Pocock tweet, so the unknown tweet
became "another one from Matt Pocock."

### Audit of what this already cost in stored memory

All three `x.com` entries in `reading_list.md`, resolved for real during planning:

| ID | Actual | What Jarvis stored |
|---|---|---|
| `…286118` | **Norvex** (@norvex1029) — "Anthropic just published the official guide to building a knowledge graph with Claude…" | "Matt Pocock, /writing-for-agents deep dive" → corrected by the owner to "Anthropic — Graph Engineering" |
| `…012992` | **Matt Pocock** — skills v1.2 (`/wait-what`, `/writing-for-agents`, `/grill-me`) | correct |
| `…296679` | **Lunar** (@LunarResearcher) — summarizing Karpathy's "5 shifts turning LLMs into agentic systems" | "Andrej Karpathy on LLMs vs Agents" — wrong author |

Two of three are misattributed, and even the owner's correction is off (that tweet is *about* an
Anthropic guide, not *by* Anthropic). The one it got right is the one whose text
("mattpocock/skills v1.2") happens to be a searchable string — which is the tell: search succeeded
exactly where the content was guessable. **Fixing these entries is part of this work, not a
follow-up.**

---

## This is a recurrence, and it is already filed

**The 2026-08-06 incident is at least the third occurrence of the same failure.** Discovered while
reviewing open issues *after* the plan was drafted:

- **[#7 — Add a URL fetch & summarize tool](../../../../issues/7)** (enhancement, priority: medium,
  filed 2026-07-04) is this plan's Phase 1, already specified. Its acceptance criteria — "fetches a
  given URL and returns a usable summary" and "handles timeouts, blocked, and non-HTML fetches
  gracefully" — anticipated both halves of what this plan builds.
- **[#36 — Turns are unbounded, terminate abnormally, and leave no legible trace](../../../../issues/36)**
  (bug, priority: **high**) records incident 2 on **2026-07-30**: the same x.com-link request, the
  same **14 consecutive `web_search` calls**, then a single LLM call burning **65,668 output tokens
  over 6m48s**, ending in `MALFORMED_FUNCTION_CALL`. 7m43s, ~$0.31, `error: null`, and
  `reading_list.md` never updated. #7's comment notes the same 14-search flail had already run
  *the previous evening* and happened to recover.

**The failure mode has been getting worse, not better.** On 2026-07-30 the turn died and delivered
nothing — loud, visible, no memory written. On 2026-08-06 the turn *succeeded*: it completed
normally, reported confidently, and wrote a fabrication into `reading_list.md` that survived until
the owner happened to catch it. A silent corruption of stored memory is the worse outcome of the
two, and it is what the same root cause produces once the model gets slightly luckier with its
token budget.

**One premise in #7 needs correcting.** Its comment states that *"x.com blocks unauthenticated
fetches, so this exact URL will still fail"*, and therefore treats failing cleanly as the criterion
that prevents recurrence. Measured, that is no longer true: Tavily `extract` reads that exact URL
in full, tweet body and author handle included. The acceptance criteria can be strengthened from
"fails cleanly on x.com" to "**succeeds** on x.com" — with clean failure still required for the
genuinely unreadable cases (nasdaq.com was measured as failing on both extract depths).

**Relationship to the other open issues:**

| Issue | Relationship |
|---|---|
| [#7](../../../../issues/7) | **Closed by this plan's Phase 1.** Should be linked from the PR. |
| [#36](../../../../issues/36) | **Partially addressed** by Phase 2's repeat-call cap, which bounds the 14-search flail. #36 is broader — unbounded turns in *both* scopes, `GraphRecursionError`, and no legible trace — so it stays open. |
| [#59](../../../../issues/59) | Split out of #36: non-STOP finish reasons collapsing to an empty reply. Adjacent, not addressed here. The 08-06 turn terminated *normally*, so #59's path did not fire — same head, different tail. |
| [#17](../../../../issues/17) | Load-time tool gating by required env vars. More attractive once `web` is a skill: with no `TAVILY_API_KEY`, the whole skill could be skipped at registration rather than failing per-call. Still low priority; current deploys always have the key. |
| [#67](../../../../issues/67) | Staging shares prod's real external services, **explicitly including web search**. `fetch_url` adds a second consumer of the same 1,000-credit Tavily pool from staging. Minor, but it makes the shared-quota point slightly sharper. |
| [#18](../../../../issues/18) | Reduce token spend. The `max_chars` decision and the N3 history-stripping follow-up both feed it. |

## Evidence gathered during planning

Measured directly against live hosts (plain `httpx` GET with a Chrome-like UA; Tavily `extract`
via the existing client and key):

| Host | plain GET | readable text | `og:description` | Tavily `extract` |
|---|---|---|---|---|
| `x.com/i/status/…` | 200, 168 KB | **0 KB** (JS shell) | yes | **3,012 chars — full tweet + author** |
| `openai.com/index/…` | **403** | — | — | **24,002 chars** |
| `reddit.com/r/LocalLLaMA` | 200, 8 KB | **0 KB** | no | 8,860 chars, mostly nav |
| `youtube.com/watch?v=…` | 200, 1.3 MB | **0 KB** | yes | not tested |
| `github.com/…/langgraph` | 200, 292 KB | 7 KB | yes | not tested |
| `arxiv.org/abs/…` | 200, 43 KB | 4 KB | yes | 1,946 chars |
| `internet-israel.com` | 200, 125 KB | 5 KB | yes | not tested |

Three conclusions:

- **A plain GET is not enough.** It fails on x.com, openai.com (403 — a URL *already in the reading
  list*), reddit, and youtube. Bot-blocking and JS rendering are the norm, not the exception.
- **Tavily `extract` rescues every failing case tested**, including the exact URL from the incident.
  It returns the tweet body and the author handle. Same client, same `TAVILY_API_KEY` — **no new
  dependency and no new secret.**
- **Therefore no per-host adapter is required.** An earlier sketch in discussion proposed an
  x.com/Twitter oEmbed special case. `publish.twitter.com/oembed` does work unauthenticated and
  resolved all three tweets cleanly, but Tavily `extract` already covers x.com, so oEmbed was only
  ever an optimization — and once Q1 removed local fetching altogether it stopped making sense at
  all. Dropped; see Phase 3 for the reasoning, kept so it is not re-proposed.

A caution about all Tavily measurements here: **Tavily caches server-side** (measured: 1.31 s →
0.47 s → 0.18 s on repeat calls, byte-identical). Any A/B probe against a URL fetched earlier in
the session will silently compare two cache hits. The depth comparison under N1 controls for this;
naive re-measurement will not.

---

## References — OpenClaw and hermes-agent

Both references solve this problem, and they converge. Following the comparison format of
[CONTEXT_HANDLING_PLAN.md §Mechanisms](CONTEXT_HANDLING_PLAN.md):

| Mechanism | OpenClaw | hermes-agent | Jarvis today |
|---|---|---|---|
| Search / fetch split | `web_search` + `web_fetch`, **two tools** | `web_search` + `web_extract`, **two tools** | `web_search` only — **no way to read a URL** |
| Grouping | web tools together | **`web` toolset**, gated on credentials | `web_search` in always-on `core` |
| Extraction | Readability (Mozilla) on HTML → markdown/text | Provider-side extraction, markdown, **no LLM summarization** | n/a |
| Provider fallback | Readability → **Firecrawl** bot-circumvention mode | Backend chain: **Tavily → Exa → Parallel → Firecrawl → SearXNG → Brave → DDGS**; Firecrawl default | n/a |
| Truncation | `maxChars` default **20,000**, cap 50,000; body cap 750 KB | `web.extract_char_limit` default **15,000**; head+tail **~75/25** with `[TRUNCATED]` footer pointing at the full file on disk; absolute cap 2 MB | n/a |
| JS pages | explicitly **not** executed — punted to a separate headless browser tool | provider's problem (Firecrawl renders) | n/a |
| Per-host special cases | **none** | **none documented** | n/a |
| URLs per call | 1 | **max 5** | n/a |
| Caching | 15 min, configurable | not documented | n/a |
| SSRF | private/internal hostnames blocked by default; headers redacted from debug captures | not documented | n/a |

**The two answers that matter most for the open questions below:**

1. **Neither has per-host special cases.** Both tier by *capability* — plain fetch → extraction →
   bot-circumvention provider → (OpenClaw only) headless browser. Any host lands in whichever tier
   can read it. That generalizes; an adapter list does not. This is the direct answer to "do we
   only need a special case for x?" — no, and neither reference has *any*.
2. **Hermes groups them in a `web` toolset**, which independently confirms the scope decision taken
   in this plan. Its `web_extract` also takes **up to 5 URLs per call**, which Jarvis should not
   copy yet (see Open Questions).

Note also the internal precedent: `CONTEXT_HANDLING_PLAN.md` WS4 already adopts Hermes's head/tail
truncation marker (`[... truncated N chars — file intact on disk ...]`) for injected files. The
same marker shape should be reused here rather than inventing a second one.

Sources: [Web fetch · OpenClaw](https://docs.openclaw.ai/tools/web-fetch),
[Web Tools | OpenClaw Docs](https://openclaw-ai.com/en/docs/tools/web),
[Web Search & Extract | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-search),
[hermes-agent tools reference](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/tools-reference.md)

**What this plan takes from them:** the two-tool split, the `web` grouping, capability tiering with
no per-host adapters, and mandatory truncation. **What it does not take:** OpenClaw's headless
browser (capability-surface expansion, ruled out under the sandbox constraint) and Hermes's
provider-abstraction layer (seven backends is infrastructure for a distributed user base; Jarvis
has one Tavily key and one owner).

---

## Decisions carried in from discussion

- **`web_search` and `fetch_url` share one activatable skill — `tools/web/`.** They are two halves
  of one capability (find a page / read a page) and belong to the same namespace. This *reverses*
  an earlier draft decision that `fetch_url` should be a core tool; see "Tool scope" below for the
  argument and the measurement that made the reversal safe.
- **[Q1 — decided] Provider-only extraction, the hermes-agent way.** `fetch_url` never makes an
  outbound HTTP request of its own; it hands the URL to Tavily `extract` and returns the markdown.
  This *reverses* the draft's local-fetch-then-fallback design (OpenClaw's shape). Rationale:
  - Measured, a plain GET returns **zero readable text** on x.com, reddit and youtube and a **403**
    on openai.com. The "free path" only pays off on the well-behaved minority, so local-first buys
    much less than it appears to.
  - It removes the SSRF guard — the single most security-sensitive piece of the plan — because a
    URL naming `192.168.x.x` is fetched from Tavily's infrastructure, which cannot reach the home
    LAN. Phase 1 drops from ~150 lines to ~40.
  - No new dependency, and no HTML parsing in-process.
  - **Confirmed cheaper than the status quo** — see the credit arithmetic below.
- **No per-host adapters in Phase 1.** Justified by the measurement above, not by taste, and
  matching both references (neither has any).
- **The error path matters more than the happy path.** A clean "could not read this" is the whole
  point; it is what bounds the loop. Phase 4 exists to make the model *act* on it.
- **Truncation is mandatory, not a nicety.** An uncapped fetch of that x.com page is 168 KB — one
  call would cost more context than the 14 searches did.
- **Accepted trade-off:** reading a link now depends on Tavily being reachable. A Tavily outage
  costs Jarvis both search *and* fetch. Judged acceptable for a single-owner assistant; it is also
  the failure mode Hermes accepts by default.

### Tavily credit arithmetic (resolves the draft's biggest unknown)

| Operation | Credits | Per single URL/query |
|---|---|---|
| Search, basic | 1 per request | **1.0** |
| Search, advanced | 2 per request | 2.0 |
| **Extract, basic** | **1 per 5 successful URLs** | **0.2** |
| Extract, advanced | 2 per 5 successful URLs | 0.4 |

Failed extractions are **never charged**. Free tier: **1,000 credits/month**.

**A basic extract is 5× cheaper than a single search.** The incident turn spent **14 credits**
searching and still got the answer wrong; one `fetch_url` would have spent **0.2** and got it
right — **70× cheaper**, before counting the ~$0.36 of model tokens it also burned. Current usage
(53 searches in ~17 days ≈ 95 credits/month) sits far inside the free tier, and adding fetches at
0.2 each does not threaten it. The draft's worry that provider-only fetching would be expensive was
backwards: **it is the cheapest option on the table.**

Sources: [Credits & Pricing — Tavily Docs](https://docs.tavily.com/documentation/api-credits)

---

## Tool scope — one `web` skill holding both tools

`web_search` lives in `tools/core/` today (always bound). It moves into a new `tools/web/` skill
alongside `fetch_url`, so the pair is activated and deactivated together.

**Why together, rather than `fetch_url` in core:** the incident was caused by an *asymmetry* — the
model had search available and fetch unavailable, so when it needed to read a URL it reached for
the only web-shaped tool in reach and searched 14 times. Binding the two into one namespace makes
that state unreachable: either both are available or neither is. There is no configuration in
which the model can substitute search for a fetch it cannot perform.

**Measurement that makes this safe** (`tool_calls.jsonl` joined to `turns.jsonl`, since 2026-07-20):

- `web_search`: **53 calls across 20 turns, 100% in `user` scope — zero in `heartbeat`.**
  So gating it behind activation costs the heartbeat nothing. This was the main risk and it is
  empirically absent.
- Activation is **sticky per thread** (`active_skills` persists in thread state — the incident turn
  itself ran with `active_skills: ["fitness"]` carried in). So the cost is one extra `activate_skill`
  round-trip on the first web-shaped request in a thread, not per turn. Against 14 wasted searches,
  that trade is not close.
- `compact_skill_list` always renders every top-level skill's one-line description, so the model
  can always *see* that a `web` skill exists — activation is discoverable, not hidden.

**Naming:** `web` rather than `search`, since the skill contains a reader as well as a searcher and
the namespace is what the model reads in the skill list. Flat namespace, no sub-skills — two tools
does not justify the two-step discovery machinery.

**Residual risk to watch in staging:** a turn where the model answers from training knowledge
instead of activating `web`. AGENTS.md line 6 currently leans on search being always-on ("Use web
search proactively…"); that line has to be rewritten to say *activate `web` and search* or the
nudge points at a tool that is not bound. Phase 2 covers it.

## Phase 1 — the `web` skill

**New directory `tools/web/`** containing:

- `__init__.py` — imports both modules, running the `@tool_register` side-effects
- `SKILL.md` — YAML frontmatter (`name: web`, `description:`) plus a short rules body: *use
  `fetch_url` for any URL; never try to identify a link by searching for it; if a fetch fails, say
  so rather than guessing*
- `search.py` — **moved** from `tools/core/search.py`, `namespace="core"` → `namespace="web"`,
  otherwise unchanged
- `fetch.py` — new

Per CLAUDE.md that is the entire wiring — the registry auto-discovers the namespace from the
directory. No registry, agent, or prompt-assembly edits.

**Callers/docs to update in the same change** (all references to search being always-on):
`prompts/AGENTS.md` lines 4 and 6, `README.md:78`, `docs/architecture/RUNTIME.md:41`,
and the CLAUDE.md core-tools description.

Pipeline (Q1 = provider-only):

```
fetch_url(url, max_chars=8000) →
  1. validate   scheme in (http, https); reject everything else
  2. extract    TavilyClient.extract(urls=[url], format="markdown")   # basic depth
  3. empty?     no results → return a plain, unambiguous failure string
  4. truncate   to max_chars, reusing the CONTEXT_HANDLING_PLAN marker shape
  → returns: final URL, title, text  |  or a clear "could not read" string
```

No `httpx` call, no HTML parsing, **no SSRF guard** — Jarvis never dereferences the URL itself.
Scheme validation stays so that `file://` and friends fail fast and locally rather than being
handed to a third party.

House pattern to follow: `tools/core/search.py` — one `try`, a typed-exception ladder
(`UsageLimitExceededError`, `InvalidAPIKeyError`, `MissingAPIKeyError`), and a helpful string on
every failure rather than a raise. `fetch.py` should mirror it almost line-for-line, since the two
tools now sit in the same skill and share a client and a key.

**Dependencies:** none. `tavily-python==0.7.24` is already in `requirements.txt` and already ships
`extract()`.

Estimate: **~40 lines.**

## Phase 2 — bound the failure

Three changes that matter even after Phase 1, because a fetch that fails will otherwise send the
model straight back to `web_search` for another 14 rounds.

- **[Q5 — decided] Input-shape guard in `web_search`.** Detect a query that is a URL or a bare long
  digit string and return, without searching:
  *"That looks like a URL or an ID, not a search query. Use `fetch_url` to read it directly."*
  Deterministic, nothing to tune, and it catches the incident's exact shape. See "Routing" below
  for why this is a *code* guard rather than a prompt rule.
- **Repeat-call cap in `tools/registry.py`.** Cap identical-tool calls per turn (~5), then return a
  hard stop: *"you have called `web_search` 5 times without success; stop and report what you could
  not determine."* Uses the existing per-turn context (`turn_context.py`). This is the single
  highest-leverage change in the plan — it bounds cost for *every* tool, not just this one.
  The guard stops the *first* wrong search; the cap stops the *fourteenth*.
- **A URL rule in `prompts/AGENTS.md`** (currently 26 lines, zero mentions of links/URLs), plus the
  rewrite of line 4 and line 6 that the `web` skill move forces anyway.

### Rejected: a relevance-score threshold on `web_search`

Recorded so it is not re-proposed. The idea was to return "no relevant results" when Tavily's
scores are all low. **Measured, it fails on exactly the case it was meant to catch:**

| Query | Top score | What came back |
|---|---|---|
| `2084703057267286118` — **the incident's exact query shape** | **1.0** | "ASCII Codes", "ASCII Character Chart" |
| `https://x.com/i/status/2084703057267286118` | 0.29 | unrelated tweet URLs |
| `qwzx plarn vetch dooble` (nonsense control) | 0.27 | plastic-bag-yarn craft pages |
| `Anthropic knowledge graph guide Claude` | 0.76 | the actual Anthropic cookbook |
| `mattpocock skills v1.2 release` | 0.83 | the actual release page |

Tavily's score measures match-to-query, not usefulness — and a bare numeric ID matches numeric
tables *perfectly*. A threshold would have caught the nonsense control and let the real incident
through at a maximal 1.0. Guard the **input shape**, not the output score.

### Routing — how the references steer search vs fetch

Checked directly, because `tools/web/SKILL.md` has to encode this:

- **Tavily's own published skill** states the rule most plainly: use extract *"when the user has one
  or more URLs and wants their content"*, and defer to the search skill *"when you don't have a
  URL."* Have a URL → read it. No URL → search. It also notes: if search results already carry the
  content, skip the extract step.
- **OpenClaw's `AGENTS.default.md` contains no web-routing guidance at all** — routing is left
  entirely to the tool descriptions.
- **Hermes likewise** ships pure capability descriptions (`web_search`: "Search the web…";
  `web_extract`: "Extract content from web page URLs…") with no routing prose.

**Conclusion: every reference routes through tool descriptions, not prompt rules** — which matches
this repo's own stated position that "tool usage is driven by tool docstrings, not prompt prose"
(CLAUDE.md). So the primary routing mechanism here should be a sharp `fetch_url` docstring plus the
`web` SKILL.md body, and `AGENTS.md` should stay thin.

**But Jarvis goes one step further than any reference, deliberately.** None of them *enforce* the
rule in code; all three trust the model to route correctly. Jarvis has a logged incident proving
that trust can fail expensively, so the input-shape guard encodes in code what the references leave
to judgment. That divergence is intentional and should be noted in the SKILL.md rules body rather
than silently added.

## Phase 3 — dropped: x.com oEmbed tier-0

**Dropped 2026-08-06.** The draft proposed `publish.twitter.com/oembed` as a free, no-auth tier-0
for x.com. Tavily `extract` is now measured as covering x.com fully (tweet body + author), and the
Q1 decision removed local fetching entirely — an oEmbed tier would reintroduce exactly the
outbound-HTTP path that decision deleted, for one host, to save 0.2 credits. Recorded here so the
idea is not re-proposed; the general principle (no per-host adapters, matching both references)
stands.

Guard rail: **resist a second special case.** Past one, the answer is the generic fallback. If a
host list starts growing, that is the signal to reconsider a Firecrawl-class provider, as OpenClaw
did — not to accumulate adapters.

## Phase 4 — repair the corrupted entries

Re-fetch and rewrite the three `x.com` entries in `reading_list.md` from real content, including
the one the owner already corrected (still wrong on authorship). Worth spot-checking the rest of
the file for other entries written from search guesses rather than fetches.

---

## Open questions

**Disposition rule.** Questions still open when the plan is otherwise settled get one of three
outcomes at end-of-plan review, decided together rather than one at a time: **adopt** (fold into a
phase), **drop** (record the decision not to do it, with the reason), or **escalate** (file a
GitHub issue and let it live outside this plan — the precedent being #32/#33 from the outbox work).
Nothing stays "open" past the plan's close.

1. ~~**Extraction library.**~~ **RESOLVED 2026-08-06 → provider-only (hermes-agent shape).** No
   extraction library, no local fetch. See "Decisions carried in" above. The alternatives
   considered (trafilatura, readability-lxml, hand-rolled `og:`+regex) are all moot once Jarvis
   stops fetching HTML itself.

2. ~~**`max_chars` default.**~~ **RESOLVED 2026-08-06 → 8,000 chars, 70/20 head+tail**, reusing the
   `[... truncated N chars ...]` marker shape already committed to by
   [CONTEXT_HANDLING_PLAN](CONTEXT_HANDLING_PLAN.md) WS4 rather than inventing a second one.

   Deliberately below both references (OpenClaw 20,000; Hermes 15,000/75-25) for two
   Jarvis-specific reasons they do not share:

   - **Tool results are re-sent verbatim.** `_add_and_trim` (`agent.py:133`) keeps a 50-message
     window, and `_strip_media_blobs` strips media blobs from history but **not text**. A fetched
     page is therefore re-sent on every following turn in the thread until it falls out. Both
     references compact or summarize history; Jarvis does not, so their numbers do not transfer.
   - **Truncation here is terminal.** Both references truncate with a pointer to the full content
     on disk. Jarvis's memory tools are sandboxed to `/app/jarvis_memory/` and `/app/jarvis_data/`
     is explicitly never in the memory tool surface, so there is no read-the-rest path. (This one
     cuts the *other* way — it argues for a larger cap — and is why 8,000 rather than something
     smaller.)

   Head+tail rather than head-only because an article's conclusions and a thread's replies both
   live at the end. Measured coverage: arxiv (1,946), x.com (3,612) and HN (3,709) fit whole;
   openai.com (24,002) and Wikipedia (242,293–600,274) truncate.

   **Related follow-up, deferred to next steps:** strip fetched text from history after its turn,
   exactly as `_strip_media_blobs` already does for images — the model has consumed the content by
   the time the turn ends. That would let the cap be generous *and* cheap.

3. ~~**Should `web_search` also learn to fail?**~~ **RESOLVED 2026-08-06 → yes, via an input-shape
   guard**, folded into Phase 2. The score-threshold approach was tested and rejected; the
   measurements are recorded under "Rejected: a relevance-score threshold" so it is not
   re-proposed.

**All five drafting questions are now closed.** What remains open is the two deferred next steps
(N1, N2) plus the history-stripping follow-up noted under question 2.

---

## Possible next steps — deferred, not in Phase 1

Both are enhancements to a shipped `fetch_url`, deliberately held back so Phase 1 stays minimal.
Disposition (adopt / drop / escalate to a GitHub issue) happens at end-of-plan review, together.

**Consequence of deferring, stated plainly:** Phase 1 therefore ships **basic depth and no cache** —
the Tavily client defaults. If the advanced-depth evidence below is judged compelling, that is an
argument for pulling it into Phase 1 rather than deferring it.

### N1 — `extract_depth="advanced"`

   **Credits are not the constraint.** The 1,000/month free tier is a *single shared balance*
   across all Tavily endpoints, not per-endpoint. Current usage is ~95 credits/month. At 100
   fetches/month, advanced depth adds ~40 credits — a combined ~13.5% of the free tier. Cost
   should carry almost no weight in this decision.

   **Measured, basic vs advanced** (fresh URLs never previously fetched; advanced deliberately run
   *first* on half the cases, because a first comparison on already-fetched URLs returned
   byte-identical results in 0.2s — Tavily serves its own cache, which silently confounds naive
   A/B probes):

   | URL | basic | advanced | delta |
   |---|---|---|---|
   | x.com tweet *(advanced first)* | 1,111 chars | **3,612** | advanced **3.3×** |
   | news.ycombinator.com *(advanced first)* | 1,577 chars | **3,709** | advanced **2.4×** |
   | Wikipedia comparison table | 600,274 chars | **153,021** | advanced — 4× *less* boilerplate |
   | nasdaq.com (JS app) | 7,335, content absent | 7,335, content absent | tie, both failed |

   Advanced won even where it ran first, so this is not a cache artifact. Tavily's docs recommend
   advanced specifically for **JS-rendered SPAs** — which is what x.com is, and what caused the
   incident. Note also that advanced *reduces* the truncation burden on Wikipedia rather than
   adding to it.

   **Against advanced:** latency. ~7 s typical vs ~1 s, and Tavily's default timeout rises from
   10 s to 30 s. Against a 283-second incident that is noise, but it is a real per-turn cost on
   every link the owner sends.

   **Sub-question, also open:** whether a quota guard is needed at all. `web_search` already
   surfaces `UsageLimitExceededError` gracefully, so mirroring that ladder in `fetch.py` may be
   sufficient without any counter or state.

   *(A third option exists — basic with escalation to advanced when the result looks thin — but it
   adds a retry path to the one code path whose purpose is to fail cleanly, and the measurements
   suggest it would fire on most social/SPA content anyway.)*

### N2 — local caching

**Measured: Tavily already caches server-side.** Same URL, same depth, three calls in a row:
**1.31 s → 0.47 s → 0.18 s**, byte-identical output. A local cache would duplicate a cache we
already inherit for free.

**The references split, and the split maps onto the Q1 decision.** OpenClaw caches for 15 minutes
(`cacheTtlMinutes`) — but *because it fetches locally*: it bears the full round-trip and HTML parse
itself and has no provider cache to lean on. Hermes documents no cache at all, consistent with its
provider-only design. Having taken the Hermes shape in Q1, OpenClaw's rationale does not transfer.

**What a local cache would still buy:** credits only. Tavily bills per successful extraction, cache
hit or not, so three repeats cost 0.6 rather than 0.2 credits. Against ~95 used of 1,000/month,
that is not a reason.

**Options if adopted:**

| | State | Buys | Costs |
|---|---|---|---|
| in-turn memo | dict, dies with the turn | dedupes within one turn | ~nil; staleness impossible |
| TTL cache (OpenClaw) | 15-min store, needs a home in `/app/jarvis_data/` per the placement principle | dedupes across turns | staleness, eviction, a new state owner |

**Caveat on the evidence:** Tavily's caching is inferred from timings, not a published guarantee.
If they change it we lose the latency win silently — though no worse off than never having cached.

**Overlap to keep in mind:** the failure mode a cache would blunt (the model hammering one URL) is
addressed more directly by the Phase 2 repeat-call cap, which *stops* the loop rather than making
it cheap. Adopting both means two mechanisms half-solving one problem.

### N3 — strip fetched text from history after its turn

`_strip_media_blobs` (`agent.py:137`) already removes media blobs from messages the LLM has
previously seen, keeping them only for the turn that needs them. Fetched page text has the same
profile: large, consumed once, then dead weight re-sent on every following turn inside the
50-message window.

Applying the same treatment would decouple `max_chars` from the re-send multiplier entirely — the
cap could be generous (Hermes's 15,000, or OpenClaw's 20,000) without a per-turn tax, since the
text would survive only the turn that fetched it.

**Against:** a follow-up question about the same page in a later turn would need a re-fetch (~0.2
credits, and Tavily's cache makes it fast). Also a slightly surprising asymmetry — the model can
see a page it read two turns ago in its *reply*, but no longer in its *context*.

Sequencing note: N3 and the Q4 decision interact. If N3 is adopted, revisit the 8,000-char cap —
its main justification disappears.

---

## Non-goals

- **No headless browser / JS execution.** That is a capability-surface expansion; the sandbox
  constraint from `docs/plans/archive/ARCHITECTURE_PLAN.md` stands.
- **No authenticated fetching**, no cookie jar, no login-walled content.
- **No crawling.** Single URL per call; Tavily's `crawl` is not used.
- **No new secrets.** If a tier needs a new API key, it does not belong in Phase 1.
