# Chat Audit Guide

How to reconstruct what an agent actually did during a single chat thread —
what it was asked, what it planned, which model/tool calls it made, and what
it produced — by reading the on-disk artifacts the backend already writes.

All examples below use a real thread as the reference:

- **Reference thread id:** `a73a4607-ab65-49d4-af18-66bb16b56c56`
- **Topic:** Singapore vs Tokyo soba comparison

You can swap that id for any other thread and the layout is identical.

---

## 1. Where the artifacts live

Every chat is sandboxed under `backend/.capyhome/threads/{thread_id}/`.
For the reference thread:

```
backend/.capyhome/threads/a73a4607-ab65-49d4-af18-66bb16b56c56/
├── logs/
│   └── trajectory/
│       └── trajectory-1779762941-run-aa4cfcd299.jsonl   # per-run event log
└── user-data/
    ├── workspace/
    │   ├── plan.md                                       # latest plan state
    │   ├── plans/                                        # versioned plan snapshots
    │   │   └── plan-20260526-023802-singapore-vs-tokyo-soba-comparison-plan.md
    │   └── .prompts/                                     # captured LLM prompts
    │       └── 20260526T023541_237143Z_lead_agent_prompt_tuning.txt
    │       └── ... (one file per model call)
    └── outputs/                                          # legacy — almost always empty; agent writes to workspace/ instead
```

> **Note on produced files:** `outputs/` is effectively unused in current runs —
> the agent writes deliverables into `user-data/workspace/` (alongside
> `plan.md` and the `plans/` snapshots). Always check `workspace/` first when
> auditing what was produced.

Supporting global stores (shared across threads):

| Path | What it holds |
|---|---|
| `backend/.capyhome/checkpoints.db` | LangGraph SQLite checkpointer — full state per turn |
| `backend/.capyhome/memory.json` | Global memory facts injected into prompts |
| `prompt-tunning/prompt_id_*/cycle_*_metadata.json` | Per-run metadata when the run was driven by `test_prompt.py` (chat_url, model, response_preview, copied prompt logs) |

