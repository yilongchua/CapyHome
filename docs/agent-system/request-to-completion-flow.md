# Request → Completion: Full Agent Harness Flow

> Traces a single user message through every middleware layer to final response.  
> Two paths shown: **Work Mode** (execute a task) and **Plan Mode** (plan then hand off).

---

## End-to-End Flow Diagram

```
══════════════════════════════════════════════════════════════════════════
 USER REQUEST
══════════════════════════════════════════════════════════════════════════

   User types a message in the UI
              │
              ▼
   HTTP POST /api/run  (or SSE stream endpoint)
              │
              ▼
   ┌──────────────────────────┐
   │  LangGraph Checkpointer  │  ← hydrate state from SQLite / memory store
   │  (restore thread state)  │
   └──────────────────────────┘
              │
              ▼

══════════════════════════════════════════════════════════════════════════
 PHASE 1 — INFRASTRUCTURE  (before_agent / before_model)
══════════════════════════════════════════════════════════════════════════

   ┌──────────────────────────────────────────────────────────────────┐
   │ thread_data          Create/verify workspace dirs                │
   │                      workspace/ │ uploads/ │ outputs/            │
   │                      Populate state["thread_data"]               │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ sandbox              Acquire sandbox (local Docker container)     │
   │                      Store sandbox_id in state                    │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ steering             Any pending one-shot steering intents?       │
   │                      YES ──► prepend HumanMessage(steering_intent)│
   │                      NO  ──► pass through                        │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ uploads              Ephemeral <uploaded_files> block injected    │
   │                      via wrap_model_call (NOT persisted to history)│
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ mount_folder         Read dreamy_mount.json                       │
   │                      Register real path → /mnt/user-data/mounted  │
   │                      Inject <mounted_folder> block once per path  │
   └──────────────────────────────────────────────────────────────────┘
              │
              ▼

══════════════════════════════════════════════════════════════════════════
 PHASE 2 — PRE-FLIGHT REPAIR  (wrap_model_call: outermost)
══════════════════════════════════════════════════════════════════════════

   ┌──────────────────────────────────────────────────────────────────┐
   │ dangling_tool_call   Scan message history                         │
   │                      Find AIMessages with tool_calls but no       │
   │                      matching ToolMessage (user interrupted?)     │
   │                      ──► Insert synthetic error ToolMessages      │
   │                          at correct history positions            │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ autoresearch         Is message "autoresearch <topic>"?           │
   │                      YES ──► Schedule control-plane job; END turn │
   │                      NO  ──► pass through                        │
   └──────────────────────────────────────────────────────────────────┘
              │
              ▼

══════════════════════════════════════════════════════════════════════════
 PHASE 3 — CONTEXT INJECTION  (before_model)
══════════════════════════════════════════════════════════════════════════

   ┌──────────────────────────────────────────────────────────────────┐
   │ summarization        Is message window near limit?                │
   │  (if enabled)        YES ──► compact history via LLM summary      │
   │                              rescue active_skills blocks         │
   │                              write audit report to .runtime/     │
   │                      NO  ──► pass through                        │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ skill_disclosure     Match skill triggers vs recent conversation  │
   │                      Inject HumanMessage(name="active_skills")    │
   │                      with skill body (LRU mtime-keyed cache)      │
   └──────────────────────────────────────────────────────────────────┘
              │
              ▼

══════════════════════════════════════════════════════════════════════════
 PHASE 4 — MODE ROUTING  (before_model)
══════════════════════════════════════════════════════════════════════════

              │
    ┌─────────┴──────────┐
    │                    │
    ▼                    ▼
 WORK MODE            PLAN MODE
    │                    │
    │  ┌─────────────────────────────────────────────────────────┐
    │  │ planner          First turn: emit write_plan tool call   │
    │  │                  Agent calls write_plan → plan stored    │
    │  │                  plan_just_written=True → halt turn      │
    │  │                  ┌──────────────────────────────────┐   │
    │  │                  │ plan_evaluator                   │   │
    │  │                  │  (1) DAG pre-check: cycles,      │   │
    │  │                  │      dangling deps, ID norms     │   │
    │  │                  │  (2) LLM call:                   │   │
    │  │                  │      {ok, issues, advice, patch} │   │
    │  │                  │  (3) Re-eval loop (max_attempts) │   │
    │  │                  └──────────────────────────────────┘   │
    │  │                  ┌──────────────────────────────────┐   │
    │  │                  │ todo_dag                         │   │
    │  │                  │  Build DAG; track ready_ids      │   │
    │  │                  │  Detect context-loss from summ.  │   │
    │  │                  └──────────────────────────────────┘   │
    │  └─────────────────────────────────────────────────────────┘
    │                    │
    │                    │  Plan approved?
    │                    │  YES ──► spawn_work_mode_handoff()
    │                    │          (guarded by _HANDOFF_GUARD + TTL)
    │                    │          ──► new Work Mode run
    │                    │  NO  ──► await user approval or clarify
    │
    ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ work_mode            Find next READY todo from DAG            │
 │                      Inject HumanMessage(work_mode_instruction)│
 │                      Emit SSE: plan_step_start               │
 └──────────────────────────────────────────────────────────────┘
    │
 ┌──────────────────────────────────────────────────────────────┐
 │ plan_execution_gate  plan.status == "draft"?                  │
 │                      YES ──► block exec tools (bash, write,   │
 │                              task, web_search…)               │
 │                              allow /recover + read-only tools │
 │                      NO  ──► pass through                     │
 └──────────────────────────────────────────────────────────────┘
    │
    ▼

══════════════════════════════════════════════════════════════════════════
 PHASE 5 — POLICY CONTROLS  (wrap_tool_call)
══════════════════════════════════════════════════════════════════════════

   ┌──────────────────────────────────────────────────────────────────┐
   │ permissions          Match tool name + args against allow/deny/   │
   │                      ask rules from PermissionsConfig            │
   │                      ask ──► emit permission_ask event; block     │
   │                      allow ──► pass through                      │
   │                      deny ──► return error ToolMessage           │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ tool_disclosure      Phase-gated allow-list?                      │
   │  (if enabled)        tool NOT in phase allow-list                 │
   │                      ──► return ToolMessage error                 │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ hooks                (if any hook configured)                     │
   │                      PreToolUse  ──► run shell command(s)        │
   │                      PostToolUse ──► run shell command(s)        │
   │                      FileChanged ──► run shell command(s)        │
   └──────────────────────────────────────────────────────────────────┘
              │
              ▼

══════════════════════════════════════════════════════════════════════════
 PHASE 6 — LLM CALL  (wrap_model_call onion, innermost)
══════════════════════════════════════════════════════════════════════════

   ┌──────────────────────────────────────────────────────────────────┐
   │ model_timeout        asyncio.wait_for(LLM call, timeout=T)        │
   │                      timeout ──► synthetic AIMessage              │
   │                                  "[model_timeout]" fingerprint    │
   └──────────────────────────────────────────────────────────────────┘
              │
              ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │          ★  ACTUAL LLM CALL  (claude-sonnet / claude-opus)  ★    │
   │                                                                  │
   │  Input:  system prompt + skill disclosures + work_mode_instr     │
   │          + message history (possibly compacted)                  │
   │          + uploaded_files block (ephemeral)                      │
   │          + steering intents                                       │
   │                                                                  │
   │  Output: AIMessage with text and/or tool_calls[]                 │
   └──────────────────────────────────────────────────────────────────┘
              │
              ▼

══════════════════════════════════════════════════════════════════════════
 PHASE 7 — TOOL EXECUTION  (wrap_tool_call onion)
══════════════════════════════════════════════════════════════════════════

   Each tool_call in AIMessage is processed through the wrap_tool_call onion:

   ┌──────────────────────────────────────────────────────────────────┐
   │ tool_error_boundary  OUTERMOST — catch any surviving exception    │
   │                      ──► ToolMessage(status="error", …)          │
   │                      re-raise: GraphBubbleUp, GraphInterrupt,    │
   │                                ParentCommand, GraphRecursionError │
   └──────────────────────────────────────────────────────────────────┘
     │ ┌────────────────────────────────────────────────────────────┐
     │ │ retry  (if enabled)                                         │
     │ │        On failure: check retryable_errors substrings        │
     │ │        backoff_ms delay ──► re-attempt (max_attempts)       │
     │ └────────────────────────────────────────────────────────────┘
     │   │
     │   │  ★  ACTUAL TOOL RUN  ★
     │   │  (bash, write_file, web_search, task, ask_user…)
     │   │
     │   ▼ ToolMessage returned
     │
     │   Is tool == web_search?
     │   │
     │   ├──► web_search_circuit_breaker
     │   │      Track consecutive failures
     │   │      ≥2 failures ──► circuit OPEN
     │   │                      return [web_search_circuit_open] msg
     │   │      circuit open ──► skip call entirely
     │   │
     │   ├──► web_search_ingestion  (if vault enabled)
     │   │      Side-effect: push raw results to vault ingest queue
     │   │      Pass ToolMessage through unchanged
     │   │
     │   ├──► web_search_summary  (if enabled)
     │   │      result > summary_threshold_chars?
     │   │      YES ──► fast LLM call → ≤250-word summary
     │   │               inline [citation:TITLE](URL) links
     │   │               append [Summarized by …] marker
     │   │      NO  ──► pass through
     │   │
     │   └──► tool_result_truncation
     │          Cap ToolMessage by per-tool routing.tool_result_caps
     │          Adaptive cap for unsummarized web_search results
     │
     │   Is tool == write_file / str_replace targeting workspace/?
     │   └──► write_file_artifact
     │          Promote output to state["artifacts"]
     │
     │   Is tool == ask_user_for_clarification?
     │   └──► clarification  (see Phase 9)
     │
     └── ToolMessage(s) returned to LLM context

══════════════════════════════════════════════════════════════════════════
 PHASE 8 — AFTER MODEL  (after_model hooks)
══════════════════════════════════════════════════════════════════════════

   ┌──────────────────────────────────────────────────────────────────┐
   │ loop_detection       (if enabled)                                 │
   │                      Layer 1: hash multiset of recent tool calls  │
   │                               detect repeated pattern            │
   │                      Layer 2: frequency saturation per tool type  │
   │                      WARN  ──► inject reminder HumanMessage       │
   │                      HARD  ──► strip tool_calls from AIMessage    │
   │                      (workflow skills exempt)                    │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ recursion_pivot      (if enabled, lead agent only)                │
   │                      At configured budget fraction               │
   │                      ──► LLM evaluator: KEEP or PIVOT            │
   │                      PIVOT ──► inject steering HumanMessage      │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ evaluator            (plan mode only)                             │
   │                      Last AIMessage has no tool_calls?            │
   │                      Check: plan.md + timestamped trace present  │
   │                      LLM: PASS / FAIL verdict                    │
   │                      FAIL ──► inject evaluator_feedback message  │
   │                               continue loop                      │
   │                      PASS ──► allow completion                   │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ todo_failure_retry   (work mode only)                             │
   │                      Agent trying to finish?                     │
   │                      Any incomplete todos remain?                │
   │                      YES ──► inject todo_failure_recovery msg    │
   │                               (cap: 10 recovery, 1 schema)       │
   │                      NO  ──► allow completion                    │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ question_generation  (if enabled)                                 │
   │                      Last AIMessage has no tool_calls?            │
   │                      ──► async LLM call → follow-up questions    │
   │                          store in state["suggested_questions"]   │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ view_image           (vision models only)                         │
   │                      All view_image tool calls complete?          │
   │                      ──► inject HumanMessage(viewed_images_disc.) │
   │                          with base64 image data                  │
   └──────────────────────────────────────────────────────────────────┘
              │
              ▼

══════════════════════════════════════════════════════════════════════════
 PHASE 9 — CLARIFICATION GATE  (wrap_tool_call intercept)
══════════════════════════════════════════════════════════════════════════

   ask_user_for_clarification tool call intercepted by clarification middleware:

   ┌──────────────────────────────────────────────────────────────────┐
   │ clarification        urgency == "blocking"?                       │
   │  (MUST BE LAST)      OR zero ready todos remain in DAG?          │
   │                      YES ──► Command(goto=END)                    │
   │                              emit clarification_request SSE       │
   │                              wait for user to respond            │
   │                      NO  ──► append to state["clarifications"]   │
   │                              continue executing other ready todos │
   │                                                                  │
   │                      Auto mode: select recommended/first answer  │
   │                      ──► inject answer; continue                 │
   └──────────────────────────────────────────────────────────────────┘
              │
              ▼

══════════════════════════════════════════════════════════════════════════
 PHASE 10 — PERSISTENCE & OBSERVABILITY  (after_model / after_agent)
══════════════════════════════════════════════════════════════════════════

   ┌──────────────────────────────────────────────────────────────────┐
   │ memory               Filter: user inputs + final AI responses     │
   │                      Strip <uploaded_files> blocks               │
   │                      Push to per-thread debounced memory queue   │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ scratchpad_task_memory  (if enabled)                              │
   │                      Persist run scratchpad (key-value)          │
   │                      Persist task-scoped episodic memory         │
   │                      write_if_changed() → disk                   │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ plan_file_sync       Background: sync plan.md + plan-N.md        │
   │                      Per-thread lock → no write races            │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ resume_state         (if enabled)                                 │
   │                      Track last/in-progress todo IDs + retries   │
   │                      Write to state["resume_meta"]               │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ title                Async background task (first turn only)      │
   │                      LLM generates thread title                  │
   │                      Emit title_update SSE                       │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ trajectory           JSONL append per turn                        │
   │                      Drain runtime_events → structured log        │
   │                      Detect [model_timeout] fingerprint          │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ execution_trace      (if enabled)                                 │
   │                      Drain runtime_events → TraceEvents          │
   │                      Persist + SSE-stream execution_trace events  │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ activity_timeline    Convert all events → user-readable lines     │
   │                      SSE-stream activity_event per turn          │
   │                      Update token usage metrics                  │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ metrics              Increment counters: model_call, tool_call,   │
   │                      tool_error, etc.                             │
   │                      Available as Prometheus /metrics endpoint   │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ plan_followup        (plan mode only, if background_followup)     │
   │                      Spawn daemon thread with CapyHomeClient     │
   │                      Schedule background follow-up run           │
   └──────────────────────────────────────────────────────────────────┘
              │
   ┌──────────────────────────────────────────────────────────────────┐
   │ LangGraph Checkpointer  Persist updated state to SQLite           │
   └──────────────────────────────────────────────────────────────────┘
              │
              ▼

══════════════════════════════════════════════════════════════════════════
 COMPLETION CHECK — does the loop continue?
══════════════════════════════════════════════════════════════════════════

              │
              ▼
   AIMessage has tool_calls?
   ├── YES ──► back to PHASE 7 (tool execution)
   │           then PHASE 8 (after_model)
   │           then back to PHASE 6 (next LLM call)
   │
   └── NO  ──► Final answer ready
               │
               ├── todo_failure_retry: all todos complete?  NO ──► re-enter loop
               ├── evaluator: PASS?                         NO ──► re-enter loop
               ├── clarification: blocking pending?         YES ──► goto END, wait
               └── ALL CLEAR
                       │
                       ▼

══════════════════════════════════════════════════════════════════════════
 RESPONSE DELIVERED TO USER
══════════════════════════════════════════════════════════════════════════

   ┌──────────────────────────────────────────────────────────────────┐
   │  Final AIMessage text streamed to client via SSE                 │
   │  + activity_timeline entries streamed                            │
   │  + execution_trace entries streamed                              │
   │  + title_update (first turn only)                                │
   │  + suggested_questions (if enabled)                              │
   │  + artifacts list (if files written)                             │
   └──────────────────────────────────────────────────────────────────┘
              │
              ▼
      ✓  Turn complete. State checkpointed.
         Next user message re-enters at PHASE 1.
```

