# Phase 1 — Vector Search + RRF Fusion

**Status**: Not started  
**Effort**: Medium-High (retrieval unification + Python vector DB)  
**Prerequisite**: None — additive to existing BM25, no pipeline changes

---

## Problem

Capybara does not currently have one vault retrieval system. It has two:

1. `backend/src/community/knowledge_vault_search/search.py`
   Used by the chat/agent tool path. This is BM25 over compiled markdown pages.

2. `VaultLearningManager.search()` in `backend/src/control_plane/vault_learning.py`
   Used by `/api/vault/search` and the frontend. This searches a manifest-backed cache with truncated text fields and simple term counting.

This creates two problems:

- **inconsistent answers**: chat and UI can surface different results for the same query
- **fragmented upgrades**: improving only one path does not improve the whole product

On top of that, neither path currently has semantic retrieval, so queries still need lexical overlap with indexed text.

---

## What llm_wiki Does

llm_wiki (`src/lib/search.ts`) implements a three-stage hybrid pipeline:

**Stage 1 — Tokenized BM25-like**
- CJK: char + bigram decomposition
- English: word split + stop word removal
- Scoring: filename exact match +200, phrase in title +50, phrase in content +20 (capped), per-token title +5, content +1

**Stage 2 — Vector search (LanceDB)**
- Query embedded via configured provider (OpenAI, Ollama, custom endpoint)
- `wiki_chunks_v2` table: one row per chunk (not per page), with `chunk_id`, `page_id`, `chunk_index`, `chunk_text`, `heading_path`, `vector`
- Cosine similarity ANN search returns top-K chunks
- Frontend aggregates chunk scores back to page-level

**Stage 3 — Reciprocal Rank Fusion**
```
score(page) = Σ  1 / (K + rank_L(page))
              L ∈ {bm25, vector}
              K = 60
```
A page ranked 1st in BM25 and 1st in vector gets the highest combined score. A page ranked top-5 in only one list still surfaces if it scores strongly there.

llm_wiki reports: BM25-only recall 58.2% → BM25+vector recall 71.4% on their eval set.

---

## What to Build in Capybara

### Target state

```
existing:
  - VaultSearcher (agent tool path)
  - VaultLearningManager.search() (API/UI path)

build:
  UnifiedVaultSearchService
    - lexical retrieval
    - optional vector retrieval
    - one ranking function
    - one result schema

add:
  VaultVectorIndex  — embeds wiki pages, stores in LanceDB or chromadb

fuse:
  HybridVaultSearcher / UnifiedVaultSearchService — RRF over lexical + vector results

surface:
  - query_knowledge_vault tool calls UnifiedVaultSearchService
  - /api/vault/search calls UnifiedVaultSearchService
  - frontend continues using /api/vault/search unchanged
```

### Implementation plan

#### 1. Choose a vector store

**LanceDB** (Python package, no server required):
```bash
uv add lancedb
```
Runs embedded — no separate process, stores in a local directory. This is the same choice llm_wiki makes in Rust, and there is a Python SDK.

Alternative: **chromadb** (also embedded, slightly simpler Python API).

Recommended: LanceDB, for consistency with llm_wiki's approach and future Rust portability if needed.

#### 2. Chunking strategy

Do not embed entire pages — chunk at heading boundaries or ~400-token windows with 50-token overlap:

```python
def chunk_wiki_page(path: str, content: str) -> list[dict]:
    # Split on ## headings; fall back to fixed-size windows if no headings
    chunks = []
    current_heading = "root"
    current_text = []
    for line in content.splitlines():
        if line.startswith("## "):
            if current_text:
                chunks.append({
                    "page_id": path,
                    "chunk_index": len(chunks),
                    "heading_path": current_heading,
                    "chunk_text": "\n".join(current_text),
                })
            current_heading = line.lstrip("# ").strip()
            current_text = []
        else:
            current_text.append(line)
    if current_text:
        chunks.append({...})
    return chunks
```

#### 3. Embedding provider

Make configurable in `config.yaml` under a new `vault.vector_search` block:

```yaml
vault:
  vector_search:
    enabled: false          # off by default, opt-in
    provider: openai        # openai | ollama | custom
    model: text-embedding-3-small
    endpoint: ~             # for custom / ollama
    api_key: $OPENAI_API_KEY
    chunk_size: 400         # tokens
    chunk_overlap: 50
```

Provider-agnostic embedding call — same pattern as the existing LLM factory in `backend/src/models/`:

