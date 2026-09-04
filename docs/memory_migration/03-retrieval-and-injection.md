# 03 — Retrieval & Injection (read path)

---

## The index that isn't a vector store

`backend/src/agents/memory/vector_store.py` — named `MemoryVectorStore`, stores
no vectors. `MemoryVectorStore.query` (L167):

```python
rows = SELECT … FROM memory_facts WHERE (scope=? AND scope_id=?) OR (…)   # full scan
for row in rows:
    lexical = _lexical_score(query, content)          # L43
    decay   = exp(-age_days / half_life_days)         # L56, half_life=60
    score   = 0.65 * lexical + 0.35 * confidence * decay
ranked.sort(desc); return ranked[:top_k]
```

`_lexical_score` is token-set overlap:

```python
overlap = |tokenize(query) ∩ tokenize(content)| / |tokenize(query)|
if query.lower() in content.lower(): overlap += 0.25
return min(1.0, overlap)
```

Tokens are `[a-zA-Z0-9_]{2,}`, lowercased, no stemming, no stopword removal at
this layer (the injection layer has its own stopword list — see below).

Properties that matter for the migration:

- **No semantic matching.** "car" never matches "vehicle". This is why the
  injection path needs a hardcoded list of city names to make location queries
  work (see `_is_relevant_injection_fact` below).
- **Full table scan per query**, scored in Python. Acceptable at 83 facts;
  it is the hard ceiling on the feature.
- **Confidence and decay are inseparable** — `0.35 * confidence * decay` means a
  perfectly-matching but old fact can be outranked by a mediocre fresh one.
- `decay_archive_threshold` (0.1) is configured but **never read** anywhere. Dead.

---

## Injection

### Call sites — duplicated verbatim

`_get_memory_context` exists **twice, identically**:

| File | Line | Consumer |
|------|------|----------|
| `backend/src/agents/work_agent/prompt.py` | 173 | `apply_prompt_template` L415 |
| `backend/src/agents/plan_agent/prompt.py` | 266 | `apply_prompt_template` L472 |

Both: read `thread_id` from `langgraph.config.get_config()["configurable"]`,
load global + workspace memory, derive `current_turn_text` from the first
non-empty of `current_turn_text` / `original_user_request` / `user_prompt`, call
`format_memory_for_injection`, wrap in `<memory>…</memory>`, and swallow every
exception with `logger.exception`.

Consolidate these into one shared helper during the migration — otherwise every
adapter change has to be made twice.

### `format_memory_for_injection`

`backend/src/agents/memory/prompt.py:264`. The function **branches hard** on
whether `current_turn_text` is non-empty:

```
has_relevance_query = bool(current_turn_text.strip())

if has_relevance_query:                       # ── normal turn
    facts   = vector_store.query(query, scopes=[workspace, global],
                                 top_k = recall_top_k * 2)          # = 10
    facts   = [f for f in facts if _is_relevant_injection_fact(f, …)]
    include_broad_context = False             # ← user/history sections OMITTED

else:                                         # ── no query available
    facts   = merged_memory["facts"] sorted by confidence DESC, [:10]
    include_broad_context = True              # ← user/history sections INCLUDED
```

Assembled sections, in order:

1. `User Context:` — Work / Personal / Current Focus — **only when no query**
2. `History:` — Recent / Earlier — **only when no query**
3. `Behavior Rules:` — active rules, max 10 — **always**
4. `Relevant Facts:` — `- [category] content`, max 15 lines — **always**

Then tiktoken (`cl100k_base`) counts the result and character-truncates to
`max_injection_tokens` (2000) at a 95 % margin, appending `"\n..."`.

**Consequence worth flagging:** on a normal turn the model never sees the
narrative profile — only rules plus ≤15 filtered facts. The elaborate
`workContext` / `topOfMind` / `history` machinery in the extraction prompt is
reachable only on turns with no derivable query text.

### `_is_relevant_injection_fact` — the filter stack

`prompt.py:237`. On top of the store's own score:

```python
category = fact["category"]
if category == "context":
    effective_threshold = max(threshold, 0.6)     # threshold default 0.5
    effective_lexical   = 0.25
else:
    effective_threshold = threshold
    effective_lexical   = 0.12

return score >= effective_threshold and (
    lexical >= effective_lexical
    or (location_sensitive_query and location_like_fact)
)
```

Where `_lexical_relevance` (L227) recomputes overlap with its own **stopword
list** (`a, an, and, are, for, from, help, home, how, is, me, my, of, or, the,
to, versus, vs, with`) — a second, differently-tuned lexical scorer alongside the
one in the store.

The escape hatch is hardcoded:

```python
location_sensitive_query = any(t in query for t in
    ("my city","where i live","location","rent","housing","relocat","move from","move to"))
location_like_fact = any(t in content for t in
    ("city","location","relocat","singapore","london","dubai","sydney","tasmania","hobart","based in","lives in"))
```

This entire stack is a **workaround for lexical retrieval**. `context` facts are
penalised because they are transient background; the lexical floor exists so
high-confidence unrelated facts do not leak in. With real embeddings most of it
should be deleted rather than ported — see [05](./05-mem0-mapping.md).

### `_merge_memory_scopes`

`prompt.py:176`. Workspace summaries override global per-section when non-empty;
workspace facts and rules are **prepended** to global ones. But when a query is
present the merged fact list is discarded — the SQLite query drives fact
selection, and merge only matters for rules and (query-less) narrative sections.

---

## Prompt-cache interaction

`apply_prompt_template` (`work_agent/prompt.py:386`):

```python
base_prompt = get_cached_prompt(build_fn=…, agent_name, subagent_enabled,
                                max_concurrent_subagents, available_skills,
                                progressive_skills)
return _inject_memory_context(base_prompt, _get_memory_context(…))
```

`_cache_key` (`prompt_cache.py:100`) does **not** include memory, and staleness
is checked against source-file mtimes + date. Memory is spliced afterwards at
`MEMORY_INJECTION_SENTINEL` by `_inject_memory_context` (`prompt.py:343`), which
also guards against double-injection by checking for an existing `<memory>` tag.

So the local prompt cache survives per-turn memory changes — **but** the memory
block sits inside the system prompt and changes every turn, so provider-side
prompt caching of the system block misses continuously. If prompt-cache hit rate
matters, the memory block wants to move to the end of the system prompt or into
a separate message.

---

## `recall` tool

`backend/src/tools/builtins/recall_tool.py:16`. Same query path, `top_k =
recall_top_k` (5), **no** `_is_relevant_injection_fact` filtering. Scopes:
workspace (if enabled and `thread_id` present) then global. Returns JSON
`{query, results:[{id, scope, content, category, confidence, score, source}]}`.

Exists as the documented mitigation for facts the injection scorer ranks too low.

---

## Other readers

| Consumer | Location | What it reads |
|----------|----------|---------------|
| SDK client | `backend/src/client.py:679` `get_memory()` | full global `memory.json` |
| Vault lint judge | `backend/src/control_plane/vault_learning/_lint.py:42` `_collect_user_context_for_judge` | `user.*` + `history.*` summaries, plus up to 8 facts with `confidence >= 0.6` and category in `{preference, goal, knowledge, context, interest}` |
| Settings UI | `frontend/.../memory-settings-page.tsx` | `GET /api/memory` for both scopes |

⚠ The vault lint judge filters on category `"interest"`, which **the extraction
prompt never produces** (its enum is `preference|knowledge|context|behavior|goal`).
Harmless, but indicative of drift between producer and consumer.

The vault judge is also the **hard dependency on the narrative sections** that
makes open decision **O-1** non-trivial.