---

## Condensed Loop View

```
                         ┌─────────────────────────┐
                         │      User Message        │
                         └────────────┬────────────┘
                                      │
                         ┌────────────▼────────────┐
                         │  Restore state from DB   │
                         └────────────┬────────────┘
                                      │
              ┌───────────────────────▼──────────────────────┐
              │               MIDDLEWARE ONION               │
              │                                              │
              │  ① Infrastructure   (dirs, sandbox, uploads) │
              │  ② Repair           (dangling tool calls)    │
              │  ③ Context          (summarize, skills)      │
              │  ④ Mode routing     (work / plan DAG)        │
              │  ⑤ Policy           (permissions, hooks)     │
              │                                              │
              │         ┌────────────────────┐              │
              │         │   ★ LLM CALL ★     │◄─────────────┼─ model_timeout wrap
              │         └─────────┬──────────┘              │
              │                   │                          │
              │          has tool_calls?                     │
              │         YES       │       NO                 │
              │          │        │        └──► final answer │
              │          ▼        │                          │
              │  ┌───────────────────────────────────┐      │
              │  │      TOOL EXECUTION ONION         │      │
              │  │  error_boundary → retry → [tool]  │      │
              │  │  → circuit_breaker → summary      │      │
              │  │  → truncation → artifact_promote  │      │
              │  └───────────────┬───────────────────┘      │
              │                  │                          │
              │  ⑥ After-model  (loop detect, evaluator,   │
              │                  recovery, questions)       │
              │  ⑦ Persist      (memory, plan sync, title) │
              │  ⑧ Observe      (trajectory, trace, SSE)   │
              │  ⑨ Gate         (clarification last)       │
              │                                              │
              └───────────────────────┬──────────────────────┘
                                      │
                         ┌────────────▼────────────┐
                         │  More tool calls or      │
                         │  recovery needed?        │
                         │  YES ──► loop back       │
                         │  NO  ──► stream response │
                         └────────────┬────────────┘
                                      │
                         ┌────────────▼────────────┐
                         │   Response to User       │
                         │   State checkpointed     │
                         └─────────────────────────┘
```

