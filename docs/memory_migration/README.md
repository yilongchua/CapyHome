# Persistent Memory → mem0 Migration

_Analysis date: 2026-09-03 · Status: **Phase 0 + Phase 1 landed; mem0 backend not started**_

> **Implementation status**
> - ✅ **Phase 0** (T0–T2): shared `_get_memory_context`, `print`→`logger`, dead code removed
> - ✅ **Phase 1** (T4, T5, T9): `MemoryBackend` protocol + `LegacyJsonBackend`; all 16 call
>   sites routed through the seam. `memory.backend: legacy` is the default and behaviour
>   is unchanged (zero test regressions).
> - ✅ **E2E verified**: `backend/scripts/e2e_memory_simulation.py` — 61/61 assertions
>   green across the whole path (write, retrieval, `recall`, REST, gates, flush hook).
>   Doubles as the mem0 parity harness; see [07](./07-implementation-strategy.md#testing).
> - ⏸️ **Phase 2+** blocked on decisions **O-1**, **O-2**, and **M-1…M-4**.
>   `memory.backend: mem0|dual` currently raises `NotImplementedError` by design.

Working document set for replacing CapyHome's hand-rolled persistent-memory
subsystem (`src/agents/memory/`) with a **self-hosted mem0** store.

The marketing description of the current system lives in
[../key-features/12-persistent-memory.md](../key-features/12-persistent-memory.md).
That document is the intent statement; these documents are the code truth. Two
of its claims do not match the implementation — see
[01-current-architecture.md](./01-current-architecture.md#doc-vs-code-discrepancies).

---

## Documents

| # | Document | Contents |
|---|----------|----------|
| 01 | [Current architecture](./01-current-architecture.md) | Module inventory, storage layout, scope model, doc-vs-code discrepancies |
| 02 | [Extraction & queue](./02-extraction-and-queue.md) | Write path: the 3 capture triggers, debounce queue, extraction prompt, merge semantics |
| 03 | [Retrieval & injection](./03-retrieval-and-injection.md) | Read path: the SQLite index, scoring formula, injection formatter, `recall` tool |
| 04 | [Surface inventory](./04-surface-inventory.md) | Every call site, endpoint, frontend module and test that touches memory — the migration blast radius |
| 05 | [mem0 mapping](./05-mem0-mapping.md) | Feature-by-feature: what mem0 replaces, what it does not have, what must be rebuilt |
| 06 | [Migration plan](./06-migration-plan.md) | Target layout under `.capyhome/memory/`, adapter design, phased cutover, data backfill, rollback |
| 07 | [Implementation strategy](./07-implementation-strategy.md) | Module design, `MemoryBackend` protocol, config schema, per-file wiring, task DAG, test plan, definition of done |
| 08 | [mem0 spike findings](./08-mem0-spike-findings.md) | **O-2 answered.** Measured results against the live fact base: Qdrant lock, backfill/query cost, threshold calibration, API corrections to 05/07 |
| — | [Issue register](./issues_memory.md) | Every verified defect with a mem0 verdict (fixed / fix-during / decide / unrelated) |

---

## Executive summary

The current system works and is coherent, but it has three structural defects
that are cheaper to replace than to fix:

1. **No fact deduplication.** Every conversation *appends* facts. Contradiction
   resolution is delegated entirely to the extraction LLM emitting
   `factsToRemove` while reading an ever-growing JSON blob (36 KB / 83 facts
   today). This is the single strongest argument for the migration — mem0's
   `ADD / UPDATE / DELETE / NOOP` decision loop is built for exactly this.
2. **Retrieval is lexical, not semantic.** `MemoryVectorStore` contains no
   vectors. It full-table-scans and scores token-set overlap. The relevance
   thresholds and category gates in the injection path exist *because* the
   scorer is lexical; real embeddings make most of that machinery unnecessary.
3. **Two extraction LLM calls per turn.** `global` and `workspace` scopes are
   extracted independently over the same conversation.

The migration seams are good: **3 write entry points** funnelling into one
`update_memory()`, and **2 read functions plus 1 tool**. The
`MemoryResponse` API schema should be preserved as a *projection over mem0*,
not as a storage format — that alone keeps the entire frontend untouched.

End-to-end verification surfaced a fourth issue the migration does **not**
automatically fix: **subagents can read memory but never write it**, only 2 of 9
can call `recall` at all, and `knowledge-researcher` — the deep-research agent —
has `recall` explicitly denied while also receiving no memory injection. It runs
with zero memory context by either channel. See
[M-1…M-4](./issues_memory.md#the-subagent-memory-boundary); this is a decision the
migration forces, not a port.

---

## Decisions already taken

| # | Decision | Consequence |
|---|----------|-------------|
| **D-1** | **Self-hosted mem0 OSS.** No hosted Platform API. | No conversation content leaves the machine. Requires an in-process vector store and a local embedder. |
| **D-2** | **mem0 store lives under `backend/.capyhome/memory/`.** | Sits beside the legacy `memory.db` it replaces; inherits the existing `Paths.base_dir` convention. Layout in [06](./06-migration-plan.md#target-storage-layout). |
| **D-3** | **Privacy / PII is no longer a product driver.** | Drops three workstreams: PII-regex redaction, the hash-chained append-only version store, and the "a local JSON file you own" positioning. See [Dropped scope](#dropped-scope). |

## Open decisions

| # | Question | Recommendation |
|---|----------|----------------|
| **O-1** | Keep or drop the narrative profile sections (`user.workContext` / `personalContext` / `topOfMind`, `history.recentMonths` / `earlierContext` / `longTermBackground`)? mem0 has **no equivalent** — it stores atomic memory strings. | **Keep, but move them out of the memory store.** They have three live consumers (settings UI, no-query injection path, vault lint judge). Regenerate them periodically from mem0 results into a generated profile artifact, in the manner of the existing `USER.md` (`Paths.user_md_file`) — rather than trying to express a maintained narrative as mem0 memories, where dedup would shred them. |
| **O-2** | Local vector-store provider: Qdrant embedded, Chroma persistent, or FAISS? | **Qdrant embedded (on-disk `path=` mode)** behind the existing `get_memory_vector_store()`-style singleton. Caveat: embedded mode takes an exclusive lock on the storage directory, so only one process may open it — fine for the single-process backend, but tests need per-`tmp_path` instances. Chroma persistent is the fallback if multi-process access is ever required. |
| **O-3** | Does `workspace` scope stay per-conversation, or become a real project scope? | **Decide during the migration, not after.** Today `workspace_id == thread_id` (see [01](./01-current-architecture.md#scope-model)). Mapping it to mem0's `run_id` preserves current behaviour honestly; introducing a real project id is a product change. Do not carry the ambiguity into the new store. |

## Dropped scope

Consequences of **D-3**. These are deliberately *not* being ported:

- `redact_memory()` and the PII regexes (`EMAIL_RE` / `PHONE_RE` / `CARD_RE`) in
  `store.py` — plus the `POST /api/memory/redact` endpoint.
- The append-only version store (`memory_versions/`, `parent_sha` hash chain,
  `expected_sha` optimistic concurrency). It is already **disabled by default**
  (`memory_versioning.enabled: false`), so nothing depends on it in practice.
  mem0's per-memory `history()` covers the residual audit need.
- The "local JSON file you own / audit-grade accountability" framing in
  [12-persistent-memory.md](../key-features/12-persistent-memory.md) L36–L44 and
  L59 needs rewriting once the store becomes a vector DB.

Deleting the redaction path removes ~120 lines from `store.py` and one endpoint.
