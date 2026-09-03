# Memory Subsystem — Issue Register

_Verified end-to-end 2026-09-03 against the working copy. Every entry below was
confirmed by reading the code path, not inferred._

Each issue carries a **mem0 verdict** — whether the migration
([06](./06-migration-plan.md)) fixes it, and if not, who has to.

| Verdict | Meaning |
|---------|---------|
| ✅ **fixed** | mem0 removes the defect as a side effect of the migration |
| 🔧 **fix during** | not fixed by mem0, but the migration touches this code — fix it there |
| ⚠️ **decide** | a product decision the migration forces; doing nothing carries it forward |
| ➖ **unrelated** | independent of the migration; listed for completeness |

---

## Severity summary

| ID | Issue | Severity | mem0 verdict |
|----|-------|----------|--------------|
| [U-1](#u-1-no-fact-deduplication) | No fact deduplication — memory only grows | **High** | ✅ fixed |
| [M-1](#m-1-subagents-read-memory-but-never-write-it) | Subagents read memory but never write it | **High** | ⚠️ decide |
| [M-2](#m-2-subagent-output-never-reaches-memory) | Subagent output never reaches memory | **High** | ⚠️ decide |
| [R-1](#r-1-retrieval-is-lexical-not-semantic) | Retrieval is lexical, not semantic | **High** | ✅ fixed |
| [Q-1](#q-1-one-debounce-timer-for-all-threads) | One debounce timer starves other threads | **Medium** | 🔧 fix during |
| [Q-2](#q-2-two-extraction-llm-calls-per-turn) | Two extraction LLM calls per turn | **Medium** | ✅ fixed |
| [U-2](#u-2-fact-truncation-ignores-recency) | Fact truncation ignores recency | **Medium** | ✅ fixed |
| [U-4](#u-4-transient-session-state-is-stored-as-durable-memory) | 26% of stored facts are transient session state | **High** | ❌ **NOT fixed** |
| [U-3](#u-3-extraction-fails-silently) | Extraction fails silently via `print` | **Medium** | 🔧 fix during |
| [M-3](#m-3-subagents-get-no-memory-injection) | Subagents get no memory injection | **Medium** | ✅ **SHIPPED** (rules only) |
| [M-4](#m-4-recall-is-absent-from-the-research-path) | `recall` absent from the research path | **Medium** | ⚠️ decide |
| [R-2](#r-2-memory-is-frozen-for-the-whole-run) | Memory frozen for the whole run | **Medium** | ➖ unrelated |
| [R-3](#r-3-the-narrative-sections-are-unreachable-on-a-normal-turn) | Narrative sections unreachable on a normal turn | **Medium** | ⚠️ decide |
| [R-4](#r-4-the-location-escape-hatch-is-unreachable) | Location escape hatch unreachable (**failing test**) | **Medium** | ✅ fixed |
| [C-1](#c-1-the-tool-catalogs-claim-embeddings-that-do-not-exist) | Catalogs claim embeddings that don't exist | **Low** | ✅ fixed |
| [C-2](#c-2-producer-and-consumer-category-drift) | Producer/consumer category drift (`interest`) | **Low** | 🔧 fix during |
| [Q-3](#q-3-debounce-eviction-is-last-write-wins) | Debounce eviction is last-write-wins | **Low** | 🔧 fix during |
| [H-1](#h-1-duplicated-_get_memory_context) | `_get_memory_context` duplicated verbatim | **Low** | 🔧 fix during |
| [H-2](#h-2-dead-code-and-dead-config) | Dead code and dead config | **Low** | 🔧 fix during |
| [H-3](#h-3-subagents-always-receive-the-work-mode-catalog) | Subagents always get the work-mode catalog | **Low** | ➖ unrelated |

---

## The subagent memory boundary

The four `M-*` issues below share one root cause and should be decided together.
`SubagentExecutor._create_agent` (`src/subagents/executor.py:211-238`) builds a
deliberately minimal agent:

```python
middlewares = [
    ToolErrorBoundaryMiddleware(),
    ThreadDataMiddleware(lazy_init=True),
    SandboxMiddleware(lazy_init=True, release_on_exit=False),
    PermissionMiddleware(),
]
return create_agent(
    model=model, tools=self.tools, middleware=middlewares,
    system_prompt=self.config.system_prompt,     # ← static string
    state_schema=ThreadState,
)
```

**No `MemoryMiddleware`. No `apply_prompt_template`.** Everything below follows.

**First, the good news:** scope propagation works. `task_tool.py:340` reads
`thread_id` from runtime context → `:378` passes it to the executor →
`executor.py:300` sets `configurable["thread_id"]`. `recall_tool`'s
`get_config()` lookup resolves the parent thread correctly, so workspace scope is
intact inside subagents. *(An earlier draft of these docs flagged this as a
suspected defect; it is not one.)*

### M-1: Subagents read memory but never write it

**Evidence:** no `MemoryMiddleware` in the subagent middleware list
(`executor.py:223-230`).

**Impact:** a subagent can spend its entire budget researching, learn something
durable about the user or the domain, and none of it is ever extracted. The
memory system is write-blind to every delegated run.

**mem0 verdict:** ⚠️ **decide.** mem0 makes this *cheaper* to fix — one
`m.add(messages, user_id=…, run_id=…)` at subagent completion — but it is a
product decision, not a mechanical port. Adding it naively means every research
subagent writes facts, which could flood the store.

**Options:**
1. Leave as-is — subagents stay read-only consumers. Simplest, preserves today's behaviour.
2. Extract at subagent completion, tagged `metadata={"origin": "subagent", "subagent_type": …}` so it can be filtered or weighted down at retrieval.
3. Extract only from *whitelisted* subagent types (e.g. `knowledge-researcher`), leaving execution helpers like `bash` write-blind.

Option 3 is the conservative middle and matches how the allow-lists already work.

### M-2: Subagent output never reaches memory

**Evidence:** `filter_messages_for_memory`
(`src/agents/middlewares/memory_middleware.py:36`) drops **all** `tool` messages
and all `ai` messages carrying `tool_calls`. The `task` result — the subagent's
entire report — arrives as a `ToolMessage`, so it is discarded before extraction.

This is distinct from M-1: even with the parent's `MemoryMiddleware` running
normally, the delegated work is invisible. The only thing that can survive is
whatever the lead agent restates in its own final text.

**Partial existing mitigation:** `memory_flush_hook`
(`summarization_hook.py:17`) synthesizes an `AIMessage` from up to 8 tool outputs
— but **only** in the pre-compaction path, and **only** when the segment has no
final AI message. On the normal path, nothing.

**mem0 verdict:** ⚠️ **decide** — same decision as M-1. Note the filter is
correct in intent (tool chatter is noise); the defect is that it has no exception
for high-value tool results.

### M-3: Subagents get no memory injection

**Evidence:** `system_prompt=self.config.system_prompt` — a static string from
the subagent definition. No `<memory>` block, no behavior rules.

**Impact:** behavior rules the user set via `/memory` do not apply inside
delegated runs. A rule like "always answer in Dutch" is honoured by the lead
agent and ignored by its subagents.

**✅ SHIPPED 2026-09-03** — `build_subagent_rules_context()` in
`memory/context.py`, wired into `SubagentExecutor._create_agent`. **Rules only,
never facts**: rules are user-authored directives that should apply everywhere,
whereas facts are retrieved evidence whose injection into a research subagent
would risk personal context being presented as sourced findings (see
[M-4](#m-4-recall-is-absent-from-the-research-path)). Dedupes across workspace +
global scopes, skips inactive rules, caps at 10, honours all three kill
switches, and returns `""` on any failure so delegation cannot break.
Verified with 13 assertions including "no facts leaked into subagent prompt".

### M-4: `recall` is absent from the research path

**Evidence:** measured across all nine subagent definitions in
`src/subagents/builtins/`:

| Subagent | `recall`? | How | Modes |
|----------|-----------|-----|-------|
| `general-purpose` | ✅ | by omission — no allow-list | work/auto |
| `comparison-dimension-researcher` | ✅ | explicit allow-list | work/auto |
| `knowledge-researcher` | ❌ | **explicitly in `disallowed_tools`** | work |
| `bash` | ❌ | allow-list excludes | work/auto |
| `docs-explorer` | ❌ | allow-list excludes | work/auto |
| `synthesis-reviewer` | ❌ | allow-list excludes | work/auto |
| `vault-source-researcher` | ❌ | allow-list excludes | work/auto |
| `finder-agent` | ❌ | allow-list excludes | plan |
| `scope-researcher` | ❌ | allow-list excludes | plan |

**2 of 9.** The one that matters most — `knowledge-researcher`, the deep-research
agent that writes Markdown reports — has `recall` **explicitly denied**. So the
agent doing the most substantial reasoning has no access to what the system knows
about the user.

Combined with M-3 (no injection), `knowledge-researcher` operates with **zero**
memory context by either channel.

**⚠️ UPDATE 2026-09-03 — the deny is deliberate, not incidental.**
`knowledge_researcher.py:23` states it in the subagent's own prompt:

> "Do not use personal-memory recall. Ground the report in retrieved research evidence."

It is enforced in **three** places: that `<scope>` line, omission from the
`tools` allow-list, and the `disallowed_tools` entry. Note also that
`comparison-dimension-researcher` grants `recall` explicitly — the two research
agents have *intentionally opposite* policies, which is a considered distinction
rather than drift. **Do not simply remove the deny**; it would take two edits
(allow-list + deny-list) and contradict a documented design decision.

The rationale is sound for a report-producing agent: a research report's claims
must trace to cited sources, and personal memory is not a source.

**But the deny conflates two different uses of memory:**

| Use | Should it be blocked? |
|-----|----------------------|
| Memory as **evidence** in the report's findings | **Yes** — this is what the prompt correctly forbids |
| Memory as **context** for what to research and how to present it (user is a ship-broking analyst → weight the maritime angle) | Arguably no — this never enters the findings |

A surgical version would grant `recall` while extending the `<scope>` rule to
"you may use recall to orient your research; never cite it as evidence or let it
substitute for a source." That is a prompt-design decision with its own
regression risk, not a one-line fix.

**Partially addressed:** behavior-rule injection ([M-3](#m-3-subagents-get-no-memory-injection),
shipped) already delivers much of the *context* benefit for all subagents,
including this one, without any evidence-contamination risk — rules are
directives, not findings.

**mem0 verdict:** ⚠️ **decide** — now a narrower question than originally framed:
does `knowledge-researcher` need memory *beyond* behavior rules?

---

## Queue issues

### Q-1: One debounce timer for all threads

**Evidence:** `MemoryUpdateQueue._reset_timer` (`queue.py:72`) cancels and
restarts a **single shared** `threading.Timer` on every `add()`.

**Impact:** a continuously active conversation resets the debounce indefinitely,
starving memory updates for every other thread in the process. Only
`queue_immediate` (the compaction flush) escapes it.

**mem0 verdict:** 🔧 **fix during.** mem0 does not touch the queue. Fix while the
queue is already open: move to a per-thread timer, or a single periodic sweeper
that drains anything older than `debounce_seconds`.

### Q-2: Two extraction LLM calls per turn

**Evidence:** `_update_context_memory` (`queue.py:137`) invokes
`MemoryUpdater.update_memory` once for `global` and once for `workspace` over the
same message list.

**mem0 verdict:** ✅ **fixed** — a single `m.add()` carrying both `user_id` and
`run_id` covers both scopes in one extraction. This is the migration's largest
per-turn cost saving.

### Q-3: Debounce eviction is last-write-wins

**Evidence:** `add()` (`queue.py:42`) removes any pending context for the same
`thread_id` before appending.

**Impact:** turn N's messages are dropped if turn N+1 arrives inside the window.
Usually harmless because `state["messages"]` is cumulative — but that stops being
true once summarization compacts them away, which is exactly why the flush hook
exists.

**mem0 verdict:** 🔧 **fix during** — low priority; the flush hook already covers
the dangerous case.

---

## Extraction issues

### U-1: No fact deduplication

**Evidence:** `_apply_updates` (`updater.py:315`) appends every new fact that
clears the confidence gate. Nothing compares against existing facts. The only
removal channel is the LLM emitting `factsToRemove` while reading the entire
current profile as JSON.

**Impact — compounding.** As the profile grows (36 KB / 83 facts today), the
extraction prompt's input grows with it, cost rises, and the model's ability to
correctly identify stale ids degrades. Near-duplicates accumulate.

**mem0 verdict:** ✅ **fixed.** This is the single strongest argument for the
migration — mem0's `ADD / UPDATE / DELETE / NOOP` loop exists for exactly this,
and sends only semantically-neighbouring memories for the decision rather than
the whole profile.

### U-4: Transient session state is stored as durable memory

**Discovered by the mem0 spike, 2026-09-03 — not visible from code reading.**

**Evidence:** measured against the live fact base (84 global facts):

```
transient/session : 22  (26%)
durable-looking   : 62
by category       : context 42, behavior 13, knowledge 12, goal 10, preference 7
```

Examples currently held as permanent memory:

- "Working with addr_fail_reason - Bad Address List.csv containing 17,842 rows"
- "Defines execution schemas via workflow.json to manage row-level tasks"
- "Phase 1 URL validation of `Bad_websites.csv` completed using `aiohttp`, resulting in 448 True"
- "Output CSV path confirmed as /mnt/user-data/workspace/COSCO_Fleet_Review_..."

These are *run artifacts* — filenames, row counts, paths, phase status. They were
true during one task and are noise forever after.

**Impact — this is why retrieval looks bad regardless of backend.** In the spike,
these facts dominated the top results for unrelated queries: "where is the user
based" returned three COSCO workflow facts, because they are the densest cluster
in the store. A quarter of the fact base is actively competing with real
preferences for injection slots.

**mem0 verdict:** ❌ **NOT fixed.** mem0 dedupes *near-duplicates*; it has no
notion of "durable vs. ephemeral". Migrating without addressing this carries the
pollution across and then embeds it.

The extraction prompt already has a hygiene rule for exactly this failure — but
it is scoped to `topOfMind` only:

> "Do NOT keep completed one-off requests in topOfMind (finished trip plans,
> product comparisons, temporary research summaries…)"

**The same rule was never applied to `newFacts`.** That is the fix: extend the
durability test to fact extraction in the custom extraction prompt
([G-6](./05-mem0-mapping.md)), and consider a pre-backfill prune so the
migration does not import 22 known-bad rows.

Note this partly explains [R-3](#r-3-the-narrative-sections-are-unreachable-on-a-normal-turn):
the prompt invests heavily in narrative hygiene that nothing reads, while the
facts that *are* read have no hygiene rule at all.


### U-2: Fact truncation ignores recency

**Evidence:** the `max_facts` cap (`updater.py:315`, end) sorts by `confidence`
descending with no recency term — while retrieval (`vector_store.py:167`) *does*
apply a 60-day decay. The two stages disagree about what matters.

**Impact:** a stale 0.9 fact permanently outranks a fresh 0.8 one at write time,
even though retrieval would have decayed it away.

**mem0 verdict:** ✅ **fixed** — dedup keeps the set bounded, so the
confidence-only sort disappears with the problem it was papering over.

### U-3: Extraction fails silently

**Evidence:** `update_memory` (`updater.py:239`) catches `json.JSONDecodeError`
and bare `Exception`, calls `print()`, returns `False`. `queue.py` does the same.

**Impact:** failures are invisible in structured logs, and the debounced path has
no retry (only `queue_immediate` retries, 3×).

**mem0 verdict:** 🔧 **fix during.** Convert to `logger` in Phase 0 —
specifically so the migration itself is observable. Without this you cannot tell
whether mem0 extraction is failing or merely quiet.

---

## Retrieval & injection issues

### R-1: Retrieval is lexical, not semantic

**Evidence:** `MemoryVectorStore` (`vector_store.py`) — despite the name, no
embedding column, no vectors. `_lexical_score` (`:43`) is token-set overlap plus a
substring bonus. Every query full-table-scans and scores in Python.

**Downstream damage:** the entire filter stack in `_is_relevant_injection_fact`
(`prompt.py:237`) exists to compensate — dual lexical floors, a `context`-category
penalty, and a **hardcoded list of city names** acting as a manual synonym table
for location queries.

**mem0 verdict:** ✅ **fixed.** Most of the compensating machinery should be
deleted rather than ported ([05, G-5](./05-mem0-mapping.md)).

### R-2: Memory is frozen for the whole run

**Evidence:** `make_work_agent` passes `system_prompt=` as a **string**
(`agent.py:844`), evaluated once at agent construction. `apply_prompt_template`
→ `_get_memory_context` therefore runs **once per run**, not per model call.

**Impact:** memory is computed from the first user turn and never updates, even
across a long multi-step run that changes topic. `recall` is the only escape
hatch — which sharpens M-4.

**mem0 verdict:** ➖ **unrelated**, but *good news for cost*: mem0 retrieval
costs **one embedding call per run**, not per LLM step. Worth stating explicitly
in the migration's cost model so nobody over-engineers a cache.

### R-3: The narrative sections are unreachable on a normal turn

**Evidence:** `format_memory_for_injection` (`prompt.py:264`) sets
`include_broad_context = not has_relevance_query`. The frontend always supplies
`current_turn_text` (`frontend/src/core/threads/hooks.ts:1457`), so
`has_relevance_query` is **true on every real turn**, and the `user.*` /
`history.*` sections are omitted.

**Impact:** the extraction prompt spends significant effort maintaining six
narrative paragraphs (with prescribed length guidance and a topOfMind hygiene
rule) that the model **never sees** in normal operation. They are read only by
the settings UI and the vault lint judge.

**mem0 verdict:** ⚠️ **decide** — this is the strongest *evidence* for open
decision **O-1**. If the sections are only consumed out-of-band, they do not
belong in the per-turn extraction path at all; regenerating them periodically
from mem0 (the O-1 recommendation) is strictly better and cheaper.

---

### R-4: The location escape hatch is unreachable

**Evidence:** `tests/test_prompt_memory_context.py::test_memory_injection_keeps_location_fact_for_my_city_query`
**fails on `main`** — a pre-existing red test, not a migration regression.

`_is_relevant_injection_fact` (`prompt.py:237`) gates on category *before*
applying the location exception:

```python
if category == "context":
    effective_threshold = max(threshold, 0.6)   # 0.25 -> 0.6
...
return score >= effective_threshold and (lexical >= … or (location_sensitive_query and location_like_fact))
```

The escape hatch lives inside the second conjunct, but a location fact scoring
0.32 fails `score >= 0.6` in the *first* conjunct and is dropped before the
exception is ever consulted. Location facts are exactly the ones the extraction
prompt categorises as `context` (its own definition: "job title, projects,
**locations**, languages"), so the hatch is dead for the only category it was
written to rescue.

**Impact:** "renting versus buying in my city" retrieves nothing, which is the
precise scenario the code comment says it exists to handle.

**mem0 verdict:** ✅ **fixed** — this whole filter stack is deleted under
[G-5](./05-mem0-mapping.md). Do **not** fix the threshold logic in place; the
hatch is a manual synonym table that semantic retrieval makes unnecessary. Delete
the test alongside the stack, or rewrite it as a semantic-retrieval assertion.


## Contract issues

### C-1: The tool catalogs claim embeddings that do not exist

**Evidence:** identical sentence in `internal_tools_plan.json:137` and
`internal_tools_work.json:105`:

> "…because the underlying retriever is **keyword + embedding based**, not conversational."

**mem0 verdict:** ✅ **fixed** — the migration makes the claim true. The
`returns` shape in both files still needs editing; see
[04](./04-surface-inventory.md#what-must-be-edited-the-returns-field).

### C-2: Producer and consumer category drift

**Evidence:** the vault lint judge
(`control_plane/vault_learning/_lint.py:42`) filters facts on category
`"interest"`, which the extraction prompt **never emits** — its enum is
`preference | knowledge | context | behavior | goal`.

**Impact:** harmless today (the filter is a union, so `interest` simply never
matches), but it means one consumer was written against a schema that does not
exist.

**mem0 verdict:** 🔧 **fix during** — pin the category enum in one place when
writing the custom extraction prompt, and make the judge read from it.

---

## Hygiene

### H-1: Duplicated `_get_memory_context`

`work_agent/prompt.py:173` and `plan_agent/prompt.py:266` are **verbatim
duplicates**, ~45 lines each. Every adapter change has to land twice.

**mem0 verdict:** 🔧 **fix during** — consolidate in Phase 0, *before* anything
else, or the migration doubles its own diff.

### H-2: Dead code and dead config

| Item | Location | Status |
|------|----------|--------|
| `FACT_EXTRACTION_PROMPT` | `prompt.py:127` | exported from `__init__.py`, **no call site** |
| `decay_archive_threshold` | `memory_config.py` | configured, **never read** |
| `memory_versioning` | whole subsystem | `enabled: false` by default; dropped under **D-3** |
| Triple upload scrubbing | 3 layers (see [02](./02-extraction-and-queue.md#message-hygiene)) | redundant but defensive — **keep** |

**mem0 verdict:** 🔧 **fix during** — Phase 0 cleanup.

### H-3: Subagents always receive the work-mode catalog

**Evidence:** `task_tool` calls `get_available_tools(model_name=…, groups=…,
subagent_enabled=False)` — **no `mode` argument**. `tools.py:73-75` defaults
unset mode to `"work"`.

**Impact:** plan-mode subagents (`finder-agent`, `scope-researcher`) receive
work-framed tool descriptions. For *memory* this is currently moot — neither has
`recall` — but it is the mechanism by which the plan catalog's memory framing
would fail to reach a planning subagent if one were granted `recall` later.

**mem0 verdict:** ➖ **unrelated** to the migration. Logged so the M-4 decision
does not accidentally depend on catalog framing that never arrives.
