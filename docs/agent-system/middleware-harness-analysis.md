# CapyHome Agent Middleware Harness — Analysis Report

> Generated: 2026-08-31  
> Scope: `backend/src/agents/middlewares/` + assembly in `work_agent/agent.py`

---

## 1. Architecture Overview

The harness is built on top of the LangGraph / LangChain Agent SDK. Every middleware subclasses `AgentMiddleware[StateT]` from the SDK and can override five hook points:

```
┌─────────────────────────────────────────────────────────┐
│                   Agent Turn (one loop iteration)        │
│                                                          │
│  before_agent()  ──► (first turn only)                  │
│                                                          │
│  before_model()  ──► inject messages, modify state      │
│                                                          │
│  wrap_model_call(next, request)                          │
│    └─► [LLM call]                                        │
│                                                          │
│  after_model()   ──► inspect AIMessage, patch state     │
│                                                          │
│  wrap_tool_call(next, request)                           │
│    └─► [Tool execution]                                  │
│                                                          │
│  after_agent()   ──► cleanup, persist                   │
└─────────────────────────────────────────────────────────┘
```

### Composition Pipeline

```
_build_middleware_registry(config)
         │
         ▼
  List[MiddlewareSpec]          ← each has: name, factory, after={}, before={}, priority
         │
         ▼
topological_sort_middleware_specs()   ← Kahn's BFS; tie-break by (priority, name)
         │
         ▼
  Ordered List[MiddlewareSpec]
         │
         ▼
_build_middlewares()           ← calls factory(); drops None (disabled middlewares)
         │
         ▼
create_agent(..., middleware=[...])   ← LangGraph SDK; composes as onion of wrap_*
```

Middlewares are **registered as a DAG** via `after` / `before` dependency sets, then
topologically sorted into a deterministic execution order. The final list is passed
to the SDK which wraps them as an **onion** for `wrap_*_call` (outermost = first in list)
and a **sequence** for `before_*` / `after_*` hooks.

---

## 2. Execution Order — Full Stack

The table below shows the resolved execution order from outermost → innermost for
`wrap_*_call`, and top → bottom for `before_*` / `after_*`.

```
 #  Name                      Mode Gate          Layer
────────────────────────────────────────────────────────────
 1  thread_data               always             Infrastructure
 2  trajectory                always             Observability
 3  steering                  always             Infrastructure
 4  uploads                   always             Infrastructure
 5  mount_folder              always             Infrastructure
 6  sandbox                   always             Infrastructure
 7  autoresearch              always             Routing
 8  write_file_artifact       always             Routing
 9  dangling_tool_call        always             Repair
10  work_mode                 work mode          Execution
11  summarization             if enabled         Context Mgmt
12  skill_disclosure          always             Context Mgmt
13  planner                   plan mode          Plan Pipeline
14  plan_evaluator            plan mode          Plan Pipeline
15  todo / todo_dag           plan mode          Plan Pipeline
16  title                     always             UX
17  question_generation       if enabled         UX
18  memory                    always             UX
19  view_image                vision models      UX
20  tool_error_boundary       always             Reliability
21  retry                     if enabled         Reliability
22  model_timeout             always             Reliability
23  web_search_circuit_breaker  always           Web Search
24  tool_result_truncation    always             Web Search
25  web_search_summary        if enabled         Web Search
26  web_search_ingestion      if vault enabled   Web Search
27  evaluator                 plan mode          Verification
28  todo_failure_retry        work mode          Recovery
29  scratchpad_task_memory    if enabled         Recovery
30  plan_file_sync            always             Persistence
31  resume_state              if enabled         Long-Running
32  plan_followup             plan mode          Long-Running
33  loop_detection            if enabled         Loop Control
34  recursion_pivot           if enabled         Loop Control
35  execution_trace           if enabled         Observability
36  activity_timeline         always             Observability
37  metrics                   always             Observability
38  clarification             always (LAST)      Terminal Gate
────────────────────────────────────────────────────────────
```

