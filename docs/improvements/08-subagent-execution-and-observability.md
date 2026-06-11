# 08 — Subagent Execution, Turn Budgets & Observability

Findings from auditing two real travel-planning threads on the local MLX model
(`mlx-community/qwen3.6-35b-a3b`):

- `a7629185-5908-4b01-838f-edeca3755c58` — Tromsø itinerary (6 subagents, **1 failed twice**)
- `6a8f61ec-0c9d-4eef-ac73-164b2f2f2c44` — Coastal/Wadden road trip (10 subagents)

Unlike files 01–07 (which audit *prompt text*), this file audits the **execution
and observability** of the subagent (`task`) subsystem: how the turn budget is
set, what the subagent actually receives, how prompts are captured, and how
failures surface (or don't).

## Implementation status (2026-06-11)

The approved execution and research-agent changes are implemented:

- `task` no longer exposes `max_turns`; graph-step limits come only from
  `subagents.max_turns` and `subagents.agents.<name>.max_turns`, with no hidden
  minimum.
- Failed, timed-out, disappeared, and polling-timeout tasks return structured
  error `ToolMessage` results. Runtime/execution-trace `task_failed` and
  `task_timed_out` events are the authoritative terminal audit signal.
- Child prompt captures are stored under
  `.prompts/subagents/<subagent-type>/<task-id>/` with explicit attribution.
- `knowledge-researcher` now accepts one coherent research topic, uses only
  web search, knowledge-vault query, and report-editing tools, and writes
  `/mnt/user-data/workspace/research/<task-id>.md`.

Circuit-breaker and loop-detection changes are explicitly deferred. No
middleware was added, removed, reordered, or modified by this implementation.
The remaining sections preserve the historical evidence that motivated the
changes; statements about the then-current code should be read in that context.

## Inventory

| # | Concern | Source | Verdict |
|---|---------|--------|---------|
| 1 | Lead assigns `max_turns` per `task` call | [task_tool.py:261,338](../../backend/src/tools/builtins/task_tool.py#L261) | Foot-gun — prefer preset |
| 2 | Global turn budget = 50 | [config.yaml](../../config.yaml) `subagents.max_turns` | Consider raising, with caveats |
| 3 | "1 turn" semantics | [executor.py:290](../../backend/src/subagents/executor.py#L290) (LangGraph `recursion_limit`) | Mislabeled; 50 ≈ ~25 model calls |
| 4 | Subagent prompt capture | [prompt_logging.py:34](../../backend/src/models/prompt_logging.py#L34) | Captured but mislabeled + flat |
| 5 | Agent-type routing (`general-purpose` vs `knowledge-researcher`) | [work_agent/prompt.py:29-31](../../backend/src/agents/work_agent/prompt.py#L29) | Correct per prompt; brief was too broad |

## Internal code assessment (2026-06-11)

The report is directionally correct and its historical artifact counts are
reproducible: the two thread directories contain 98 + 120 prompt captures, and
all 218 declare `"actor": "work_agent"`. The Tromsø prompt history also contains
both recursion-limit failures (50, then 30), while its persisted trajectory
records the corresponding `task` calls as `error: null, timed_out: false`.

Current-code assessment:

| Finding | Status | Assessment |
|---|---|---|
| Lead-controlled `max_turns` is a foot-gun | **Confirmed** | Still present in the Python tool signature and both JSON tool catalogs; tests explicitly preserve the override |
| Only `prompt` becomes the initial child message | **Confirmed with caveat** | `description` is also used parent-side for grouping and broad-prompt scope matching, but it is not sent as a separate child-context field |
| 50 means LangGraph recursion steps, not 50 model calls | **Confirmed** | `max_turns` is assigned directly to `recursion_limit`; ~25 model/tool cycles is a sound approximation for the normal alternating ReAct path |
| Prompt capture cannot identify child task/type | **Confirmed** | Executor supplies only the shared `thread_id`; it passes no subagent tag, task id, or type into callback-visible config |
| `general-purpose` routing matched the broad brief | **Confirmed** | `knowledge-researcher` rejects broad prompts unless a single ready-todo scope can be inferred |
| Failed tasks are wholly invisible today | **Partly stale** | Current `task_tool` emits `task_failed`/`task_timed_out` runtime and execution-trace events. However, generic `tool_call_end` still cannot distinguish a returned failure string from success, so the machine contract remains inconsistent |

Two additional historical implementation findings raised the priority of the
execution fix:

1. **Failure semantics differed by terminal path.** A failed subagent returned a
   normal string (`"Task failed. Error: ..."`), while a timeout raises
   `TimeoutError`. Consequently the failed path can become an ordinary
   successful `ToolMessage`, and trajectory middleware records no error. The
   "task disappeared" branch also emits only an SSE event and no runtime/trace
   event. Terminal outcomes need one structured contract.
2. **The advertised lower-budget behavior was false below 25.** The tool schema
   says callers may lower `max_turns` to bound cost, but the executor silently
   applies `max(25, max_turns)`. Values 1-24 are accepted by validation and then
   ignored at execution time.

The historical error/circuit counts in the synthesis should be treated as
thread-wide evidence. Because prompt logs are flat and mislabeled, they cannot
reliably attribute every repeated error or circuit-open message to one specific
subagent after the fact.

---

## 1. Stop the lead agent from assigning `max_turns`; preset it instead

**Evidence.** The subagent's entire initial state is just the prompt text:

```python
# executor.py:241-260
state = {"messages": [HumanMessage(content=task)]}   # task == the `prompt` arg
```

Of the four `task` arguments, only `prompt` becomes the subagent's initial human
message. `description` remains parent-side metadata (UI/grouping and, for broad
`knowledge-researcher` calls, ready-todo scope matching); `subagent_type`
selects the system prompt + tools; **`max_turns` is consumed only by the
executor** to set `recursion_limit`
([executor.py:290](../../backend/src/subagents/executor.py#L290)) and is never
visible to the subagent.

The `task` tool exposes `max_turns` as an optional integer the lead can pass
([task_tool.py:338](../../backend/src/tools/builtins/task_tool.py#L338)). On
thread `a7629185` the lead used the default (50) on first dispatch, then
re-dispatched the **already-budget-exhausted** retry with `max_turns: 30`
(confirmed in the checkpointer tool-call args: msgpack `max_turns` → `\x1e` = 30).
Lowering the budget on a task that failed *because* it ran out of turns is
exactly the wrong move.

**Recommendation (preferred): preset the budget, remove the knob.**
Drop `max_turns` from the `task` schema and let the budget come solely from
`config.yaml`. Most predictable; removes the foot-gun; nothing the lead can
mis-tune.

**Alternative: coarse intent, resolved executor-side.**
If per-task differentiation is genuinely wanted, replace the raw integer with a
`complexity: [simple, normal, complex]` enum and map it to a budget *in the
executor/config* (e.g. `simple→50, normal→120, complex→200`). The subagent still
never sees it (consistent with how `max_turns` works today), but the lead can't
invent a pathological value.

**Do not** pass `complexity` *into* the subagent's prompt — it is not consumed
there and would be noise. A category only matters if something maps it to a
budget.

---

## 2. Raising all subagents to 200 — viable, but treats the symptom

Mechanically this is one line: `subagents.max_turns: 200`. Caveats:

- **The real cap shifts to `timeout_seconds: 1800` (30 min), not turns.** The
  Tromsø "activities and tours" subagent was looping on `websearch.search`
  failures (~5–10 s each). At 200 turns it would grind far longer before dying,
  so 200 raises the *wall-clock wasted on a doomed loop*.
- **Cost/concurrency.** `subagents.max_concurrent_limit: 4` → four subagents
  each allowed 200 turns is a ~4× worst-case token ceiling per fan-out batch.
- **Floor/ceiling.** Floor is `MIN_SUBAGENT_RECURSION_LIMIT = 25`
  ([executor.py:26](../../backend/src/subagents/executor.py#L26)); there is no
  hard ceiling, so 200 is honored verbatim.

**Recommendation.** 200 is reasonable *headroom* for deep research, but pair it
with circuit-breaker-aware early exit (see §1 of the root cause below) or a
tighter per-subagent timeout — otherwise you trade "fails at 50" for "burns 25
minutes then fails at 200." The budget was never the root cause; the retry storm
was.

---

## 3. What "1 turn" actually means (and why the name misleads)

`max_turns` is wired straight to LangGraph's `recursion_limit`, which counts
**graph super-steps (node executions)**, not model calls. The subagent is a
ReAct graph that alternates:

```
model node → tool node → model node → tool node → … → END
```

Each node execution is **1 super-step**, so one *reasoning turn* (model thinks /
calls tools, then tools run) is **~2 super-steps**. Therefore:

- `max_turns = 50` ≈ **~25 model calls**, not 50.
- Parallel tool calls in one step collapse to **one** tool-node super-step —
  three concurrent `web_search` calls cost the same as one.

So ~25 reasoning rounds is plenty for a *well-behaved* scoped task. The Tromsø
failure was not "50 is too small for normal work" — it was a retry storm
spending one full think→act cycle per failed search until ~25 cycles evaporated.

**Naming nit.** The argument is effectively `max_super_steps`; a reader assumes
`50 = 50 model calls` when it's ~25. Worth a doc/rename clarification.

---

## 4. Subagent prompts ARE captured — but mislabeled and dumped flat

[prompt_logging.py](../../backend/src/models/prompt_logging.py) captures *every*
chat-model call (lead **and** subagent) into one flat dir,
`…/workspace/.prompts/`, named `{timestamp}_{actor}_{purpose}.txt`. In thread
`a7629185`, ~77 of the 98 captures are subagent prompts — but you cannot tell
which subagent, or which `task`, produced any of them.

**Root cause** — `_detect_actor`
([prompt_logging.py:34](../../backend/src/models/prompt_logging.py#L34)):

```python
if "tool" in haystack:      return "tool"
if "subagent" in haystack:  return "sub_agent"   # dead code — never fires
return "work_agent"
```

It inspects the serialized model name (`"ChatOpenAI"`) + tags. Subagents are
built with the *same* `create_chat_model()` and carry **no `"subagent"` tag**, so
the `sub_agent` branch never executes — every capture falls through to
`work_agent`. That is why all 218 captures across both audited threads share one
label.

**Proposed layout** (your request):
`/mnt/user-data/workspace/.prompts/subagents/<subagent_type>/<task_id>/…`

**Feasibility — the hooks already exist.** The executor knows `subagent_type`,
`description`, `trace_id`, and `task_id`, and already injects
`configurable.thread_id` into the subagent `run_config`
([executor.py:296](../../backend/src/subagents/executor.py#L296)). To fold the
captures:

1. Tag the subagent's model invocation (or pass identity via `configurable`) so
   the callback can read `subagent_type` + `task_id`.
2. Nest in `_resolve_output_dir` / the filename builder by that identity when
   present; otherwise keep the current flat lead-level path.
3. **Subfolder by `task_id`, not `description`** — descriptions are not unique
   (the activities subtask reused the same brief across 3 retries and would
   collide). Slugified description is fine as a *label*; `task_id` is the stable
   key. The lead's `description` can name the folder for readability *if*
   suffixed with `task_id`.

**Caveat.** Lead and subagent calls share the same `thread_id`, so today they
cannot be separated by directory at all, and the actor label is lost after the
fact — identity must flow from the executor into the callback. This is a small
code change, not a config toggle.

---

## 5. Historical routing behavior

At the time of the audited threads, this was correct per the prompt rather than
a model error. The work-mode
`<subagent_system>` block
([work_agent/prompt.py:29-31](../../backend/src/agents/work_agent/prompt.py#L29))
defines:

- `general-purpose`: *"web research, code exploration, file analysis,
  **multi-source investigation**"*
- `knowledge-researcher`: *"**one narrow** live-source/RSS/direct-source research
  objective"*

The redesigned `knowledge-researcher` now accepts a coherent topic with
multiple related questions and writes a self-contained report. Unrelated
topics should still be split; mixed research/execution and local-file work
should route to `general-purpose`.

Historically, the Tromsø/coastal briefs were broad, multi-part web research ("Research
activities, attractions, and tours… Find: [many items]") — `general-purpose` by
the prompt's own taxonomy. Moreover `knowledge-researcher` would have **rejected**
those briefs: the task tool runs `_source_research_prompt_is_broad()` and returns
*"Task rejected: knowledge-researcher accepts one narrow objective only"* for a
broad prompt with no scope hint
([task_tool.py:347-357](../../backend/src/tools/builtins/task_tool.py#L347)).

**The more defensible flag** is that the lead violated its own "Task quality bar"
([work_agent/prompt.py:46-51](../../backend/src/agents/work_agent/prompt.py#L46)):
*"one objective per subagent… split any mega-brief with 6+ bullets."* The
activities brief was multi-bullet. If those were decomposed into narrow,
single-source objectives, `knowledge-researcher` would both apply and accept
them.

---

## Root cause of the audited failure (synthesis)

The `a7629185` failure was **not** caused by agent-type choice nor by 50 being
inherently small. It was:

> a **broad multi-bullet brief** handed to one subagent + a **flaky
> `websearch.search`** (428 `ExceptionGroup` + 30 `BrokenResourceError` + 36
> circuit-breaker trips in the run) → a retry storm that ate ~25 think→act cycles
> → recursion limit hit → retried at a **lower** budget (30) → failed again.

Critically, the trajectory's `task` end payload reported `error: null,
timed_out: false` for both failed attempts — the failure was only visible in the
UI and in the lead's `.prompts/` tool messages. (See the audit guide's new
§3.7.1 for trajectory-based failure detection:
[docs/audit/README.md](../audit/README.md).)

## Ranked recommendations

| Priority | Change | Effort | Why |
|---|---|---|---|
| **Done** | Normalize all `task` terminal outcomes and preserve lifecycle events | M | Structured error results and authoritative terminal events |
| **Done** | Remove the lead's `max_turns` knob and the silent minimum | S | Configured graph-step values are honored exactly |
| **Done** | Nest and attribute subagent prompt captures | S–M | Makes child runs auditable by type and task |
| **Done** | Document `max_turns` as LangGraph graph steps | S | Removes the model-call misconception |
| **Done** | Redesign `knowledge-researcher` around coherent report-producing research | M | Better tool fit and a deterministic handoff artifact |
