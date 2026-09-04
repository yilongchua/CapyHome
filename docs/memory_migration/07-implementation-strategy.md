# 07 — Implementation Strategy

_The concrete **how**. [06](./06-migration-plan.md) has the phases, rollback and
risk register; this document has the module design, signatures, config schema and
per-file wiring._

> Code below is design intent, not copy-paste. Every mem0 API call must be
> validated against the version pinned in `backend/pyproject.toml` at
> implementation time.

---

## Three invariants

Everything else follows from these. If a change violates one, it is wrong.

**I-1 · `MemoryResponse` is an API projection, not a storage format.**
The 13 REST endpoints and ~550 LoC of frontend keep working because the adapter
*renders* mem0 into the existing schema. Never migrate the schema; migrate what
backs it.

**I-2 · Every mem0 call runs on a background thread.**
mem0's client is synchronous. This repo has a known failure mode where a sync
call inside an `async def` router wedges the entire gateway. The existing queue
already runs on daemon threads — keep all mem0 traffic there, and make touched
router endpoints `def`, not `async def`.

**I-3 · Not everything belongs in mem0.**
Three stores, deliberately:

| Store | Holds | Why not mem0 |
|-------|-------|--------------|
| mem0 | atomic facts | — |
| `rules.json` | behavior rules | deterministic, always injected in full; semantic recall would make a *rule* probabilistic |
| narrative artifact | the six profile sections | mem0's dedup would shred a maintained narrative (**O-1**) |

---

## Module layout

```
backend/src/agents/memory/
├── backend.py           NEW  MemoryBackend Protocol + get_memory_backend()
├── backends/
│   ├── legacy.py        NEW  LegacyJsonBackend — wraps today's code
│   └── mem0_backend.py  NEW  Mem0Backend
├── rules.py             NEW  BehaviorRuleStore (rules.json)
├── narrative.py         NEW  NarrativeProfile regenerator (gated on O-1)
├── extraction_prompt.py NEW  CAPYHOME_FACT_EXTRACTION_PROMPT
├── projection.py        NEW  mem0 memory ⇄ Fact ⇄ MemoryResponse
│
├── queue.py             KEEP, patched (Q-1 per-thread timers; single ingest call)
├── updater.py           SHRINK — CRUD delegates to the backend; extraction removed
├── prompt.py            SHRINK — keep formatter, drop MEMORY_UPDATE_PROMPT + filter stack
├── summarization_hook.py KEEP unchanged
├── compaction_archive.py KEEP — unrelated concern
├── vector_store.py      DELETE at Phase 5
└── store.py             DELETE at Phase 5 (versioning + redaction dropped, D-3)
```

---

## The seam: `MemoryBackend`

```python
# src/agents/memory/backend.py
from typing import Protocol, Any, Literal

Scope = Literal["global", "workspace"]

class MemoryScopes:
    """Resolved scope identity for one operation."""
    user_id: str                 # "global" — single-user install
    run_id: str | None           # thread_id when workspace scope applies
    agent_id: str | None         # custom agent name, when set

class MemoryBackend(Protocol):
    def ingest(self, messages: list[Any], *, scopes: MemoryScopes,
               source: str | None = None) -> bool:
        """Extract and persist facts from a filtered conversation. One call
        covers every scope in `scopes` (fixes Q-2)."""

    def search(self, query: str, *, scopes: MemoryScopes,
               top_k: int) -> list[dict[str, Any]]:
        """Return Fact-shaped dicts, ranked. Must include `score`."""

    def get_profile(self, *, scopes: MemoryScopes) -> dict[str, Any]:
        """MemoryResponse-shaped dict. Assembled from facts + rules + narrative."""

    def upsert_fact(self, fact_id: str | None, content: str, *,
                    scopes: MemoryScopes, **meta) -> dict[str, Any]: ...
    def delete_fact(self, fact_id: str, *, scopes: MemoryScopes) -> bool: ...
    def delete_scope(self, *, scopes: MemoryScopes) -> None: ...
    def forget_run(self, run_id: str) -> int: ...
```