**Harness kill-switch** (`harness.enabled=false`): only these survive:
`thread_data`, `sandbox`, `dangling_tool_call`, `execution_trace`, `activity_timeline`, `clarification`

---

## 3. Layer Diagrams

### 3.1 Infrastructure & Initialization

```
User Request
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ thread_data        Create workspace/uploads/outputs dirs │
│                    Populate state["thread_data"]         │
├─────────────────────────────────────────────────────────┤
│ steering           Inject pending steering intents as    │
│                    HumanMessage before model turns       │
├─────────────────────────────────────────────────────────┤
│ uploads            Ephemerally inject <uploaded_files>   │
│                    block via wrap_model_call (no persist)│
├─────────────────────────────────────────────────────────┤
│ mount_folder       Read dreamy_mount.json; register real │
│                    path under /mnt/user-data/mounted     │
├─────────────────────────────────────────────────────────┤
│ sandbox            Acquire sandbox (local or Docker)     │
│                    Store sandbox_id; release on complete │
└─────────────────────────────────────────────────────────┘
    │
    ▼ (to Routing layer)
```

### 3.2 Early Routing & Repair

```
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ autoresearch       Detect "autoresearch" commands →      │
│                    schedule control-plane jobs           │
├─────────────────────────────────────────────────────────┤
│ write_file_artifact  Intercept write_file / str_replace  │
│                    targeting workspace → promote to      │
│                    state["artifacts"]                    │
├─────────────────────────────────────────────────────────┤
│ dangling_tool_call Scan history for AIMessages with      │
│                    tool_calls missing ToolMessage pairs  │
│                    → inject synthetic error ToolMessages │
└─────────────────────────────────────────────────────────┘
    │
    ▼ (to Execution layer)
```

### 3.3 Work Mode Execution & Plan Pipeline

```
    │
    ▼
┌─────────────────────────────────────────────────────────┐  WORK MODE
│ work_mode          Find next ready DAG todo              │
│                    Inject HumanMessage(work_mode_instr.) │
│                    Emit SSE events; detect plan stall    │
│                    Emit plan_adapted SSE on change       │
├─────────────────────────────────────────────────────────┤
│ plan_execution_gate  Block exec tools while plan.status  │
│                    == "draft"; exempt /recover + reads   │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐  PLAN MODE
│ planner            Orchestrate: halt turn after          │
│                    plan_just_written; spawn work-mode    │
│                    handoff on approved+foreground plan   │
├─────────────────────────────────────────────────────────┤
│ plan_evaluator     Quality gate on todo graph:           │
│                    (1) deterministic pre-check           │
│                    (2) LLM {ok, issues, advice, patch}   │
│                    (3) re-eval loop (max_attempts)       │
├─────────────────────────────────────────────────────────┤
│ todo / todo_dag    Manage DAG todo graph state           │
│                    Track ready_ids; detect context-loss  │
│                    Enforce premature-exit guard          │
└─────────────────────────────────────────────────────────┘
    │
    ▼ (to Context Management)
```

### 3.4 Context Management & UX

```
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ summarization      Compaction with pre-hooks             │
│                    Rescue active_skills blocks           │
│                    Detect degenerate summaries           │
├─────────────────────────────────────────────────────────┤
│ skill_disclosure   Progressive injection of skill bodies │
│                    LRU mtime-keyed cache                 │
│                    Match triggers vs conversation text   │
├─────────────────────────────────────────────────────────┤
│ title              Async background title generation     │
│                    Emit title_update SSE                 │
├─────────────────────────────────────────────────────────┤
│ question_generation  (if enabled)                        │
│                    LLM follow-up suggestions after       │
│                    final AI responses (no tool calls)    │
├─────────────────────────────────────────────────────────┤
│ memory             Filter user+final-AI messages         │
│                    Push to per-thread memory update queue│
├─────────────────────────────────────────────────────────┤
│ view_image         (vision models only)                  │
│                    Inject base64 images after all        │
│                    view_image calls resolved             │
└─────────────────────────────────────────────────────────┘
    │
    ▼ (to Reliability layer)
```

### 3.5 Tool Reliability & Web Search

