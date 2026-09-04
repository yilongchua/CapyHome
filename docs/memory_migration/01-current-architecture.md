# 01 — Current Architecture

_Code truth as of 2026-09-03. All paths relative to repo root._

---

## Module inventory

| File | Role | LoC |
|------|------|-----|
| `backend/src/agents/middlewares/memory_middleware.py` | Capture trigger, message hygiene, `/memory` rule command | 218 |
| `backend/src/agents/memory/queue.py` | Debounce queue, per-scope locks, immediate path | 265 |
| `backend/src/agents/memory/updater.py` | Extraction LLM call, merge semantics, CRUD, mtime cache | 569 |
| `backend/src/agents/memory/prompt.py` | Extraction prompt + injection formatter + scope merge | 427 |
| `backend/src/agents/memory/vector_store.py` | SQLite fact index + scoring | 228 |
| `backend/src/agents/memory/store.py` | Versioned persistence, sha chain, redaction | 357 |
| `backend/src/agents/memory/summarization_hook.py` | Pre-compaction flush | 81 |
| `backend/src/agents/memory/compaction_archive.py` | Compaction audit log (**separate concern**) | 168 |
| `backend/src/tools/builtins/recall_tool.py` | Agent-facing explicit fetch | 66 |
| `backend/src/gateway/routers/memory.py` | 13 REST endpoints | ~400 |
| `frontend/src/core/memory/{api,hooks,types}.ts` | Client + React Query hooks + types | 282 |
| `frontend/src/components/workspace/settings/memory-settings-page.tsx` | Settings UI | 266 |

**Not part of this system** — do not drag into the migration:

- `ScratchpadTaskMemoryMiddleware` (`scratchpad_task_memory_middleware.py`) —
  in-agent-state, per-todo episodic notes. Disabled by default
  (`task_memory.enabled: false`, `scratchpad.enabled: false`). Never persisted
  to `memory.json`.
- `compaction_archive.py` — compaction event log + markdown audit reports.
  Surfaced through `/api/memory/compactions` for historical reasons only.

---

## End-to-end flow

```
                            ┌─ WRITE ────────────────────────────────────────┐
 agent turn ends            │                                                │
   │                        │  MemoryMiddleware.after_agent   (L162)         │
   ├───────────────────────▶│    ├─ gates: enabled / add_to_memory /         │
   │                        │    │         thread_id / has human+ai          │
   │                        │    ├─ filter_messages_for_memory (L36)         │
   │                        │    └─ queue.add(thread_id, msgs, ws=thread_id) │
   │                        │                    │                           │
 summarization              │                    │  30s debounce             │
 about to compact           │                    ▼  (single global Timer)    │
   ├───────────────────────▶│  memory_flush_hook ──▶ queue_immediate         │
   │                        │    (bypasses debounce, own daemon thread)      │
   │                        │                    │                           │
   │                        │                    ▼                           │
   │                        │  _update_context_memory  (queue.py L137)       │
   │                        │    ├─ MemoryUpdater.update_memory(global)  ◀── LLM call 1
   │                        │    └─ MemoryUpdater.update_memory(workspace)◀── LLM call 2
   │                        │            │                                   │
 "/memory <rule>"           │            ├─ _apply_updates      (L315)       │
   └───────────────────────▶│            ├─ persist_memory_data  (store.py)  │
     before_agent (L126)    │            └─ vector_store.upsert_facts        │
     add_behavior_rule      └────────────────────────────────────────────────┘
                                                 │
                            ┌─ READ ─────────────▼──────────────────────────┐
 prompt build               │  _get_memory_context                          │
   ├───────────────────────▶│    (DUPLICATED in work_agent + plan_agent)    │
   │                        │      ├─ get_memory_data(global)               │
   │                        │      ├─ get_memory_data(workspace, thread_id) │
   │                        │      └─ format_memory_for_injection (L264)    │
   │                        │            ├─ vector_store.query() if query   │
   │                        │            └─ confidence-sort if no query     │
   │                        │                  │                            │
   │                        │            <memory>…</memory> spliced at      │
   │                        │            MEMORY_INJECTION_SENTINEL          │
   │                        │                                               │
 agent calls `recall`       │  recall_tool ──▶ vector_store.query(top_k=5)  │
   └───────────────────────▶│                                               │
                            └───────────────────────────────────────────────┘
```

---

## Storage layout (current)

```
backend/.capyhome/                       ← Paths.base_dir
├── memory.json                          ← GLOBAL scope. 36 KB, 83 facts, 0 rules
├── USER.md                              ← hand-written profile, separate system
├── memory/
│   └── memory.db                        ← SQLite fact index (lexical, no vectors)
├── memory_versions/                     ← DISABLED by default
│   ├── global/{latest.json, versions/*.json}
│   ├── workspaces/<id>/…
│   └── agents/<name>/…
├── agents/<name>/memory.json            ← PER-AGENT scope
└── threads/<thread_id>/
    ├── memory.json                      ← "WORKSPACE" scope — 23 files present
    └── compaction_log.jsonl             ← separate concern
```

### `memory.json` schema (v2.0)

```json
{
  "version": "2.0",
  "scope": "global" | "workspace",
  "scopeId": "global" | "<thread_id>",
  "lastUpdated": "2026-09-02T…Z",
  "user": {
    "workContext":     { "summary": "…", "updatedAt": "…" },
    "personalContext": { "summary": "…", "updatedAt": "…" },
    "topOfMind":       { "summary": "…", "updatedAt": "…" }
  },
  "history": {
    "recentMonths":       { "summary": "…", "updatedAt": "…" },
    "earlierContext":     { "summary": "…", "updatedAt": "…" },
    "longTermBackground": { "summary": "…", "updatedAt": "…" }
  },
  "facts": [
    { "id": "fact_4f98888a", "content": "…", "category": "context",
      "confidence": 0.8, "createdAt": "…Z", "source": "<thread_id>" }
  ],
  "behaviorRules": [
    { "id": "rule_…", "instruction": "…", "active": true,
      "scope": "workspace", "scopeId": "…", "source": "…",
      "createdAt": "…", "updatedAt": "…" }
  ]
}
```