**Why `MemoryScopes` as one object rather than loose kwargs:** today's code
threads `scope` + `workspace_id` + `agent_name` through 20+ signatures and
re-normalizes them in three places (`store.py:37`, `updater.py:32`,
`prompt.py`). Collapsing to one resolved object kills that duplication and makes
the mem0 id mapping a single function.

Selection:

```python
_BACKEND: MemoryBackend | None = None

def get_memory_backend() -> MemoryBackend:      # singleton, mirrors get_memory_vector_store()
    ...
def reset_memory_backend() -> None:             # test hook — REQUIRED, see Testing
    ...
```

---

## `Mem0Backend`

### Client construction

```python
# src/agents/memory/backends/mem0_backend.py
def _build_client() -> "Memory":
    paths, cfg, app = get_paths(), get_memory_config(), get_app_config()
    root = paths.base_dir / "memory" / "mem0"        # decision D-2
    root.mkdir(parents=True, exist_ok=True)

    return Memory.from_config({
        "vector_store": {"provider": cfg.mem0.vector_provider, "config": {
            "collection_name": "capyhome_memory",
            "path": str(root / "vector_store"),
            "on_disk": True,
        }},
        # I-3 corollary: mem0 must NOT hold independent credentials.
        "llm":      _llm_config_from_router(app, cfg),
        "embedder": _embedder_config_from_vault(app, cfg),
        "history_db_path": str(root / "history.db"),
        "custom_fact_extraction_prompt": CAPYHOME_FACT_EXTRACTION_PROMPT,
    })
```

`_llm_config_from_router` resolves through
`ModelRouter().resolve("memory_extractor", …)` — the same path
`updater.py:236` uses today, so `config.yaml` stays the single source of truth
(**C-3**). `_embedder_config_from_vault` reuses
`knowledge_vault.vector_embedding_model` and its dimensions (**C-4**).

### `ingest` — one call, both scopes

```python
def ingest(self, messages, *, scopes, source=None) -> bool:
    payload = [{"role": _role(m), "content": _text(m)} for m in messages]
    if not payload:
        return False
    self._client.add(
        messages=payload,
        user_id=scopes.user_id,
        run_id=scopes.run_id,          # workspace scope, one call (fixes Q-2)
        agent_id=scopes.agent_id,
        metadata={"source": source or "unknown"},
    )
    return True
```

The confidence gate (`fact_confidence_threshold`) is applied **in the adapter**
after mem0 returns its extraction result, not inside the prompt — mem0 owns the
ADD/UPDATE/DELETE decision, we own the storage policy.

### `search` — mem0 + decay re-rank

```python
def search(self, query, *, scopes, top_k):
    raw = self._client.search(query=query, user_id=scopes.user_id,
                              run_id=scopes.run_id, limit=top_k * 2)
    out = []
    for m in raw.get("results", raw if isinstance(raw, list) else []):
        fact = to_fact(m)                                   # projection.py
        decay = _decay_multiplier(fact["createdAt"],        # ported verbatim
                                  half_life_days=cfg.decay_half_life_days,
                                  enabled=cfg.decay_enabled)
        fact["score"] = float(m.get("score", 0.0)) * decay  # G-4
        out.append(fact)
    out.sort(key=lambda f: f["score"], reverse=True)
    return out[:top_k]
```

`_decay_multiplier` is lifted unchanged from `vector_store.py:56` — 10 lines,
already config-driven. It is the only thing worth keeping from that file.

### Projection

```python
# src/agents/memory/projection.py
def to_fact(m: dict) -> dict:
    meta = m.get("metadata") or {}
    return {
        "id":         m["id"],
        "content":    m["memory"],
        "category":   meta.get("category", "context"),
        "confidence": float(meta.get("confidence", 0.8)),
        "createdAt":  m.get("created_at", ""),
        "source":     m.get("run_id") or meta.get("source", "unknown"),
    }

def to_memory_response(facts, rules, narrative, *, scope, scope_id) -> dict:
    """Assemble the MemoryResponse contract (invariant I-1)."""
```

`to_memory_response` is the single place invariant I-1 is enforced. Every REST
endpoint returns its output.

---

## `BehaviorRuleStore`

~60 lines, no LLM, no vectors. Backs `add_/update_/delete_behavior_rule` and the
`/memory` command.

