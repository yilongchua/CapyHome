# Knowledge Vault — Functional Analysis & Improvement Roadmap

_Analysis date: 2026-06-18 · Status updated: 2026-06-18_

---

## Implementation Status (2026-06-18)

| Item | Decision | State |
|------|----------|-------|
| **K-1** Entity/concept pages aggregate source summaries | Implemented — source summaries written into the page `## Overview` **body**. This is the part that actually moved retrieval (the searcher reads page bodies from disk) | ✅ Done |
| **S-1** Search index text built from summaries | Implemented **but a no-op for retrieval.** It enriched the manifest `search_index` sidecar (`_index_document`), which nothing reads for search — its only consumer is a `len()` stats counter (`_search_and_summary.py`). Actual search (`VaultSearcher`) BM25s `title + tags + body` read straight from the `.md` files. K-1's body change is what helped; this didn't. | ⚠️ Ineffective |
| **I-2** Two LLM calls per document | Shipped as **merge + derive-in-code**: one `ANALYZE_SOURCE_PROMPT` call returns analysis + `evidence_markdown`; summary/claims/backlinks derived in `_generate_source_sections` with no model call | ✅ Done |
| **I-5** `gap_queries` extracted but discarded | **Reversed.** Rather than surface them, `gap_queries` were removed entirely — they fed no search path and the autoresearch loop has its own taxonomy. Backlog writer + `/api/vault/gap-queries` endpoint deleted | ✅ Removed |
| **open_questions** (part of I-4) | Removed from ingest, source pages, and `_pages.py`; synthesis-scoring/lint coupling neutralized (the `question_closure` term was dropped from `_coverage_progress` and its 0.15 weight redistributed) | ✅ Removed |
| **S-4** Bump search limit (5→10) | Implemented — default `limit` 5→10 and clamp ceiling 20→30 in `knowledge_vault_search/tool.py` | ✅ Done |
| **S-5** Tool framing | Implemented — `query_knowledge_vault` docstring rephrased to equal-priority with web_search | ✅ Done |
| **K-2** Synthesis pages don't synthesize | Not started | ⬜ Open |
| **K-4 / S-2** depth_score + RRF/freshness | Not started | ⬜ Open |
| **S-3** Cross-result dedup | Not started | ⬜ Open |

> **Two corrections (2026-06-18):** (1) The original I-5 plan (surface gap_queries) and I-2 plan (one 13-field JSON) were both superseded. (2) Retrieval runs off the compiled `.md` **bodies on disk**, not the manifest `search_index` sidecar — so any "fix the index text" work (S-1; a mooted `evidence_markdown` indexing idea) has no effect. To change what search sees, change the page **body** or the **ranking** in `unified_vault_search.py`. See §6/§7.

---

## System Overview

The vault stores four types of pages that the agent retrieves via `query_knowledge_vault`:

| Page type | Location | Created by |
|-----------|----------|-----------|
| **Source** | `02_compiled/sources/` | One per ingested URL — summary, claims, evidence |
| **Entity** | `02_compiled/entities/` | One per proper noun extracted from sources |
| **Concept** | `02_compiled/concepts/` | One per recurring idea/theme |
| **Synthesis** | `02_compiled/syntheses/` | One per topic, aggregating cross-source evidence |

Content enters via three paths:
- **Passive** — websearch results queued automatically by `WebSearchIngestionMiddleware`
- **Active** — browser clipper (`POST /api/vault/clip`)
- **Explicit** — agent `save_to_knowledge_vault` tool or `POST /api/vault/save`

Websearch is the dominant path. The middleware captures crawl4ai-extracted content (`extracted_content` from each search result) and passes it to the prefetch step, so vault content gets crawl4ai quality rather than raw `httpx`+`strip_html`.

---

## 1. Ingestion Pipeline

### What works well

- Queue-based passive ingestion is architecturally sound
- Crawl4ai content is correctly used (not re-fetched from raw HTML)
- Parallel prefetch / serial manifest commit prevents lock contention
- SHA256 content hashing prevents redundant re-ingestion
- Trust scoring (`min_trust_score: 0.55`) filters junk domains

