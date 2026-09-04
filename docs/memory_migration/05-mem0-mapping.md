# 05 — mem0 Mapping

_What mem0 replaces, what it lacks, and what has to be rebuilt._

> **Verify against the pinned version.** API and config-key details below reflect
> mem0 OSS as understood at authoring time. Confirm every key against the
> `mem0ai` version actually pinned in `backend/pyproject.toml` before
> implementing — treat the snippets as shape, not gospel.

---

## Concept mapping

| CapyHome | mem0 | Notes |
|----------|------|-------|
| `scope="global"` | `user_id="global"` | One profile per install; CapyHome is single-user |
| `scope="workspace"`, `workspace_id=thread_id` | `run_id=<thread_id>` | Preserves today's per-conversation semantics honestly (see **O-3**) |
| per-agent (`agent_name`) | `agent_id=<agent_name>` | Rarely used; maps directly |
| `fact.id` | mem0 `memory_id` | UUID; CapyHome's `fact_{hex8}` ids are not portable |
| `fact.content` | `memory` | 1:1 |
| `fact.category` | `metadata["category"]` | mem0 has no native category |
| `fact.confidence` | `metadata["confidence"]` | **Not native** — requires a custom extraction prompt |
| `fact.createdAt` | `created_at` | Native |
| `fact.source` (thread id) | `run_id` + `metadata["source"]` | `run_id` makes forget-thread a first-class operation |
| `behaviorRules[]` | — | **No equivalent.** Keep local — see below |
| `user.*` / `history.*` narrative | — | **No equivalent.** See **O-1** |

---

## What mem0 replaces cleanly

| Current | mem0 | Gain |
|---------|------|------|
| `MEMORY_UPDATE_PROMPT` + `_apply_updates` | `m.add(messages, …)` with its `ADD / UPDATE / DELETE / NOOP` loop | **Fixes defect U-1.** Dedup and contradiction resolution become the store's job instead of a hope pinned on one LLM call reading a 36 KB blob |
| `MemoryVectorStore` (228 LoC, lexical) | mem0 vector store | Real semantic retrieval; deletes the whole lexical-workaround stack |
| `forget_thread_facts` (`fact.source == thread_id` scan) | `m.delete_all(run_id=thread_id)` | First-class, indexed |
| `clear_memory` + `delete_scope` | `m.delete_all(user_id=…)` | 1:1 |
| `upsert_fact` / `delete_fact` | `m.update()` / `m.delete()` | 1:1 |
| Version store (dropped under D-3) | `m.history(memory_id)` | Per-memory append-only change log covers residual audit need |
| `max_facts` truncation (defect U-2) | Dedup keeps the set bounded naturally | The confidence-only sort disappears with the problem |

---

## What mem0 does NOT have

### G-1: Narrative profile sections — **the hard gap**

mem0 stores atomic memory strings. It has no notion of a maintained multi-
paragraph narrative like `topOfMind` or `history.recentMonths`. Feeding them in
as memories would get them shredded by the dedup loop.

Three live consumers:

1. Settings UI (`memory-settings-page.tsx`) renders all six sections
2. Query-less injection path (`format_memory_for_injection`, `include_broad_context`)
3. **Vault lint judge** (`_lint.py:42`) — a hard read of `user.*` and `history.*`