This schema is **the frontend contract** (`UserMemory` in
`frontend/src/core/memory/types.ts` mirrors it field-for-field, and
`MemoryResponse` in the router validates it). Treat it as an API projection to
be preserved, not as a storage format to be migrated.

### SQLite index schema

`backend/.capyhome/memory/memory.db`, created by `MemoryVectorStore._init_db`
(`vector_store.py:83`):

```sql
CREATE TABLE memory_facts (
    id TEXT PRIMARY KEY,   scope TEXT NOT NULL,   scope_id TEXT NOT NULL,
    content TEXT NOT NULL, category TEXT NOT NULL, confidence REAL NOT NULL,
    created_at TEXT, updated_at TEXT, source TEXT
);
CREATE INDEX idx_memory_facts_scope ON memory_facts(scope, scope_id);
```

Note there is **no embedding column** — see [03](./03-retrieval-and-injection.md).

---

## Scope model

Three scopes exist in code, resolved by `_memory_file_path` (`store.py:48`) and
`_get_memory_file_path` (`updater.py:47`):

| Scope | Keyed by | File | Enabled by |
|-------|----------|------|------------|
| `global` | — | `{base_dir}/memory.json` | `memory.global_scope_enabled` |
| `workspace` | `workspace_id` | `{base_dir}/threads/{workspace_id}/memory.json` | `memory.workspace_scope_enabled` |
| per-agent | `agent_name` | `{base_dir}/agents/{name}/memory.json` | implicit — when `agent_name` is passed |

### ⚠ `workspace_id == thread_id`

The "workspace" scope is **per-conversation, not per-project**:

- `MemoryMiddleware.after_agent` calls `queue.add(..., workspace_id=thread_id)`
  (`memory_middleware.py:211`).
- `_update_context_memory` falls back the same way:
  `workspace_id = context.workspace_id or context.thread_id` (`queue.py:145`).
- Storage therefore lands in `threads/{thread_id}/memory.json` — 23 such files
  exist, one per conversation.

Consequence: workspace-scope facts are effectively **write-only**. They are
extracted per conversation, injected only into that same conversation, and never
shared across the threads of a project. See **O-3** in the [README](./README.md#open-decisions).

---

## Configuration

`memory:` block in `config.yaml` L227, model `MemoryConfig` in
`backend/src/config/memory_config.py`.

| Key | Default | In `config.yaml`? | Used by |
|-----|---------|-------------------|---------|
| `enabled` | `true` | ✅ | every entry point |
| `storage_path` | `""` → `{base_dir}/memory.json` | ✅ `memory.json` | `_get_memory_file_path` |
| `debounce_seconds` | `30` | ✅ | `queue._reset_timer` |
| `model_name` | `null` | ✅ | `MemoryUpdater._get_model` |
| `max_facts` | `100` | ✅ | `_apply_updates` truncation |
| `fact_confidence_threshold` | `0.7` | ✅ | `_apply_updates` gate |
| `injection_enabled` | `true` | ✅ | `_get_memory_context` |
| `max_injection_tokens` | `2000` | ✅ | tiktoken truncation |
| `global_scope_enabled` | `true` | ❌ (default only) | `_scope_flags_allow`, injection, `recall` |
| `workspace_scope_enabled` | `true` | ❌ | as above |
| `behavior_rules_enabled` | `true` | ❌ | `before_agent` `/memory` capture |
| `decay_enabled` | `true` | ❌ | `_decay_multiplier` |
| `decay_half_life_days` | `60` | ❌ | `_decay_multiplier` |
| `decay_archive_threshold` | `0.1` | ❌ | **dead — never read** |
| `recall_top_k` | `5` | ❌ | `recall` tool; injection uses `×2` |
| `injection_relevance_threshold` | `0.5` | ❌ | `_is_relevant_injection_fact` |

Related blocks: `memory_versioning:` (L292, disabled), `task_memory:` (L287,
disabled — separate concern), `scratchpad:` (L281, disabled — separate concern).

Extraction model resolves through `ModelRouter().resolve("memory_extractor", …)`
(`updater.py:236`). No `routing.stages.memory_extractor` entry exists in
`config.yaml`, so it falls through to `routing.fallback` → app default model.

---

## Doc vs. code discrepancies

Two claims in [../key-features/12-persistent-memory.md](../key-features/12-persistent-memory.md)
do not hold:

| Doc | Reality |
|-----|---------|
| L50 — "a **SQLite-backed index** scores `0.65·lexical + 0.35·(confidence × decay)`" — reads as semantic retrieval, and the module is named `vector_store.py` | The formula is accurate, but `lexical` is plain token-set overlap. **There are no embeddings anywhere in the memory path.** |
| L48 — "two scopes — global (you everywhere) and **workspace (per-project)**" | Workspace scope is keyed by `thread_id`. No project-level scope exists. |

A third claim becomes obsolete under decision **D-3**: L36–L44 and L59 on local
JSON ownership, real forgetting, and hash-chained audit accountability.

A fourth lives outside this doc, in the **tool catalogs** — both
`internal_tools_plan.json` and `internal_tools_work.json` tell the model the
retriever is "keyword + embedding based". It is keyword-only. Unlike the three
above, the migration **makes that claim true**; see
[04](./04-surface-inventory.md#the-catalogs-already-claim-embeddings).
