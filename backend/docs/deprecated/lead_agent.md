# DEPRECATED — `lead_agent` module

**Status:** Removed. **Removed on:** 2026-05-30.
**Superseded by:** [`src/agents/work_agent/`](../../src/agents/work_agent/) and [`src/agents/plan_agent/`](../../src/agents/plan_agent/).

> This document is the deprecation record for the old `src/agents/lead_agent/`
> package. The package was deleted because it had already been split into two
> mode-specific graphs. Nothing in the runtime imports it. This file preserves
> the history and tells you how to reconstruct a single unified lead-agent
> entry point if that direction is ever wanted again.

---

## What it was

`src/agents/lead_agent/` was the original home of the **single top-level
orchestrating agent** ("lead agent") — the agent that holds the user
conversation and `ThreadState`, runs the full middleware chain, and delegates
to subagents via the `task` tool (as opposed to the subagents themselves).

Original layout (all source, now gone):

```
src/agents/lead_agent/
├── __init__.py
├── agent.py          # make_lead_agent(config) factory + middleware registry wiring
├── prompt.py         # apply_prompt_template() — system prompt assembly, SOUL.md injection
├── prompt_cache.py   # mtime-based system-prompt cache
└── todo_prompts.py   # plan/todo prompt fragments
```

Entry point: `make_lead_agent(config: RunnableConfig)`, registered in
`langgraph.json` as the graph `lead_agent` (`src.agents:make_lead_agent`).
`is_plan_mode` in `config.configurable` toggled the TodoList / planner
middlewares at runtime inside the one factory.

## Why it was removed

"Lead agent" stopped being a *module* and became a *role*. The single factory
was split into two LangGraph graphs sharing one state schema, so the frontend's
manual Plan/Work toggle (Shift+Tab) can route to a distinct entry point and the
LLM-facing tool catalog can be selected up-front per mode rather than filtered
at runtime:

| Old (`lead_agent`)            | New                                                              |
| ----------------------------- | --------------------------------------------------------------- |
| `make_lead_agent`             | `make_work_agent` (`src.agents:make_work_agent`)                |
| `is_plan_mode` runtime toggle | `make_plan_agent` (`src.agents:make_plan_agent`), `current_mode="plan"` |
| `lead_agent/agent.py`         | [`work_agent/agent.py`](../../src/agents/work_agent/agent.py) (`_build_work_agent`) |
| `lead_agent/prompt.py`        | [`work_agent/prompt.py`](../../src/agents/work_agent/prompt.py) (+ [`plan_agent/prompt.py`](../../src/agents/plan_agent/prompt.py) overlay) |
| `lead_agent/prompt_cache.py`  | [`work_agent/prompt_cache.py`](../../src/agents/work_agent/prompt_cache.py) |
| `lead_agent/todo_prompts.py`  | [`work_agent/todo_prompts.py`](../../src/agents/work_agent/todo_prompts.py) |

[`plan_agent/agent.py`](../../src/agents/plan_agent/agent.py) is a thin wrapper:
it forces `current_mode="plan"` and delegates to `_build_work_agent(...,
prompt_template_fn=plan_apply_prompt_template)`. The shared middleware registry
conditionally activates plan-mode middlewares (`PlannerMiddleware`,
`PlanEvaluatorMiddleware`, `PlanExecutionGateMiddleware`,
`PlanFileSyncMiddleware`, `TodoDagMiddleware`) when `is_plan_mode=True`.

The default agent build in `work_agent/agent.py` is still labelled
`# Default lead agent`, and the system prompt still names the agent
`"Lead Agent"` by default — confirming the role lives on, only the module name
changed.

## How to reimplement a unified lead-agent entry point

If you ever want to collapse Work/Plan back into one graph (e.g. to expose a
single `make_lead_agent` again), you do **not** recreate the old package —
build a thin wrapper over the existing `_build_work_agent`, mirroring how
`make_plan_agent` already works:

1. **Add the factory.** In a new `src/agents/lead_agent/agent.py` (or anywhere):

   ```python
   from langchain_core.runnables import RunnableConfig
   from src.agents.common.mode import resolve_current_mode
   from src.agents.work_agent.agent import _build_work_agent
   from src.agents.work_agent.prompt import apply_prompt_template as work_prompt
   from src.agents.plan_agent.prompt import apply_prompt_template as plan_prompt

   def make_lead_agent(config: RunnableConfig):
       cfg = dict(config.get("configurable") or {})
       mode = resolve_current_mode(cfg)            # "work" | "plan"
       prompt_fn = plan_prompt if mode == "plan" else work_prompt
       # dual-write the legacy fields so middlewares that still read them agree
       cfg["current_mode"] = mode
       cfg["is_plan_mode"] = mode == "plan"
       cfg["mode"] = mode
       forced: RunnableConfig = {**config, "configurable": cfg}
       return _build_work_agent(forced, prompt_template_fn=prompt_fn)
   ```

   Note this re-introduces the **runtime** mode branch the split was meant to
   avoid: tool-catalog selection (`internal_tools_plan.json` vs
   `internal_tools_work.json`) happens at build time in
   `get_available_tools(mode=...)`, so a single graph must settle `mode` before
   the agent is built (as above) — you cannot flip mode mid-run within one graph
   instance.

2. **Register the graph** in [`langgraph.json`](../../langgraph.json):

   ```json
   "graphs": {
     "lead_agent": "src.agents:make_lead_agent"
   }
   ```

3. **Export it** from [`src/agents/__init__.py`](../../src/agents/__init__.py)
   (add `make_lead_agent` to the imports and `__all__`).

4. **Point the frontend** at the single `lead_agent` graph id and drop the
   Shift+Tab graph switch (mode would travel in `configurable.current_mode`
   instead of selecting a graph).

Everything else — middlewares, prompts, tools, `ThreadState` — is already
shared, so there is no other code to restore.

## Related reading

- [docs/plan_mode_usage.md](../plan_mode_usage.md) — current Plan/Work usage
- [docs/ARCHITECTURE.md](../ARCHITECTURE.md) — current entry points
- `backend/CLAUDE.md` → *Agent System* — authoritative description of
  `make_work_agent` / `make_plan_agent` and the middleware chain
