# Instinct (personal agent) — memory and context system, as of 2026-10

Labels used throughout: **[official]** = Instinct/Spear Street Technology's own material or founder statements; **[RE]** = Dhravya Shah's reverse-engineering (X post + Supermemory blog, 2026-09-20); **[3P]** = other third-party coverage. Every RE claim is a black-box reconstruction: Shah states "because I haven't seen their code, some things may be wrong", and Instinct has not confirmed it.

Access note: x.com was not fetched directly. The post was read through the api.fxtwitter.com mirror. The full linked article was read on the Supermemory blog, which carries the same title and content. A youmind.com mirror of the X article agrees with it.

## What exactly is Instinct (company, product, launch date, platform)?

### Takeaway
Instinct is a personal agent you reach by text message (iMessage) or phone call, and it has no app of its own. It is built by Spear Street Technology, Inc., which was founded by Noah Shinn (an early Sierra employee and author of Reflexion). It launched as an invite-only beta on 2026-08-26. Shah's post refers to this product: he calls it an "iMessage assistant" that "has taken the world by storm over the last two weeks", and RuntimeWire independently ties the post to Shinn's Instinct.

### Cited Findings
- [official, site, fetched 2026-10] The assistant "understands what you're working on and what's important to you". It connects to "email, messaging, screen, audio, location, and more". You text or call it. Example tasks include following up on dropped threads, texting or calling you first, arranging airport rides and booking a handyman — [instinct.com](https://instinct.com)
- [3P, eesel, last edited 2026-09-28] The product is described as "A personal agent you text or call. It has no app of its own. It uses a phone and a computer the way you would, with your email, calendar, accounts and cards, to book, buy, cancel, call and follow up". It is built by Spear Street Technology, Inc. and "Launched as an invite-only beta on August 26, 2026". Accounts live at app.instinct.com — [eesel.ai](https://www.eesel.ai/blog/instinct-ai)
- [official, privacy policy "Last revised on August 26, 2026"] Data is used by default to "evaluate, fine-tune, and train the AI models", with an opt-out. "Third-party AI model providers" receive data. Google Workspace data is excluded from training. No retention periods are given for conversation history or memory — [instinct.com/privacy-policy](https://instinct.com/privacy-policy)
- [3P, RuntimeWire] Instinct was "introduced publicly in August 2026 by founder Noah Shinn". Shinn was previously at Sierra and published Reflexion — [RuntimeWire](https://runtimewire.com/article/supermemory-reverse-engineers-instinct-memory-git-markdown-grep)
- [RE, 2026-09-20] Shah calls Instinct "one of the best iMessage assistants I've used" — [Supermemory blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)
- [3P, conflicting] The funding and valuation figures disagree across sources. One search summary reports a $1B Series C at a $10B valuation in September 2026 (Sequoia, Benchmark, Coatue). A Chinese founder interview (woshipm, 2026-09-30) reports $1B Series C at a $100B valuation, "up from $2.5B one month prior". An ecosistemastartup headline says "vale US$2.500M". Treat all of these as unverified — [woshipm](https://www.woshipm.com/ai/6472662.html); [ecosistemastartup](https://ecosistemastartup.com/instinct-agente-ia-de-23-anos-vale-us2-500m-y-reta-a-meta/)

### Inferences
- No other product named "Instinct" turned up in an agent-memory context, so I see no conflation risk for this post. Its iMessage and "past two weeks" framing matches the late-August launch.

### Gaps
- I found no official Instinct engineering blog, documentation, or memory FAQ. The site shows no docs or blog links.
- I could not verify funding from a primary source.

## What did Shah's post reveal about the memory architecture?

### Takeaway
[RE] Memory is a set of **git-tracked markdown files in a typed directory tree**, searched with **grep and keyword/alias matching rather than vectors**. The answering agent can only **read** memory: it gets an injected profile and one-pager and uses bash-like tools to grep, list and inspect git. All writing and consolidation happens in a **once-daily background ingestion/reconciliation job** that produces git commits. Injected context is a profile (~4,250 tokens) plus a compaction recap (~8,750–10k tokens), a to-do board and a session ID.

### Cited Findings
**Method and confidence**
- [RE] Shah spent 3 days probing "on the surface of the agent (the iMessage interface)", working through assumptions and then trying to verify them. Posted 2026-09-20. The X post had about 626k views, 2.3k likes and 5.6k bookmarks when fetched — [fxtwitter mirror](https://api.fxtwitter.com/status/2101745550752428340); [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)
- [RE] Shah's caveat is "Most of it should be correct, but because I haven't seen their code, some things may be wrong". He lists these as unknown: "whether the generator reads all files, changed files, search results, earlier summaries, or independently stored facts", and also the generator ("dreaming, or learning") model, its prompt, scheduling, conflict handling and how it handles forget requests — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)
- [3P] RuntimeWire says "Instinct did not confirm the architecture" and that testing stayed under 1M tokens — [RuntimeWire](https://runtimewire.com/article/supermemory-reverse-engineers-instinct-memory-git-markdown-grep)

**Injected context: what the answering model receives**
- [RE] The answering model receives the current conversation context, an identity "profile" of the user, "a memory one-pager (of what's going on)", "a compaction recap", "a to-do / tasks board" and a session identifier. The figures are "~4,250 tokens of profile" plus "~10k tokens of compacted conversation context (which, obviously, depends on the conversation)". The blog also gives ~8,750 tokens for the recap, which covers "conversation anchors and open loops". The one-pager is described as derived from the filesystem and updated daily. To-dos carry no timestamps — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)
- [RE] The profile holds name, timezone and account email, plus three sections: "Life context: a summary of selected user circumstances and relevant people or work", "Autonomy calibration: selected preferences about when the assistant should act or ask", and "Channel communication style: selected preferences about how to communicate" — [youmind mirror](https://youmind.com/landing/x-viral-articles/instinct-ai-memory-reverse-engineering)
- [RE] "A part of the profile is an index for the available things". The agent then "makes use of the tools available to look up more information" — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)

**Retrieved context: tools and search**
- [RE] "Instinct seems to be using bash-like tools to do grep, list and inspect git, plus a few tools to manage its todo list." "Memory is read-only, at least for the agent" — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)
- [RE] Search tests: "pasta" put the dining record first, and "takeout" did too. "Italian noodles I enjoy" returned no hits. The misspelling "pazta" returned no hits. A person's name with one extra character still ranked that person first. This is consistent with keyword or alias matching, possibly with mild fuzziness on names. Aliases exist "so that every file has a good chance of showing up when the agent is looking for it" — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)

**Storage layout and record format**
- [RE] Directory layout: `entities/people/`, `entities/orgs/`, `knowledge/facts/`, `knowledge/preferences/`, `knowledge/decisions/`, `comms/phone/` (conversation digests), `timeline/daily/`, `timeline/weekly/`, `workstreams/active/`, `workstreams/completed/` — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)
- [RE] Each record has YAML frontmatter (`id`, `type`, `aliases`) followed by prose and bullets. Facts are dated inline (example: "Loves pasta; stated on 2026-09-15"). Records cross-link with `[[related-record-id]]` wikilinks, forming a loose graph. Shah saw about four types: preference, person, organization, conversation — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)

**Writing and consolidation**
- [RE] Ingestion and reconciliation runs "once every 24 hours". This was inferred from a preference taking "approximately 23 hours 16 minutes from message to reported commit" — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)
- [RE] "Reconciliation commits do more than just append information". They move temporary details into workstreams, shorten durable records while linking to fuller notes, turn examples into broader traits, remove incidental details, and replace incorrect facts with dated corrections. Example commits: `c12e56c` "Moved pending transfer detail from a person record to a workstream"; `59b7f36` "Compressed narrative and generalized a behavioral example"; `61fb47e` "Replaced literal one-time codes with generic wording"; `7e9e1c2` "Replaced a travel-fee claim with corrective wording" — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)

**Compaction and its limit**
- [RE] "if you text Instinct too much in 24 hours it will quite literally tell you to come back tomorrow, since you can't compact beyond a certain token threshold" — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)