```python
class VaultEmbeddingClient:
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

#### 4. Unify the two retrieval paths first

Introduce a shared service, for example:

```python
class UnifiedVaultSearchService:
    def search(self, query: str, limit: int = 10) -> dict:
        ...
```

Responsibilities:

- load searchable pages from one canonical source
- run lexical retrieval
- optionally run vector retrieval
- fuse/rank results
- return one shared result schema

Then wire both callers to it:

- `query_knowledge_vault_tool()` in `backend/src/community/knowledge_vault_search/tool.py`
- `ControlPlaneService.search_vault()` in `backend/src/control_plane/service.py`

This is the key architectural simplification. The API can remain separate from the tool, but the search implementation should not be.

#### 5. Index build and upsert

On first enable: walk `02_compiled/` markdown files, chunk, embed, upsert into LanceDB.

On each ingest: after `VaultLearningManager.ingest()` writes a compiled page, trigger re-embed for that page (delete old chunks by `page_id`, insert new chunks). This mirrors llm_wiki's upsert semantics:

```python
# delete all chunks for this page, then insert fresh
table.delete(f"page_id = '{page_id}'")
table.add(new_chunks_with_vectors)
```

#### 6. Hybrid retrieval

```python
class UnifiedVaultSearchService:
    def search(self, query: str, limit: int = 5) -> list[VaultSearchResult]:
        lexical_results = self._lexical.search(query, limit=limit * 2)
        if self._vector_index.enabled:
            vec_results  = self._vector_index.search(query, limit=limit * 2)
            return self._rrf(lexical_results, vec_results, k=60)[:limit]
        return lexical_results[:limit]

    def _rrf(self, *ranked_lists, k=60) -> list[VaultSearchResult]:
        scores = defaultdict(float)
        for ranked in ranked_lists:
            for rank, item in enumerate(ranked, start=1):
                scores[item.id] += 1.0 / (k + rank)
        return sorted(all_items, key=lambda x: scores[x.id], reverse=True)
```

#### 7. Files to create / modify

| File | Change |
|---|---|
| `backend/src/community/knowledge_vault_search/vector_index.py` | New — `VaultVectorIndex` class |
| `backend/src/community/knowledge_vault_search/search.py` | Refactor toward shared lexical retrieval primitives |
| `backend/src/community/knowledge_vault_search/tool.py` | Route to unified search service |
| `backend/src/control_plane/service.py` | Route `/api/vault/search` to unified search service |
| `backend/src/control_plane/vault_learning.py` | Stop treating `search()` as a separate ranking system; keep manifest as state, not primary search logic |
| `backend/src/control_plane/services/` | Add `UnifiedVaultSearchService` if you want a neutral shared home |
| `backend/src/config/` | Add `vault.vector_search` config block |
| `backend/src/control_plane/vault_learning.py` | Call `vector_index.upsert(page_id, content)` after each ingest |
| `backend/pyproject.toml` | Add `lancedb` dependency |

### /api/vault/search changes

No API contract change required. The endpoint signature can stay identical:
```
GET /api/vault/search?q=<query>&limit=<n>
```
The important change is internal: `/api/vault/search` and the chat tool should hit the same implementation.

---

## Index build on first enable

When the user enables `vault.vector_search` for the first time, the index is empty. A one-time build job should run on startup if the LanceDB directory doesn't exist:

```python
async def _maybe_build_vector_index(vault_manager: VaultLearningManager):
    if not vector_index.exists() and config.vault.vector_search.enabled:
        pages = vault_manager.list_compiled_pages()
        for page in pages:
            vector_index.upsert(page.id, page.content)
```

This runs in the background and does not block API startup. Progress can be surfaced via `/api/vault/status` under a new `vector_index` key.

---

## Testing

Write a fixture with 20 compiled pages from `02_compiled/sources/` and `02_compiled/syntheses/`. Queries should include:

1. Exact keyword match (should score high in BM25 and appear in hybrid)
2. Semantic-only match (zero token overlap — verifies vector search is contributing)
3. Ambiguous query (verifies RRF ranks the most relevant page first)

Test file location: `backend/tests/community/knowledge_vault_search/test_hybrid_search.py`

---

## What This Unlocks

Once retrieval is unified and vector search is added:

- the chat window and vault UI return the same top results
- repeated web research can be answered from cached vault pages more reliably
- semantically similar material can surface even when wording differs
- later phases only need to improve one retrieval stack, not two

This is why Phase 1 is not just "add vectors". It should be "make vault retrieval one system, then make that system better."
