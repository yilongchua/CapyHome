# 02 — Extraction & Queue (write path)

_Memory management: how a conversation becomes stored facts._

---

## The three capture triggers

All three converge on `MemoryUpdateQueue`. There are no other write entry points.

### A. Normal turn end — `MemoryMiddleware.after_agent`

`backend/src/agents/middlewares/memory_middleware.py:162`

Gates evaluated in order; any failure is a silent skip:

1. `get_memory_config().enabled`
2. `runtime_add_to_memory_enabled(runtime)` (L20) — reads
   `runtime.context["add_to_memory"]`. Accepts `False` or the strings
   `"0" | "false" | "no" | "off"`. Set by:
   - `frontend/src/components/workspace/input-box.tsx:239` → `add_to_memory: false`
   - `backend/src/gateway/routers/workflow.py:207,644` → workflow executions
     default to `False`
3. `thread_id` present in runtime context
4. After filtering: ≥1 human message **AND** (≥1 final AI message **OR** an
   `ask_user_for_clarification` tool call in state). The clarification branch
   exists because that tool fires `Command(goto=END)` before any plain AI reply,
   which would otherwise lose the turn entirely.

Then: `queue.add(thread_id, filtered_messages, agent_name, workspace_id=thread_id)`.

### B. Pre-compaction flush — `memory_flush_hook`

`backend/src/agents/memory/summarization_hook.py:17`

Registered as a `before_summarization` hook on the summarization middleware
(`work_agent/agent.py:277`, only when memory is enabled). Without it, messages
about to be compressed out of the context window would vanish before the 30 s
debounce fires, permanently losing whatever they contained.

Distinctive behaviour: if the segment has human messages but **no** final AI
message (a tool-heavy stretch), it **synthesizes** an `AIMessage` from up to 8
tool outputs (240 chars each) so the segment still passes the human+ai gate:

```python
filtered.append(AIMessage(content="Tool-heavy segment before compaction:\n" + …))
```

Calls `queue_immediate` — bypasses the debounce entirely, spawns its own daemon
thread, retries 3× with linear backoff. Fire-and-forget: must never raise into
the summarization path.

### C. `/memory <instruction>` — `MemoryMiddleware.before_agent`

`backend/src/agents/middlewares/memory_middleware.py:126`

Scans backwards for the last human message; if it starts with `/memory `, the
remainder becomes a behavior rule via
`add_behavior_rule(scope="workspace", workspace_id=thread_id)`. **Not LLM
extracted** — a direct, deterministic write. Gated on
`config.behavior_rules_enabled`.

Note the middleware does **not** strip the command from the message, so
`/memory always answer in Dutch` also flows through to the model as the turn's
user input.

---

## Message hygiene

`filter_messages_for_memory` — `memory_middleware.py:36`

Keeps **only** user inputs and final assistant responses:

- drops all `tool` messages
- drops all `ai` messages carrying `tool_calls` (intermediate steps)
- strips `<uploaded_files>` blocks from human messages, preserving the real
  question via a `copy()` of the message with rewritten content
- if nothing remains after stripping (upload-only turn), drops that turn **and**
  its paired AI response via a `skip_next_ai` flag

Upload scrubbing happens at **three** layers, which is redundant but defensive:

| Layer | Function | File |
|-------|----------|------|
| message | `filter_messages_for_memory` | `memory_middleware.py:36` |
| conversation text | `format_conversation_for_update` | `prompt.py:391` |
| stored memory | `_strip_upload_mentions_from_memory` | `updater.py:170` |

The third uses `_UPLOAD_SENTENCE_RE` (`updater.py:159`) to delete whole sentences
mentioning uploads from narrative summaries and to drop matching facts, and runs
on **every** save — so it also scrubs pre-existing memory retroactively.

`format_conversation_for_update` additionally truncates any message over 1000
chars and renders the transcript as `User: …` / `Assistant: …` blocks.

---

## `MemoryUpdateQueue`

`backend/src/agents/memory/queue.py:26` — module-global singleton via
`get_memory_queue()` (L243), `threading.Timer`-based.

