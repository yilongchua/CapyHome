# Phase Tool Filter — How It Works and Where It Can Break

How the `PhaseToolFilterMiddleware` and `ToolDisclosureMiddleware` control which
tools the model can call at each stage of a work-mode or plan-mode run.

Reference threads used for this analysis:
- **`615d7ff9-86ca-4a48-8bd3-060d6f430697`** — stuck run (model called `web_search` in draft phase)
- **`ab005275-7a90-494c-8063-7ee7914c734f`** — healthy run (`scope_search` turn 1, `web_search` turn 2+)

---

## 1. The two filter layers

| Layer | File | When it runs | What it does |
|---|---|---|---|
| `PhaseToolFilterMiddleware` | `src/agents/middlewares/phase_tool_filter_middleware.py` | `wrap_model_call` — **before** the LLM sees the request | Removes tool schemas from the `ModelRequest.tools` list so the model can't call them |
| `ToolDisclosureMiddleware` | `src/agents/middlewares/tool_disclosure_middleware.py` | `wrap_tool_call` — **when the tool is about to execute** | Returns a blocking `ToolMessage` if the model somehow called a tool outside its allowed phase |

These are defense-in-depth: the first prevents the model from seeing the tool, the second prevents execution if the model calls it anyway.

---

## 2. Phase determination (`_should_filter`)

`PhaseToolFilterMiddleware._should_filter(state, runtime)` returns `True` (hide execution tools) when any of:

1. **Plan exists with `status="draft"`** (or status unknown/empty) — waiting for plan approval.
2. **Plan mode is active** (`runtime.context.mode == "plan"`) — regardless of plan state.
3. **No plan, not plan mode, and no AI messages yet** (first turn of any work-mode request).

```python
# From phase_tool_filter_middleware.py lines 79–112
if isinstance(plan, dict):
    status = _normalize_plan_status(plan.get("status"))
    if status == "draft":
        return True
    if status in {"approved", "executing", "completed"}:
        return False
    return True          # unknown status on existing plan — treated as draft

if _is_plan_mode(runtime):
    return True

# First turn of a work-mode query (no AI messages yet):
has_ai_messages = any(getattr(m, "type", None) == "ai" for m in messages)
if not has_ai_messages:
    return True

return False             # work mode, turn 2+, no plan → full catalog
```

The **first-turn filter** exists to give `PlanExecutionGateMiddleware` time to act before
the model fires heavy tools. From turn 2 onward (when the first AI message exists), the
full execution catalog is available.

---

## 3. Tools hidden by phase

### Draft phase (filter = True)

```python
_DRAFT_HIDDEN_TOOLS = frozenset({
    "web_search",
    "query_knowledge_vault",
    "search_internal_documents",
    "task",
    "write_file",
    "str_replace",
})
```

`scope_search` is **not** in this set — it is intentionally available in draft phase
as a lightweight scope-discovery tool before the model commits to a full web search.

### Work phase (filter = False)

```python
_WORK_HIDDEN_TOOLS = frozenset({"scope_search"})
```

`scope_search` is hidden in work phase so the model cannot use it as a lightweight
substitute for `web_search` once execution is approved.

### Phase transition by turn

| Turn | `has_ai_messages` | Effective phase | Available execution tools |
|---|---|---|---|
| 1 (no plan) | False | **draft** | `scope_search` ✓ — `web_search` ✗ |
| 2+ (no plan) | True | **work** | `web_search` ✓ — `scope_search` ✗ |
| Any (plan=draft) | — | **draft** | same as turn 1 |
| Any (plan=approved+) | — | **work** | same as turn 2+ |

---

## 4. Observed behavior in the two reference threads

### Thread `ab005275` — healthy run (remote work location query)

```
ts=614.0  model_call_end  tool_calls=1
          phase_tool_filter: phase=draft, hidden=[web_search, write_file, ...]
          tool_call_start: scope_search   ← correct: model used the available tool
          tool_call_end:   scope_search (5.5s)

ts=633.5  model_call_end  tool_calls=4
          phase_tool_filter: phase=work, hidden=[scope_search]
          tool_call_start x4: web_search  ← all 4 timed out at 30s

ts=663.6  tool_call_timeout x4 → model fell back to query_knowledge_vault → completed
```

Phase switching worked correctly. The model respected the draft phase by calling
`scope_search` on turn 1 and `web_search` from turn 2.

