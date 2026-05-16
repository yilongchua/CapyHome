# Phase 2 — Two-Step CoT Ingest

**Status**: Not started  
**Effort**: Medium (prompt and pipeline refactor within existing ingest flow)  
**Prerequisite**: Phase 1 (so improved pages feed a single unified retrieval path)

---

## Problem

The current vault ingest in `backend/src/control_plane/vault_learning.py` is much closer to:

`read source -> derive summary/claims/backlinks -> write compiled artifacts`

than to a true knowledge compilation step.

For general web research this is acceptable, but it leaves quality on the table:

- shallow entity extraction
- weak concept stabilization across adjacent sources
- missed contradictions or tensions with existing syntheses
- weak cross-referencing to existing pages
- generic page structure because comprehension and writing are collapsed into one step

This matters because `knowledge_vault` is increasingly intended to be a **shared research cache**. If ingestion is shallow, future answers from that cache are shallow too.

---

## What llm_wiki Does (Two-Step CoT)

llm_wiki (`src/lib/ingest.ts`) splits ingest into two sequential LLM calls:

**Call 1 — Analysis** (read-only, no file writes):
```
Input:  raw source content + existing wiki index.md
Output: structured analysis containing —
  - Key entities
  - Key concepts
  - Core arguments / findings / takeaways
  - Connections to existing wiki pages
  - Contradictions and tensions with existing knowledge
  - Structural recommendations (what page types to create)
  - Review items needing human judgment
  - Web search queries to fill knowledge gaps
```

**Call 2 — Generation** (uses Call 1 analysis as context, writes files):
```
Input:  Call 1 analysis + source content + wiki schema
Output: ---FILE: wiki/path/file.md--- blocks containing —
  - Source summary page with YAML frontmatter
  - Entity pages
  - Concept pages
  - Updated index.md and log.md entries
  - Optional review queue items
```

The separation matters because the model gets a full structural understanding of the source **before** it writes anything. Call 2 becomes a generation task, not a comprehension-plus-generation task.

Quality improves especially for:

- multi-entity sources
- long-form reports and papers
- sources that revise or challenge earlier findings
- pages where useful entities/concepts are not obvious from a quick summary
- any ingest where future retrieval quality matters more than raw speed

---

## What to Build in Capybara

The existing `VaultLearningManager.ingest()` remains the ingest entrypoint. The main change is the LLM call structure inside that flow.

### Current flow

```
ingest(url, content) →
  trust_score(content) →
  if trust_ok:
    write_raw_package(01_raw/) →
    derive compiled source page / syntheses / references →
    write_compiled_pages(02_compiled/) →
    update_manifest()
```

### Target flow

```
ingest(url, content) →
  trust_score(content) →
  if trust_ok:
    write_raw_package(01_raw/) →
    existing_index = read_compiled_index()
    analysis = llm_call(            # Call 1: analysis only
        prompt_analyze,
        source=content,
        index=existing_index,
        schema=vault_schema,
    ) →
    pages = llm_call(               # Call 2: generation using analysis
        prompt_generate,
        analysis=analysis,
        source=content,
        schema=vault_schema,
    ) →
    parse_file_blocks(pages) →
    write_compiled_pages(02_compiled/) →
    update_manifest()
    enqueue_review_items(analysis.review_items)
    enqueue_gap_queries(analysis.gap_queries)
```

---

## Prompt: Call 1 — Analysis

```python
ANALYZE_PROMPT = """
You are analyzing a source document to prepare structured ingest into a knowledge vault.

## Existing vault index
{index_md}

## Vault schema (page types and rules)
{schema_md}

## Source document
URL: {url}
Content:
{content}

Produce a JSON analysis with these fields:
{{
  "source_type": "web_article | report | paper | notes | transcript | dataset | other",
  "key_entities": [
    {{"name": str, "type": "person|org|product|tool|dataset|company|place|other", "description": str}}
  ],
  "key_concepts": [
    {{"name": str, "description": str, "importance": "high|medium|low"}}
  ],
  "core_arguments": [str],
  "existing_page_connections": [
    {{"page": str, "relationship": str}}
  ],
  "contradictions": [
    {{"existing_page": str, "conflict": str}}
  ],
  "page_structure_recommendation": [
    {{"path": str, "type": "entity|concept|source|synthesis", "rationale": str}}
  ],
  "review_items": [
    {{"item": str, "action": "create_page|deep_research|skip"}}
  ],
  "gap_queries": [str]
}}

Return only valid JSON.
"""
```

