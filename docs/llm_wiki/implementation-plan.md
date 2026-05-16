# Knowledge Vault Implementation Plan

**Date**: 2026-05-16  
**Status**: Ready for implementation  
**Scope**: Improve `knowledge_vault` into a single shared local cached research system for repeated web search, autoresearch reuse, browser clipping, and explicit user saves

---

## Goal

Turn `backend/.capybara-home/knowledge_vault/` into Capybara's default durable research cache so that:

- repeated or adjacent searches reuse prior work
- chat, API, and frontend all retrieve from the same ranking system
- higher-quality compiled pages improve future answer quality
- useful new information can enter the vault from more than one path
- the vault becomes more inspectable and maintainable over time

This plan assumes:

- one shared `knowledge_vault`
- one primary retrieval implementation
- metadata-based provenance
- no multi-vault routing layer in this phase

---

## Current State

### What already exists

- vault storage and manifest: `backend/.capybara-home/knowledge_vault/`
- vault manager: `backend/src/control_plane/vault_learning.py`
- chat/agent tool search: `backend/src/community/knowledge_vault_search/`
- API/UI search route: `backend/src/gateway/routers/vault.py`
- control-plane integration: `backend/src/control_plane/service.py`
- web search queue append path: `backend/src/community/web_search/tools.py`

### Main architectural issue

The vault currently has two different retrieval implementations:

1. tool path: `VaultSearcher`
2. API/UI path: `VaultLearningManager.search()`

This is the first thing to fix, because every later phase depends on retrieval behaving consistently.

---

## Implementation Order

1. Phase 1A: unify retrieval
2. Phase 1B: add vector search and hybrid ranking
3. Phase 2: replace shallow ingest with two-step compile
4. Phase 3A: add browser clipper ingest path
5. Phase 3B: add explicit "save to vault" path from chat/agent output
6. Phase 4: add graph endpoint and graph UI

---

## Phase 1A — Unify Retrieval

### Objective

Create one shared search service used by:

- `query_knowledge_vault`
- `/api/vault/search`
- vault frontend views

### Deliverables

- `UnifiedVaultSearchService`
- one result schema
- one lexical retrieval implementation
- one ranking path for chat and UI

### Files

- new: `backend/src/control_plane/services/unified_vault_search.py`
- update: `backend/src/community/knowledge_vault_search/tool.py`
- update: `backend/src/control_plane/service.py`
- refactor: `backend/src/community/knowledge_vault_search/search.py`
- optional cleanup: `backend/src/control_plane/vault_learning.py`

### Design decisions

- keep separate interfaces, but one shared backend implementation
- keep lexical retrieval first so behavior is stable before vector work
- search compiled vault content, not only manifest snippets
- keep manifest for state/metrics, not primary ranking

### Exit criteria

- same query from chat tool and `/api/vault/search` returns the same top results
- same query from UI and agent uses the same result schema
- existing behavior remains functional without vector search enabled

### Testing

- add unit tests for shared ranking behavior
- add regression test proving tool path and API path match on the same fixture set

---

## Phase 1B — Add Vector Search And Hybrid Ranking

### Objective

Improve recall by adding semantic retrieval and RRF fusion on top of unified lexical retrieval.

### Deliverables

- chunking strategy for compiled pages
- embedded local vector index
- hybrid lexical + vector ranking
- background first-build process

### Files

- new: `backend/src/community/knowledge_vault_search/vector_index.py`
- update: `backend/src/control_plane/services/unified_vault_search.py`
- update: `backend/src/control_plane/vault_learning.py`
- update: config models under `backend/src/config/`
- update: dependency config in backend package metadata

### Technical tasks

- choose LanceDB
- chunk `02_compiled/` pages at heading boundaries or fixed windows
- implement configurable embedding provider abstraction
- upsert vectors after ingest
- fuse lexical and vector results with RRF

### Exit criteria

- vector search can be disabled with no behavior regression
- hybrid search improves recall on curated test cases
- first-build indexing runs in background and reports status

### Testing

- unit tests for chunking
- unit tests for RRF behavior
- integration test for background index build on sample vault

---

## Phase 2 — Two-Step CoT Ingest

### Objective

Improve the quality of compiled vault pages by separating:

1. source understanding
2. page generation

### Deliverables

- analysis prompt
- generation prompt
- two-step ingest flow
- review-item surfacing
- gap-query surfacing

### Files

- update: `backend/src/control_plane/vault_learning.py`
- new: `backend/src/control_plane/prompts/vault_analyze.py`
- new: `backend/src/control_plane/prompts/vault_generate.py`
- update: vault-related config under `backend/src/config/`

### Technical tasks

- add `_analyze_source()`
- add `_generate_pages()`
- parse structured analysis payload
- wire `review_items` into action items
- wire `gap_queries` into follow-up discovery
- keep source-type handling generic at infrastructure level