```
add(thread_id, messages, agent_name, workspace_id)          L42
  ├─ config.enabled? else return
  ├─ evict any pending context with same thread_id  ← last-write-wins per thread
  ├─ append ConversationContext
  └─ _reset_timer()                                          L72
       └─ cancel existing Timer; start new 30s daemon Timer

_process_queue()                                             L90
  ├─ if _processing: _reset_timer(); return   ← reschedule, do not enqueue
  ├─ drain queue under lock, set _processing
  └─ for each context:
       ├─ _update_context_memory(context)                    L137
       └─ sleep(0.5) between contexts (rate-limit guard)

_update_context_memory(context)                              L137
  ├─ global_lock    = _scope_lock(agent, "global",    None)
  ├─ workspace_lock = _scope_lock(agent, "workspace", ws_id)
  ├─ with global_lock:    updater.update_memory(scope="global")      ← LLM call
  └─ with workspace_lock: updater.update_memory(scope="workspace")   ← LLM call
```

Also: `queue_immediate` (L169), `flush` (L201), `clear` (L213),
`pending_count` / `is_processing` properties, `reset_memory_queue()` (L256) for
tests.

### Defect Q-1 — one timer for all threads

`_reset_timer` cancels and restarts a **single shared** `threading.Timer` on
every `add()`. A continuously active conversation therefore resets the debounce
indefinitely, starving memory updates for every other thread in the process.
Only `queue_immediate` escapes it.

### Defect Q-2 — double extraction

`_update_context_memory` invokes the extraction LLM **twice per conversation**
over the same message list — once for `global`, once for `workspace`. At the
default model this is the dominant cost of the memory subsystem.

### Defect Q-3 — last-write-wins eviction

`add()` removes any pending context for the same `thread_id` and appends the new
one. If turn N+1 arrives inside the debounce window, turn N's messages are
dropped — recovered only because `state["messages"]` is cumulative, so the newer
context usually contains the older turns. It stops being true once summarization
compacts them away, which is exactly what trigger **B** exists to cover.

---

## The extraction prompt

`MEMORY_UPDATE_PROMPT` — `backend/src/agents/memory/prompt.py:18`, ~120 lines.

**Design: full-state rewrite.** Every call receives
`json.dumps(current_memory, indent=2)` — the *entire* profile, 36 KB today — plus
the formatted conversation, and returns a complete replacement decision.

### Output contract

```json
{
  "user": {
    "workContext":     { "summary": "…", "shouldUpdate": true },
    "personalContext": { "summary": "…", "shouldUpdate": false },
    "topOfMind":       { "summary": "…", "shouldUpdate": true }
  },
  "history": {
    "recentMonths":       { "summary": "…", "shouldUpdate": true },
    "earlierContext":     { "summary": "…", "shouldUpdate": false },
    "longTermBackground": { "summary": "…", "shouldUpdate": false }
  },
  "newFacts": [
    { "content": "…", "category": "preference|knowledge|context|behavior|goal",
      "confidence": 0.0-1.0 }
  ],
  "factsToRemove": ["fact_id_1", "fact_id_2"]
}
```

### Prescribed semantics

**Categories**

| Category | Definition per prompt |
|----------|----------------------|
| `preference` | Tools, styles, approaches the user prefers/dislikes |
| `knowledge` | Expertise, technologies mastered, domain knowledge |
| `context` | Background facts — job title, projects, locations, languages |
| `behavior` | Working patterns, communication habits, problem-solving approaches |
| `goal` | Stated objectives, learning targets, project ambitions |

**Confidence bands**

| Band | Meaning |
|------|---------|
| 0.9–1.0 | Explicitly stated ("I work on X") |
| 0.7–0.8 | Strongly implied from actions/discussion |
| 0.5–0.6 | Inferred pattern — "use sparingly" (and below the 0.7 storage gate, so effectively discarded) |

**Length guidance per section** — `workContext` / `personalContext` concise
(1–3 sentences); `topOfMind` a detailed paragraph holding **3–5 concurrent
themes**; `recentMonths` 4–6 sentences covering 1–3 months; `earlierContext`
3–12 months; `longTermBackground` foundational.

### Three hand-tuned behavioural patches

