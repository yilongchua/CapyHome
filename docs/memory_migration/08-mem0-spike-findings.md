# 08 — mem0 Spike Findings

_Run 2026-09-03 against the live 84-fact global memory (read-only) and the local
LM Studio embedding endpoint. `mem0ai 2.0.20`, `qdrant-client 1.19.0`, installed
into a throwaway venv — the project's `pyproject.toml` / `uv.lock` were **not**
touched. Real `memory.json` verified byte-unchanged by md5 before and after._

**Verdict: proceed, with three corrections to the plan and one blocker that mem0
does not solve.**

---

## O-2 answered: Qdrant embedded, plus extras

| Question | Result |
|----------|--------|
| Does embedded Qdrant + the local embedder initialise? | ✅ Yes, 5.5–6.2 s cold start |
| Does the store hold an exclusive directory lock? | ✅ **Yes** — a second client on the same path raises `RuntimeError` |
| Backfill throughput | 84 facts in 8.8 s (p50 41 ms/fact) → **all 162 facts in ~17 s** |
| Query latency | p50 34 ms, max 40 ms |
| Store footprint | 84 memories → 909 KB (`storage.sqlite`, `history.db`, `meta.json`, `.lock`) |

The lock confirms the design already in place: **one shared singleton** (which
`get_memory_backend()` now enforces with double-checked locking) and
**per-`tmp_path` instances in tests**.

Query cost is negligible in context: retrieval runs **once per run**, not per
model call ([R-2](./issues_memory.md#r-2-memory-is-frozen-for-the-whole-run)), so
~35 ms is the entire added latency per conversation.

### `mem0ai[extras]` is required, not optional

Without it mem0 logs `fastembed not installed - BM25 keyword search disabled`
and runs vector-only. Re-running the identical queries with extras installed
materially improved results:

| Query | vector-only | hybrid (BM25 + vector) |
|-------|-------------|------------------------|
| "vessel address validation work" | 0.656 / 0.635 / **0.630 wrong** | **0.806 / 0.776 / 0.769 — all three correct** |
| "what languages does the user speak" | 0.691 (correct, rank 1) | 0.767 (correct, rank 2) |

Hybrid keeps exact-term matching that pure embeddings lose (`company_imo`,
`17,842`) while adding the semantic reach the current lexical scorer lacks.
Pin `mem0ai[extras]`, not bare `mem0ai`.

---

## R-1 confirmed, but narrower than claimed

Semantic retrieval is a clear win **where the answer exists**:

```
Q: "what languages does the user speak"
  mem0    0.767  Prefers Dutch-language communication but accepts English translations
  current 0.532  User is in a workflow planning phase to create `workflow.json`...   <- noise
```

The current lexical scorer returns pure noise here — zero token overlap with
"languages", so confidence ordering wins and surfaces irrelevant workflow facts.

**But where the answer does *not* exist, semantic retrieval still returns
confident-looking results.** "where is the user based" returned three COSCO
workflow facts at 0.53–0.60. Cosine similarity always returns *something*; there
is no natural zero. So **[G-5](./05-mem0-mapping.md) was too optimistic** — you
cannot simply delete the relevance-filter stack. You replace hand-tuned lexical
hacks with a *calibrated* threshold, which is better, but it is still a floor
that has to be chosen deliberately.

### The threshold is calibratable — and today's value is wrong

Discrimination probe, answerable vs. unanswerable queries:

```
answerable   0.903  'Dutch language preference'
             0.905  'company_imo address columns'
             0.822  '17,842 row CSV'
unanswerable 0.516  "user's favourite football team"
             0.540  "the user's blood type"
             0.632  'preferred brand of shoes'

separation gap = +0.190
```

A single global threshold around **0.70–0.75** separates them cleanly.

**`injection_relevance_threshold` is currently `0.5`.** Carried over unchanged it
would admit every unanswerable query's noise. This quantifies risk **RI-7** —
retuning is mandatory at cutover, not a nice-to-have.

---

## The blocker mem0 does not solve

**26% of the live fact base is transient session state** — run artifacts,
filenames, row counts, phase status. Logged as
[U-4](./issues_memory.md#u-4-transient-session-state-is-stored-as-durable-memory).

This is why retrieval looks poor under *both* backends: those 22 facts are the
densest cluster in the store and dominate unrelated queries. mem0 dedupes
near-duplicates; it has no concept of durable vs. ephemeral.

The extraction prompt already contains the right rule — but scoped only to
`topOfMind`, never to `newFacts`. Fix it in the custom extraction prompt and
prune before backfill, or the migration imports 22 known-bad rows and embeds
them.

---

## API corrections to docs 05 and 07

`mem0ai 2.0.20` differs from what the strategy documents assumed. The docs
warned to verify against the pinned version; this is that verification.

| Documented | Actual in 2.0.20 |
|-----------|------------------|
| `search(query, user_id=…, run_id=…, limit=…)` | `search(query, *, top_k=20, filters=None, threshold=0.1, rerank=False, explain=False, …)` — ids go in `filters` |
| `get_all(user_id=…)` | `get_all(*, filters=None, top_k=20, show_expired=False)` |
| `custom_fact_extraction_prompt` in config | **Not a config key.** Top level is `custom_instructions`, `embedder`, `history_db_path`, `llm`, `reranker`, `vector_store`, `version`. Per-call override is `add(..., prompt=…)` |
| — | `reranker` is a first-class config block; `search(rerank=True)` exists. Relevant to [G-4](./05-mem0-mapping.md) |
| — | `add()` also takes `timestamp`, `expiration_date`, `memory_type`. **`expiration_date` is a candidate native fix for U-4** |

`add(messages, *, user_id, agent_id, run_id, metadata, infer)` matches the design
as written — the `MemoryScopes` → `(user_id, run_id, agent_id)` mapping holds.

Confirmed available: `add`, `search`, `get_all`, `get`, `update`, `delete`,
`delete_all`, `history`, `reset`. `delete_all(run_id=…)` backs the forget-thread
endpoint as planned.

---

## Recommended next steps

1. **Fix U-4 before migrating.** Extend the durability rule to fact extraction
   and prune the 22 transient rows. Improves retrieval on the *current* backend
   too, so it is not migration-contingent work.
2. **Pin `mem0ai[extras]`** and `qdrant-client`; use embedded Qdrant with a
   shared singleton.
3. **Build T8** and calibrate the threshold against it — expect ~0.70, not 0.5.
4. **Load a chat model in LM Studio** to finish the spike. Only embedding
   models are currently served on `:1234`, so extraction quality and the
   ADD/UPDATE/DELETE/NOOP dedup loop — the two questions that decide whether U-1
   is genuinely fixed — remain unmeasured.