```
    │
    ▼
┌─────────────────────────────────────────────────────────┐  RELIABILITY (wrap_tool_call onion)
│ tool_error_boundary  OUTERMOST wrap_tool_call            │
│                    Catch surviving exceptions →          │
│                    ToolMessage(status="error")           │
│                    Re-raise control-flow signals         │
├─────────────────────────────────────────────────────────┤
│ retry              (if enabled)                          │
│                    Per-tool: max_attempts, backoff_ms    │
│                    retryable_errors, idempotent flag     │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐  RELIABILITY (wrap_model_call onion)
│ model_timeout      asyncio.wait_for per LLM call         │
│                    On timeout → synthetic AIMessage      │
│                    with [model_timeout] fingerprint      │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐  WEB SEARCH PIPELINE
│ web_search_circuit_breaker                               │
│                    Track consecutive failures            │
│                    After 2 failures → open circuit       │
│                    Health check WEBSEARCH_BASE_URL/health│
├─────────────────────────────────────────────────────────┤
│ tool_result_truncation                                   │
│                    Cap ToolMessage size per-tool         │
│                    Adaptive web_search unsummarized cap  │
├─────────────────────────────────────────────────────────┤
│ web_search_summary  (if enabled)                         │
│                    LLM summary when result > threshold   │
│                    ≤250 words + [citation:TITLE](URL)    │
│                    Appends [Summarized by ...] marker    │
├─────────────────────────────────────────────────────────┤
│ web_search_ingestion  (if vault enabled)                 │
│                    Bridge raw results → vault ingest q   │
│                    Pure side-effect; passes through      │
└─────────────────────────────────────────────────────────┘
    │
    ▼ (to Verification & Recovery)
```

### 3.6 Verification, Recovery & Persistence

```
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ evaluator          (plan mode)                           │
│                    Check plan.md + trace artifacts        │
│                    LLM PASS/FAIL verdict                 │
│                    On FAIL → inject evaluator_feedback   │
├─────────────────────────────────────────────────────────┤
│ todo_failure_retry  (work mode)                          │
│                    On finish with incomplete todos →     │
│                    inject todo_failure_recovery message  │
│                    Cap: 10 recovery, 1 schema attempts   │
├─────────────────────────────────────────────────────────┤
│ scratchpad_task_memory  (if enabled)                     │
│                    Bounded run scratchpad (key-value)    │
│                    Task-scoped episodic memory           │
│                    Persist both to disk via write_if_chg │
├─────────────────────────────────────────────────────────┤
│ plan_file_sync     Background sync plan.md / plan-N.md   │
│                    Per-thread locks → no write races     │
├─────────────────────────────────────────────────────────┤
│ resume_state       (if enabled)                          │
│                    Track last/in-progress todo IDs       │
│                    Store retry counts in resume_meta     │
├─────────────────────────────────────────────────────────┤
│ plan_followup      (plan mode)                           │
│                    Schedule background follow-up runs    │
│                    Daemon thread via submit_background   │
└─────────────────────────────────────────────────────────┘
    │
    ▼ (to Loop Control & Observability)
```

### 3.7 Loop Control & Observability

```
    │
    ▼
┌─────────────────────────────────────────────────────────┐  LOOP CONTROL
│ loop_detection     (if enabled)                          │
│                    Layer 1: hash-based (multiset of      │
│                    tool name+args in sliding window)     │
│                    Layer 2: frequency saturation         │
│                    Warn: inject reminder                 │
│                    Hard limit: strip tool_calls from msg │
│                    (workflow skills exempt)              │
├─────────────────────────────────────────────────────────┤
│ recursion_pivot    (if enabled, lead agent only)         │
│                    At budget fractions → LLM evaluator   │
│                    KEEP or PIVOT + DIRECTIVE             │
│                    On PIVOT → inject steering message    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐  OBSERVABILITY
│ execution_trace    (if enabled)                          │
│                    SSE-stream + persist trace entries    │
│                    Drain runtime_events → TraceEvents    │
├─────────────────────────────────────────────────────────┤
│ activity_timeline  always                                │
│                    Convert events → user-readable lines  │
│                    SSE activity_event stream             │
│                    Track token usage metrics             │
├─────────────────────────────────────────────────────────┤
│ metrics            always                                │
│                    Counter-based in-memory metrics       │
│                    Prometheus text-format export         │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐  TERMINAL GATE (must be last)
│ clarification      always                                │
│                    Intercept ask_user_for_clarification  │
│                    Non-blocking: append to queue         │
│                    Blocking: Command(goto=END) when      │
│                    urgency==blocking OR no ready todos   │
│                    Auto mode: inject answer automatically│
└─────────────────────────────────────────────────────────┘
    │
    ▼
  Response
```