### Rollout strategy

- start behind config flag
- optionally gate by minimum source length
- validate on existing raw source set before enabling widely

### Exit criteria

- compiled pages show better entity/concept coverage
- cross-references improve measurably on test fixtures
- follow-up review/gap artifacts are actionable

### Testing

- fixture re-ingest comparison on existing vault sources
- prompt-contract tests for analysis and generation outputs
- regression tests for parser behavior on malformed model output

---

## Phase 3A — Browser Clipper

### Objective

Let users send useful web pages directly into `knowledge_vault` from the browser.

### Deliverables

- `/api/vault/clip` endpoint
- Chrome extension using Readability.js + Turndown.js
- queue integration into existing ingest path
- clip metrics visible in vault UI

### Files

- update: `backend/src/gateway/routers/vault.py`
- update: `backend/src/control_plane/vault_learning.py`
- update: vault frontend page under `frontend/src/app/workspace/vault/page.tsx`
- new extension directory if built inside this repo, or separate extension repo if preferred

### Technical tasks

- accept clipped markdown payload
- enqueue as search-result-style ingest item
- apply same trust scoring and provenance fields
- add CORS allowance for extension origin
- expose clip counts in vault status

### Exit criteria

- clipped page enters queue successfully
- clipped page can be ingested without manual file placement
- clip-origin items are visible in status and manifest

### Testing

- API test for `/api/vault/clip`
- trust-scoring tests on clipped payloads
- manual browser test with a real article page

---

## Phase 3B — Explicit Save From Chat

### Objective

Allow the user or agent to explicitly persist a useful answer into the vault.

### Deliverables

- vault save action or endpoint
- answer-to-vault transformation path
- provenance metadata for saved outputs

### Candidate interfaces

- chat action: "save this to vault"
- internal tool callable by agent
- API route such as `POST /api/vault/save`

### Data to store

- user request
- final answer
- cited sources if available
- tags/topic metadata
- source channel: `chat_save`

### Exit criteria

- user can intentionally persist a good answer
- saved outputs are searchable through the same unified retrieval layer

---

## Phase 4 — Knowledge Graph

### Objective

Add a structural view over the vault so users can see:

- clusters
- isolated pages
- bridge pages
- sparse knowledge areas

### Deliverables

- `/api/vault/graph`
- graph insights computation
- vault graph UI

### Files

- update: `backend/src/gateway/routers/vault.py`
- add graph service under `backend/src/control_plane/services/`
- update: `frontend/src/app/workspace/vault/page.tsx`
- add: `frontend/src/components/workspace/vault/VaultGraph.tsx`
- add related graph UI components

### Technical tasks

- parse wikilinks from compiled content
- derive source-overlap edges
- run community detection
- compute isolated/bridge/sparse insights
- connect graph insights back to autoresearch objective creation

### Exit criteria

- graph loads for current vault size without blocking UI
- insights are understandable and actionable
- graph reflects the same compiled knowledge used by retrieval

### Testing

- backend graph-shape tests on sample vault fixtures
- frontend render smoke tests
- manual performance check on current vault size

---

## Cross-Cutting Work

### Provenance model

Standardize these fields across ingest paths:

- `source_tool`
- `source_type`
- `import_channel`
- `trust_score`
- `topic_tags`
- `content_hash`

### Observability

Add status visibility for:

- vector index build progress
- clip count
- saved-from-chat count
- ingest mode usage
- queue depth by source channel

### Backward compatibility

- keep current `/api/vault/search` contract
- keep current `query_knowledge_vault` tool name
- keep vault directory structure stable unless migration is required

---

## Suggested Milestones

### Milestone 1

Unify retrieval with no vector search yet.

Success signal:

- chat and UI agree on search results

### Milestone 2

Add vector search and RRF.

Success signal:

- semantic recall improves on curated repeated-search cases

### Milestone 3

Ship two-step ingest behind config flag.

Success signal:

- newly compiled pages are visibly more structured and reusable

### Milestone 4

Ship browser clipper and explicit save path.

Success signal:

- useful information can enter the vault from browser and chat without manual file operations

### Milestone 5

Ship graph view.

Success signal:

- users can spot gaps and trigger more research from graph insights

---

## Recommended First Sprint

If implementation starts now, the first sprint should only include:

1. create `UnifiedVaultSearchService`
2. route tool path and API path to it
3. add tests proving result parity
4. add config scaffold for vector search, but do not enable it yet

This gives the cleanest foundation for every later phase.

---

## Related Docs

- [Overview](README.md)
- [Phase 1](phase-1-vector-search.md)
- [Phase 2](phase-2-cot-ingest.md)
- [Phase 3](phase-3-web-clipper.md)
- [Phase 4](phase-4-knowledge-graph.md)
- [Vault Topology](vault-topology.md)