```python
# src/agents/memory/rules.py — {base_dir}/memory/rules.json
{"version": 1, "rules": [
  {"id": "rule_…", "instruction": "…", "active": true,
   "scope": "workspace", "scopeId": "…", "source": "…",
   "createdAt": "…", "updatedAt": "…"}
]}
```

Same atomic `.tmp` + `replace()` write as `persist_memory_data` uses today. Rules
are always injected in full (≤10 active) and never retrieved by relevance.

⚠️ Rules are **completely unexercised today** — 0 in global, 0 across all 23
thread files ([06](./06-migration-plan.md#data-backfill)). The backfill will not
catch a regression here. Write a direct unit test.

---

## `NarrativeProfile` — gated on O-1

Only build this if **O-1** resolves to "keep". Evidence for keeping is in
[R-3](./issues_memory.md#r-3-the-narrative-sections-are-unreachable-on-a-normal-turn):
the sections are already invisible on normal turns, so moving them off the hot
path costs nothing and saves an LLM call's worth of prompt.

```python
# src/agents/memory/narrative.py → {base_dir}/memory/narrative.json
def regenerate(scopes) -> dict:
    """One LLM pass over m.get_all() → the six sections.
    Triggered by update-count or schedule — NOT per turn."""
```

Carries the **topOfMind hygiene** patch (G-6) that today lives inside
`MEMORY_UPDATE_PROMPT`. Consumers to repoint: settings UI (via `get_profile`),
the query-less injection branch, and the vault lint judge (`_lint.py:42`).

---

## Custom extraction prompt

`src/agents/memory/extraction_prompt.py`. Must carry forward, from
[02](./02-extraction-and-queue.md#the-extraction-prompt):

1. **Category enum** — `preference | knowledge | context | behavior | goal`,
   emitted into `metadata["category"]`. Define the enum **once** here and have
   the vault lint judge import it (fixes **C-2**).
2. **Confidence bands** — 0.9+ explicit / 0.7–0.8 implied / 0.5–0.6 inferred,
   emitted into `metadata["confidence"]` (**G-3**).
3. **Upload suppression** — defence in depth; the three pre-extraction code
   layers still run and are unchanged.
4. **Multilingual preservation** — mem0's default prompt is English-centric and
   will otherwise normalise proper nouns.

Not carried: topOfMind hygiene (moves to the narrative regenerator) and the
section length guidance (no sections here).

---

## Config schema

```python
# src/config/memory_config.py
class Mem0Config(BaseModel):
    vector_provider: str = "qdrant"           # O-2
    collection_name:  str = "capyhome_memory"
    storage_dir:      str = "memory/mem0"     # relative → Paths.base_dir (D-2)
    embedding_model:  str = ""                # "" → inherit knowledge_vault
    embedding_dims:   int = 256

class MemoryConfig(BaseModel):
    backend: Literal["legacy", "mem0", "dual"] = "legacy"
    mem0: Mem0Config = Field(default_factory=Mem0Config)
    narrative_enabled: bool = True            # O-1
    narrative_refresh_every_n_updates: int = 20
    # ... existing fields unchanged ...
    # REMOVED: decay_archive_threshold (H-2, never read)
```

```yaml
# config.yaml — memory: block
memory:
  backend: legacy          # legacy | dual | mem0
  mem0:
    vector_provider: qdrant
    storage_dir: memory/mem0
    embedding_model: ""     # inherit knowledge_vault.vector_embedding_model
    embedding_dims: 256
  narrative_enabled: true
  narrative_refresh_every_n_updates: 20
# DELETE the memory_versioning: block (D-3)
```

Path resolution follows the existing `storage_path` rule exactly: absolute paths
as-is, relative resolved against `Paths.base_dir` (**not** the backend CWD). Add
`Paths.mem0_dir` beside `Paths.memory_file`.

---

## Per-file wiring

| File | Change |
|------|--------|
| `middlewares/memory_middleware.py` | **None.** Gates + hygiene are storage-agnostic. |
| `memory/summarization_hook.py` | **None.** |
| `memory/queue.py` | `_update_context_memory` → one `backend.ingest()` (Q-2). Per-thread timers (Q-1). `logger` not `print` (U-3). |
| `memory/updater.py` | Delete `MemoryUpdater`, `MEMORY_UPDATE_PROMPT` use, `_apply_updates`, `_memory_cache`. Keep `_strip_upload_mentions_from_memory`. CRUD → backend. |
| `memory/prompt.py` | Keep `format_memory_for_injection`, `_merge_memory_scopes`, `format_conversation_for_update`, `_count_tokens`. **Delete** `MEMORY_UPDATE_PROMPT`, `FACT_EXTRACTION_PROMPT`, `_is_relevant_injection_fact`, `_lexical_relevance` (G-5). |
| `work_agent/prompt.py` + `plan_agent/prompt.py` | Consolidate the duplicated `_get_memory_context` into one shared helper **first** (H-1), then retarget. |
| `tools/builtins/recall_tool.py` | `get_memory_vector_store().query()` → `backend.search()`. |
| `tools/internal_tools_{plan,work}.json` | `returns` shape + score-scale wording. **Keep the two framings diverged.** |
| `gateway/routers/memory.py` | Endpoints → backend. Delete `/redact`, `/versions`, `/versions/{id}` (D-3). Verify `def` vs `async def` (**I-2**). |
| `control_plane/vault_learning/_lint.py` | Repoint at `get_profile()`; import the category enum (C-2). |
| `client.py:679` | `get_memory()` → `backend.get_profile()`. |
| `config/memory_versioning_config.py` | **Delete** (D-3). |
| `pyproject.toml` | `+ mem0ai`, `+ qdrant-client`. |

Untouched: all of `frontend/` (invariant I-1), `compaction_archive.py`,
`scratchpad_task_memory_middleware.py`.

---

## Task ordering

Dependencies, not phases — several tracks run in parallel:

```
T0  consolidate _get_memory_context (H-1)  ─┐   ✅ DONE — memory/context.py
T1  print → logger (U-3)                   ─┤   ✅ DONE — 12 prints replaced
T2  delete dead code (H-2)                 ─┤   ✅ DONE
T3  pin mem0ai + qdrant, verify config keys ┘   ⏸️ blocked on O-2
            │
            ▼
T4  MemoryBackend protocol + MemoryScopes       ✅ DONE — memory/backend.py
            │
            ├──────────────┬──────────────┬─────────────────┐
            ▼              ▼              ▼                 ▼
T5 LegacyJsonBackend   T6 rules.py   T7 extraction    T8 eval set
   ✅ DONE                (rules)       prompt           (~20 threads,
            │                                             hand-labelled)
            ▼
T9  route all call sites through the protocol   ← Phase 1 exit  ✅ DONE
            │
            ▼
T10 Mem0Backend.ingest ──▶ T11 dual-mode write ──▶ T12 diff on eval set
            │
            ▼
T13 Mem0Backend.search + decay ──▶ T14 retune threshold ──▶ T15 shadow compare
            │                                                      │
            ▼                                                      ▼
T16 delete filter stack (G-5)                             ⟵ GO / NO-GO ⟶
            │
            ├──▶ T17 narrative.py (only if O-1 = keep) ──▶ T18 repoint vault judge
            ├──▶ T19 catalog `returns` update
            └──▶ T20 subagent boundary decision (M-1…M-4)
                        │
                        ▼
T21 backfill (dry-run → reconcile → apply)
T22 flip backend: mem0 · soak
T23 delete legacy code + files   ← the one-way door
```

**T8 is the gate on everything.** Without a fixed eval set, T12 and T15 have no
pass criterion and the go/no-go at T15 is a vibe.

---

## Testing

| Test | Fate | Note |
|------|------|------|
| `test_memory_upload_filtering.py` | ✅ unchanged | pure text hygiene; must stay green throughout |
| `test_memory_middleware_runtime_flags.py` | ✅ unchanged | `add_to_memory` gating |
| `test_memory_update_queue.py` | ⚠️ rewrite | scope-lock model changes with Q-1/Q-2 |
| `test_prompt_memory_context.py` | ⚠️ rewrite | injection + prompt-cache assertions |
| `test_memory_vector_store_consistency.py` | 🔄 replace | index consistency becomes mem0's problem |
| `test_memory_versioning_store.py` | ❌ delete | D-3 |
| `test_tool_schema_sync.py` | ➕ extend | assert `recall`'s `returns` keys match the projection — it does not check this today |

New tests required:

- `test_memory_projection.py` — round-trip mem0 → Fact → `MemoryResponse`; assert
  `MemoryResponse(**backend.get_profile(...))` validates. **This is the guard on
  invariant I-1** and the reason the frontend needs no changes.
- `test_behavior_rule_store.py` — rules are unexercised in production data.
- `test_memory_backend_parity.py` — same input through `LegacyJsonBackend` and
  `Mem0Backend`, assert both satisfy the Protocol and produce valid projections.

**The parity harness exists**: `backend/scripts/e2e_memory_simulation.py`.
61 assertions across the full path — middleware → queue → backend → disk +
SQLite, injection branching, `recall`, the `MemoryResponse` projection, all 15
REST routes, the capture gates, the `/memory` command, and the pre-compaction
flush hook. Only the extraction LLM is stubbed.

Run it under each backend and diff:

```bash
CAPYBARA_HOME=$(mktemp -d) REAL_MEMORY_MD5=$(md5 -q .capyhome/memory.json) \
  PYTHONPATH=. uv run python scripts/e2e_memory_simulation.py
```

It refuses to run unless `CAPYBARA_HOME` points at a temp directory (it destroys
its own home) and asserts the real `memory.json` is byte-unchanged.

**Section 9 asserts defect U-1 and is expected to FAIL under mem0.** It replays
an identical conversation and requires the fact count to double. When that
assertion flips to red, dedup is working — that inversion is the single clearest
signal the migration achieved its main goal. Invert the assertion at cutover
rather than deleting it.

Fixtures: per-`tmp_path` backend instances via `reset_memory_backend()`.
Embedded Qdrant takes an **exclusive directory lock** (**O-2**), so a
module-scoped shared instance will fail under parallel test runs — mirror the
existing `MemoryVectorStore(tmp_path / "memory.db")` fixture pattern.

---

## Definition of done

- [ ] `memory.backend: mem0`, legacy files deleted, `memory.db` gone
- [ ] `MemoryResponse` byte-compatible — frontend diff is **zero lines**
- [ ] One extraction LLM call per turn (was two) — Q-2 verified in logs
- [ ] `recall` returns semantically-matched results on the eval set; the
      hardcoded city list is deleted
- [ ] Vault lint judge input unchanged (or O-1 explicitly signed off)
- [ ] Both tool catalogs' `returns` match reality; drift test asserts it
- [ ] M-1…M-4 subagent boundary explicitly decided and recorded — a decision, not
      a default
- [ ] `docs/key-features/12-persistent-memory.md` corrected: L48 and L50
      discrepancies fixed, privacy framing (L36–L44, L59) dropped per D-3
- [ ] These migration docs updated to describe the built system, or archived

---

## Implementation notes (from the landed work)

Deviations from the design above, discovered while building it:

1. **`MemoryScopes` carries a `scope`/`scope_id` discriminator**, not just
   `user_id`/`run_id`. CRUD operations are genuinely single-scope (the REST API
   asks for one scope at a time; legacy stores them in separate files), while
   retrieval spans both. `include_global` expresses that widening, and
   `retrieval_pairs()` / `run_id` give the legacy and mem0 views respectively.
2. **Per-scope write locks moved from `MemoryUpdateQueue` into the backend.**
   Serializing writers is a storage invariant, not a queue concern, and the mem0
   backend will want its own policy. `MemoryUpdateQueue._scope_locks` is gone;
   the queue now owns only batching and debounce.
3. **`get_memory_backend()` needs double-checked locking.** Backends hold those
   per-scope locks, so a racing construction hands threads private instances and
   silently defeats write serialization. `test_memory_update_queue.py` caught
   this — it was green only after the lock was added.
4. **`_scope_args` was kept alongside the new `_scopes` helper** in the router.
   It still performs the 400-on-missing-workspace_id validation, so `_scopes`
   delegates to it rather than duplicating the check.