---

## 4. Cross-Middleware Dependency Graph

```
todo_dag_middleware
  ├─► work_mode_middleware         (_materialize_ready_ids)
  ├─► plan_evaluator_middleware    (_is_acyclic, _legacy_todos,
  │                                 _materialize_ready_ids,
  │                                 merge_todo_nodes, normalize_todo_nodes)
  └─► clarification_middleware     (compute_effective_ready_ids)

model_timeout_middleware
  ├─► web_search_circuit_breaker   (TIMEOUT_MESSAGE_FINGERPRINT)
  └─► trajectory_middleware        (TIMEOUT_MESSAGE_FINGERPRINT)

web_search_summary_middleware
  └─► tool_result_truncation       (_WEB_SEARCH_SUMMARY_MARKER string in sync)

plan_execution.py (shared helpers)
  ├─► planner_middleware
  └─► clarification_middleware

work_run_handoff.py
  └─► planner_middleware            (spawn_work_mode_handoff)

handoff_sync.py
  └─► plan_file_sync_middleware     (sync_handoff_files_from_state)

runtime_events.py  (inter-middleware event bus)
  ├─► trajectory_middleware         (drain)
  └─► execution_trace_middleware    (drain)
```

---

## 5. Key Support Modules

| Module | Purpose |
|--------|---------|
| `runtime_events.py` | Inter-middleware event bus — transient, run-scoped, per-consumer cursors via `drain_runtime_events()` |
| `run_scoped.py` | Per-`Runtime` scratch storage via `WeakKeyDictionary` — used by `autoresearch` to prevent double-triggers |
| `message_selection.py` | Message-type utilities: `is_synthetic_human_message()`, `original_user_prompt()`, `_SYNTHETIC_HUMAN_NAMES` set |
| `_fs_utils.py` | Crash-safe atomic file writes: `atomic_write_text()`, `write_if_changed()` |
| `_timeout_utils.py` | Daemon-thread timeout wrapper for synchronous LLM calls: `run_with_timeout()` |
| `plan_execution.py` | Plan lifecycle state-machine helpers shared across planner + clarification |
| `handoff_sync.py` | plan.md rendering, disk sync, versioned filenames |
| `work_run_handoff.py` | Transition from approved plan → fresh Work Mode run (concurrent-spawn guard via `_HANDOFF_GUARD` + TTL) |
| `daemon_agent_invoke.py` | Invoke LangGraph agents from sync daemon threads; handles sync/async SQLite checkpointer mismatch |
| `clarification_resolution.py` | Thin compatibility shim re-exporting from `plan_execution` |

---

## 6. Data Flow Through State Dict

```
state["thread_data"]       ← thread_data_middleware
state["artifacts"]         ← write_file_artifact_middleware
state["todo_graph"]        ← todo_dag_middleware
state["title"]             ← title_middleware
state["suggested_questions"] ← question_generation_middleware
state["clarifications"]    ← clarification_middleware
state["scratchpad"]        ← scratchpad_task_memory_middleware
state["task_memory"]       ← scratchpad_task_memory_middleware
state["resume_meta"]       ← resume_state_middleware
state["background_followups"] ← plan_followup_middleware
state["activity_timeline"] ← activity_timeline_middleware
state["plan"]              ← planner_middleware / plan_file_sync
state["uploaded_files"]    ← (input) consumed by uploads_middleware
state["pending_steering_intents"] ← (input) consumed by steering_middleware
```

