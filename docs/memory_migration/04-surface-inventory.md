# 04 — Surface Inventory (migration blast radius)

_Every place the memory subsystem is touched. Use as the migration checklist._

---

## Write entry points — 3

| # | Site | File:line | Action |
|---|------|-----------|--------|
| W-1 | `MemoryMiddleware.after_agent` | `backend/src/agents/middlewares/memory_middleware.py:162` | **Keep.** Gates + hygiene are orthogonal to the store. Only `queue.add` changes. |
| W-2 | `memory_flush_hook` | `backend/src/agents/memory/summarization_hook.py:17` | **Keep.** Registered at `work_agent/agent.py:277`. |
| W-3 | `/memory` command → `add_behavior_rule` | `memory_middleware.py:126` | **Keep**, retarget at the behavior-rule store (see [06](./06-migration-plan.md)). |

Everything funnels through `MemoryUpdateQueue._update_context_memory`
(`queue.py:137`) → `MemoryUpdater.update_memory` (`updater.py:239`). **That
method body is the migration's primary target.**

## Read entry points — 3

| # | Site | File:line | Action |
|---|------|-----------|--------|
| R-1 | `_get_memory_context` (work agent) | `backend/src/agents/work_agent/prompt.py:173` | Consolidate with R-2, then retarget |
| R-2 | `_get_memory_context` (plan agent) | `backend/src/agents/plan_agent/prompt.py:266` | **Verbatim duplicate of R-1** |
| R-3 | `recall` tool | `backend/src/tools/builtins/recall_tool.py:16` | Retarget at mem0 `search()`. **The LLM-facing contract is not here** — see [Tool-catalog surface](#tool-catalog-surface-the-pull-channel) |

Both R-1/R-2 flow through `format_memory_for_injection` (`prompt.py:264`) →
`MemoryVectorStore.query` (`vector_store.py:167`).

## Secondary readers — 3

| # | Site | File:line | Reads | Risk |
|---|------|-----------|-------|------|
| S-1 | `CapyHomeClient.get_memory()` | `backend/src/client.py:679` | whole global `memory.json` | Low — returns the projection |
| S-2 | Vault lint judge | `backend/src/control_plane/vault_learning/_lint.py:42` | `user.*`, `history.*`, facts ≥0.6 | **High — hard dependency on narrative sections (O-1)** |
| S-3 | Settings UI | `frontend/src/components/workspace/settings/memory-settings-page.tsx` | `GET /api/memory` | Low if the schema is preserved |

---

## Tool-catalog surface (the pull channel)

Memory reaches the agent through **two independent channels**, and the migration
affects them differently:

| | Push | Pull |
|---|------|------|
| Mechanism | `<memory>` block spliced into the system prompt | `recall` tool |
| Agent choice | none — it is simply present | agent decides to query |
| Governed by | `format_memory_for_injection` (**R-1/R-2**) | **the two JSON tool catalogs** |
| Changes under mem0 | new relevance filter, thresholds retuned | new score scale, new result shape |

The pull channel matters more than it looks: on a normal turn the push channel
omits the narrative sections entirely (see
[03](./03-retrieval-and-injection.md#format_memory_for_injection)), so `recall`
is the agent's only route to anything beyond ≤15 filtered facts.

### The catalogs are the contract, not the docstring

With `json_driven_tools: true` (the default), `build_structured_tool`
(`src/tools/loader.py`) **overrides** the handler's Python docstring and
per-argument descriptions with the JSON entry. The model never sees
`recall_tool.py`'s docstring.

| File | Entry | LLM-facing text |
|------|-------|-----------------|
| `backend/src/tools/internal_tools_plan.json` | L136-L172 | plan-mode framing |
| `backend/src/tools/internal_tools_work.json` | L104-L131 | work/auto framing |

**Keep the two diverged.** The plan entry carries a sentence the work entry does
not — *"especially valuable while planning, where prior decisions, conventions,
and user preferences should shape the plan before you ever ask the user"* — which
is the entire reason per-mode catalogs exist. Do not homogenize them into one
description during the migration.

### The catalogs already claim embeddings

Both files contain this sentence verbatim:

> "…because the underlying retriever is **keyword + embedding based**, not conversational."

There are no embeddings in the memory path today (see
[03](./03-retrieval-and-injection.md#the-index-that-isnt-a-vector-store)). This is
a **third** documented-vs-code discrepancy alongside the two in
[01](./01-current-architecture.md#doc-vs-code-discrepancies) — but unlike those,
the migration **makes this claim true** rather than requiring an edit. The
catalogs were written aspirationally.

### What must be edited: the `returns` field

Both entries document the exact shape the migration changes:

```
{query, results: [{id, scope, content, category, confidence, score, source}]}
```

| Field | Under mem0 |
|-------|-----------|
| `confidence` | survives **only if G-3 lands** (metadata via custom extraction prompt) — otherwise drop it from the contract rather than returning a fabricated default |
| `score` | **scale changes** — embedding similarity, not `0.65·lexical + 0.35·conf·decay`. "higher = more relevant" stays true, but any agent behaviour calibrated to today's magnitudes shifts |
| `category`, `scope`, `source`, `id` | preserved via the projection in [06](./06-migration-plan.md#fact-to-mem0-projection) |
| `'No relevant memory found.'` | **documented literal** — preserve verbatim |
| `'Memory scopes are disabled.'` | **documented literal** — preserve verbatim |

### The wider `recall` surface — 6 more sites

| Site | File:line | Migration note |
|------|-----------|----------------|
| Draft-plan gate allow-list | `plan_execution_gate_middleware.py:40` | `recall` is in `_ALLOWED_WHEN_DRAFT`; it runs while a plan is unapproved. No change needed, but do not drop it |
| `comparison-dimension-researcher` subagent | `subagents/builtins/comparison_dimension_researcher.py:50` | grants `recall` via allow-list |
| `general-purpose` subagent | `subagents/builtins/general_purpose.py` | grants `recall` **by omission** (no allow-list) |
| `knowledge-researcher` subagent | `subagents/builtins/knowledge_researcher.py:65` | **explicitly denies** `recall` via `disallowed_tools` — see [issues_memory.md](./issues_memory.md#m-4-recall-is-absent-from-the-research-path) |
| Plan-agent prompt guidance | `plan_agent/prompt.py:50,61,97,230` | Frames recall as scope discovery, not content gathering |
| Work-agent prompt guidance | `work_agent/prompt.py:137` | one reference |
| `ask_user_for_clarification` description (**both** catalogs) | plan L4 / work L4 | *"Do NOT use for: questions you can answer by … calling `recall`"* — recall is positioned as the alternative to interrupting the user. If recall gets better under mem0, this framing gets stronger, not weaker |
| `write_plan` description | plan L212 | lists `recall` among investigation tools |

**Subagent scope propagation is fine** — verified end-to-end. `task_tool` reads
`thread_id` from runtime context (`task_tool.py:340`), hands it to
`SubagentExecutor` (`:378`), which sets `configurable["thread_id"]`
(`executor.py:300`). `recall_tool`'s `get_config()` lookup therefore resolves the
parent thread, and workspace scope works inside subagents.

What is **not** fine is everything around it — subagents can read memory but
never write it, and only 2 of 9 can even call `recall`. Full analysis in
[issues_memory.md](./issues_memory.md#the-subagent-memory-boundary).

### Gap: the drift validator will not catch a stale `returns`

`tests/test_tool_schema_sync.py` validates `parameters` against the handler
signature, asserts descriptions are ≥60 chars, and asserts `returns` / `examples`
are non-empty. It **never checks that `returns` describes the actual return
shape**. A stale `returns` after the migration passes CI silently. Consider
adding an assertion that pins the `recall` result keys against the projection.

---

## REST API — 13 endpoints

`backend/src/gateway/routers/memory.py`, mounted at `/api` (`app.py:372`).

| Method | Path | Backed by | Migration |
|--------|------|-----------|-----------|
| GET | `/api/memory` | `get_memory_data` | Projection over mem0 |
| POST | `/api/memory/reload` | `reload_memory_data` | Cache-bust; likely no-op |
| GET | `/api/memory/config` | `get_memory_config` | Extend with mem0 fields |
| GET | `/api/memory/status` | config + data + version ref | Drop `memory_version_ref` (D-3) |
| GET | `/api/memory/versions` | `list_memory_versions` | **Delete (D-3)** |
| GET | `/api/memory/versions/{id}` | `get_memory_version` | **Delete (D-3)** |
| POST | `/api/memory/redact` | `redact_memory` | **Delete (D-3)** |
| POST | `/api/memory/facts/{id}` | `upsert_fact` | mem0 `update()` / `add()` |
| DELETE | `/api/memory/facts/{id}` | `delete_fact` | mem0 `delete()` |
| POST | `/api/memory/forget-thread` | `forget_thread_facts` | mem0 `delete_all(run_id=…)` — **cleaner than today** |
| POST | `/api/memory/rules` | `add_behavior_rule` | Behavior-rule store |
| PATCH | `/api/memory/rules/{id}` | `update_behavior_rule` | Behavior-rule store |
| DELETE | `/api/memory/rules/{id}` | `delete_behavior_rule` | Behavior-rule store |
| POST | `/api/memory/clear` | `clear_memory` | mem0 `delete_all(scope)` |
| GET | `/api/memory/compactions` | `read_compaction_entries` | **Unrelated** — leave alone |

Response models to preserve: `MemoryResponse`, `Fact`, `BehaviorRule`,
`UserContext`, `HistoryContext`, `ContextSection`, `MemoryConfigResponse`.

## Frontend — 4 modules, ~550 LoC

| File | LoC | Migration |
|------|-----|-----------|
| `frontend/src/core/memory/api.ts` | 140 | **Untouched** if the schema holds |
| `frontend/src/core/memory/hooks.ts` | 72 | Untouched |
| `frontend/src/core/memory/types.ts` | 70 | Untouched — `UserMemory` mirrors `MemoryResponse` |
| `frontend/src/components/workspace/settings/memory-settings-page.tsx` | 266 | Untouched, **unless O-1 drops the narrative sections** (it renders them) |
| `frontend/src/components/workspace/input-box.tsx:239` | — | `add_to_memory: false` flag — untouched |

**The frontend is the strongest argument for preserving `MemoryResponse` as a
projection.** Zero frontend work if the schema holds.

---

## Tests

Seven files, 817 LoC (excluding the unrelated scratchpad file).

| File | LoC | Fate |
|------|-----|------|
| `backend/tests/test_memory_upload_filtering.py` | 214 | ✅ **Survives unchanged** — pure message/text hygiene |
| `backend/tests/test_memory_middleware_runtime_flags.py` | 44 | ✅ **Survives unchanged** — `add_to_memory` gating |
| `backend/tests/test_memory_update_queue.py` | 33 | ⚠ Rewrite — asserts per-scope lock serialization; scope model changes |
| `backend/tests/test_prompt_memory_context.py` | 229 | ⚠ Rewrite — asserts injection + prompt-cache interaction |
| `backend/tests/test_memory_vector_store_consistency.py` | 120 | 🔄 **Replace** — index-consistency invariants become mem0's problem |
| `backend/tests/test_memory_versioning_store.py` | 88 | ❌ **Delete (D-3)** |
| `backend/tests/test_scratchpad_task_memory_middleware.py` | 89 | ➖ Unrelated — leave alone |

---

## Config surface

| File | Change |
|------|--------|
| `backend/src/config/memory_config.py` | Add a `mem0` sub-block; retire `decay_archive_threshold` (dead) |
| `backend/src/config/memory_versioning_config.py` | **Delete (D-3)** |
| `config.yaml` / `config.example.yaml` L227 | Add mem0 keys; remove `memory_versioning:` L292 |
| `backend/src/config/app_config.py` | Loader wiring for the above |
| `backend/pyproject.toml` | `+ mem0ai`, `+ qdrant-client` (O-2) |

## Files deleted at completion

| File | LoC | Reason |
|------|-----|--------|
| `backend/src/agents/memory/vector_store.py` | 228 | Replaced by mem0's store |
| `backend/src/agents/memory/store.py` | 357 | Versioning + redaction dropped (D-3); persistence moves to mem0 |
| `backend/src/config/memory_versioning_config.py` | ~40 | D-3 |
| `FACT_EXTRACTION_PROMPT` in `prompt.py:127` | ~25 | Already dead code — no call site |
| `MEMORY_UPDATE_PROMPT` in `prompt.py:18` | ~120 | Superseded by mem0's extraction (patches migrate — see [05](./05-mem0-mapping.md)) |

Net: roughly **−800 LoC**, plus whatever the adapter costs (~300 estimated).

---

## Runtime & operational constraints

| # | Constraint | Why it matters |
|---|-----------|----------------|
| C-1 | **Sync client in an async gateway.** mem0's client is synchronous. Per the known gateway async-blocking pattern in this repo, a sync call from an `async def` router wedges the entire gateway. | Keep every mem0 call inside the existing background threads. Router endpoints must use `def`, not `async def`, or offload. |
| C-2 | **Module-global singletons.** `_memory_queue`, `_VECTOR_STORE`, `_memory_cache` are process globals with test-reset hooks. | The mem0 client must follow the same pattern, including a reset hook — Qdrant embedded holds an exclusive directory lock (O-2). |
| C-3 | **Model routing.** Extraction resolves via `ModelRouter().resolve("memory_extractor")` (`updater.py:236`). mem0 configures its own LLM/embedder internally. | Needs an adapter pointing mem0 at the same endpoints, or you acquire a second unmanaged model dependency outside `config.yaml`. |
| C-4 | **Embedding contention.** Memory has **no** embedding dependency today. The knowledge vault already drives an OpenAI-compatible embedder (`vector_embedding_model`, 256 dims) against the local endpoint, and rebuild storms there are a known issue. | mem0 adds a second consumer on the same endpoint. Budget for it; consider a smaller/faster embedding model for memory. |
| C-5 | **`print()` not `logger`.** `updater.py` and `queue.py` report failures via `print`. | Migration is the moment to fix this — silent failures are currently invisible in logs. |