The 4 `web_search` timeouts on turn 2 are a separate issue (SearXNG/network latency),
not a phase-filter problem. The run recovered via `query_knowledge_vault`.

### Thread `615d7ff9` — stuck run (AI job impact query)

```
ts=855.7  model_call_end  tool_calls=4
          phase_tool_filter: phase=draft, hidden=[web_search, write_file, ...]
          tool_call_start x4: web_search  ← model called web_search despite it being hidden
          (no tool_call_end, no after_agent — run stuck forever)
```

Last checkpoint state: `HumanMessage + AIMessage(4 tool_calls)` with 4 pending
`__pregel_tasks: Send(node='tools', ...)`. The web_search tool ran and produced
summarization prompt captures (17:24:29–17:24:32 UTC), but tool results were never
committed back to the graph.

---

## 5. The gap: enforcement layer is disabled

`ToolDisclosureMiddleware.wrap_tool_call` is the fallback that blocks execution when
the model calls a hidden tool. Its enabled state comes from `config.yaml`:

```yaml
tool_disclosure:
  enabled: false   # ← enforcement layer is OFF
```

With `enabled: false`, if the model calls `web_search` in draft phase:

1. `PhaseToolFilterMiddleware` hid it from the catalog ✓
2. Model calls it anyway (non-compliant model response, or tools were already bound)
3. `ToolDisclosureMiddleware.wrap_tool_call` checks `if not self._config.enabled: return handler(request)` — **passes through**
4. Tool executes without the graph being prepared to handle results in this phase
5. Run hangs with pending `__pregel_tasks`

---

## 6. Why the model can call hidden tools

`PhaseToolFilterMiddleware._maybe_rewrite` calls `request.override(tools=kept)` to
return a new `ModelRequest` with the filtered tool list. However:

- If `request.tools` is empty the middleware returns early without filtering (line: `if not tools: return request`)
- Some model backends bind tools to the model object at construction via `model.bind_tools(tools)` and may not respect a per-request override
- Non-compliant model behavior: `qwen/qwen3.6-35b-a3b` may generate tool calls for
  tools not in the current request's tool list if they were present in earlier turns
  or in the system prompt

The `phase_tool_filter` middleware event IS emitted before the stuck runs (confirming
the filter attempted to run), but the model's API call may have already included the
full tool schemas.

---

## 7. Fix options

### Option A — Enable `ToolDisclosureMiddleware` (recommended)

```yaml
# config.yaml
tool_disclosure:
  enabled: true
  block_mode: tool_error
  default_phase: generator
  phase_tools:
    generator: []      # empty = no restriction in this phase
    evaluator: []
```

With `enabled: true`, any tool the model calls outside its allowed phase returns a
blocking `ToolMessage` immediately instead of executing. The model sees the error,
understands it called a restricted tool, and can retry with an allowed tool.

This prevents the stuck-run scenario: instead of hanging pending `__pregel_tasks`, the
model gets a `[tool_disclosure_blocked]` message and continues.

### Option B — Add `web_search` to the `default_phase` allow-list

If first-turn `web_search` in work mode is acceptable (no plan required):

```yaml
tool_disclosure:
  enabled: true
  default_phase: generator
  phase_tools:
    generator: ["web_search", "query_knowledge_vault", "scope_search"]
    evaluator: []
```

This relaxes the first-turn restriction for work-mode research queries.

### Option C — Make the first-turn filter mode-aware

In `_should_filter` (line 106–111), skip the first-turn filter when no planner is
configured and the mode is explicitly `work`. This avoids hiding tools that the model
needs on turn 1 for simple work-mode queries:

```python
# Only block on first turn if planner is actually enabled
if _is_planner_enabled(runtime) and not has_ai_messages:
    return True
```

---

## 8. Related files

| File | Role |
|---|---|
| `backend/src/agents/middlewares/phase_tool_filter_middleware.py` | Pre-model tool catalog filter |
| `backend/src/agents/middlewares/tool_disclosure_middleware.py` | Per-tool-call execution enforcement |
| `backend/src/config/tool_disclosure_config.py` | `ToolDisclosureConfig` — `enabled`, `default_phase`, `phase_tools` |
| `config.yaml` → `tool_disclosure` | Runtime configuration |
| `backend/src/agents/middlewares/plan_execution_gate_middleware.py` | Paired gate that blocks execution-phase tool results if plan is not approved |
