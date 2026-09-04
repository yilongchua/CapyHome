# 06 — Migration Plan

_Strangler-adapter cutover. Six phases, each independently shippable and
revertible._

---

## Target storage layout

Decision **D-2**: the mem0 store is self-hosted and lives under
`backend/.capyhome/memory/`, beside the legacy index it replaces.

```
backend/.capyhome/                              ← Paths.base_dir
├── memory.json                                 ← legacy global profile
│                                                  (kept: narrative source, O-1)
├── memory/
│   ├── memory.db                               ← legacy lexical index
│   │                                              DELETED at end of Phase 5
│   ├── rules.json                              ← NEW: behavior rules (G-2)
│   └── mem0/                                   ← NEW: the mem0 store
│       ├── vector_store/                       ← Qdrant embedded, on-disk (O-2)
│       │   └── collection/capyhome_memory/…
│       └── history.db                          ← mem0 append-only change log
├── agents/<name>/memory.json                   ← legacy per-agent profiles
└── threads/<id>/memory.json                    ← legacy per-thread profiles
                                                   DELETED at end of Phase 5
```

Resolution rule: follow the existing `storage_path` convention exactly —
absolute paths used as-is, relative paths resolved against `Paths.base_dir`
(**not** the backend CWD). Add `Paths.mem0_dir` alongside `Paths.memory_file`.

`.gitignore`: `.capyhome/` is already ignored; confirm `mem0/` inherits it and
that `vector_store/` never lands in a commit.

---

## Adapter design

The whole migration hangs on one idea:

> **`MemoryResponse` is an API projection, not a storage format.**

Preserve the JSON schema at the API boundary; swap everything behind it. That is
what keeps the ~550 LoC of frontend at zero change.

```
       ┌──────────── unchanged ────────────┐
       │ MemoryMiddleware · flush hook     │
       │ /memory command · REST endpoints  │
       │ frontend (api/hooks/types/UI)     │
       └─────────────────┬─────────────────┘
                         │
       ┌─────────────────▼─────────────────┐
       │  src/agents/memory/backend.py     │   NEW — the seam
       │                                   │
       │  MemoryBackend (Protocol)         │
       │    ingest(messages, scopes) -> bool
       │    search(query, scopes, k)  -> list[Fact]
       │    get_profile(scope)        -> MemoryResponse-shaped dict
       │    upsert / delete / delete_scope / forget_run
       └────┬──────────────────────────┬───┘
            │                          │
   LegacyJsonBackend            Mem0Backend            NEW
   (wraps today's code)         ├─ mem0.Memory
                                ├─ BehaviorRuleStore   (rules.json, G-2)
                                └─ NarrativeProfile    (regenerated, G-1/O-1)
```

Selected by `memory.backend: legacy | mem0 | dual` in `config.yaml`. `dual`
exists only for Phase 3.

### Fact to mem0 projection

```python
# mem0 memory  →  CapyHome Fact
{
  "id":         m["id"],
  "content":    m["memory"],
  "category":   m.get("metadata", {}).get("category", "context"),
  "confidence": m.get("metadata", {}).get("confidence", 0.8),
  "createdAt":  m.get("created_at", ""),
  "source":     m.get("run_id") or m.get("metadata", {}).get("source", "unknown"),
}
```

`get_profile()` assembles `MemoryResponse` from: mem0 `get_all(scope)` → `facts`,
`rules.json` → `behaviorRules`, narrative artifact → `user` / `history`.

---

## Phases

### Phase 0 — Preparation *(no behaviour change)*

