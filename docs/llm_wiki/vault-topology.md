# Vault Topology — Single Shared Cached Vault

**Status**: Design / pre-implementation  
**Relates to**: All phases — topology defines where knowledge enters and how retrieval should stay unified

---

## Overview

The current direction is a **single shared `knowledge_vault/`** that acts as a local cached knowledge layer for repeated research, not a family of domain-specific vaults.

This vault should accumulate and normalize knowledge from multiple upstream paths:

- autoresearch pipeline output
- agent/web search results
- user-triggered browser clips
- explicit "store this in vault" actions from chat or agent output
- optional MCP-provided source packages from login-gated systems

The goal is simple:

**If Capybara has already done the work recently, it should answer from the vault first instead of rediscovering the same information again.**

---

## Why Single-Vault

The earlier multi-vault design was useful for thinking through provenance and domain isolation, but it adds a lot of product and implementation complexity:

- vault registry and `vault_id` routing
- multiple queue files and managers
- duplicate APIs and frontend selectors
- split search experiences
- more places for knowledge to become fragmented

That complexity is not aligned with the current goal. The current goal is to make `knowledge_vault` the **default local cache for repeated research** and the **first durable memory layer for research artifacts**.

Domain-specific, login-gated acquisition paths can still exist, but they should be handled in MCP or external scrapers and then feed the same vault in a controlled way.

---

## Directory Structure

```
backend/.capybara-home/
  knowledge_vault/
    00_schema/
      VAULT_SCHEMA.md
      RESEARCH_POLICY.md
      QUERY_RETENTION_POLICY.md
      INGEST_GUIDELINES.md          # new: generic ingest rules for all sources

    01_raw/
      sources/
        2026/05/
          source-id/
            source.md | source.html
            metadata.json

    02_compiled/
      sources/                      # source summaries / cached source pages
      syntheses/                    # topic-level synthesis pages
      entities/                     # durable entity pages
      concepts/                     # durable concept pages
      queries/                      # transient query-memory pages
      index.md
      log.md

    03_ops/
      queues/
        search_results_ingestion_queue.json
      reports/
      tasks/
      inbox/
      quarantine/

    .vault_state/
      manifest.json
```

---

## Ingestion Sources

| Source path | Target | Mechanism | Notes |
|---|---|---|---|
| Autoresearch output | `knowledge_vault/` | existing queue + ingest pipeline | durable cached research |
| Agent/web search output | `knowledge_vault/` | `enqueue_search_results()` | same cache as autoresearch |
| Browser clipper | `knowledge_vault/` | `POST /api/vault/clip` | user-curated capture |
| Explicit user save | `knowledge_vault/` | chat action / future endpoint | lets users persist good answers |
| MCP-gated sources | `knowledge_vault/` | controlled import / file drop | login-gated acquisition stays outside vault design |

---

## Core Invariant

There should be **one durable research cache** and **one primary retrieval path** for it.

That means:

- one vault root
- one queueing model
- one ingest model
- one search endpoint for API/UI/chat
- one set of compiled artifacts

If provenance segmentation is needed later, it should be expressed in metadata and policy:

- `source_tool`
- `source_type`
- `trust_score`
- `import_channel`
- `topic_tags`
- `access_constraints`

not by immediately splitting into multiple vault implementations.

---

## Retrieval Principle

The current codebase has two search implementations:

1. `VaultSearcher` in `backend/src/community/knowledge_vault_search/search.py`
   This is used by the agent tool and does on-demand BM25 over compiled markdown files.

2. `VaultLearningManager.search()` in `backend/src/control_plane/vault_learning.py`
   This is used by `/api/vault/search` and the frontend, and searches a manifest-backed cache with truncated text fields.

For the long-term architecture, this split should be removed.

### Target state

```
query_knowledge_vault tool
          │
          ├──────────────┐
          │              │
          ▼              ▼
      /api/vault/search  chat / agent retrieval
              │
              ▼
      UnifiedVaultSearchService
        - lexical retrieval
        - optional vector retrieval
        - optional graph/citation boosts
        - one ranking policy
        - one result schema
```

The API route and the chat tool can stay as separate interfaces, but they should call the **same underlying search service**.

---

## What Belongs In The Vault

The vault is not meant to be a dump of every raw response. It should hold artifacts that improve future retrieval quality:

- source summaries with provenance
- synthesis pages that consolidate repeated research
- entity pages that gather facts from multiple sources
- concept pages that stabilize terminology across sessions
- short-lived query pages to reduce repeat work over a small time window

This makes it useful both as:

- a **cache** for repeated research in short horizons
- a **knowledge substrate** for better future answers over longer horizons

---

## What Does Not Belong In This Topology

This topology no longer tries to directly model:

- separate `vault-sla/` and `vault-equasis/` directories
- vault pickers across many vaults
- vault-specific routing rules
- domain-specific page taxonomies baked into infrastructure

Those can still exist at the **source acquisition** layer through MCP servers, external scrapers, or dedicated workflows, but they should feed a single vault unless there is a later, proven need to split storage.

---

## Implication For The Phase Plan

This topology changes the implementation priorities:

1. unify retrieval first
2. improve ingest quality
3. add browser capture and explicit-save pathways
4. add graph/insight UX after the data quality improves

It also means every phase should assume:

- one `knowledge_vault`
- one search API surface
- one ingest queue
- metadata-based provenance instead of vault-based provenance