### Issues

**I-1 — Content truncated before analysis at 20K chars**

[`_ingest.py`](../backend/src/control_plane/vault_learning/_ingest.py) truncates to `max_content_chars` before passing to `_analyze_source`. Long-form content (research papers, detailed technical guides) loses conclusions, methodology, and references — the most citable parts.

**I-2 — Two sequential LLM calls per document**

[`_analysis.py:152`](../backend/src/control_plane/vault_learning/_analysis.py#L152) and [`_analysis.py:196`](../backend/src/control_plane/vault_learning/_analysis.py#L196) make separate calls: `_analyze_source` then `_generate_source_sections`. The second call receives the first call's JSON output plus the full content again. One structured prompt can produce both in a single call, halving cost and latency per item.

**I-3 — No document type awareness**

Every document gets the same extraction prompt regardless of content type. A recipe, a research paper, a forum post, and a news article all produce a generic `summary + key_claims`. Type-specific prompts would extract far more useful information:
- Recipe → ingredients, steps, substitutions
- Paper → methodology, findings, limitations
- Forum → consensus position, dissenting views
- Documentation → API/usage patterns, version constraints

**I-4 — Heuristic fallback produces noisy entities**

When CoT is disabled or content < 1200 chars ([`_analysis.py:149`](../backend/src/control_plane/vault_learning/_analysis.py#L149)), entity extraction falls back to regex-matching capitalized phrases from the title. The stopwords list is only ~60 words. This produces false entity pages from title-case adjectives and generic nouns.

The heuristic `open_questions` are static templates:
```python
# _analysis.py:99-100
[f"What evidence is still missing around {topic or title}?",
 f"Which facts should be re-verified from {url}?"]
```
These identical questions appear on every heuristic-mode source and pollute synthesis pages.

**I-5 — `gap_queries` are extracted and discarded**

During analysis the LLM extracts `gap_queries` — web search strings that would fill knowledge gaps. These are written to source page frontmatter and then never used. They are not surfaced to the user, not fed to the autoresearch loop, not returned by the vault API. This is the most immediately wasted signal in the pipeline.

---

## 2. Knowledge Retained — Quality

### What works well

- Source pages have summary + claims + evidence — genuinely useful for retrieval
- Entity/concept graph enables navigation across topics
- Synthesis pages provide a gathering point for cross-source evidence

### Issues

**K-1 — Entity and concept pages are stubs**

[`_pages.py:90-97`](../backend/src/control_plane/vault_learning/_pages.py#L90-L97) generates:

```markdown
## Overview
Maintained entity page derived from ingested sources.

## Evidence
- Supports source `source-id-1`
- Supports source `source-id-2`
```

That is the entire content. When the agent queries the vault for "Singapore" and gets the entity page back, the snippet returned is literally "Maintained entity page derived from ingested sources." This is zero-information retrieval. The meaningful content about Singapore exists in the source pages that reference it, but the entity page does not aggregate or summarize any of it.

**K-2 — Synthesis pages accumulate, they don't synthesize**

[`_pages.py:144-146`](../backend/src/control_plane/vault_learning/_pages.py#L144-L146) appends a timestamped evidence line per source:

```
- `2026-06-15T12:00:00Z` Some Article Title: first 280 chars of excerpt
```

After 10 sources, a synthesis page is a chronological list of 10 dated excerpts. There is no: "Three sources agree that X. One source contradicts by saying Y. The evidence suggests Z." The synthesis concept is implemented as *evidence accumulation*, not synthesis. Users and the agent get a fragmented list rather than a distilled insight.

**K-3 — Contradiction detection is a string search**

[`_lint.py:756`](../backend/src/control_plane/vault_learning/_lint.py#L756):
```python
if "contradiction" in body.lower():
    contradictions.append(path.name)
```
Two sources can completely disagree on a fact and neither will use the word "contradiction." Semantic contradictions are never flagged.

**K-4 — No content quality / depth scoring**

A 200-word marketing fluff page and a 10,000-word technical research report both produce identically-structured source pages. There is no depth or quality signal stored anywhere. Search ranking cannot favour deep sources over shallow ones. The agent has no way to know whether vault coverage is shallow or authoritative on a topic.

---

## 3. Search and Retrieval

### Issues

**S-1 — Search snippets are boilerplate for entity/concept pages**

The search index stores `text[:500]` as the snippet ([`_pages.py:48`](../backend/src/control_plane/vault_learning/_pages.py#L48)). For entity and concept pages, the first 500 chars is the boilerplate overview described in K-1. The agent receives this as a search result and learns nothing useful. The actual relevant text — what source pages say about this entity — is not included in the entity page's indexed text at all.

**S-2 — No freshness factor in ranking**

BM25 + cosine similarity are purely content-based. A six-month-old page ranks identically to a page ingested today if they contain the same terms. For time-sensitive topics (prices, schedules, events, legislation) this surfaces stale knowledge ahead of fresh knowledge.

**S-3 — Search results triple-count the same content**

A single source about Amsterdam produces: a source page, an entity page for "Amsterdam", and a synthesis page for "netherlands-travel" — all three referencing the same underlying content. A search for "Amsterdam travel" can return all three, giving the agent three low-information results instead of one high-information one. There is no cross-result deduplication.

**S-4 — Default search limit of 5 is too low for compound queries**

A question like "what do I know about healthcare costs for expats in Amsterdam?" spans sources, entities, and synthesis pages across health/finance/Netherlands topics. 5 results is often insufficient. The agent must make multiple vault calls or miss relevant knowledge.

**S-5 — Tool framing under-signals passive content**

The `query_knowledge_vault` description says *"Prefer this over web_search when looking for information the user has deliberately collected."* Most vault content is passively ingested from websearch, not deliberately saved. This framing may cause the agent to skip the vault when it should be checking it alongside web search. The description should reflect equal priority: "Check this alongside web_search for topics the user has recently researched or asked about — most vault content is collected automatically from prior searches, not manually saved."

---

## 4. Autoresearch Loop

### Issues

**A-1 — Jaccard dedup misses semantic duplicates**

The autoresearch dedup uses token-Jaccard overlap. "What are the best neighborhoods in Amsterdam for tourists?" and "Top areas to stay in Amsterdam as a visitor?" share almost no tokens and both pass the dedup filter, generating redundant research. Embedding-based semantic similarity would catch these.

**A-2 — Researcher novelty is not confirmed back to the reflector**

The `vault-source-researcher` subagent writes findings to the vault but returns only a summary to the reflector LLM. The reflector generates follow-up questions without knowing whether the research produced genuinely new information or found nothing useful. Questions can recur across rounds because the stop criterion is novelty-of-*questions*, not novelty-of-*answers*.

---

## 5. Summary of Issues

| ID | Area | Impact | Effort |
|----|------|--------|--------|
| K-1 | Entity/concept pages are stubs | High | Low |
| S-1 | Boilerplate snippets in search results | High | Low |
| I-5 | `gap_queries` extracted but discarded | High | Low |
| K-2 | Synthesis pages don't synthesize | High | Medium |
| I-2 | Two LLM calls per document (cost) | Medium | Low |
| S-4 | Default limit 5 too low | Medium | Low |
| S-5 | Tool framing under-signals passive vault | Medium | Low |
| I-3 | No document type awareness | Medium | Medium |
| S-2 | No freshness factor in ranking | Medium | Medium |
| S-3 | Triple-counting same content in results | Medium | Medium |
| K-4 | No content quality/depth scoring | Medium | Medium |
| I-1 | Content truncated before analysis | Low | Low |
| I-4 | Heuristic fallback produces noisy entities | Low | Low |
| K-3 | Contradiction detection is string match | Low | High |
| A-1 | Jaccard dedup misses semantic duplicates | Low | Medium |
| A-2 | Researcher novelty not confirmed | Low | Medium |

---

## 6. Improvement Roadmap

### Phase 1 — Fix zero-value cases (low effort, high impact)

These are the changes that turn currently useless pages into genuinely useful ones.

**1.1 Entity/concept pages should aggregate source summaries**

When writing an entity/concept page, include one-line summaries pulled from the source pages that reference it. The data is already available in the manifest — no new LLM call needed. The `_update_reference_page` function receives `source_title` but not the source summary; wiring it through would give entities a real overview section instead of boilerplate.

```python
# In _update_reference_page: replace the boilerplate overview with
# "## Overview\n\n" + "\n".join(f"- {summary}" for summary in source_summaries[:5])
```

**1.2 Fix search index text for entity/concept pages**

In `_index_document`, the `text` field for entity/concept pages should include aggregated summaries from referenced source pages, not just the boilerplate body. This fixes snippet quality for all entity/concept search results without touching the page files.

**1.3 Surface gap_queries as actionable output**

After each ingest batch, write all `gap_queries` from newly ingested sources into `03_ops/tasks/backlog/gap-queries-{date}.md`. Wire the vault router to return the top gap queries via a new `GET /api/vault/gap-queries` endpoint. The autoresearch loop can consume these as seed questions, and the UI can surface them as suggested research.

**1.4 Bump default search limit from 5 to 10**

One-line change in [`tool.py:32`](../backend/src/community/knowledge_vault_search/tool.py#L32). Increase cap from 20 to 30.

**1.5 Update tool description framing**

Change `query_knowledge_vault` docstring to reflect equal priority with web_search: "Check this alongside web_search for topics the user has recently researched or asked about — most vault content is collected automatically from prior searches, not manually saved." This removes the implication that vault is only for deliberately saved content.

---

### Phase 2 — Improve knowledge quality (medium effort)

**2.1 Synthesis pages: trigger LLM synthesis at 3+ source refs**

When `_update_synthesis_page` adds a source and the synthesis page now has 3 or more live source refs, run a focused LLM call (reuse `_call_vault_model_json`) that reads the accumulated evidence lines and produces a `## Synthesis` section:
- 2-4 sentence consensus statement
- Key tension or open question
- Confidence level (high/medium/low)

Store this section in the page above the `## Latest Supporting Evidence` lines. Update on every new source addition (the LLM call is cheap for this size).

**2.2 Merge analysis + page generation into one LLM call**

Combine `ANALYZE_SOURCE_PROMPT` and `GENERATE_PAGE_PROMPT` into a single structured JSON prompt that returns all fields in one call. This halves LLM cost and reduces ingest latency per document by the round-trip time of one model call (typically 2-10s).

**2.3 Add `depth_score` to source pages**

Compute a simple heuristic: `min(10, len(raw_text) // 500)`. Store it as `depth_score` in the source page frontmatter and manifest. Use it as a small RRF boost factor: `rrf_score *= (1 + 0.05 * depth_score)`. Deep sources rank slightly higher than shallow ones for the same query match.

**2.4 Improve heuristic `open_questions`**

Replace the two static templates with questions derived from the extracted `key_claims`. For each claim, generate "Is [claim] still true as of [year]?" or "What evidence contradicts [entity]?" This requires no LLM call — just structured string templates over the claims list.

---

### Phase 3 — Better search (medium effort)

**3.1 Freshness decay in RRF scoring**

In `UnifiedVaultSearchService`, after computing the fused RRF score, multiply by a freshness factor:
```python
days_old = (now - page_updated_at).days
freshness_factor = max(0.5, 0.98 ** days_old)  # ~50% floor, 2% decay/day
fused_score *= freshness_factor
```
This keeps old knowledge accessible while prioritising fresh pages for the same query.

**3.2 Cross-result deduplication**

After ranking, check if the top results contain both a source page and its entity/synthesis pages. If the source page is in the top-N, drop its derived entity/concept pages from results (they contain less information). Replace with the next-ranked page. This increases result diversity.

**3.3 Document type detection at ingest**

Detect content type before LLM analysis and pass it as context:
- Simple heuristics first: JSON-LD `@type`, presence of "Ingredients"/"Method" headers, "Abstract"/"References" sections
- Add `content_type` to `ANALYZE_SOURCE_PROMPT` and tune extraction focus per type
- Store `content_type` in source page frontmatter

Even coarse type detection (article / recipe / paper / forum / docs) would meaningfully improve claim extraction quality.

---

### Phase 4 — Autoresearch improvements (lower priority)

**4.1 Semantic dedup for autoresearch**

Replace Jaccard dedup with cosine similarity over question embeddings, using the vault's own vector index infrastructure. Threshold: similarity > 0.85 = duplicate. This catches semantic reformulations that Jaccard misses.

**4.2 Novelty signal from researcher back to reflector**

Have the `vault-source-researcher` return a structured `novelty_assessment` alongside its summary: `{new_sources_found: int, new_entities: list, contradictions_found: list}`. The reflector prompt should include this when generating follow-up questions, so it can stop generating follow-ups for topics where the researcher found nothing new.

---

## 7. Quick-Win Implementation Order

Done (2026-06-18):

- [x] **Fix entity/concept page body** (K-1) — source summaries written into the page `## Overview` body (the searcher reads bodies, so this moved retrieval)
- [x] **Merge analysis+generation into one call** (I-2) — `_analysis.py` now does one LLM call + code-derived sections
- [x] **Remove `gap_queries` + `open_questions`** (supersedes I-5) — dropped from the LLM contract and all downstream consumers
- [x] **Bump search limit default** (S-4) — default `limit` 5→10, clamp ceiling 20→30 in `knowledge_vault_search/tool.py`
- [x] **Update tool framing** (S-5) — `query_knowledge_vault` docstring rephrased to equal-priority with web_search
- [⚠️] **Fix search index text for entities/concepts** (S-1) — implemented but ineffective; the manifest `search_index` it touched is not read by retrieval (see Implementation Status)

Still open, in suggested order:

1. **Synthesis LLM trigger at 3+ refs** (K-2) — new LLM call in `_update_synthesis_page`; writes a `## Synthesis` section into the page **body**
2. **Add depth_score + RRF boost** (K-4 + S-2) — frontmatter + ranking tweak in `unified_vault_search.py`
3. **Freshness decay** (S-2) — score multiplier in `unified_vault_search.py`
4. **Cross-result dedup** (S-3) — post-ranking filter in search service

### What can still be improved (next, ranked by value/effort)

1. **K-2 synthesis (medium) — now the top item.** The single biggest *quality* gap: synthesis pages still just accumulate dated excerpts, and the searcher returns that raw list as the excerpt. The merge freed an LLM call's worth of budget per source — reinvest part of it here (one cheap call at 3+ refs, written into the page **body** so search picks it up).
2. **K-4 depth_score (low).** `min(10, len(raw_text)//500)` in source frontmatter, read into `CompiledVaultPage`, applied as `score *= (1 + 0.05*depth_score)` in **both** scoring exits of `unified_vault_search.py` (lexical-only early return and the RRF-fused path). Zero extra LLM cost; helps deep sources outrank keyword-dense fluff.
3. **I-1 truncation (low).** 20K-char cap still drops conclusions/references from long sources before analysis — now cheaper to revisit since there's only one call consuming the content.

> **Dropped:** the earlier "`evidence_markdown` is generated but not indexed" idea. It *is* indexed — it's written into the source page `## Evidence` body, which both BM25 and the vector index read. No action needed.

> **Architectural note for all future search work:** retrieval reads compiled `.md` **bodies from disk** (`VaultSearcher._load_pages` → BM25 on `title + tags + body`; `VaultVectorIndex` chunks the same bodies). The manifest `search_index` sidecar (`_index_document`) is **not** a retrieval input. To change what search finds, edit the page **body**; to change ranking, edit `unified_vault_search.py`.
