# Analysis: lucasastorian/llmwiki

**Repo**: https://github.com/lucasastorian/llmwiki  
**Local path**: `/Users/yi.long.chua/Desktop/llmwiki`  
**Analysis date**: 2026-05-16  
**Context**: Evaluated as a Docker-deployable alternative to nashsu/llm_wiki, as a domain-specific knowledge database or individual user knowledge vault integrated with Capybara.

---

## What This Repo Actually Is

This is the most architecturally complete of the three implementations evaluated (nashsu/llm_wiki, Capybara's own vault, and this one). It is a **full-stack web application** implementing the Karpathy LLM Wiki pattern with:

- **FastAPI backend** (`/api`) — Python, SQLite (local) or PostgreSQL (hosted)
- **Next.js frontend** (`/web`) — React 19, PDF viewer, graph visualization, wiki editor
- **Native MCP server** (`/mcp`) — Claude connects via Model Context Protocol, stdio or hosted
- **CLI** (`./llmwiki`) — one-command init/serve/open
- **Browser extension** (`/extension`) — WXT (Chrome/Firefox/Edge), saves clips and highlights
- **Converter service** (`/converter`) — optional async PDF/Office processor for hosted mode

Key principle: **filesystem is truth, SQLite is the derived index**. The wiki lives in plain markdown files. The database is a rebuildable index, not the source of record.

---

## Docker Compose Reality Check

The user noted this as a "docker compose deployable repo." The reality is more nuanced:

**What `docker-compose.yml` actually contains:**
```yaml
services:
  db:
    image: postgres:16-alpine   # ← PostgreSQL only, for hosted/dev mode
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./supabase/migrations:/docker-entrypoint-initdb.d
```

**`docker-compose.test.yml`**: PostgreSQL for integration tests only (tmpfs, ephemeral).

**Bottom line**: The docker-compose.yml spins up a PostgreSQL database only — it is NOT a full-stack compose file. However, **every service has its own Dockerfile**:

| Service | Dockerfile | Base |
|---|---|---|
| API backend | `api/Dockerfile` | `python:3.11-slim-bookworm` |
| MCP server (hosted) | `mcp/Dockerfile` | `python:3.11-slim-bookworm` |
| Web frontend | `web/Dockerfile` | `node:22-alpine` (multi-stage) |
| Converter | `converter/Dockerfile` | Python slim |

A full `docker-compose.yml` running all services together **does not exist** but could be assembled from the existing Dockerfiles with moderate effort (environment variable wiring, volume mounts, service networking). This is a one-time ~2 hour task.

**Local mode requires no Docker at all**: `./llmwiki open ~/research` runs entirely on localhost with SQLite — no containers needed.

---

## Architecture Map

```
┌────────────────────────────────────────────────────────────────┐
│                  Workspace Folder (filesystem)                 │
│  sources/paper.pdf    wiki/concepts/attention.md               │
│  notes.md             wiki/log.md    wiki/overview.md          │
│  data.xlsx            .llmwiki/index.db   .llmwiki/cache/      │
└──────────────────────────────┬─────────────────────────────────┘
                               │ file watcher (watchfiles)
         ┌─────────────────────┼──────────────────────┐
         ▼                     ▼                      ▼
  FastAPI :8000         MCP stdio server        Next.js :3000
  REST API              (llmwiki mcp)           Web UI
  - documents           - guide tool            - wiki editor
  - search              - search tool           - PDF viewer
  - highlights          - read tool             - graph viz
  - upload (Tus)        - write tool            - file browser
  - graph               - delete tool           - highlights
         ▲
         │ (hosted mode only)
  PostgreSQL + Supabase + S3
```

**Two operating modes:**

| Aspect | Local mode | Hosted mode |
|---|---|---|
| Database | SQLite (`.llmwiki/index.db`) | PostgreSQL |
| Storage | Local filesystem | S3 |
| Auth | No auth (single user) | Supabase JWT |
| Search | FTS5 (Porter stemming) | PGRoonga (ranked FTS) |
| Multi-user | No | Yes |
| Setup | `./llmwiki open <folder>` | Supabase + S3 + Railway |

---

## Key Capabilities in Detail

### 1. MCP Server (the standout feature)

The MCP server is what distinguishes this implementation from nashsu/llm_wiki (which has none). The MCP server (`/mcp/local_server.py`) runs as a stdio process and exposes five tools that Claude calls directly:

**`guide`** — Entry point. Returns wiki structure, lists knowledge bases, describes the ingest workflow.

**`search`** — Three modes:
- `list`: glob files (e.g., `*.pdf`, `/wiki/**`)
- `search`: FTS keyword search with chunk-level results including page numbers and header breadcrumbs
- `references`: citation graph queries (what cites this? what does this cite? uncited sources?)

**`read`** — Retrieve document content:
- PDFs with page ranges (`pages="1-10"`)
- Batch glob reads
- Renders user highlights as appendix
- Returns text + base64 images

**`write`** — Create/edit/append wiki pages:
- `create()` with frontmatter validation
- `edit()` with `str_replace()` and context
- `append()` to existing page
- Automatically parses citations (`[^1]: filename.pdf, p.3`) and wikilinks
- Updates citation graph on every write

**`delete`** — Remove documents by path or glob, updates database.

The MCP config snippet for Claude Code:
```json
{
  "mcpServers": {
    "sla-wiki": {
      "command": "python",
      "args": ["-m", "local_server", "/path/to/vault-sla"],
      "cwd": "/Users/yi.long.chua/Desktop/llmwiki/mcp"
    }
  }
}
```

### 2. Citation Graph

One capability not in capybara's vault or nashsu/llm_wiki: **automatic citation tracking**. Every time Claude writes a wiki page with footnotes like `[^1]: paper.pdf, p.3`, the `write` tool:
1. Parses footnote references → extracts source document path and page number
2. Parses wikilinks → extracts links to other wiki pages
3. Stores edges in `document_references` table: `source_id → target_id` with type (`cites` / `links_to`) and page number

This enables the `references` search mode:
- "What wiki pages cite this PDF?" → backlinks query
- "What sources has this wiki page referenced?" → forward links
- "Which sources have never been cited?" → uncited sources discovery
- "Which wiki pages are stale?" → flag pages whose referenced sources have been updated

For SLA case law, this is directly valuable: a case page `[[sghc-2026-123]]` cites other cases and statutes. The citation graph makes the "cases cited" network navigable without manual cross-referencing.

### 3. Document Processing

| Format | Method |
|---|---|
| PDF | `opendataloader-pdf` (Rust-based, fast) |
| PDF (enhanced) | Mistral OCR (optional, `$MISTRAL_API_KEY`) |
| DOCX/PPTX | LibreOffice subprocess (optional) |
| Excel/CSV | openpyxl multi-sheet |
| Markdown/Text | Direct indexing |
| HTML | Cleaned extraction (strips nav/ads) |
| Images | Stored inline |

### 4. Search Implementation

**Local mode (FTS5)**:
- Porter stemming (stem-based matching, not semantic)
- unicode61 tokenizer
- Chunk-level results: returns the specific ~512-token chunk that matched, with `header_breadcrumb` (e.g., `## Methods > ### Evaluation`) and page number
- Title match bonus

**Hosted mode (PGRoonga)**:
- Ranked full-text search on PostgreSQL
- Better multilingual support
- Still not semantic/vector — no embedding search in either mode

**No vector/semantic search** in either mode. This is the primary gap versus nashsu/llm_wiki (which has LanceDB + RRF).

### 5. Browser Extension

Built with WXT (Chrome/Firefox/Edge compatible, Manifest V3). Saves web clips and user highlights directly into a selected knowledge base. In local mode, communicates with the FastAPI backend on `:8000`. This removes the need to build a custom Chrome extension for capybara (Phase 3 of the original plan).

---

## Comparison: Three Implementations

| Feature | capybara vault | nashsu/llm_wiki | lucasastorian/llmwiki |
|---|---|---|---|
| **Language** | Python | Rust + React (Tauri) | Python + TypeScript |
| **MCP server** | No (uses capybara's MCP layer) | **No** | **Yes — native stdio** |
| **REST API** | Yes (FastAPI) | No | Yes (FastAPI) |
| **Search** | BM25 | BM25 + vector (LanceDB) | FTS5 / PGRoonga |
| **Semantic search** | No | **Yes** | No |
| **Two-step CoT ingest** | No | **Yes** | No (Claude via MCP) |
| **Citation graph** | No | No | **Yes — automatic** |
| **Browser extension** | No | Chrome only | **Chrome/Firefox/Edge (WXT)** |
| **PDF viewer** | No | No | **Yes (react-pdf)** |
| **Knowledge graph** | No | sigma.js + Louvain | react-force-graph-2d |
| **Docker** | Yes (full stack) | No | Partial (db only, all Dockerfiles exist) |
| **Autoresearch objectives** | **Yes** | No | No |
| **Scheduled pipelines** | **Yes** | No | No |
| **Trust scoring** | **Yes** | No | No |
| **Loop guards** | **Yes** | No | No |
| **Sufficiency evaluation** | **Yes** | No | No |
| **Multi-user** | Single | Single | **Yes (hosted + Supabase)** |
| **Deployment** | Docker compose | Desktop binary | Local CLI or Railway/Vercel |
| **Obsidian compat** | Partial | **Native** | Partial (plain markdown) |

---

## How It Could Fit Into Capybara

### Role: Reference Pattern, Not Parallel Vault Topology

The clearest role for `lucasastorian/llmwiki` in the Capybara system is now as a **reference implementation** for:

- filesystem-as-truth knowledge storage
- MCP-native local knowledge access
- chunked local search
- citation/backlink tracking

rather than as a second family of domain-specific vaults running alongside Capybara.

Capybara's `knowledge_vault` should remain the main durable cache for repeated research. Login-gated or highly structured acquisition paths can still be handled through MCP or dedicated scrapers, but they should feed the same vault unless a future requirement proves otherwise.

```
Capybara Agent (lead)
    │
    ├── query_knowledge_vault_tool / /api/vault/search
    │        └── unified retrieval over knowledge_vault
    │
    └── optional MCP / scraper acquisition paths
             └── normalized content eventually lands in knowledge_vault
```

### Integration path ideas

The most useful ideas to port from this repo are:

1. chunk-level local search behavior
2. citation/backlink tracking
3. filesystem-truth discipline
4. MCP-friendly tooling for reading and writing curated knowledge

### Impact on the Phase plan from the previous docs

| Phase | Original plan | Updated with llmwiki |
|---|---|---|
| Phase 1 (retrieval) | Unify search path + add vector search | Still needed; llmwiki is useful mainly as inspiration for local/chunk retrieval |
| Phase 2 (CoT ingest) | Split capybara vault ingest into two steps | Still highly valuable |
| Phase 3 (capture) | Browser clipper + explicit save → capybara API | Still needed for the single shared vault |
| Phase 4 (knowledge graph) | Build graph UX in capybara frontend | Still needed |

---

## Gaps and What Still Needs Building

### 1. No semantic search (the critical gap)

FTS5 Porter stemming means the same token-mismatch problems as capybara's BM25. A query for `"beneficial ownership transfer"` will miss a vessel page titled `"Change of Registered Owner"` if the tokens don't overlap.

**Options**:
- Add vector search to llmwiki's local mode (LanceDB Python package, medium effort — similar to Phase 1 plan)
- Accept FTS5 for now, rely on Claude's `read` tool to do semantic synthesis after FTS retrieval
- Use hosted mode with PGRoonga (better, still not semantic)

### 2. No built-in Capybara-style ingest policy enforcement

llmwiki accepts any content written by Claude. There is no `DOMAIN_RULES.md`-driven schema enforcement, trust scoring, or source allowlist. Claude must be instructed via the wiki's own `schema.md` and `purpose.md` files (the Karpathy pattern — human curates schema, LLM maintains).

If Capybara borrows this pattern, it should do so as prompt/schema guidance inside the single shared vault, not as a reason to split storage into multiple vault roots.

### 3. Full docker-compose does not exist

No single `docker-compose.yml` runs the full stack (API + MCP + web + db). For a fully containerized deployment (e.g., running on a server rather than the user's Mac), a compose file needs to be written. The Dockerfiles for all services exist — this is a ~2 hour assembly task.

### 4. No autoresearch / scheduling

llmwiki has no autonomous research loop. All ingest is Claude-driven (user or agent initiates). For Capybara's general cached-research objective, Capybara's autoresearch system remains the stronger foundation.

### 5. MCP server startup cost

Each llmwiki MCP server (`local_server.py`) is a Python subprocess. Startup is fast, but using many parallel workspace servers adds operational overhead.

---

## Recommendation

**Use lucasastorian/llmwiki as a reference pattern, not as the primary topology for Capybara's vault.**

**Reasons:**
1. The native MCP server is a strong example of clean local knowledge tooling
2. Python-based — same language as the Capybara backend, easy to inspect and adapt
3. Citation/backlink handling is genuinely useful to borrow
4. Local-first, filesystem-transparent storage is a strong architectural pattern
5. The browser extension and chunked search are useful reference implementations

**What to keep in Capybara as the core platform:** autoresearch objectives, trust scoring, loop guards, sufficiency evaluation, scheduled pipelines, and the single shared `knowledge_vault`.

**Next step**: borrow the best ideas selectively:

1. unify retrieval in Capybara
2. improve ingest with a two-step compile
3. add clipper and explicit-save pathways into the existing vault
4. consider citation/backlink tracking as a later enhancement
