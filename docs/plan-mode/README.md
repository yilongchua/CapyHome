# Plan Mode — Architectural Analysis

This folder documents the **Plan Mode** flow in CapyHome end-to-end: from the user
typing a request, through trigger resolution, mode resolution, middleware
activation, planner LLM call, plan-gate enforcement, plan approval, and
finally the handoff into Work Mode for execution.

It reflects the state of the codebase on **2026-05-27** after the
`plan_agent` / `work_agent` graph split and the canonical `plan.md`
handoff (`plan_version: 5`).

## Files in this folder

- [`README.md`](README.md) — this index
- [`01_overview.md`](01_overview.md) — what Plan Mode is, why it exists, the
  two trigger paths (manual vs auto-escalation) and the high-level lifecycle.
- [`02_components.md`](02_components.md) — exhaustive inventory of every
  prompt, middleware, tool, skill, route and helper involved in Plan Mode.
- [`03_flow_narrative.md`](03_flow_narrative.md) — step-by-step narrative of
  a single Plan Mode turn end-to-end with file:line references.
- [`04_handoff_contract.md`](04_handoff_contract.md) — the canonical
  `plan.md` schema and how `plan_agent → work_agent` exchange state.
- [`plan_mode_flow.png`](plan_mode_flow.png) — visual flow diagram (rendered
  from [`plan_mode_flow.mmd`](plan_mode_flow.mmd) source).
- [`plan_mode_flow.mmd`](plan_mode_flow.mmd) — Mermaid source for the diagram.

## TL;DR

Plan Mode is a **dedicated LangGraph entry point** (`plan_agent`, registered
in [backend/langgraph.json](../../backend/langgraph.json#L7)) that shares
all infrastructure with `work_agent` but:

1. **Forces** `current_mode="plan"` in `config.configurable` so mode-aware
   middlewares activate ([plan_agent/agent.py:29-41](../../backend/src/agents/plan_agent/agent.py#L29-L41)).
2. **Overrides** the system prompt to append `PLAN_MODE_SECTION`
   ([plan_agent/prompt.py:18-89](../../backend/src/agents/plan_agent/prompt.py#L18-L89)).
3. **Hides** execution tools (`web_search`, `task`, `write_file`,
   `str_replace`, `query_knowledge_vault`, …) from the LLM tool catalog via
   [`PhaseToolFilterMiddleware`](../../backend/src/agents/middlewares/phase_tool_filter_middleware.py),
   exposing `scope_search` (a Plan-Mode wrapper around `web_search`) in its
   place.
4. **Runs the planner LLM** in [`PlannerMiddleware.before_model`](../../backend/src/agents/middlewares/planner_middleware.py#L793)
   on the first eligible turn, producing a structured `PlannerOutput` that
   is serialized to canonical `plan.md` via
   [`serialize_plan_md`](../../backend/src/agents/common/handoff.py#L32).
5. **Backstops** any execution tool that slips past the catalog filter via
   [`PlanExecutionGateMiddleware.wrap_tool_call`](../../backend/src/agents/middlewares/plan_execution_gate_middleware.py#L315).
6. **Optionally** evaluates plan quality with
   [`PlanEvaluatorMiddleware`](../../backend/src/agents/middlewares/plan_evaluator_middleware.py).
7. **Persists** `plan.md` (latest alias + timestamped version) to
   `/mnt/user-data/workspace/` via
   [`PlanFileSyncMiddleware`](../../backend/src/agents/middlewares/plan_file_sync_middleware.py).
8. **Hands off** to Work Mode when the user clicks **Execute Plan**
   ([gateway/routers/steering.py:480](../../backend/src/gateway/routers/steering.py#L480))
   or when the planner auto-approves (auto_mode + no pending clarifications).

## Two ways to enter Plan Mode

Plan Mode is reachable via two distinct triggers (see memory note
*Plan mode two triggers*):

| Trigger | Source | Path |
|---|---|---|
| Manual toggle | User clicks the Plan-Mode chip in the input toolbar | [frontend/src/components/workspace/input-box.tsx:505-549](../../frontend/src/components/workspace/input-box.tsx#L505-L549) → sets `context.mode = "plan"` → LangGraph SDK posts to `plan_agent` graph |
| Auto-escalation | Work Mode detects a complex request with no plan | [`WorkModeMiddleware._handle_complexity_escalation`](../../backend/src/agents/middlewares/work_mode_middleware.py#L649) → emits `complexity_escalation` SSE; if `auto_mode=True`, spawns a daemon `_spawn_plan_rerun` ([work_mode_middleware.py:233](../../backend/src/agents/middlewares/work_mode_middleware.py#L233)) that re-invokes the agent with `current_mode="plan"` |

Both converge on the same `plan_agent` graph, so everything downstream of the
mode-resolution step is identical.