**Recommendation (O-1):** keep them, but *outside* mem0. Regenerate periodically
from `m.get_all()` into a generated profile artifact, in the manner of the
existing `USER.md` (`Paths.user_md_file`). This preserves all three consumers,
keeps a human-readable profile, and removes narrative maintenance from the
per-turn hot path — where today it costs an LLM call for six paragraphs that a
normal turn never even reads (see [03](./03-retrieval-and-injection.md#format_memory_for_injection)).

### G-2: Behavior rules

Imperative, always-injected, user-authored instructions with an `active` toggle.
No mem0 equivalent.

**Recommendation: do not put them in mem0.** They are deterministic and must be
injected in full every turn — subjecting them to semantic recall would make them
probabilistic, which is the opposite of what a rule is. Keep a small local
`rules.json` under `.capyhome/memory/`, wrapping the existing `add_` /
`update_` / `delete_behavior_rule` functions. ~60 lines, no LLM involvement.

### G-3: Confidence scores

mem0 returns a similarity `score` on search, not an extraction confidence.
Dependents: the `0.7` storage gate, the retrieval formula's
`0.35 · confidence · decay` term, the UI confidence bar, and the vault judge's
`≥ 0.6` filter.

**Recommendation:** supply a `custom_fact_extraction_prompt` that emits
`confidence` and `category` into metadata, carrying over the current prompt's
confidence bands and category enum. Apply the `0.7` gate in the adapter before
calling `add()`. If mem0's extraction contract makes metadata-per-fact
impractical in the pinned version, fall back to a fixed default confidence and
mark the UI bar as vestigial — but try metadata first, because the vault judge
filter depends on it.

### G-3b: The `recall` tool contract

Both tool catalogs publish the `recall` return shape to the model as
`{query, results: [{id, scope, content, category, confidence, score, source}]}`.
Two fields move under mem0: `confidence` (depends on **G-3** landing) and `score`
(embedding similarity, a **different scale** from today's
`0.65·lexical + 0.35·conf·decay`). The two sentinel strings
`'No relevant memory found.'` and `'Memory scopes are disabled.'` are documented
contract and must be returned verbatim.

Full catalog surface, including the six other sites that reference `recall`, is in
[04](./04-surface-inventory.md#tool-catalog-surface-the-pull-channel).

### G-4: Temporal decay

mem0 ranks by embedding similarity. The 60-day half-life has no equivalent.

**Recommendation:** re-rank client-side over mem0 results using `created_at`.
Port `_decay_multiplier` (`vector_store.py:56`) verbatim — it is 10 lines and
already config-driven via `decay_half_life_days`.

### G-5: Category gating and injection thresholds

`_is_relevant_injection_fact` (`prompt.py:237`) — the `context`-category penalty,
the dual lexical floors, and the hardcoded city-name escape hatch.

**Recommendation: delete most of it rather than port it.** That stack exists
*because* retrieval is lexical — the city list is literally a manual synonym
table. With embeddings, keep only:

- a similarity-score floor (retune `injection_relevance_threshold`; today's 0.5
  is calibrated against a different scale and will not transfer)
- optionally the `context`-category penalty, which encodes a genuine product
  judgment (transient background facts are less reusable), not a scorer defect

### G-6: The three behavioural prompt patches

From [02](./02-extraction-and-queue.md#three-hand-tuned-behavioural-patches) —
these were each earned from a production failure and must not be lost:

| Patch | New home |
|-------|----------|
| **topOfMind hygiene** (drop completed one-off requests) | Moves to the narrative regenerator (G-1). Does not apply to atomic facts |
| **Upload suppression** | Already enforced at three code layers *before* extraction ([02](./02-extraction-and-queue.md#message-hygiene)) — those survive untouched. Also restate it in the custom extraction prompt as defence in depth |
| **Multilingual preservation** | Restate in the custom extraction prompt. mem0's default prompt is English-centric and will otherwise normalise proper nouns |

### G-7: Whole-profile atomic writes

`persist_memory_data` writes the whole profile atomically via `.tmp` + `replace`.
mem0 writes per-memory. A crash mid-`add()` can leave a partially-applied batch.

**Assessment: acceptable.** Memory is advisory context, not a system of record,
and the current design already tolerates loss (silent `except` → return `False`).

---

## Dropped, not ported (decision D-3)

| Feature | Location |
|---------|----------|
| `redact_memory` + PII regexes + `/api/memory/redact` | `store.py:265-357` |
| Version store: `memory_versions/`, `parent_sha` chain, `latest.json` | `store.py:129-263` |
| `expected_sha` optimistic concurrency | `store.py:149-151` |
| `memory_versioning` config + its tests | `memory_versioning_config.py`, `test_memory_versioning_store.py` |

---

## Cost model

| | Today | After |
|---|---|---|
| Extraction LLM calls per turn | **2** (global + workspace) | **1** — one `add()` scoped with both `user_id` and `run_id` (fixes defect Q-2) |
| Extraction input size | Full profile JSON (36 KB and growing) + conversation | Conversation + mem0's retrieved-neighbours-only context |
| Retrieval | 0 LLM, full SQLite scan | 1 embedding call/query + ANN lookup |
| Narrative maintenance | Bundled into the extraction call | Separate periodic call (G-1) — off the hot path |
| New runtime deps | — | embedding model + vector store (**C-4**) |

The extraction-input reduction is the largest single saving: mem0 sends only
semantically-neighbouring memories for the update decision, not the entire
profile. That saving *grows* as the profile grows, whereas today's cost grows
with it.

---

## Indicative configuration

Paths follow decision **D-2** — everything under `backend/.capyhome/memory/`.

```python
config = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "collection_name": "capyhome_memory",
            "path": str(paths.base_dir / "memory" / "mem0" / "vector_store"),
            "on_disk": True,
        },
    },
    "llm": {
        "provider": "openai",              # OpenAI-compatible local endpoint
        "config": {
            "model": ModelRouter().resolve("memory_extractor"),   # C-3
            "openai_base_url": <same endpoint the vault uses>,
        },
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "model": <knowledge_vault.vector_embedding_model>,    # C-4
            "embedding_dims": 256,
        },
    },
    "history_db_path": str(paths.base_dir / "memory" / "mem0" / "history.db"),
    "custom_fact_extraction_prompt": CAPYHOME_FACT_EXTRACTION_PROMPT,  # G-3, G-6
}
```

Open items on this config: **O-2** (provider choice and the embedded-Qdrant
directory lock), **C-3** (routing both mem0's LLM and embedder through
`config.yaml` rather than letting mem0 hold independent credentials), and
**C-4** (embedding-endpoint contention with the vault).
