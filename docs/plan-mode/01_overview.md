# 01 — Overview

## What is Plan Mode?

Plan Mode is one of two **runtime modes** in CapyHome (the other being Work
Mode). Both run on a shared agent factory and middleware registry; what
differs is:

- Which prompt overlay is applied
- Which middlewares activate
- Which tools the LLM is allowed to see and call
- What the agent is *supposed to produce*

In Plan Mode, the agent's **single deliverable is a `plan.md` file** — a
canonical, structured handoff artifact that a subsequent Work Mode run will
parse and execute. The agent is explicitly told **not** to produce the user's
answer; it scopes the work, drafts todos with dependencies, asks
clarifications when scope is ambiguous, and stops.

## Why a separate mode?

The original architecture had a single `lead_agent` with conditional plan
behavior. The refactor split it into two LangGraph entry points so that:

1. The frontend and auto-escalation paths can address `plan_agent` **by
   name** (`graph_id="plan_agent"`).
2. Plan-mode discipline (prompt + middleware + tool catalog) is reified —
   you cannot accidentally run plan logic against the work_agent graph or
   vice-versa.
3. A future divergence (separate prompt body, narrower tool surface) can
   happen without touching work_agent.

Today `plan_agent` is a **thin wrapper** around `_build_work_agent` that
forces `current_mode="plan"` and injects the plan-mode prompt template
([plan_agent/agent.py:29-41](../../backend/src/agents/plan_agent/agent.py#L29-L41)).

## High-level lifecycle

```
┌────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ User input │ →  │ Trigger      │ →  │ plan_agent   │ →  │ plan.md      │
│ (chat)     │    │ resolution   │    │ run          │    │ (canonical)  │
└────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
                                                                 │
                                                                 ▼
                                                          ┌─────────────────┐
                                                          │ User reviews    │
                                                          │ (Execute Plan / │
                                                          │  Clarify popup) │
                                                          └─────────────────┘
                                                                 │
                                                       approve   │
                                                                 ▼
                                                          ┌─────────────────┐
                                                          │ work_agent run  │
                                                          │ (parses plan.md │
                                                          │  + executes)    │
                                                          └─────────────────┘
```

## Three states a plan can be in

`plan.status` ∈ `{"draft", "approved", "executing", "completed"}`.

- **`draft`** — Planner has written `plan.md`. LLM is gated from execution
  tools. Clarifications may be pending. Re-planning is allowed (capped at
  5 revisions).
- **`approved`** — Either (a) the user clicked **Execute Plan** in the UI
  and `/api/threads/{id}/plan/execute` flipped the status, or (b)
  `auto_mode=True` + no pending clarifications caused the planner to
  auto-approve on creation. A Work Mode run is spawned.
- **`executing`** — Work Mode has started; `WorkModeMiddleware` drives the
  todo loop.
- **`completed`** — All todos closed; final summary emitted.

## Two trigger paths in detail

### Path A — Manual toggle (user picks Plan Mode)

The input-box toolbar exposes a Plan-Mode chip
([input-box-left-toolbar.tsx:146](../../frontend/src/components/workspace/input-box-left-toolbar.tsx#L146)).
Clicking it flips `settings.context.mode` to `"plan"`
([input-box.tsx:505-549](../../frontend/src/components/workspace/input-box.tsx#L505-L549)).

On send, the thread hook posts to LangGraph with:

```json
{
  "configurable": {
    "current_mode": "plan",
    "mode": "plan",             // legacy dual-write
    "is_plan_mode": true,        // legacy dual-write
    "plan_behavior": "plan_foreground"
  }
}
```

LangGraph routes to whichever graph the frontend named; both
`work_agent` and `plan_agent` factories read `current_mode` and behave
identically when it equals `"plan"`. In practice, modern clients target
`plan_agent` directly.

### Path B — Auto-escalation (Work Mode escalates)

While running in Work Mode, [`WorkModeMiddleware.before_model`](../../backend/src/agents/middlewares/work_mode_middleware.py#L333)
classifies the latest user prompt with `_classify_complexity`
([work_mode_middleware.py:110](../../backend/src/agents/middlewares/work_mode_middleware.py#L110)).
If it lands as `"complex"` AND no plan exists yet, the middleware calls
[`_handle_complexity_escalation`](../../backend/src/agents/middlewares/work_mode_middleware.py#L649):

1. Emit a `complexity_escalation` SSE event so the UI can prompt the user.
2. If `auto_mode=True` is in the runtime context, spawn a daemon
   ([`_spawn_plan_rerun`](../../backend/src/agents/middlewares/work_mode_middleware.py#L233))
   that re-invokes the same thread with `current_mode="plan"` after the
   current Work Mode run reaches its checkpoint.

The same machinery also fires on **`plan_adapted`** events (Work Mode finds
blocked todos and asks Plan Mode to revise), capped at
`_MAX_AUTO_ADAPTATION_ATTEMPTS = 2`
([work_mode_middleware.py:53](../../backend/src/agents/middlewares/work_mode_middleware.py#L53)).

## What Plan Mode is NOT

- **Not** an alternative chat mode for "structured answers". It produces a
  plan, not the answer. The plan-mode prompt explicitly tells the LLM to
  suppress training-data answers ([plan_agent/prompt.py:37-48](../../backend/src/agents/plan_agent/prompt.py#L37-L48)).
- **Not** a place to call `web_search`, `task`, or content-gathering tools.
  Those are stripped from the catalog by `PhaseToolFilterMiddleware`. The
  only research tool exposed is `scope_search` (narrow scope discovery).
- **Not** the trivial fast path. Trivial requests skip the planner LLM via
  `_classify_complexity` ([planner_middleware.py:444](../../backend/src/agents/middlewares/planner_middleware.py#L444))
  and `_looks_like_direct_answer_request` ([planner_middleware.py:459](../../backend/src/agents/middlewares/planner_middleware.py#L459)).