Folder layout is created by `ThreadDataMiddleware`; the `.prompts/` capture is
gated by the `CAPYBARA_PROMPT_LOGGING_ENABLED=1` env var that `test_prompt.py`
sets in
[prompt-tunning/test_prompt.py:59](../../prompt-tunning/test_prompt.py#L59).

---

## 2. The audit checklist

For a single chat, an audit should answer:

1. **What was asked?** — initial user prompt and any follow-ups.
2. **What did the agent plan?** — plan.md and the dated plan snapshot.
3. **What ran?** — every `model_call_*` and `tool_call_*` event, in order.
4. **What did the model see?** — the rendered system prompt + messages sent
   for each model call.
5. **What did it produce?** — files written under `user-data/workspace/`
   (the agent's working directory; `outputs/` is legacy and usually empty) and
   any `present_files` tool calls.
6. **Did anything go wrong?** — timeouts, retries, failed middleware events.
7. **What context was injected?** — which memory facts and skills appeared in
   the system prompt for that turn.

---

## 3. Step-by-step walkthrough (reference thread)

### 3.1 Locate the thread

```bash
THREAD=a73a4607-ab65-49d4-af18-66bb16b56c56
TDIR="backend/.capyhome/threads/$THREAD"
ls "$TDIR"
```

### 3.2 Read the original ask

The submitted prompt is preserved verbatim inside the first captured prompt
file's `messages[]` (and, for `test_prompt.py` runs, in
`prompt-tunning/prompt_id_*/cycle_*_metadata.json` under `initial_prompt`).

For this thread the prompt focuses on comparing soba options in Singapore
versus Tokyo.

### 3.3 Read the plan

```bash
cat "$TDIR/user-data/workspace/plan.md"
ls "$TDIR/user-data/workspace/plans/"
```

`plan.md` is overwritten as the plan evolves; `plans/` keeps timestamped
snapshots. For the reference thread, the snapshot is
`plan-20260526-023802-singapore-vs-tokyo-soba-comparison-plan.md` — status
`draft`, awaiting Execute Plan approval.

### 3.4 Replay the run from the trajectory

The trajectory JSONL is the canonical event log. One run = one file:

```bash
TRAJ="$TDIR/logs/trajectory/trajectory-1779762941-run-aa4cfcd299.jsonl"
```

Each line is `{ts, run_id, thread_id, event, payload}`. The events the
trajectory middleware emits are:

| Event | Meaning |
|---|---|
| `before_agent` / `after_agent` | Agent invocation boundary |
| `before_model` / `after_model` | Each LangGraph model node entry/exit |
| `model_call_start` / `model_call_end` | Underlying LLM HTTP call |
| `tool_call_start` / `tool_call_end` | Tool invocations (with `tool` name) |
| `tool_call_timeout` | A tool hit its per-call timeout |
| `middleware_event` | Free-form payloads from individual middlewares |

Quick aggregations for the reference thread:

```bash
# event counts
awk -F'"event":' '{print $2}' "$TRAJ" | awk -F'"' '{print $2}' | sort | uniq -c

# tools used
grep -o '"tool": "[^"]*"' "$TRAJ" | sort -u
```

For `a73a4607-...` this yields 3 model calls, 3 tool calls, and the tools
touched were `web_search` and `write_todos`.

A timeline view (`ts` is unix epoch seconds, sort by it):

```bash
jq -c '{ts, event, tool: (.payload.tool // null)}' "$TRAJ" | sort
```

### 3.5 Inspect what the model actually saw

Every model call writes a JSON file under `user-data/workspace/.prompts/`
named `{utc_ts}_lead_agent_{purpose}.txt`. The reference thread has 10 such
captures from the prompt-tuning run.

```bash
ls "$TDIR/user-data/workspace/.prompts/"
jq '{timestamp_utc, model_name, invocation_params, messages: (.messages|length)}' \
  "$TDIR/user-data/workspace/.prompts/20260526T023541_237143Z_lead_agent_prompt_tuning.txt"
```

Each file contains:

- `timestamp_utc`, `actor`, `purpose`, `thread_id`
- `model_name` + `invocation_params` (provider, model id, temperature, etc.)
- the full `messages[]` exactly as sent to the LLM, including the system
  prompt with injected memory/skills and the `<memory>` block

This is the artifact to use when the question is "did the model see what we
think it saw?" — e.g. checking that a memory fact got injected, or that a
skill body was loaded.

### 3.6 Check produced files

```bash
# Primary: agent writes deliverables into the workspace dir
ls "$TDIR/user-data/workspace/"

# Legacy path — almost always empty, kept for backwards compat
ls "$TDIR/user-data/outputs/"
```

`workspace/` is where the agent actually drops files (alongside `plan.md`,
`plans/`, and `.prompts/`). `outputs/` exists in the directory layout but is
effectively unused — do not rely on it as a signal that nothing was produced.
A completed plan typically leaves Markdown/PDF deliverables in `workspace/`,
mirrored by `present_files` tool calls in the trajectory.

### 3.7 Check for failures

```bash
grep -E '"event":"(tool_call_timeout|tool_call_failed|after_model)"' "$TRAJ" | head
```

The reference thread has one `tool_call_timeout` line — useful as a flag
even when the final response looked fine.

### 3.7.1 Detecting subagent (`task`) failures

> **Authoritative signal:** use runtime/execution-trace `task_failed` and
> `task_timed_out` events. Terminal tool results also use
> `ToolMessage(status="error")` with `task_id`, `terminal_status`,
> `subagent_type`, `error_type`, and `error` in the artifact.

Historical threads may predate this contract. Their generic `tool_call_end`
payload can under-report a subagent failure, so use the legacy prompt-log
checks below only when auditing those older captures.

For current runs, inspect the terminal lifecycle events directly:

```bash
TRACE="$TDIR/logs/execution-trace/"*.jsonl
jq -rc 'select(.event=="task_failed" or .event=="task_timed_out")
        | {event, task_id: .payload.task_id, subagent_type: .payload.subagent_type,
           error: .payload.error}' $TRACE
```

For historical runs without terminal lifecycle events:

```bash
TRAJ="$TDIR/logs/trajectory/"*.jsonl

# (a) How many subagents were dispatched, and how long each ran?
#     Durations near subagents.timeout_seconds, or clustered just under a
#     recursion ceiling, are suspicious.
jq -rc 'select(.payload.tool=="task" and .event=="tool_call_end")
        | "dur=\((.payload.duration_ms//0)/1000|floor)s timeout=\(.payload.timed_out) err=\(.payload.error)"' $TRAJ

# (b) Retry tell: count task spans vs. the number of distinct subtasks the lead
#     planned (todos / fan-out batches). MORE task spans than planned subtasks
#     means at least one failed and was re-dispatched. Fan-out batches share an
#     identical start ts:
jq -rc 'select(.payload.tool=="task" and .event=="tool_call_start") | .ts|floor' $TRAJ | sort | uniq -c
```

Confirm legacy failures in `.prompts/` by grepping the lead-agent captures:

```bash
PD="$TDIR/user-data/workspace/.prompts"

# Definitive subagent-failure markers (lead's tool-result messages):
grep -rhoE 'Task failed\. Error: [^"]*' $PD/*.txt | sort | uniq -c

# Common root causes that drive a subagent into a failure loop:
grep -rhoE 'Recursion limit of [0-9]+ reached' $PD/*.txt | sort | uniq -c   # turn budget exhausted
grep -rhc 'web_search_circuit_open' $PD/*.txt | awk -F: '$2{s+=$2}END{print s" circuit-open hits"}'
grep -rhoE 'Tool `websearch\.search` failed[^.]*' $PD/*.txt | sort | uniq -c  # search transport errors
```

> **Count caveat:** a failed-task tool message persists in the lead's message
> history, so it re-appears in *every* subsequent prompt capture. The grep
> counts above reflect how many captures contain the message, **not** how many
> distinct failures occurred. To count distinct failures, pair them with the
> retry tell from Step 1(b), or read the unique `Recursion limit of N` values
> (e.g. `50` and `30` = two distinct failed attempts on `a7629185`).

**Subagent turn budget (why these fail) — see also §5.** A subagent's
recursion/turn budget is **not** a normal chat budget. A normal lead/work-agent
run gets `recursion_limit = 1000` (`DEFAULT_RECURSION_LIMIT`,
[backend/src/config/app_config.py:57](../../backend/src/config/app_config.py#L57)).
A subagent gets an exact LangGraph graph-step limit from
`subagents.max_turns` in `config.yaml` (**default 50**) or the
`subagents.agents.<name>.max_turns` override. The value is not a model-call
count, has no hidden minimum, and cannot be supplied by the lead through the
`task` tool. Historical captures may still contain the removed per-call
`max_turns` argument.

### 3.8 Cross-reference checkpointer state (optional)

For deeper audits you can dump per-turn state from the SQLite checkpointer:

```bash
sqlite3 backend/.capyhome/checkpoints.db \
  "SELECT thread_id, checkpoint_id, type, length(checkpoint) \
     FROM checkpoints WHERE thread_id='$THREAD' ORDER BY checkpoint_id;"
```

Each row is a serialized `ThreadState` (schema:
[backend/src/agents/thread_state.py](../../backend/src/agents/thread_state.py)).
This is the source of truth for `messages`, `todos`, `plan`, `artifacts`,
`uploaded_files`, etc. at every step.

### 3.9 Cross-reference the driver metadata (only for `test_prompt.py` runs)

When a thread was launched by `prompt-tunning/test_prompt.py`, its driver
metadata lives in `prompt-tunning/prompt_id_{N}/cycle_{C}_metadata.json` and
includes `thread_id`, `chat_url`, `run_config`, `response_preview`, and
references to copied prompt logs. Grep across them by thread id:

```bash
grep -rl "$THREAD" prompt-tunning/ 2>/dev/null
```

The reference thread predates per-thread driver metadata, so this returns
nothing — but it's the right starting point for newer runs.

---

## 4. One-shot audit script

For repeatable audits, the following snippet prints the full picture for any
thread id:

```bash
audit_thread() {
  local thread="$1"
  local tdir="backend/.capyhome/threads/$thread"
  echo "== Thread $thread =="
  echo "-- plan --"
  [ -f "$tdir/user-data/workspace/plan.md" ] && head -20 "$tdir/user-data/workspace/plan.md"
  echo "-- captured prompts --"
  ls "$tdir/user-data/workspace/.prompts/" 2>/dev/null
  echo "-- workspace (deliverables live here) --"
  ls "$tdir/user-data/workspace/" 2>/dev/null
  echo "-- outputs (legacy, usually empty) --"
  ls "$tdir/user-data/outputs/" 2>/dev/null
  echo "-- trajectories --"
  for traj in "$tdir"/logs/trajectory/*.jsonl; do
    [ -f "$traj" ] || continue
    echo "  $traj"
    awk -F'"event":' '{print $2}' "$traj" | awk -F'"' '{print $2}' | sort | uniq -c | sed 's/^/    /'
    grep -o '"tool": "[^"]*"' "$traj" | sort -u | sed 's/^/    used /'
  done
}

audit_thread a73a4607-ab65-49d4-af18-66bb16b56c56
```

---

## 5. What to flag in a review

When auditing for quality/regressions, look for:

- **Plan never advanced past `draft`** — gate failed or user never approved
  (the reference thread is in this state).
- **`tool_call_timeout` events** — usually point at a slow external tool
  (knowledge vault, web search) and may correlate with degraded answers.
- **Repeated model calls with near-identical messages** — possible
  `LoopDetectionMiddleware` miss; check `middleware_event` payloads.
- **Memory facts injected that don't match the user turn** — see
  `<memory>` block inside the captured prompt; if irrelevant facts appear,
  `injection_relevance_threshold` may need tightening
  ([backend/CLAUDE.md](../../backend/CLAUDE.md) → Memory System).
- **No `present_files` despite workspace deliverables** — files written to
  `user-data/workspace/` but never surfaced to the user; check
  `WriteFileArtifactMiddleware` events. (Don't use an empty `outputs/` dir as
  the signal — it's almost always empty regardless of run success.)
- **Subagent runs hidden from the timeline** — verify `task_*` events in the
  trajectory and that `subagent_type` / `group_id` payload fields are set. In
  practice they currently are **not**: `task` spans carry only `tool_call_id`,
  the end event drops even that, and the subagent's internal model/tool calls
  never reach the trajectory (they live only in `.prompts/`).
- **Subagent terminal failures** — use `task_failed` and `task_timed_out`
  runtime/execution-trace events as the authoritative signal. For historical
  runs created before structured terminal results, use the legacy checks in
  §3.7.1.
- **Subagent graph-step exhaustion** — a subagent gets
  `subagents.max_turns` (default **50**) or its per-agent config override, not
  the lead's 1000-step budget. Search-heavy work can still exhaust this exact
  graph-step limit before answering.

---

## 6. Related references

- ThreadState schema:
  [backend/src/agents/thread_state.py](../../backend/src/agents/thread_state.py)
- Lead agent + middleware order:
  [backend/src/agents/lead_agent/agent.py](../../backend/src/agents/lead_agent/agent.py)
- Trajectory middleware (writes the JSONL):
  see `TrajectoryMiddleware` in
  [backend/src/agents/lead_agent/agent.py](../../backend/src/agents/lead_agent/agent.py)
- Path layout helpers:
  [backend/src/config/paths.py](../../backend/src/config/paths.py)
- Prompt-tuning driver that produced the reference thread:
  [prompt-tunning/test_prompt.py](../../prompt-tunning/test_prompt.py)