---

## 7. Mode-Conditional Middleware Map

```
                         ┌──────────┬──────────┬──────────┐
 Middleware               │  Always  │Work Mode │Plan Mode │
─────────────────────────┼──────────┼──────────┼──────────┤
 thread_data             │    ✓     │          │          │
 trajectory              │    ✓     │          │          │
 steering                │    ✓     │          │          │
 uploads                 │    ✓     │          │          │
 mount_folder            │    ✓     │          │          │
 sandbox                 │    ✓     │          │          │
 autoresearch            │    ✓     │          │          │
 write_file_artifact     │    ✓     │          │          │
 dangling_tool_call      │    ✓     │          │          │
 work_mode               │          │    ✓     │          │
 plan_execution_gate     │          │    ✓     │          │
 summarization           │  opt     │          │          │
 skill_disclosure        │    ✓     │          │          │
 planner                 │          │          │    ✓     │
 plan_evaluator          │          │          │    ✓     │
 todo / todo_dag         │          │          │    ✓     │
 title                   │    ✓     │          │          │
 question_generation     │  opt     │          │          │
 memory                  │    ✓     │          │          │
 view_image              │  opt     │          │          │
 tool_error_boundary     │    ✓     │          │          │
 retry                   │  opt     │          │          │
 model_timeout           │    ✓     │          │          │
 web_search_circuit_breaker │ ✓    │          │          │
 tool_result_truncation  │    ✓     │          │          │
 web_search_summary      │  opt     │          │          │
 web_search_ingestion    │  opt     │          │          │
 evaluator               │          │          │    ✓     │
 todo_failure_retry      │          │    ✓     │          │
 scratchpad_task_memory  │  opt     │          │          │
 plan_file_sync          │    ✓     │          │          │
 resume_state            │  opt     │          │          │
 plan_followup           │          │          │    ✓     │
 loop_detection          │  opt     │          │          │
 recursion_pivot         │  opt     │          │          │
 execution_trace         │  opt     │          │          │
 activity_timeline       │    ✓     │          │          │
 metrics                 │    ✓     │          │          │
 clarification           │    ✓     │          │          │
─────────────────────────┴──────────┴──────────┴──────────┘
 opt = conditionally enabled via config flag
```

---

## 8. Notable Design Patterns

### Onion Model for wrap_* calls
`wrap_model_call` and `wrap_tool_call` are composed as an onion — the middleware
first in the resolved list becomes the **outermost** wrapper. This is why:
- `trajectory` is registered first → it observes synthetic responses from inner middlewares
- `tool_error_boundary` is placed before `retry` → it catches what retry cannot handle

### Synthetic Human Messages
Middlewares communicate to the LLM by injecting `HumanMessage` with a `name` field.
These are tracked in `_SYNTHETIC_HUMAN_NAMES` in `message_selection.py` so
`summarization` can rescue them from compaction and `is_synthetic_human_message()`
can filter them out when needed.

### Inter-Middleware Event Bus
`runtime_events.py` provides a transient, per-run append-log. Consumers (trajectory,
execution_trace) call `drain_runtime_events(runtime, consumer_id)` to read only new
events since their last drain. This decouples producers (e.g. work_mode emitting
`plan_adapted`) from consumers without direct imports.

### Harness Kill-Switch
Setting `harness.enabled=false` strips 32 of 38 middlewares, leaving only the minimum
viable set for safe passthrough. Used in testing and emergency fallback.

### DAG-Centric Architecture
`todo_dag_middleware.py` is the single source of truth for todo DAG operations.
`work_mode`, `plan_evaluator`, and `clarification` all import from it rather than
reimplementing graph logic — the only shared import relationship explicitly called
out in the codebase comments.

---

## 9. Deprecated Middleware

| Middleware | File | Reason Deprecated |
|---|---|---|
| `PhaseToolFilterMiddleware` | `phase_tool_filter_middleware.py` | Superseded by `plan_execution_gate`; factory commented out in `agent.py` but file remains on disk |
