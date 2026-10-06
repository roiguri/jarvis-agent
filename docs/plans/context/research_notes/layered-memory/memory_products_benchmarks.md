# Memory-layer products and benchmark evidence for agent long-term memory (as of 2026-10)

Scope: Mem0, Zep/Graphiti, Supermemory, MemOS, Hindsight, Mastra Observational Memory, Letta; the LongMemEval / LoCoMo / BEAM benchmarks; critiques of vendor claims. Consumer: Jarvis, a single-owner assistant (a few chat turns/day, ~10 ticks/day, Gemini Flash, SQLite, markdown memory).

Labelling convention: **[vendor]** = number published by the system's own authors/company; **[independent]** = third party; **[competitor]** = published by a rival vendor about another system. Dates are publication dates of the source.

---

## Q1. Mem0: pipeline, published results, costs, OSS vs hosted

### Takeaway
Mem0's 2025 paper describes an LLM extract→compare→ADD/UPDATE/DELETE/NOOP pipeline over a vector store (plus an optional Neo4j graph variant), and in its own paper it scores *below* a plain full-context baseline on LoCoMo while being ~10x cheaper and faster. By April 2026 Mem0 itself abandoned in-place UPDATE/DELETE for an ADD-only, accumulate-and-rank design with hybrid (semantic + BM25 + entity) retrieval. Its 2026 headline numbers are self-reported, and the one independent test found the OSS version scoring far lower.