---

## Key Decision Points Summary

| Decision Point | Middleware | Outcome |
|---|---|---|
| History has dangling tool calls? | `dangling_tool_call` | Inject synthetic ToolMessages before LLM sees history |
| "autoresearch" command? | `autoresearch` | Schedule job; end turn immediately |
| Context window near limit? | `summarization` | Compact history via LLM; rescue skill blocks |
| Plan status == draft? | `plan_execution_gate` | Block all execution tools |
| Tool permission denied? | `permissions` | `ask` → emit SSE + block; `deny` → error message |
| LLM call times out? | `model_timeout` | Synthetic `[model_timeout]` AIMessage |
| Web search fails ≥2 times? | `web_search_circuit_breaker` | Open circuit; skip future calls |
| Web result too large? | `web_search_summary` | LLM-summarise to ≤250 words |
| Repeated tool call pattern? | `loop_detection` | Warn → inject reminder; Hard → strip tool_calls |
| Near recursion budget? | `recursion_pivot` | KEEP or PIVOT directive from LLM evaluator |
| Plan mode terminal answer? | `evaluator` | PASS or FAIL + re-inject feedback |
| Todos still incomplete on finish? | `todo_failure_retry` | Inject recovery prompt; continue loop |
| Clarification blocking? | `clarification` | `Command(goto=END)`; wait for user |
