# 03 — End-to-End Flow Narrative

This document walks through a single Plan Mode turn from user keystroke to
`plan.md` on disk, then through the Execute-Plan handoff into Work Mode.
References use `file:line` so each step can be traced in the codebase.

## Phase 0 — Frontend prepares the request

User types a request into the input box. If the Plan-Mode chip is on,
`settings.context.mode === "plan"`
([input-box.tsx:505](../../frontend/src/components/workspace/input-box.tsx#L505)).
On submit, `core/threads/hooks.ts` builds a LangGraph `stream` request with:

```ts
configurable: {
  current_mode: "plan",
  mode: "plan",            // legacy alias (kept until step 8 of migration)
  is_plan_mode: true,       // legacy boolean (same)
  plan_behavior: "plan_foreground",
  auto_mode,
  thinking_enabled, subagent_enabled, model_name, …
}
```

The LangGraph SDK posts to the `plan_agent` graph (or `work_agent` with
`mode="plan"` — same behaviour after mode resolution).

## Phase 1 — Graph factory

LangGraph invokes `make_plan_agent(config)`
([plan_agent/agent.py:29](../../backend/src/agents/plan_agent/agent.py#L29)).
It forces the canonical mode flags and delegates to
`_build_work_agent(config, prompt_template_fn=plan_apply_prompt_template)`
([work_agent/agent.py:712](../../backend/src/agents/work_agent/agent.py#L712)).

`_build_work_agent` then:

1. Calls `_extract_runtime_params` to unpack `is_plan_mode=True`,
   `plan_behavior`, `auto_mode`, etc.
2. Resolves the chat model via `ModelRouter.resolve("generator", ...)`.
3. Reconciles `thinking_enabled` vs the model's `supports_thinking`.
4. Calls the **plan-mode prompt builder**
   ([plan_agent/prompt.py:104](../../backend/src/agents/plan_agent/prompt.py#L104))
   which produces `work_base_prompt + "\n\n" + PLAN_MODE_SECTION`.
5. Calls `_build_middlewares(config, ...)` →
   `_build_middleware_registry` → `topological_sort_middleware_specs`
   ([work_agent/agent.py:466](../../backend/src/agents/work_agent/agent.py#L466)).
   The `ctx.is_plan_mode` flag drives conditional construction of
   `PlannerMiddleware`, `PlanEvaluatorMiddleware`,
   `TodoDagMiddleware`/`TodoMiddleware`. `WorkModeMiddleware` is NOT built
   (its factory returns `None` when `is_work_mode=False`).
6. Calls `create_agent(model, tools, middleware, system_prompt, state_schema)`.
   `get_available_tools` is called once and returns the **full** tool list
   — the per-call filtering happens later inside `PhaseToolFilterMiddleware`.

## Phase 2 — First model cycle: planning

LangGraph kicks the run; middlewares run in topologically-sorted order
(see [work_agent/agent.py:491-560](../../backend/src/agents/work_agent/agent.py#L491-L560)
for the full spec list).

### 2a. `before_model` hooks (in order)

- `ThreadDataMiddleware` — creates per-thread workspace directory.
- `SteeringMiddleware` — drains any pending steering intents.
- `UploadsMiddleware` — injects newly uploaded files.
- `SandboxMiddleware` — acquires sandbox.
- `WorkModeMiddleware` — **not present** (plan mode).
- `PlanExecutionGateMiddleware` — passive at this stage (no plan yet).
- `PermissionMiddleware`, `ToolDisclosureMiddleware`, `HooksMiddleware` —
  declarative gating.
- `SummarizationMiddleware` — uses the `"plan"` mode override for
  trigger/keep thresholds.
- `SkillDisclosureMiddleware` — injects active skill bodies.
- **`PlannerMiddleware.before_model`** ← *the main event*.

### 2b. PlannerMiddleware fires

[planner_middleware.py:793](../../backend/src/agents/middlewares/planner_middleware.py#L793).

1. Check `_should_plan(state, runtime)`:
   - If a draft plan exists with a fresh user message → re-plan (capped at 5).
   - If no plan yet and there is at least 1 HumanMessage → plan.
   - In plan mode, allow planning even when prior AI turns exist.
2. Extract `user_prompt = original_user_prompt(messages)`.
3. **Complexity classification** (cheap, local):
   - `_classify_complexity(prompt)` → `"trivial" | "moderate" | "complex"`.
   - `"trivial"` short-circuits, no LLM call.
   - `_looks_like_direct_answer_request` short-circuits checklists, comparisons,
     etc.
4. Emit `planning_started` SSE.
5. **Call the planner LLM** with `PLANNER_SYSTEM_PROMPT`
   ([planner_middleware.py:204](../../backend/src/agents/middlewares/planner_middleware.py#L204)).
   The planner uses the same chat-selected model
   (`resolve_model_name(requested_model)` — single-model invariant).
6. Parse JSON output via `_parse_plan_response` into `PlannerOutput`.
   Tolerates markdown fences and falls back to per-line todos if JSON parse
   fails.
7. Normalize todos into DAG nodes via
   `normalize_todo_nodes` + `_materialize_ready_ids`.
8. Augment domain-specific clarifications via `_ensure_research_clarifications`
   (timeframe / scope for research-domain plans).
9. Decide plan_status:
   - `auto_mode AND not clarification_pending` → `"approved"` (auto-approve)
   - otherwise → `"draft"`
10. **Write `plan.md`** twice:
    - Versioned: `<workspace>/plans/plan-YYYYMMDD-HHMMSS-<slug>.md`
    - Latest alias: `<workspace>/plan.md`
    - Both use `serialize_plan_md(plan, todo_graph, body_renderer=render_plan_md)`
      so the frontmatter is canonical (`plan_version: 5`).
11. Emit `plan_created` SSE with inline clarifications so the frontend can
    render the popup directly.
12. Build the **`planner_handoff` ephemeral HumanMessage** describing the
    plan to the model on the next cycle.
13. If `should_spawn_work_handoff` (auto-mode + approved + no clarifications),
    call `spawn_work_mode_handoff` to fire the daemon-thread Work Mode
    handoff. If `plan_behavior == "plan_foreground"`, set `jump_to="end"`
    so the planner turn ends here.
14. Return the state update with `plan`, `todo_graph`, `todos`,
    `complexity_tier`, `planner_ephemeral_handoff`, etc.

### 2c. PlanEvaluatorMiddleware

[plan_evaluator_middleware.py:223](../../backend/src/agents/middlewares/plan_evaluator_middleware.py#L223)
runs after the planner. It:

1. Skips trivial plans and already-evaluated ones.
2. Calls the planner model with `_PLAN_EVAL_PROMPT` under a hard timeout
   (`evaluator.plan_evaluator_timeout_seconds`, default 10s).
3. If issues are found AND `revised_todos` is structurally valid, rewrites
   the `todo_graph`. Otherwise keeps the original.
4. Sets `plan_evaluated=True` to short-circuit on subsequent cycles.

### 2d. `PhaseToolFilterMiddleware.wrap_model_call`

[phase_tool_filter_middleware.py:201](../../backend/src/agents/middlewares/phase_tool_filter_middleware.py#L201).

When the LangChain agent is about to call the LLM, this middleware:

1. Reads `state.plan` → `plan.status == "draft"` → `_should_filter=True`.
2. Drops `_DRAFT_HIDDEN_TOOLS` from the LLM's bound tool catalog
   ([phase_tool_filter_middleware.py:41](../../backend/src/agents/middlewares/phase_tool_filter_middleware.py#L41)).
3. Also drops any JSON-policy tool whose `mode`/`phase` excludes `plan`/`draft`.
4. Emits a `tools_hidden` runtime event.

The LLM call now goes out with the **restricted** catalog — the model
literally cannot emit a tool call for `web_search` etc.

### 2e. The LLM responds

In `plan_foreground` mode the planner middleware has already set
`jump_to="end"`, so the LLM **never runs this turn**. The frontend receives
`plan_created` SSE and renders the Execute-Plan popup.

If `plan_foreground` is not in effect (legacy/single-graph flow), the LLM
sees the `<planner_handoff>` system message and either drafts refinements
via `write_todos` / `ask_user_for_clarification` / `scope_search` or stops.

## Phase 3 — User interaction with the popup

Three branches:

### 3a. User answers a clarification inline

Frontend POSTs to `/api/threads/{id}/plan/clarify`
([steering.py:649](../../backend/src/gateway/routers/steering.py#L649)).
That endpoint:

1. Fetches current state via `client.threads.get_state`.
2. Validates the answer matches an existing option label.
3. Synthesizes a `HumanMessage(content=selected_option_label)`.
4. Calls `apply_clarification_progress(plan, messages + [prompt, answer])`
   to advance `clarification_index`.
5. Persists the new plan state + the synthetic answer in thread messages.

When the next planning turn fires, `PlannerMiddleware.before_model` sees
`clarification_pending=False` and either auto-approves (auto_mode) or stays
in draft awaiting the Execute Plan click.

### 3b. User edits `plan.md` directly

The on-disk `plan.md` is the canonical source. `PlanFileSyncMiddleware`
keeps it in sync after model turns
([plan_file_sync_middleware.py:52](../../backend/src/agents/middlewares/plan_file_sync_middleware.py#L52)),
but at handoff time `_load_canonical_plan_overrides`
([work_run_handoff.py:22](../../backend/src/agents/middlewares/work_run_handoff.py#L22))
re-reads the file from disk and parses it via `parse_plan_md`. If
`plan_version >= 5`, the parsed `(plan, todo_graph)` **override** the
checkpointed state on the Work Mode run.

### 3c. User clicks Execute Plan

Frontend POSTs to `/api/threads/{id}/plan/execute`
([steering.py:480](../../backend/src/gateway/routers/steering.py#L480)).
The endpoint:

1. Fetches current state.
2. Refuses if no plan exists or `plan_id` mismatches.
3. Handles `current_status` cases:
   - Already `approved`/`executing`/`completed`: dedupe via
     `execute_plan_should_duplicate`, or recover by creating a fresh Work
     Mode run.
   - `draft` + clarification still pending: returns `409 conflict`.
   - `draft` + ready: flips `status="approved"`, sets `approved_at`,
     marks handoff requested, updates `plan_history`.
4. Calls `_create_work_mode_run(client, thread_id, …)`
   ([steering.py:188](../../backend/src/gateway/routers/steering.py#L188))
   which registers a new run on the LangGraph Server with:
   - `assistant_id="work_agent"`
   - `input={"messages": [HumanMessage(name="execute_plan", content="<execute_plan/>")]}`
   - `context={"current_mode": "work", "plan_behavior": "work_interactive", auto_mode, …}`
5. Marks the plan with `mark_handoff_succeeded`.
6. Returns `{run_id, assistant_id}` so the frontend can subscribe to the
   Work Mode SSE stream.

## Phase 4 — Work Mode picks up the plan

The new run lands in `make_work_agent`. Mode resolution gives `"work"`, so
`WorkModeMiddleware` is constructed and `PlannerMiddleware` is not.

Notable handoff steps:

- The checkpointer-restored `plan` already has `status="approved"`.
- `PhaseToolFilterMiddleware` now drops `scope_search` (work phase) and
  keeps the full execution catalog.
- `WorkModeMiddleware.before_model` finds the first ready todo via
  `_materialize_ready_ids`, emits `phase_started` SSE, and injects a
  `<work_mode_instruction>` HumanMessage instructing the model to execute
  that todo and emit no other text.
- The model executes the todo (often via subagent dispatch through `task`).
- Each cycle, `WorkModeMiddleware` detects newly-completed todos via
  set diff and emits `phase_completed` SSE.
- When all todos complete, the middleware returns `None`, letting the
  model produce the final user-facing summary.

## Phase 5 — Background plan file sync

After every Work Mode turn, `PlanFileSyncMiddleware.after_model`:

1. Detects a terminal AI response (no tool calls, has content).
2. Snapshots state and starts a daemon thread that sleeps 1s then calls
   `ensure_plan_state` + `sync_handoff_files_from_state`.
3. The on-disk `plan.md` stays current with `status="executing"`,
   completed todos, etc.

This ensures the user-visible `plan.md` always matches reality even if a
run is interrupted.

## Alternate entry — Auto-escalation from Work Mode

When the user submits a complex request in Work Mode without Plan Mode
selected, the sequence is:

1. `make_work_agent` builds the work-mode middleware chain.
2. `WorkModeMiddleware.before_model` runs and, finding no plan, classifies
   the prompt via `_classify_complexity`
   ([work_mode_middleware.py:110](../../backend/src/agents/middlewares/work_mode_middleware.py#L110)).
3. On `"complex"` it calls `_handle_complexity_escalation`
   ([work_mode_middleware.py:649](../../backend/src/agents/middlewares/work_mode_middleware.py#L649))
   which:
   - Emits `complexity_escalation` SSE (frontend can show "switch to Plan
     Mode?" prompt).
   - If `auto_mode=True`, calls `_spawn_plan_rerun` to schedule a
     `plan_agent` run on the same thread once the current cycle reaches its
     checkpoint.
4. The daemon re-enters at Phase 1 with `current_mode="plan"` and the same
   user prompt, this time landing in `make_plan_agent` flow.

A related path — `_handle_plan_adapted` — fires when Work Mode finds
**blocked todos** (dependencies unsatisfied). It spawns a similar Plan
Mode re-run, capped at `_MAX_AUTO_ADAPTATION_ATTEMPTS = 2`
([work_mode_middleware.py:53](../../backend/src/agents/middlewares/work_mode_middleware.py#L53)).