**Forgetting**
- [RE] "An explicit 'this is not happening' will be forgotten, but 'I have my exams this weekend' will remain in the records, unless the model looks at it and chooses to remove it." "Instinct does forget things based on when the ingestion runs etc. But, this is not 'automatic' right now." Old content survives in git history and dated notes — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/); [youmind](https://youmind.com/landing/x-viral-articles/instinct-ai-memory-reverse-engineering)

**Shah's quality verdict**
- [RE] Single-fact recall ✅. Multi-hop across sessions: weak. Temporal/recency ✅. Update & contradiction ✅. Abstention ✅. Forgetting/decay: partial (pruning but no automatic decay). Performance above 1M tokens: untested. Procedural/skill memory ❌. Test-time learning ✅. Implicit personalization ❌. Explicit personalization ✅. Multimodal: weak. Write-side cost: "likely expensive". Overall: "Capable under explicit retrieval instructions, inconsistent in natural personalization, with forgetting guarantees unresolved" — [youmind](https://youmind.com/landing/x-viral-articles/instinct-ai-memory-reverse-engineering); [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)

**Supermemory re-implementation**
- [RE] The blog includes a roughly 60-line TypeScript re-implementation on Supermemory. A `withSupermemory()` wrapper auto-injects the profile. There are tools to list and create profile "buckets" and a search tool with options for forgotten memories and history. It uses a per-day conversation ID, so each day is ingested as one entry, and adds memory on every turn. Shah positions Supermemory as having "forgetfulness embedded into the system" and a "specialized model" for ingestion. This is vendor self-promotion — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)

### Inferences
- The architecture separates the read path from the write path. The online agent is stateless with respect to memory: it reads an injected snapshot and greps on demand. A single offline "dreaming" job owns every mutation and commits the results to git. This maps directly onto Jarvis's candidate design of "offline consolidation + stateless background work", and onto the existing heartbeat daily-log task, generalized into a nightly reconcile.
- The reconcile operations — promote to workstream, compress, generalize, redact secrets, dated correction — are a concrete checklist a consolidation prompt could follow.
- Daily latency is a real cost. A fact stated today is not in durable memory until about 24 hours later. Instinct bridges this gap with the compaction recap of the live conversation. Jarvis would need the same bridge, either today's raw thread or a recap.
- The "come back tomorrow" limit suggests the compaction recap is bounded and only reset by the nightly ingest. That hard UX failure is worth avoiding, for example by triggering consolidation on demand at a threshold.

### Gaps
- The reply thread on X (108 replies) was not readable, so I found no replies from Instinct staff.
- How the one-pager and profile are generated, and whether one-pager and profile are the same artifact, is unclear. The blog groups ~4,250 tokens as "profile" while also listing a separate one-pager.
- No information on the model or prompt used for ingestion.

## Other sources on Instinct's memory and context

### Takeaway
I found no official technical description of memory. The only first-party signal is founder rhetoric about learning preferences and heavy background compute. Everything architectural traces back to Shah's post, and the coverage I found (RuntimeWire, youmind, eesel) restates or mirrors it. I found no independent second reverse-engineering and no HN or Reddit thread.

### Cited Findings
- [official-ish, founder interview, woshipm 2026-09-30, Chinese, translated by fetch tool] Shinn says the agent "possesses its own operational lifecycle" and that "Over 90% of … workload and token throughput happens invisibly in backend" systems. Proactive agents need "2–3 orders of magnitude more compute than passive tools". He describes batch and asynchronous use of idle compute and a proprietary hybrid inference pipeline "approaching Opus 5-level" performance. Memory is described only as preference learning: "The more information you provide, the smoother future experiences become". Users take 2–3 weeks to trust it with sensitive data — [woshipm](https://www.woshipm.com/ai/6472662.html)
- [3P] RuntimeWire: "the harder engineering sits around the files: deciding what deserves to be saved, reconciling contradictions, building a compact user profile and feeding the right material back into the agent" — [RuntimeWire](https://runtimewire.com/article/supermemory-reverse-engineers-instinct-memory-git-markdown-grep)

### Inferences
- The founder's "90% of tokens in the background" and batch-processing remarks are consistent with Shah's offline daily-ingestion finding, but do not confirm it.

### Gaps
- No podcast, talk, docs page or engineering post from Instinct on memory was found. Searches for Shinn plus memory returned nothing relevant.
- No HN or Reddit discussion surfaced in search.

## What is distinctive or transferable versus ChatGPT, Claude, OpenClaw, hermes and Letta?

### Takeaway
What sets Instinct apart, per the RE: (1) the agent has **read-only** memory and a single offline writer, instead of agent-written memory tools; (2) a **typed markdown tree with aliases, dated facts and wikilinks, plus git history**, searched lexically; (3) a **structured, injected profile**, including an "autonomy calibration" section on when to act versus ask; (4) a daily **reconcile** pass that also compresses, generalizes and redacts. Its known weak spots are multi-hop recall, implicit personalization, no automatic decay, recall misses on synonyms and misspellings, roughly 24-hour write latency, and a hard compaction ceiling.

### Cited Findings
- [RE] All the strengths and failure modes listed in the verdict table above, plus the synonym and misspelling misses ("Italian noodles I enjoy" and "pazta" returned no hits) — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)
- [RE] The blog's only explicit comparison is with Supermemory. It has no direct comparison with ChatGPT, Claude, OpenClaw or Letta — [blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)

### Inferences (my comparison, not sourced to the post)
- Compared with Letta/MemGPT-style self-editing core memory, and with OpenClaw-style or current Jarvis designs where the agent writes MEMORY/USER files through tools, Instinct removes all writes from the hot path. The benefits are cheaper turns, less mid-conversation memory churn, and auditable diffs. The costs are latency and no immediate "remember this".
- Its design is closest to a markdown and lexical-search pattern (OpenClaw/Claude Code style files plus grep), but with a curated typed schema and aliases that make up for having no embeddings.
- What transfers to Jarvis: a small capped injected profile with an "act vs ask" section; a typed directory schema with frontmatter aliases and dated bullets; git-backed memory for audit and rollback; a nightly consolidation job that runs as a heartbeat task with its own operation checklist; a recap layer bridging same-day facts; and alias generation at write time to make grep recall reliable.
- What to avoid copying: the hard "come back tomorrow" cap, and depending on aliases alone, since a cheap fuzzy or embedding fallback would fix the synonym misses.

### Gaps
- I found no independent evaluation or user reports on Instinct's memory quality beyond Shah's single-tester verdict.