- [ ] Pin `mem0ai` + `qdrant-client` in `backend/pyproject.toml`; verify the
      config-key shapes in [05](./05-mem0-mapping.md#indicative-configuration)
      against the pinned version
- [ ] Consolidate the duplicated `_get_memory_context` (R-1/R-2) into one shared
      helper — **do this first**, or every later change lands twice
- [ ] Delete dead code: `FACT_EXTRACTION_PROMPT` (`prompt.py:127`, no call site)
      and `decay_archive_threshold` (never read)
- [ ] Replace `print()` with `logger` in `updater.py` / `queue.py` (**C-5**) so
      the migration is observable
- [ ] Add `Paths.mem0_dir`; extend `MemoryConfig` with the `mem0` sub-block
- [ ] Land a fixed extraction-quality eval set: ~20 real conversations from
      `.capyhome/threads/` with hand-labelled expected facts. **Without this
      there is no way to tell whether mem0 extraction is better or worse**

**Exit:** test suite green, zero behaviour change.

### Phase 1 — Backend protocol + legacy adapter

- [ ] Add `src/agents/memory/backend.py` with the `MemoryBackend` Protocol
- [ ] Implement `LegacyJsonBackend` wrapping today's `updater` / `vector_store`
- [ ] Route `MemoryUpdater.update_memory`, `format_memory_for_injection`, the
      `recall` tool and all 13 endpoints through the Protocol
- [ ] `memory.backend: legacy` default

**Exit:** every existing test passes unmodified against `LegacyJsonBackend`.
This phase is pure indirection and is the natural revert point for everything
that follows.

### Phase 2 — mem0 backend (write path)

- [ ] `Mem0Backend.ingest()` — single `m.add()` carrying **both** `user_id` and
      `run_id` (fixes defect Q-2: 2 LLM calls → 1)
- [ ] `CAPYHOME_FACT_EXTRACTION_PROMPT` — port the category enum, confidence
      bands, upload suppression and multilingual preservation (**G-3, G-6**)
- [ ] `BehaviorRuleStore` over `rules.json` (**G-2**) — retarget
      `add_` / `update_` / `delete_behavior_rule` and the `/memory` command
- [ ] Client singleton with a test-reset hook, mirroring
      `get_memory_vector_store()` (**C-2**); route mem0's LLM and embedder
      through `ModelRouter` / app config (**C-3**)
- [ ] Confirm every mem0 call runs on a background thread, never in an
      `async def` router (**C-1**)
- [ ] **Decide the subagent memory boundary** (M-1…M-4 in
      [issues_memory.md](./issues_memory.md)) — scope propagation already works,
      but subagents read-without-writing and only 2 of 9 can call `recall`

**Exit:** `dual` mode writes to both stores; diff the resulting fact sets on the
Phase-0 eval set.

### Phase 3 — mem0 backend (read path) + shadow comparison

- [ ] `Mem0Backend.search()` — mem0 `search()` + client-side decay re-rank (**G-4**)
- [ ] Retune `injection_relevance_threshold` against embedding-similarity scale.
      **Today's 0.5 is calibrated for token overlap and will not transfer**
- [ ] Delete the lexical-workaround stack (**G-5**): dual lexical floors, the
      hardcoded city list. Retain only the score floor and (optionally) the
      `context`-category penalty
- [ ] Shadow mode: serve injection from legacy, log mem0's results alongside,
      compare over real traffic
- [ ] **Update the `recall` entry in BOTH tool catalogs** — `returns` shape and
      score-scale wording, keeping the plan/work framings diverged
      ([04](./04-surface-inventory.md#tool-catalog-surface-the-pull-channel))

**Exit:** mem0 retrieval matches or beats legacy on the eval set. **This is the
go/no-go gate for the whole migration.**

### Phase 4 — Narrative profile *(gated on **O-1**)*

- [ ] `NarrativeProfile` — regenerate the six sections from `m.get_all()` on a
      schedule or an update-count trigger, **off the per-turn hot path**
- [ ] Port the topOfMind hygiene patch into this prompt (**G-6**)
- [ ] Repoint the vault lint judge (`_lint.py:42`) at it (**S-2**)
- [ ] Verify `memory-settings-page.tsx` still renders all six sections

**Exit:** vault lint judge output unchanged; settings UI unchanged.

### Phase 5 — Cutover

- [ ] **Backfill** (script below) — 83 global facts + 23 thread profiles
- [ ] Flip `memory.backend: mem0`
- [ ] Soak. Keep legacy files on disk, read-only
- [ ] Delete `vector_store.py`, `store.py`, `memory_versioning_config.py`,
      `MEMORY_UPDATE_PROMPT`, the redact endpoint and the version endpoints
      (**D-3**)
- [ ] Delete `.capyhome/memory/memory.db` and the per-thread `memory.json` files
- [ ] Update tests per [04](./04-surface-inventory.md#tests)
- [ ] Rewrite [12-persistent-memory.md](../key-features/12-persistent-memory.md):
      correct the two discrepancies (L48, L50) and drop the privacy framing
      (L36–L44, L59) per **D-3**

---

## Data backfill

Source inventory today:

Measured on this working copy, 2026-09-03:

| Source | Volume | Destination |
|--------|--------|-------------|
| `.capyhome/memory.json` → `facts[]` | **83 facts** | `m.add(..., user_id="global")` with `infer=False` |
| `.capyhome/threads/*/memory.json` → `facts[]` | **79 facts across 23 files** | `m.add(..., user_id="global", run_id=<thread_id>)`, `infer=False` |
| `behaviorRules[]` | **0** — none in global, none across all 23 threads | `rules.json` — straight copy. The feature is shipped but unused; verify it still works post-migration rather than trusting the backfill to exercise it |
| `user.*` / `history.*` narrative | 6 sections × 24 profiles | Narrative artifact (**O-1**) — **not** into mem0 |
| `agents/<name>/memory.json` | **0 files** — no custom agents have memory yet | `agent_id=<name>`; implement the path, but there is nothing to backfill |

Total to migrate: **162 facts**. Small enough that the backfill can run
synchronously and be eyeballed in full.

**Use `infer=False`.** These facts were already LLM-extracted; re-inferring would
re-summarise and lose the original wording. Carry `category`, `confidence`,
`createdAt` and `source` through as metadata.

```python
# scripts/migrate_memory_to_mem0.py  — idempotent, dry-run by default
for fact in legacy["facts"]:
    m.add(
        messages=[{"role": "user", "content": fact["content"]}],
        user_id="global",
        run_id=fact.get("source") if scope == "workspace" else None,
        metadata={
            "category":     fact.get("category", "context"),
            "confidence":   fact.get("confidence", 0.8),
            "legacy_id":    fact["id"],          # for the reconciliation report
            "source":       fact.get("source", "unknown"),
            "migrated_at":  now,
        },
        infer=False,
    )
```

Requirements: `--dry-run` default; idempotent via `metadata["legacy_id"]`;
emits a reconciliation report (in → stored → deduped-away); **never deletes
legacy files** — Phase 5 does that, manually, after soak.

Expect the stored count to be **lower** than 83. mem0's dedup will collapse
near-duplicates accumulated by defect U-1. That is the migration working, not
failing — but read the report to confirm it is collapsing duplicates rather than
distinct facts.

---

## Rollback

| Phase | Rollback |
|-------|----------|
| 0–1 | Standard revert. No data written. |
| 2–4 | Flip `memory.backend: legacy`. Legacy files remain authoritative and current — `dual` mode kept writing them. |
| 5 (pre-delete) | Flip back to `legacy`. Legacy files are stale by however long the soak ran; facts extracted during soak exist only in mem0. |
| 5 (post-delete) | No rollback. Do not delete legacy files until mem0 has been authoritative through a full soak. |

**The one-way door is deleting the legacy files.** Everything before it is a
config flip.

---

## Risk register

| # | Risk | Mitigation |
|---|------|------------|
| RI-1 | mem0 extraction is *worse* than the hand-tuned prompt on CapyHome's mixed domains (the product spans code, law/admin, Excel, food, local events, shopping) | Phase-0 eval set + Phase-3 go/no-go gate. `custom_fact_extraction_prompt` carries the tuning over |
| RI-2 | Embedding-endpoint contention with the knowledge vault (**C-4**) | Measure during Phase 3 soak. Consider a smaller embedding model for memory, or a separate endpoint |
| RI-3 | Qdrant embedded directory lock breaks concurrent test runs (**O-2**) | Per-`tmp_path` instances in tests, mirroring the existing `MemoryVectorStore(tmp_path/…)` fixture pattern |
| RI-4 | Sync mem0 call from an `async def` router wedges the gateway (**C-1**) | Known repo failure mode. Audit every endpoint touched; keep calls on background threads |
| RI-5 | Dedup collapses facts that were genuinely distinct | Reconciliation report on the backfill; keep legacy `memory.json` for comparison |
| RI-6 | Narrative sections silently regress the vault lint judge (**S-2**) | Phase 4 exit criterion is byte-comparable judge input |
| RI-7 | Retrieval threshold left at 0.5 on a different score scale silently injects everything, or nothing | Explicit retune task in Phase 3; assert on injected-fact counts in tests |