Accreted from production failures — each must survive the migration in some form:

1. **topOfMind hygiene** — "Do NOT keep completed one-off requests in topOfMind
   (finished trip plans, product comparisons, temporary research summaries,
   checklist requests)."
2. **Upload suppression** — "IMPORTANT: Do NOT record file upload events in
   memory. Uploaded files are session-specific and ephemeral."
3. **Multilingual preservation** — keep proper nouns and technical terms in
   their original form; note language capability in `personalContext`.

### Secondary prompt

`FACT_EXTRACTION_PROMPT` (`prompt.py:127`) — single-message fact extraction.
Exported from `src/agents/memory/__init__.py` but **has no call site**. Dead code;
delete during migration.

---

## Merge semantics — `_apply_updates`

`backend/src/agents/memory/updater.py:315`

```
1. narrative sections  → replaced wholesale when shouldUpdate AND summary non-empty
                         (stamps updatedAt = now)
2. factsToRemove       → filter current facts by id
3. newFacts            → append if confidence >= fact_confidence_threshold (0.7)
                         assign id = f"fact_{uuid4().hex[:8]}"
                         stamp createdAt = now, source = thread_id
4. cap                 → if len(facts) > max_facts (100):
                            sort by confidence DESC, truncate
```

### Defect U-1 — no deduplication *(the headline problem)*

Nothing compares a new fact against existing facts. Every conversation appends.
The only removal channel is the LLM emitting `factsToRemove`, which requires it
to correctly identify stale ids while reading a growing JSON blob. As the profile
grows, extraction quality degrades and cost rises — a compounding failure.

**This is the single strongest argument for mem0**, whose
`ADD / UPDATE / DELETE / NOOP` loop is purpose-built for it. See
[05](./05-mem0-mapping.md).

### Defect U-2 — confidence-only truncation

The `max_facts` cap sorts by `confidence` with **no recency term**. A stale 0.9
fact permanently outranks a fresh 0.8 one. Note the retrieval path *does* apply
decay (`03`), so the two stages disagree about what matters.

### Defect U-3 — silent failure

`update_memory` (`updater.py:239`) catches `json.JSONDecodeError` and bare
`Exception`, `print()`s, and returns `False`. Failures are invisible in logs
(`print`, not `logger`) and there is no retry on the debounced path — only
`queue_immediate` retries.

---

## Persistence & index sync

`_save_memory_to_file` (`updater.py:184`) → `persist_memory_data`
(`store.py:129`):

1. load previous file, compute `_stable_sha`
2. enforce `expected_sha` precondition if supplied or if
   `require_expected_sha` (**dropped under D-3**)
3. stamp `lastUpdated` / `scope` / `scopeId`
4. if versioning enabled (**it is not, and is dropped under D-3**): write
   `versions/memv-*.json` with `parent_sha` chain + `latest.json` pointer
5. atomic write via `.tmp` + `replace()`

Then back in `update_memory` (L288-312), the SQLite index is reconciled:

```python
stale = previous_fact_ids - updated_fact_ids
vector_store.delete_fact_ids(scope, scope_id, stale)
vector_store.upsert_facts(scope, scope_id, updated_facts)   # full re-upsert
```

`get_memory_data` (`updater.py:93`) caches by `(agent, scope, workspace_id)` with
**mtime invalidation** — a module-global dict, unbounded, never evicted.

---

## Direct CRUD (bypasses extraction)

All in `updater.py`, all API-driven, all keeping the SQLite index in sync:

| Function | Line | Notes |
|----------|------|-------|
| `add_behavior_rule` | 371 | also the `/memory` command path |
| `update_behavior_rule` | 398 | raises `ValueError` if id missing |
| `delete_behavior_rule` | 424 | |
| `upsert_fact` | 440 | update-in-place or append; index-synced |
| `delete_fact` | 490 | index-synced |
| `forget_thread_facts` | 513 | filters by `fact.source == thread_id` |
| `clear_memory` | 534 | writes empty structure + `delete_scope` on index |

Behavior rules are **not** indexed in SQLite — they are always injected in full
(up to 10 active), never retrieved by relevance.
