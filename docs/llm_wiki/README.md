# LLM Wiki Integration — Overview

**Decision date**: 2026-05-16  
**Status**: Planning / phased implementation

---

## Context

This document captures the evaluation of [llm_wiki](https://github.com/nashsu/llm_wiki) (a Karpathy-pattern desktop knowledge base) as a potential data source layer for Capybara-Home's agent system, and the resulting integration roadmap.

### The Problem

Users and agents often repeat the same research within a short time window:

- asking adjacent versions of the same web search
- revisiting the same sources across multiple chat turns
- re-synthesizing information that was already collected yesterday
- manually rediscovering pages the system has already seen

That makes web research slower, more expensive, and noisier than it needs to be. The goal is to improve `knowledge_vault` into a **local cached research system** that can absorb high-value information from web search, autoresearch, browser clipping, and explicit user saves, then answer future requests from that cache first when appropriate.

Login-gated or domain-specific acquisition paths may still exist, but they are no longer the primary topology driver for this roadmap. Those can be handled through MCP or dedicated acquisition workflows and then feed the same vault.

### Why llm_wiki Was Evaluated

llm_wiki implements the Karpathy pattern: raw documents → LLM-compiled structured wiki → persistent, interlinked knowledge pages. It ships a Chrome web clipper, vector search, a knowledge graph, and Obsidian compatibility out of the box. The question was whether to adopt it as an external tool or lift its patterns into Capybara's existing vault.

---

## What Capybara Already Has

The existing `knowledge_vault` (introduced before this evaluation) is a production-ready research knowledge base:

| Component | Location |
|---|---|
| Vault data | `backend/.capybara-home/knowledge_vault/` |
| Core manager | `backend/src/control_plane/vault_learning.py` |
| Control plane agent | `backend/src/control_plane/agents/knowledge_vault_agent.py` |
| BM25 search tool | `backend/src/community/knowledge_vault_search/` |
| API routes | `backend/src/gateway/routers/vault.py` |
| Frontend tab | `frontend/src/app/workspace/vault/page.tsx` |
| Skill | `skills/knowledge-vault/SKILL.md` |

**Current data volume**: 1,216 compiled sources, 282 syntheses, 5 active autoresearch objectives (maritime-focused).

**Current pipeline**: discover → ingest → compile → lint → synthesize → sufficiency. Autoresearch objectives drive scheduled web search cycles. Trust scoring (0.55 threshold), loop guards (24h cooldown, SHA1 fingerprint), sufficiency evaluation (78% coverage threshold).

**Important implementation note**: Capybara currently has two different search paths over the same vault:

- agent/tool path: `VaultSearcher` BM25 over compiled markdown
- API/frontend path: `VaultLearningManager.search()` over the manifest cache

One of the first roadmap corrections is to unify those into a single retrieval service so chat, API, and UI all see the same ranked results.

---

## What llm_wiki Offers That Capybara Lacks

| Feature | Capybara vault | llm_wiki |
|---|---|---|
| Keyword search | BM25 | BM25 |
| Semantic/vector search | **No** | LanceDB + RRF fusion |
| Ingest quality | Single-step LLM | **Two-step CoT** (analyze → generate) |
| Web clipper | **No** | Chrome extension → port 19827 |
| Knowledge graph | **No** | sigma.js + Louvain community detection |
| Obsidian compatibility | Partial | Native (auto-generates `.obsidian/`) |
| Autoresearch objectives | Yes | No |
| Scheduled pipelines | Yes | No |
| Trust scoring | Yes | No |
| Loop guards | Yes | No |
| Sufficiency evaluation | Yes | No |
| Deployable as API/Docker | Yes | No (Tauri desktop app only) |

### Why Direct Integration Is Not Feasible

llm_wiki is a **Tauri desktop app** — its search, vector store, and ingest pipeline live inside a Rust binary. The only network surface is a `127.0.0.1:19827` HTTP daemon used exclusively by the Chrome extension. There is no REST API, no Docker Compose, no headless mode, and no containerisation path.

Running it as a service inside Capybara's stack would require rewriting the Rust backend as a Python service — which is equivalent to reimplementing the relevant patterns directly. That is the correct approach.

---

## Integration Strategy

**Don't integrate llm_wiki as a product — lift its patterns into Capybara's existing vault.**

Capybara contributes what llm_wiki entirely lacks: scheduling, trust scoring, loop detection, multi-user API, sufficiency evaluation, and the control plane agent. llm_wiki contributes patterns: two-step CoT ingest, RRF search fusion, knowledge graph structure.

### Four Phases

| Phase | What | Primary gap addressed | Doc |
|---|---|---|---|
| 1 | Unified retrieval + vector search + RRF | One search surface, better recall | [phase-1-vector-search.md](phase-1-vector-search.md) |
| 2 | Two-step CoT ingest | Better source understanding and compiled page quality | [phase-2-cot-ingest.md](phase-2-cot-ingest.md) |
| 3 | Chrome web clipper + explicit save pathways | Capture useful information into the cache | [phase-3-web-clipper.md](phase-3-web-clipper.md) |
| 4 | Knowledge graph visualization | Gap visibility, cluster view | [phase-4-knowledge-graph.md](phase-4-knowledge-graph.md) |

**Phase ordering rationale**: Phase 1 should first unify retrieval, because there is little value in improving only one of the two current search paths. Once retrieval is unified, Phase 2 improves the quality of what gets indexed, Phase 3 increases the number of useful things that can enter the cache, and Phase 4 becomes much more informative because the underlying graph is built from better structured content.

For the concrete execution sequence, file targets, milestones, and rollout guidance, see [implementation-plan.md](implementation-plan.md).

### Vault Topology

The roadmap now assumes a **single shared `knowledge_vault/`** instead of multiple domain-specific vault directories. Provenance should be expressed through metadata and ingest policy, not separate vault roots. See [vault-topology.md](vault-topology.md).

---

## Reference: llm_wiki Architecture Summary

For those needing to understand llm_wiki's internals when implementing patterns:

**Ingest pipeline** (`src/lib/ingest.ts` + `src-tauri/src/commands/`):
1. File preprocessing via Rust (PDF → pdfium-render, DOCX/XLSX → calamine)
2. LLM Call 1: structured analysis (entities, arguments, contradictions, structural recommendations)
3. Optional: image extraction → vision LLM captions
4. LLM Call 2: generate wiki files using Call 1 analysis as context; output is `---FILE: wiki/path/file.md---` blocks
5. Atomic file writes; vector embedding upsert into LanceDB `wiki_chunks_v2`

**Search pipeline** (`src/lib/search.ts`):
1. Tokenized BM25-like scoring (CJK bigram, title bonus +200/+50, phrase match +20 per occurrence)
2. Vector search via LanceDB cosine similarity (optional, embedding provider-agnostic)
3. Reciprocal Rank Fusion: `score(p) = Σ 1/(K + rank_L(p))` where K=60

**Vault structure**:
```
project_root/
  wiki/              # LLM-generated pages (entities/, concepts/, sources/)
  raw/               # Immutable user sources
  .llm-wiki/         # App state: ingest-queue.json, lancedb/, project.json
  purpose.md         # Why this wiki exists (directional intent)
  schema.md          # Structural rules and page types
```

**Chrome extension → app bridge**: Extension POSTs to `localhost:19827/clip`. Rust clip_server writes to in-memory queue. Frontend polls `/clips/pending`, adds to ingest queue. Uses `Readability.js` + `Turndown.js` for HTML → Markdown.
