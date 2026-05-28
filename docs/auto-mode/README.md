# Auto Mode — Code Investigation & Flow

_Last updated: 2026-05-29_

This document is an exhaustive walkthrough of the **Auto Mode** feature in CapyHome:
where the flag lives, what it actually changes, and how the request flows from a
frontend toggle through to backend plan approval and clarification bypass.

Auto Mode is a **modifier on Plan Mode**, not a separate mode. It does **not**
auto-escalate Work Mode into Plan Mode (that behavior was removed — see
[01_plan_mode.md](../code_review/01_plan_mode.md) finding #26). Auto Mode only
removes two human-in-the-loop gates while planning:

1. The **draft → approved** approval gate on a freshly minted plan.
2. The **interrupt-for-clarification** gate when a planner-asked question has a
   `recommended` option.

Work Mode execution itself is unchanged in Auto Mode — phase gating, DAG
ordering, evaluators, and retries all behave identically.

---

## 1. What Auto Mode does (behavioral contract)

| Gate | Normal mode | Auto Mode |
|------|-------------|-----------|
| Planner emits a `draft` plan with no clarifications | Status stays `draft`; UI shows **Execute Plan** button; user must click | Status flips to `approved` immediately; SSE `decision` becomes `plan_auto_approved`; work handoff can run |
| Planner emits a plan with clarifications | Each clarification interrupts — user picks an option in the side panel | If a clarification has a `recommended` option, it is selected automatically with prefix `[Auto Mode] Selected: …`; no interrupt |
| Clarification has no `recommended` option | Interrupts as normal | **Falls through to normal queue** (see [`clarification_middleware.py:233`](../../backend/src/agents/middlewares/clarification_middleware.py#L233)) |
| Last clarification is answered | Plan stays `draft`; user still has to approve | Plan auto-flips to `approved` and Work Mode handoff is spawned ([`planner_middleware.py:858-893`](../../backend/src/agents/middlewares/planner_middleware.py#L858-L893)) |
| Work Mode execution | Unchanged | Unchanged |

Auto Mode does **not** make the system fully autonomous: there is still an
implicit pause before Work Mode begins via `_create_work_mode_run` — see test
[`test_planner_middleware.py:74-81`](../../backend/tests/test_planner_middleware.py#L74-L81)
(`test_auto_mode_approves_plan_and_still_pauses_before_execution`).

---

## 2. State & wire format

### 2.1 `ThreadState.auto_mode`

Declared in [`backend/src/agents/thread_state.py:332-334`](../../backend/src/agents/thread_state.py#L332-L334):

```python
auto_mode: NotRequired[bool]
```

Sits alongside `plan` and Work Mode phase tracking. Reducers leave it as a plain
bool — no merge/clear logic.

### 2.2 Frontend context type

[`frontend/src/core/threads/types.ts:127`](../../frontend/src/core/threads/types.ts#L127):

```ts
interface AgentThreadContext {
  // ...
  auto_mode?: boolean;
}
```

Optional, undefined ≡ `false`.

### 2.3 Run configurable payload

The frontend passes `auto_mode` through to the LangGraph run on submit
([`frontend/src/core/threads/hooks.ts:1359`](../../frontend/src/core/threads/hooks.ts#L1359)):

```ts
runConfigurable: {
  // ...
  auto_mode: context.auto_mode ?? false,
}
```

The backend reads it from **two places** with the same precedence everywhere:
runtime context first, thread state second.

---

## 3. The two backend resolvers

There are two helpers — they look similar but live in different modules and are
called from different middlewares. Both return `True` if **either** source has
`auto_mode` truthy.

### 3.1 `resolve_auto_mode()` — used by the REST endpoint

[`backend/src/agents/middlewares/plan_execution.py:319-324`](../../backend/src/agents/middlewares/plan_execution.py#L319-L324):

```python
def resolve_auto_mode(values, *, request_auto_mode=None) -> bool:
    if request_auto_mode is not None:
        return bool(request_auto_mode)
    if bool(values.get("auto_mode")):
        return True
    return False
```

Precedence: **request body override → ThreadState value → False**. Called from
`POST /threads/{id}/execute_plan` so the user can override per-call.

### 3.2 `_auto_mode_enabled()` — used by the planner middleware

[`backend/src/agents/middlewares/planner_middleware.py:82-86`](../../backend/src/agents/middlewares/planner_middleware.py#L82-L86):

```python
def _auto_mode_enabled(runtime, state) -> bool:
    ctx = _runtime_context(runtime)
    if bool(ctx.get("auto_mode")):
        return True
    return bool(state.get("auto_mode"))
```

Precedence: **runtime configurable context → ThreadState**. This is the path
the live agent loop uses.

### 3.3 `ClarificationMiddleware.before_model` — cache for tool wrapper

[`clarification_middleware.py:120-133`](../../backend/src/agents/middlewares/clarification_middleware.py#L120-L133)
caches the flag on `runtime.context[_AUTO_MODE_CTX_KEY]` so the async
`wrap_tool_call` path can read it without re-pulling configurable. Precedence
chain reads `configurable["auto_mode"] → context["auto_mode"] → state["auto_mode"]`.

---

## 4. Where the gates are bypassed

### 4.1 Plan auto-approval on creation

[`planner_middleware.py:984-986`](../../backend/src/agents/middlewares/planner_middleware.py#L984-L986):

```python
auto_mode = _auto_mode_enabled(runtime, state)
plan_status = "approved" if auto_mode and not clarification_pending else "draft"
approved_at = _utc_now_iso() if plan_status == "approved" else None
```

The SSE `plan_created` event downstream
([`planner_middleware.py:1081-1083`](../../backend/src/agents/middlewares/planner_middleware.py#L1081-L1083))
re-labels its `decision` field:

```python
if plan_status == "approved":
    plan_created_event["decision"] = "plan_auto_approved"
```

The planner handoff `HumanMessage` content adjusts its system-prompt-style
trailer too — `(auto-approved; you may use execution tools now)` vs.
`(draft — do NOT call web_search, task, or write_file until the user approves
via Execute Plan)` — see lines 1118-1123 of the same file.

### 4.2 Plan auto-approval after the last clarification

[`planner_middleware.py:858-897`](../../backend/src/agents/middlewares/planner_middleware.py#L858-L897):
when a clarification answer is recorded and **no further** clarifications
remain, `approve_plan_if_auto_mode` flips status to `approved`, then
`spawn_work_mode_handoff` is fired in the same turn if the plan is ready for
handoff.

### 4.3 `approve_plan_if_auto_mode()` helper

[`plan_execution.py:395-405`](../../backend/src/agents/middlewares/plan_execution.py#L395-L405):

```python
def approve_plan_if_auto_mode(plan, *, auto_mode: bool) -> dict[str, Any]:
    if not auto_mode:
        return plan
    if str(plan.get("status") or "").strip().lower() != "draft":
        return plan
    return {
        **plan,
        "status": "approved",
        "approved_at": _utc_now_iso(),
        "awaiting_execution_approval": False,
    }
```

Idempotent — only acts on `draft` plans. Used by both the clarification
resolution path (`planner_middleware`) and the recover-stalled-plan path
(`steering.execute_plan`).

### 4.4 Clarification auto-selection

[`clarification_middleware.py:198-233`](../../backend/src/agents/middlewares/clarification_middleware.py#L198-L233):

```python
context = getattr(request.runtime, "context", None) or {}
auto_mode = bool(context.get(_AUTO_MODE_CTX_KEY, False))

entry = self._build_entry(args, tool_call_id)

if auto_mode:
    recommended = self._get_recommended_label(args)
    if recommended:
        entry["status"] = "answered"
        entry["answer"] = recommended
        entry["answered_at"] = _utc_now_iso()
        logger.info("Auto mode: auto-selecting '%s' for clarification: %s", ...)
        return Command(update={
            "clarifications": [entry],
            "messages": [
                ToolMessage(
                    content=f"[Auto Mode] Selected: {recommended}",
                    tool_call_id=tool_call_id,
                    name="ask_user_for_clarification",
                )
            ],
        })
    logger.info("Auto mode: no recommended option for clarification '%s'; falling through to normal queue", ...)
```

Two observable consequences:

- The tool-message content always starts with the literal `[Auto Mode] Selected: `.
  The constant `_AUTO_MODE_PREFIX = "[Auto Mode] Selected:"`
  ([`plan_execution.py:20`](../../backend/src/agents/middlewares/plan_execution.py#L20))
  is the matching marker used elsewhere to recognize an auto-selection in the
  message history.
- If the planner forgot to mark any option as `recommended`, Auto Mode silently
  becomes a no-op for that one question and the normal interrupt path is used.

---

## 5. End-to-end paths

There are three entry points that carry `auto_mode` into the agent runtime.

### 5.1 Path A — submit a brand-new message in Auto Mode

```
User toggles Auto Mode switch
  ↓ input-box-left-toolbar.tsx PrivacyAndAutoMenu (Switch onCheckedChange)
  ↓ input-box.tsx handleToggleAutoMode → onContextChange({...ctx, auto_mode: !})
  ↓ hooks.ts: runConfigurable.auto_mode = context.auto_mode ?? false
  ↓ POST /threads/{id}/runs (LangGraph SDK)
  ↓ plan_agent runtime → PlannerMiddleware.before_model
  ↓ _auto_mode_enabled(runtime, state)
  ├─ plan has no clarifications     → status = "approved" (4.1)
  │                                  → SSE plan_created decision="plan_auto_approved"
  │                                  → planner emits "execute_plan" handoff
  │                                    via spawn_work_mode_handoff (Path C below)
  └─ plan has clarifications        → status = "draft"
                                     → first clarification asked
                                     → ClarificationMiddleware caches auto_mode
                                     → recommended option auto-selected (4.4)
                                     → next clarification, or all-done
                                     → after final answer, approve_plan_if_auto_mode (4.2)
                                     → spawn_work_mode_handoff
```

### 5.2 Path B — user manually clicks "Execute Plan" with optional auto-mode override

[`backend/src/gateway/routers/steering.py:480-613`](../../backend/src/gateway/routers/steering.py#L480-L613)
handles `POST /api/threads/{id}/execute_plan`:

```python
auto_mode = resolve_auto_mode(values, request_auto_mode=request.auto_mode)
# ...
await _create_work_mode_run(
    client=client,
    thread_id=thread_id,
    requested_model_name=requested_model_name,
    auto_mode=auto_mode,         # ← injected into run context
    original_user_request=user_prompt,
)
```

The request body field `ExecutePlanRequest.auto_mode: bool | None`
([`steering.py:66-76`](../../backend/src/gateway/routers/steering.py#L66-L76))
lets the UI override per-execute. `_create_work_mode_run`
([`steering.py:188-242`](../../backend/src/gateway/routers/steering.py#L188-L242))
injects `auto_mode` into the run's `context` dict, alongside `current_mode="work"`,
`plan_behavior="work_interactive"`, etc., so the next Work Mode turn sees the
same flag.

### 5.3 Path C — embedded Python client

[`backend/src/client.py:113, 139, 193, 365, 458`](../../backend/src/client.py#L113):

```python
class CapyHomeClient:
    def __init__(self, ..., auto_mode: bool = False):
        self._auto_mode = auto_mode

    def chat(self, message, thread_id, *, overrides=None):
        context = {
            # ...
            "auto_mode": overrides.get("auto_mode", self._auto_mode),
        }
```

Every embedded call (`chat`, `stream`, `resume_run`) injects `auto_mode` into
the run context, so programmatic API users get the same behavior as the UI.

---

## 6. SSE / event surface

Auto Mode does **not** add a dedicated SSE event type. It surfaces through
existing events:

| Event | Field added by Auto Mode |
|-------|--------------------------|
| `plan_created` (planner runtime event) | `decision = "plan_auto_approved"` instead of `"plan_created"`; `plan_status = "approved"` |
| `plan_created` (state plan) | `status = "approved"`, `approved_at` set |
| `clarification_resolved` (planner runtime event) | `clarification_pending = false` arrives faster (no user wait) |
| Tool message `ask_user_for_clarification` | `content` starts with `[Auto Mode] Selected: <label>` |

There is one tangentially related event — `plan_adapted` — emitted by
`WorkModeMiddleware` when a plan stalls in Work Mode. It is **not** Auto Mode
specific (any plan can stall), but in Auto Mode you'll see the plan reach Work
Mode faster, which can make `plan_adapted` more visible. See
[`docs/code_review/01_plan_mode.md`](../code_review/01_plan_mode.md) for the
auto-escalation removal history.

---

## 7. Frontend UI

The toggle lives in the input box, next to Plan Mode, in the sliders dropdown.

### 7.1 Dropdown component — `PrivacyAndAutoMenu`

[`frontend/src/components/workspace/input-box-left-toolbar.tsx:130-203`](../../frontend/src/components/workspace/input-box-left-toolbar.tsx#L130-L203)

- Trigger: `SlidersHorizontalIcon` button.
- Two switches in one menu under header **"Plan Mode & Auto Mode"**:
  - Plan Mode switch (`planModeEnabled = mode === "plan"`)
  - Auto Mode switch (`autoModeEnabled` prop, fed by `context.auto_mode`)

Both switches are independent — Auto Mode can be on while Plan Mode is off
(the flag is harmless until Plan Mode plans something).

### 7.2 Wiring inside `InputBox`

[`frontend/src/components/workspace/input-box.tsx:509, 544-549, 1368-1370`](../../frontend/src/components/workspace/input-box.tsx#L509)

```tsx
const autoModeEnabled = context.auto_mode === true;

const handleToggleAutoMode = useCallback(() => {
  onContextChange({ ...context, auto_mode: !context.auto_mode });
}, [context, onContextChange]);
```

The context object flows up to the thread store, which then injects it into
`runConfigurable` on the next submit.

---

## 8. Tests

| File | Test | What it covers |
|------|------|----------------|
| [`tests/test_planner_middleware.py:74-81`](../../backend/tests/test_planner_middleware.py#L74-L81) | `test_auto_mode_approves_plan_and_still_pauses_before_execution` | Plan flips to `approved`, `approved_at` set, SSE `jump_to="end"`, BUT work handoff does not auto-fire. |
| `tests/test_clarification_middleware.py` | various | Recommended-option auto-selection, fallthrough when no recommendation, `[Auto Mode] Selected:` prefix. |
| `tests/test_work_mode_middleware.py` | plan-adapted detection | Unrelated to Auto Mode directly; relevant because Auto Mode makes Work Mode reachable faster. |
| `tests/test_planner_evaluator_middleware.py` | evaluator + Auto Mode interaction | Evaluator still runs on auto-approved plans. |
| `tests/test_daemon_agent_invoke.py` | handoff daemon | Verifies `spawn_work_mode_handoff` is invoked with the right `auto_mode` arg. |
| `tests/test_client.py` | `CapyHomeClient` | `auto_mode=True` constructor and `overrides={"auto_mode": True}` propagate to run context. |

---

## 9. Distinction from Plan Mode

| | Plan Mode | Auto Mode |
|---|-----------|-----------|
| What it is | A graph selector — `make_plan_agent` vs `make_work_agent` | A boolean modifier in run context |
| How to enable | Shift+Tab or the Plan Mode switch | The Auto Mode switch |
| Default | Off | Off |
| What it does | Activates planner / evaluator / DAG middlewares | Skips draft→approved approval and auto-answers clarifications with `recommended` |
| Effect on Work Mode execution | Loads work_agent after handoff | None (Work Mode runs identically) |
| Can be auto-triggered? | **No** (auto-escalation removed; user must toggle) | No (user must toggle) |

See memory note [`plan_mode_two_triggers.md`](../../.. /…/memory/plan_mode_two_triggers.md):
since 2026-05-28 there's only one plan-mode trigger (manual toggle); the prior
auto-escalation is gone, and `plan_adapted` SSE replaces it.

---

## 10. File index

### Backend
- [`backend/src/agents/thread_state.py:332-334`](../../backend/src/agents/thread_state.py#L332-L334) — `auto_mode: NotRequired[bool]`
- [`backend/src/agents/middlewares/plan_execution.py:20`](../../backend/src/agents/middlewares/plan_execution.py#L20) — `_AUTO_MODE_PREFIX`
- [`backend/src/agents/middlewares/plan_execution.py:319-324`](../../backend/src/agents/middlewares/plan_execution.py#L319-L324) — `resolve_auto_mode`
- [`backend/src/agents/middlewares/plan_execution.py:395-405`](../../backend/src/agents/middlewares/plan_execution.py#L395-L405) — `approve_plan_if_auto_mode`
- [`backend/src/agents/middlewares/planner_middleware.py:82-86`](../../backend/src/agents/middlewares/planner_middleware.py#L82-L86) — `_auto_mode_enabled`
- [`backend/src/agents/middlewares/planner_middleware.py:858-897`](../../backend/src/agents/middlewares/planner_middleware.py#L858-L897) — auto-approve after last clarification + spawn handoff
- [`backend/src/agents/middlewares/planner_middleware.py:984-986, 1081-1083, 1118-1123`](../../backend/src/agents/middlewares/planner_middleware.py#L984) — plan-creation auto-approval + SSE labeling
- [`backend/src/agents/middlewares/clarification_middleware.py:120-133`](../../backend/src/agents/middlewares/clarification_middleware.py#L120-L133) — `before_model` caches flag
- [`backend/src/agents/middlewares/clarification_middleware.py:198-233`](../../backend/src/agents/middlewares/clarification_middleware.py#L198-L233) — auto-selection in `_handle_clarification`
- [`backend/src/agents/middlewares/work_run_handoff.py`](../../backend/src/agents/middlewares/work_run_handoff.py) — `spawn_work_mode_handoff` consumer
- [`backend/src/gateway/routers/steering.py:66-76`](../../backend/src/gateway/routers/steering.py#L66-L76) — `ExecutePlanRequest.auto_mode`
- [`backend/src/gateway/routers/steering.py:188-242`](../../backend/src/gateway/routers/steering.py#L188-L242) — `_create_work_mode_run` injects `auto_mode` into context
- [`backend/src/gateway/routers/steering.py:480-613`](../../backend/src/gateway/routers/steering.py#L480-L613) — `execute_plan` endpoint
- [`backend/src/client.py:113, 139, 193, 365, 458`](../../backend/src/client.py#L113) — embedded client `auto_mode`

### Frontend
- [`frontend/src/core/threads/types.ts:127`](../../frontend/src/core/threads/types.ts#L127) — `AgentThreadContext.auto_mode`
- [`frontend/src/core/threads/hooks.ts:1359`](../../frontend/src/core/threads/hooks.ts#L1359) — submit-time configurable injection
- [`frontend/src/components/workspace/input-box.tsx:509, 544-549, 1368-1370`](../../frontend/src/components/workspace/input-box.tsx#L509) — derived state, toggle handler, menu wiring
- [`frontend/src/components/workspace/input-box-left-toolbar.tsx:130-203`](../../frontend/src/components/workspace/input-box-left-toolbar.tsx#L130-L203) — `PrivacyAndAutoMenu` dropdown

### Tests
- [`backend/tests/test_planner_middleware.py`](../../backend/tests/test_planner_middleware.py)
- [`backend/tests/test_clarification_middleware.py`](../../backend/tests/test_clarification_middleware.py)
- [`backend/tests/test_work_mode_middleware.py`](../../backend/tests/test_work_mode_middleware.py)
- [`backend/tests/test_planner_evaluator_middleware.py`](../../backend/tests/test_planner_evaluator_middleware.py)
- [`backend/tests/test_daemon_agent_invoke.py`](../../backend/tests/test_daemon_agent_invoke.py)
- [`backend/tests/test_client.py`](../../backend/tests/test_client.py)

---

## 11. Diagram

See [`auto-mode-flow.png`](auto-mode-flow.png) (source: [`auto-mode-flow.mmd`](auto-mode-flow.mmd)).