This should stay **domain-agnostic** at the infrastructure level. Domain-specific structure can still come from:

- source content itself
- vault schema docs
- upstream MCP or scraper normalization

but the ingest phase should not be hard-coded around legal or vessel examples anymore.

---

## Prompt: Call 2 — Generation

```python
GENERATE_PROMPT = """
You are generating wiki pages from a source document. Use the analysis below to produce well-structured, cross-referenced pages.

## Analysis from step 1
{analysis_json}

## Source document
{content}

## Vault schema
{schema_md}

Write each file using this exact format:
---FILE: wiki/relative/path/file.md---
(file content here)
---END---

Requirements:
- Every page must include YAML frontmatter with: type, title, sources (list of source IDs), tags, created_at
- Use [[wikilinks]] for all cross-references to other pages where appropriate
- Source summary page path: wiki/sources/{source_slug}.md
- Entity pages: wiki/entities/{slug}.md
- Concept pages: wiki/concepts/{slug}.md
- Update wiki/index.md to add new pages (append only, preserve existing entries)
- Append to wiki/log.md with date, source URL, and pages created
- Do not include the source document content verbatim — synthesize and structure it
- Prefer durable, reusable pages over one-off summaries when the source introduces concepts or entities that are likely to recur
- Preserve provenance clearly enough that future answers can cite where the information came from
- Favor structure that improves future retrieval, not just present readability
"""
```

---

## Review Item Surfacing

Call 1's `review_items` should be wired into the existing action-item mechanism:

```python
for item in analysis.review_items:
    self._manifest.add_action_item(
        kind="review",
        priority="medium",
        title=item["item"],
        detail=f"Source: {url}",
        suggested_action=item["action"],
    )
```

These should surface through the existing vault action-items UI and `/api/vault/action-items`.

---

## Gap Query Discovery

Call 1's `gap_queries` should feed the discovery queue:

```python
for query in analysis.gap_queries:
    self._manifest.add_candidate(
        url=None,
        source_query=query,
        source_tool="cot_ingest_gap",
        objective_id=current_objective_id,
    )
```

This means ingest can identify follow-up searches automatically when the vault is thin or inconsistent on a topic.

---

## Incremental Rollout

The two-step approach adds one LLM call per ingest. Cost and latency implications:

- **Cost**: roughly 2× per ingest
- **Latency**: sequential calls add ~5-15s per source depending on model and source length
- **Mitigations**: gate on source class, source length, or ingestion channel at first if needed

Add a config toggle:

```yaml
vault:
  ingest:
    cot_enabled: true
    cot_for_sources: all          # all | long_form_only | autoresearch_only | manual_only
    cot_min_chars: 2500
```

---

## Files to Modify

| File | Change |
|---|---|
| `backend/src/control_plane/vault_learning.py` | Split ingest into `_analyze_source()` + `_generate_pages()` and wire analysis output into review/gap queues |
| `backend/src/control_plane/prompts/` | Add `ANALYZE_PROMPT` and `GENERATE_PROMPT` |
| `backend/src/config/` | Add `vault.ingest.cot_enabled`, `vault.ingest.cot_for_sources`, and `vault.ingest.cot_min_chars` |

No frontend changes are required. No API changes are required.

---

## Quality Validation

Before rolling out broadly:

1. Take 10 existing compiled source pages from `02_compiled/sources/`
2. Re-ingest their corresponding raw sources using two-step CoT
3. Compare entity coverage
4. Compare concept coverage and cross-references
5. Compare review items and gap queries
6. Check whether the resulting pages would improve future retrieval quality

Manual review is still the gate. The real question is not "is the JSON valid?" but "does this make the vault more useful as a reusable knowledge cache?"