### Cited Findings
- **Extraction phase (2025 paper):** the extraction prompt is the conversation summary S plus the last m=10 messages plus the new message pair. An async module refreshes the summary. [vendor] — [Mem0 paper, arXiv 2504.19413 (Apr 2025)](https://arxiv.org/html/2504.19413)
- **Update phase:** for each candidate fact, the top s=10 semantically similar existing memories are retrieved. The LLM then chooses, via function calling, one of ADD (no equivalent exists), UPDATE (augment with complementary info), DELETE (contradicted) or NOOP. [vendor] — [arXiv 2504.19413](https://arxiv.org/html/2504.19413)
- **Mem0g (graph variant):** uses Neo4j. Models used: GPT-4o-mini for inference, OpenAI text-embedding-3-small for embeddings. [vendor] — [arXiv 2504.19413](https://arxiv.org/html/2504.19413)
- **LoCoMo LLM-as-Judge (J) per category, from the paper's Table 1** [vendor]:
  - Single-hop: Mem0 67.13, Mem0g 65.71, best baseline Zep 61.70
  - Multi-hop: Mem0 51.15, Mem0g 47.19, LangMem 47.92
  - Temporal: Mem0g 58.13, Mem0 55.51, A-Mem 49.91
  - Open-domain: Zep 76.60 (best), Mem0g 75.71

  Source: [arXiv 2504.19413](https://arxiv.org/html/2504.19413)
- **Headline claims in the abstract:** 26% relative J improvement over OpenAI memory, 91% lower p95 latency and >90% token savings vs full-context. Mem0g is ~2% higher overall than base Mem0. [vendor] — [arXiv 2504.19413 abstract](https://arxiv.org/abs/2504.19413)
- **Latency and tokens (Table 2)** [vendor]:
  - p95 latency: Mem0 search 0.200 s, Mem0 total 1.440 s, Mem0g search 0.657 s, full-context total 17.117 s.
  - Memory footprint per conversation: Mem0 ~7k tokens, Mem0g ~14k, Zep "600k+", raw conversation ~26k.

  Source: [arXiv 2504.19413](https://arxiv.org/html/2504.19413)
- **Overall scores, as quoted by Zep:** Mem0's best configuration scores 68.44% (Mem0g) and base Mem0 65.99%. Mem0's own full-context baseline scored ~73%, i.e. higher than Mem0. [competitor, quoting Mem0's paper] — [Zep blog, 6 May 2025](https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/)
- **April 2026 algorithm change:**
  - Extraction is now a single pass with **ADD-only** operations ("no UPDATE or DELETE steps, allowing memories to accumulate").
  - Agent-confirmed facts are weighted like user-stated ones.
  - Entities are extracted and linked across memories.
  - Retrieval is multi-signal: semantic + BM25 + entity match, scored in parallel.

  [vendor] — [Mem0 blog "AI memory benchmarks in 2026"](https://mem0.ai/blog/ai-memory-benchmarks-in-2026)
- **2026 self-reported scores:** LoCoMo 92.5%, LongMemEval 94.4%, BEAM-1M 64.1%, BEAM-10M 48.6%. Cost is 6.7k–7.0k tokens/query at p50 latency 0.88–1.09 s. Mem0 claims "3-4x lower token cost at competitive accuracy" vs full-context (25k+ tokens/query). [vendor] — [Mem0 blog 2026](https://mem0.ai/blog/ai-memory-benchmarks-in-2026)
- **Independent test (Aug 2026):** Mem0 OSS scored 32–49% (R@5) on LongMemEval_S through its production retrieval pipeline, against Mem0's claimed ~93%. The author maintains a competing product (Awareness) but disclosed this and published reproducible code. [independent-ish, competitor-affiliated] — [dev.to, Everest An, 7 Aug 2026](https://dev.to/everest_an/-i-benchmarked-ai-agent-memory-in-2026-and-the-numbers-tell-a-different-story-than-the-marketing-2ae4)
- The same author argues that vendor features producing the best scores often sit behind the paid or cloud tier ("Vendors tune for their own harness, on their own data, with their own retrieval stack"). — [dev.to, 7 Aug 2026](https://dev.to/everest_an/-i-benchmarked-ai-agent-memory-in-2026-and-the-numbers-tell-a-different-story-than-the-marketing-2ae4)

### Inferences
- The ADD/UPDATE/DELETE/NOOP design costs extra LLM calls per turn: one to extract, then a retrieval plus one decision per fact. Its own paper never beat the full-context baseline on accuracy.
- Mem0's 2026 move to ADD-only plus hybrid retrieval suggests the vendor found destructive in-place updates harmful. It is a step toward "keep everything, rank well at read time", which is closer to raw-log retrieval.
- For Jarvis volumes, Mem0's per-turn extraction cost is small in absolute terms. But the accuracy case over "transcript + good retrieval" is not established by independent evidence.

### Gaps
- No independent, dated per-operation dollar cost for Mem0 hosted vs OSS was found.
- The detailed feature differences between OSS and hosted (e.g. whether the 2026 algorithm ships in OSS) were not confirmed from primary docs.
- The full-context J number (~73%) comes via Zep's quotation of the Mem0 paper. I did not directly confirm it in the Table 1 extraction.

---

## Q2. Zep / Graphiti: temporal knowledge graph, bi-temporal edges, invalidation, results, infra

### Takeaway
Zep stores raw episodes plus an LLM-extracted entity/fact graph with bi-temporal edges. Contradicted facts are invalidated (timestamped) rather than deleted. Retrieval is hybrid: cosine + BM25 + graph BFS, then reranked. It beats full-context on LongMemEval_S with GPT-4o (71.2% vs 60.2%), but it needs a graph DB (Neo4j/FalkorDB/Neptune) and an LLM with structured output. Its LoCoMo numbers are mutually disputed with Mem0.

### Cited Findings
- **Three subgraphs:**
  - Episode subgraph: raw messages, a "non-lossy" record.
  - Semantic entity subgraph: extracted entities and facts as edges.
  - Community subgraph: label-propagation clusters with summaries.

  [vendor] — [Zep paper, arXiv 2501.13956 (Jan 2025)](https://arxiv.org/html/2501.13956)
- **Bi-temporal edges:** each edge carries t'_created / t'_expired (transaction time) and t_valid / t_invalid (when the fact was true in the world). [vendor] — [arXiv 2501.13956](https://arxiv.org/html/2501.13956)
- **Invalidation:** when a new edge contradicts an existing edge with an overlapping validity range, the old edge's t_invalid is set to the new edge's t_valid. History is preserved. [vendor] — [arXiv 2501.13956](https://arxiv.org/html/2501.13956)
- **Retrieval:** three searches — cosine (embeddings), Okapi BM25 (Neo4j Lucene) and breadth-first graph search. Results are reranked by RRF, MMR, episode-mention frequency, node distance or a cross-encoder. Entity extraction uses n=4 messages of context. The graph is queried with predefined Cypher, not LLM-generated queries. Models: BGE-m3 embeddings/reranker and gpt-4o-mini for graph construction. [vendor] — [arXiv 2501.13956](https://arxiv.org/html/2501.13956)
- **DMR results:** Zep 94.8% vs MemGPT 93.4% vs full-conversation 94.4% (gpt-4-turbo). With gpt-4o-mini, Zep 98.2% vs full-conversation 98.0%. DMR is therefore near-saturated by full context. [vendor] — [arXiv 2501.13956](https://arxiv.org/html/2501.13956)
- **LongMemEval_S results** [vendor] — [arXiv 2501.13956](https://arxiv.org/html/2501.13956):
  - gpt-4o-mini: full-context 55.4% vs Zep 63.8%.
  - gpt-4o: full-context 60.2% vs Zep 71.2%.
  - Latency: 31.3 s → 3.20 s (mini) and 28.9 s → 2.58 s (4o).
  - Zep's average context is 1.6k tokens.
- **Per-category, gpt-4o, full-context → Zep** [vendor] — [arXiv 2501.13956](https://arxiv.org/html/2501.13956):
  - single-session-preference 20.0 → 56.7
  - temporal-reasoning 45.1 → 62.4
  - multi-session 44.3 → 57.9
  - single-session-assistant **dropped** 94.6 → 80.4. Extraction loses what the assistant itself said.
- **Zep's critique of Mem0's evaluation of Zep:** Mem0 assigned the user role to both speakers, appended timestamps to message text instead of using `created_at`, and ran searches sequentially, inflating latency. Corrected, Zep scores **75.14% ±0.17** on LoCoMo vs Mem0's best of 68.44%. [competitor/vendor] — [Zep blog, 6 May 2025](https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/)
- **Later claim:** Mem0's 2026 comparison table lists Zep at 94.7% on LoCoMo (claimed) and calls it "Disputed; independent testing shows lower results". [competitor] — [Mem0 blog 2026](https://mem0.ai/blog/ai-memory-benchmarks-in-2026)
- **Independent Zep number:** 63.8% on LongMemEval_S, which matches Zep's own gpt-4o-mini number. [competitor-affiliated independent] — [dev.to, 7 Aug 2026](https://dev.to/everest_an/-i-benchmarked-ai-agent-memory-in-2026-and-the-numbers-tell-a-different-story-than-the-marketing-2ae4)
- **Graphiti infra:**
  - Backends: Neo4j 5.26+ (default), FalkorDB 1.1.2+ (with an embedded "FalkorDB Lite" for Python 3.12+) and Amazon Neptune (needs OpenSearch Serverless for full-text). Kuzu (embedded) is deprecated because upstream is unmaintained.
  - Requires an LLM with structured output; OpenAI, Anthropic and Gemini are supported.
  - Concurrency is capped by SEMAPHORE_LIMIT (default 10). It ships an MCP server. ~31.5k stars.

  — [Graphiti GitHub (fetched 2026-10)](https://github.com/getzep/graphiti)

### Inferences
- Bi-temporal invalidation is the most principled published answer to "the user changed their mind": the old fact stays queryable with an end date. Its benefit shows in Zep's knowledge-update and temporal categories.
- Even so, the infrastructure is heavy for Jarvis: a graph DB, several LLM calls per episode, and an embedding model. There is no SQLite backend, and the embedded Kuzu option is deprecated. FalkorDB Lite is the only lightweight path.
- The idea (validity intervals on facts, superseded not deleted) can be copied cheaply into SQLite or markdown without Graphiti.

### Gaps
- No published per-episode LLM call count or dollar cost for Graphiti ingestion was found.
- I could not verify the provenance of the "Zep 94.7% LoCoMo" claim.

---

## Q3. Benchmarks: what LongMemEval, LoCoMo and newer benchmarks measure; full-context vs memory; best results

### Takeaway
LongMemEval_S (~115k tokens per question) is the most informative public benchmark. Full context with GPT-4o scores only ~60–64% on it, while retrieval-based and compression-based systems reach 82–95%. LoCoMo is short (16–26k tokens), has a 6.4% wrong answer key and a lenient judge, and has no knowledge-update category, so it barely separates systems from full context. Newer benchmarks (BEAM at 1M/10M tokens) exist because both are now considered saturated or "fits in context".

### Cited Findings
- **LongMemEval abilities and size:**
  - Five abilities: information extraction, multi-session reasoning, temporal reasoning, knowledge updates and abstention.
  - 500 curated questions. LongMemEval_S is ~115k tokens per question; LongMemEval_M is ~500 sessions (~1.5M tokens).

  — [LongMemEval paper, arXiv 2410.10813 (Oct 2024)](https://arxiv.org/html/2410.10813)
- **Long context falls short of oracle:** long-context LLMs show a 30–60% decline vs oracle retrieval, and GPT-4o shows a ~30% accuracy drop. Commercial assistants (ChatGPT, Coze) drop 37% and 64% vs offline reading. — [arXiv 2410.10813](https://arxiv.org/html/2410.10813)
- **LoCoMo structure:** 10 multi-session conversations, each ~600 turns and ~16k tokens, with 1,986 QA pairs (1,540 in the main eval). — [search summary of LoCoMo description via MemOS/related papers](https://arxiv.org/pdf/2605.30771) (secondary)
- **Zep on LoCoMo's flaws:** conversations of 16k–26k tokens fit in modern context, there are no knowledge-update questions, category 5 has no ground truth, and some speaker attributions are wrong. Full-context (~73%) beats Mem0 (~68%) on it. [competitor] — [Zep blog, 6 May 2025](https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/)
- **LoCoMo audit:**
  - 99 errors in 1,540 questions (6.4%), so the maximum achievable score is ~93.6%.
  - The gpt-4o-mini judge accepted 62.81% of intentionally wrong but topically related answers.
  - The audit also calls LongMemEval_S a context-window test rather than a memory test.
  - It lists six requirements for meaningful evals: corpus exceeds the context window, current judge, adversarially validated judge, realistic ingestion, standardized pipeline and verified ground truth.

  [independent, from a vendor (Penfield)] — [Penfield Substack, 8 Apr 2026](https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer)
- **Full-context baselines on LongMemEval_S:**
  - GPT-4o 60.2% and gpt-4o-mini 55.4%. — [Zep paper](https://arxiv.org/html/2501.13956)
  - GPT-4o 63.8% and o3 76.0%, against oracle GPT-4o 82.4%. — [Emergence AI blog](https://www.emergence.ai/blog/sota-on-longmemeval-with-rag) (the page shows a date of "June 18, 2024", which predates LongMemEval's Oct 2024 release and is likely wrong; treat it as 2025)
  - OSS-20B 39.0%. — [Hindsight paper, arXiv 2512.12818 (Dec 2025)](https://arxiv.org/html/2512.12818)
- **Top reported LongMemEval QA accuracies (all vendor-reported):**

  | System | Score | Source |
  |---|---|---|
  | Mastra Observational Memory | 94.87% (gpt-5-mini), 93.27% (gemini-3-pro), 84.23% (gpt-4o) | [Mastra research](https://mastra.ai/research/observational-memory) |
  | Mem0 | 94.4% | [Mem0 blog 2026](https://mem0.ai/blog/ai-memory-benchmarks-in-2026) |
  | Hindsight | 91.4% (Gemini-3), 89.0% (OSS-120B), 83.6% (OSS-20B) | [arXiv 2512.12818](https://arxiv.org/html/2512.12818) |
  | Supermemory | 81.6% (GPT-4o), 84.6% (GPT-5) | [Hindsight Table 3](https://arxiv.org/html/2512.12818); 85.2% per [Supermemory/aggregators](https://supermemory.ai/blog/supermemory-vs-cognee/) |
  | MemOS | 77.80 LongMemEval, 75.80 LoCoMo | [MemOS eval results (HF)](https://huggingface.co/datasets/MemTensor/MemOS_eval_result/blob/refs%2Fpr%2F1/README.md) |
  | ByteRover | 92.8% (S variant) | [Mem0 blog 2026](https://mem0.ai/blog/ai-memory-benchmarks-in-2026) |
  | Agent Zero | 95.60% | search summary citing [arXiv 2608.29606](https://arxiv.org/pdf/2608.29606) (not verified) |
- **LoCoMo, other reported numbers:** Hindsight 83.18% / 85.67% / 89.61% (OSS-20B / OSS-120B / Gemini-3); Memobase 75.78%; Zep 75.14%; Mem0g 68.44% [vendor tables] — [arXiv 2512.12818](https://arxiv.org/html/2512.12818). Mem0 2026: 92.5% [vendor].
- **BEAM:** 100 conversations of up to 10M tokens each, 2,000 probing questions and 10 capabilities. It is built to stay meaningful where context windows could hold everything. Mem0 self-reports 64.1% (1M) and 48.6% (10M). — [Mem0 blog 2026](https://mem0.ai/blog/ai-memory-benchmarks-in-2026)
- **Metric confusion is widespread:**
  - Supermemory's research page reports **Recall@20** (97% overall) in the same table as Zep's and full-context's **QA accuracy** (71.2% / 60.2%), which is not a like-for-like comparison. [vendor] — [Supermemory research, May 2026](https://supermemory.ai/research/)
  - MemPalace's claimed 96.6% was retrieval-only R@5. An independent QA run got 82.6%. Its "raw mode" ran no MemPalace code at all (just ChromaDB + MiniLM). Its LoCoMo 100% used top_k=50 on 32-session conversations, which retrieves essentially everything. — [MemPalace discussion #747, 9 Apr 2026](https://github.com/MemPalace/mempalace/discussions/747)

### Inferences
- The only fair comparisons are within one paper, using the same reader model and judge. Within those comparisons, structured memory or good retrieval beats full context on LongMemEval_S by roughly 10–25 points with GPT-4o-class readers. On LoCoMo the gap is near zero or negative.
- Reader-model quality explains much of the 2026 jump: the same system gains 8–11 points moving from gpt-4o to gpt-5-mini or Gemini-3. Absolute scores across papers are not comparable.

### Gaps
- No standardized, independent leaderboard covering all products with the same reader/judge was found.
- No published results specifically for Gemini Flash as the reader were found.
- The BEAM paper itself was not fetched, and the Cognee results (Cognee promotes its own benchmark page) were not verified.

---

## Q4. Raw-transcript retrieval vs extracted-fact memory; simple approaches for low-volume settings

### Takeaway
The evidence leans toward keeping raw turns and retrieving them well (hybrid BM25 + embeddings, turn-level keys, time-aware queries), optionally with extracted facts as *extra index keys*. Replacing raw text with extracted facts loses information. Two simple designs reach near-state-of-the-art: plain RAG over sessions/turns, and Mastra's no-retrieval "observation log" (a compressed running log kept in context). A filesystem agent with grep plus search beats Mem0 on LoCoMo.

### Cited Findings
- **LongMemEval design ablations** — [arXiv 2410.10813](https://arxiv.org/html/2410.10813):
  - Decomposing sessions into rounds (turns) as the stored value helps.
  - "Further compression into facts harmed overall results except for multi-session questions."
  - Using extracted user facts as *additional keys* (key expansion) improves recall@k by 9.4% and final accuracy by 5.4% on average.
  - Time-aware query expansion improves temporal retrieval by 11.3% (with rounds).
  - Chain-of-Note plus structured JSON reading adds up to 10 points.
- **Emergence AI (simple RAG):** retrieve whole sessions ranked by NDCG of matching turns after a cross-encoder rerank, then answer with CoT on GPT-4o.
  - Scores 82.4% on LongMemEval_S, equal to oracle GPT-4o and above Zep's 71.2% and full-context GPT-4o's 63.8%.
  - A "fast" two-call variant (extract relevant facts, then answer) scores 79.0% at 3.59 s per question.
  - The authors conclude RAG has largely "solved" LongMemEval_S.

  [vendor-of-a-different-product, but the design is simple] — [Emergence AI blog](https://www.emergence.ai/blog/sota-on-longmemeval-with-rag)
- **Hybrid retrieval over raw turns:** BM25-only reaches 86.2% R@5 on LongMemEval_S; BM25 + vector hybrid reaches 95.2% R@5 and 98.6% R@10 (retrieval-only, not QA). The authors read this as "verbatim text with good retrieval outperforms LLM extraction". [agentmemory maintainer] — [MemPalace discussion #747](https://github.com/MemPalace/mempalace/discussions/747)
- **LoCoMo metric bias:** LoCoMo's public F1 is substring-based, which by construction rewards lexical matches and favours BM25. — [search summary of the lexical-dense fusion literature, e.g. arXiv 2606.04194](https://arxiv.org/pdf/2606.04194) (secondary; not fetched)
- **Letta filesystem agent:**
  - Conversation stored as files and accessed with grep, semantic `search_files` and open/close tools, on GPT-4o-mini.
  - Scores **74.0%** on LoCoMo vs Mem0g's reported 68.5%.
  - Conclusion: memory quality depends more on the agent's context management and tool use than on specialised memory infrastructure, since agents are post-trained heavily on filesystem tools.

  [vendor (Letta)] — [Letta blog, 12 Aug 2025](https://www.letta.com/blog/benchmarking-ai-agent-memory)
- **Mastra Observational Memory:**
  - An Observer agent compresses messages into dated observations (3–6x compression, up to 40x for tool-heavy work), and a Reflector periodically condenses them further.
  - There is no retrieval: the log sits in context, so the window is stable and fully prompt-cacheable.
  - Scores 84.23% with gpt-4o, which beats oracle by 1.8 points, Supermemory's 81.6% and Hindsight's 83.6% (OSS-20B). With gpt-5-mini it scores 94.87%. Multi-session stays the hardest category at 87.2%.

  [vendor] — [Mastra research](https://mastra.ai/research/observational-memory)
- **Zep's own extraction drops single-session-assistant** accuracy from 94.6% to 80.4% vs full context, showing extraction loses detail. — [arXiv 2501.13956](https://arxiv.org/html/2501.13956)
- **Hindsight** uses a heavier design: four memory networks (world, experience, opinion with confidence, observation) and four-way retrieval (vector, BM25, graph spreading activation, temporal filter) fused by RRF plus a cross-encoder. It reaches 91.4% on LongMemEval with Gemini-3. Authors are from Vectorize.io, The Washington Post and Virginia Tech. [vendor-affiliated] — [arXiv 2512.12818](https://arxiv.org/html/2512.12818)

### Inferences
- **For Jarvis's volume:** a few turns/day is about 1k–3k turns/year, which is tiny. SQLite FTS5 (BM25) over raw turns, plus optional sqlite-vec embeddings fused by RRF, covers the retrieval side with zero new infrastructure. Turn-level granularity and keeping timestamps on every row matter most.
- The Mastra pattern maps naturally onto Jarvis's existing markdown and daily logs: a heartbeat-driven "observer/reflector" that maintains a dated, compressed observation file injected every turn. It is cache-friendly with Gemini, needs no vector DB, and has the strongest gpt-4o-class result of any simple design.
- Extraction is best used to *add* index keys or summaries alongside the raw log, never to replace it, as both the LongMemEval ablation and Mem0's 2026 ADD-only pivot suggest.
- Overkill for Jarvis: Neo4j/FalkorDB graph stores (Zep/Graphiti, Mem0g), community detection, cross-encoder rerankers, and multi-network designs like Hindsight. Their gains are demonstrated at 100k–10M-token scales that Jarvis will take years to reach.
- Full context alone is not enough even at ~115k tokens with GPT-4o (60–64%). Retrieval or compression helps well before context limits are reached, because long contexts dilute attention.

### Gaps
- No study was found that specifically evaluates single-user, low-volume (<100k tokens/year) assistants. All benchmarks use synthetic or companion-chat histories.
- No independent QA-accuracy (not recall) numbers for a pure SQLite FTS5 baseline on LongMemEval were found.
- No published numbers for Gemini Flash as the reader or observer.

---

## Q5. Knowledge updates / contradiction ("user changed their mind")

### Takeaway
Three approaches appear:
- **Destructive update/delete** (Mem0 2025): LLM-decided UPDATE/DELETE. Mem0 itself later dropped it.
- **Non-destructive temporal invalidation** (Zep/Graphiti bi-temporal edges, Supermemory-style versioning).
- **Keep everything dated and let the reader resolve it at answer time** (Mastra observation dates, raw-turn retrieval with timestamps, Mem0 2026 ADD-only).

Systems that keep dated history and surface recency score highest on the knowledge-update category.

### Cited Findings
- Mem0 2025 DELETE removes "contradicted information" by LLM decision. [vendor] — [arXiv 2504.19413](https://arxiv.org/html/2504.19413)
- Mem0's April 2026 pipeline is ADD-only, "no UPDATE or DELETE steps, allowing memories to accumulate", with ranking left to retrieval. [vendor] — [Mem0 blog 2026](https://mem0.ai/blog/ai-memory-benchmarks-in-2026)
- Zep invalidates rather than deletes: t_invalid on the old edge is set to t_valid of the new one, preserving history. [vendor] — [arXiv 2501.13956](https://arxiv.org/html/2501.13956); [Graphiti README](https://github.com/getzep/graphiti)
- **Knowledge-update category scores:**
  - Mastra OM: 96.2% (gpt-5-mini), 94.9% (gemini-3-pro), 85.9% (gpt-4o). — [Mastra](https://mastra.ai/research/observational-memory)
  - Hindsight OSS-120B: 92.3%. — [arXiv 2512.12818](https://arxiv.org/html/2512.12818)
  - Full-context GPT-4o: 78.2%; Zep: 83.3%. — [Supermemory research table](https://supermemory.ai/research/) (Supermemory's own 100% there is Recall@20, not accuracy)
  - All of the above are vendor-reported.
- Mastra attaches three dates to each observation (creation, referenced and relative) "for temporal reasoning accuracy". [vendor] — [Mastra](https://mastra.ai/research/observational-memory)
- LoCoMo has no knowledge-update questions, so LoCoMo scores say nothing about this ability. — [Zep blog, May 2025](https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/)

### Inferences
- For Jarvis, timestamp every stored fact or observation and never silently overwrite. Either append a superseding dated entry (mirroring Zep's valid-from / valid-to in a SQLite column or a markdown "(superseded YYYY-MM-DD)" marker), or keep raw dated turns and let Gemini resolve by recency. This gets most of the benefit of bi-temporal graphs at near-zero cost.
- Durable profile files (USER.md) are effectively "current state" views. Pairing them with a dated change log preserves "what did I used to prefer" queries, which LongMemEval's knowledge-update category tests.

### Gaps
- No controlled head-to-head isolating destructive-update vs invalidation vs append-only on the same reader model was found.
- The 2026 versioning model of Supermemory (relations such as updates/extends) was not confirmed from primary docs.
