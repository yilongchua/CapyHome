# Architecture

## Design Intent

`/workflow` is implemented as a normal Work Mode feature, not as a new chat mode. The user sends a slash command that produces a specialized user prompt. The assistant then creates `/mnt/user-data/workspace/runtime/workflow.json`. After review, the frontend calls a blocking backend endpoint to process the next row or batch.

Runtime state is parent-thread-owned:

- `/mnt/user-data/workspace/runtime/workflow.json`
- `/mnt/user-data/workspace/runtime/workflow.sqlite`
- `/mnt/user-data/workspace/uploads/<source_stem>_output<source_suffix>`

Child Work Mode threads are intentionally stateless workers for one row. They do not write SQLite, CSV, or parent workspace files.

## Backend

Primary implementation:

- `backend/src/gateway/routers/workflow.py`
- Mounted in `backend/src/gateway/app.py`
- Exported through `backend/src/gateway/routers/__init__.py`

The backend router is responsible for:

- Resolving virtual paths through `get_paths()`.
- Normalizing and persisting `workflow.json`.
- Importing CSV rows into SQLite.
- Claiming pending rows with an immediate SQLite transaction.
- Creating normal Work Mode child threads/runs.
- Waiting for child run completion.
- Parsing child output as JSON.
- Recording results in SQLite.
- Updating execution counters in `workflow.json`.
- Exporting merged CSV output periodically and on terminal events.
- Cancelling active child runs when `/workflow/stop` is called.

Key code areas:

- Workflow path constants and router setup: `backend/src/gateway/routers/workflow.py:26`
- Workflow normalization: `backend/src/gateway/routers/workflow.py:122`
- CSV import: `backend/src/gateway/routers/workflow.py:195`
- Row claiming: `backend/src/gateway/routers/workflow.py:240`
- Child result parsing: `backend/src/gateway/routers/workflow.py:307`
- Result accounting: `backend/src/gateway/routers/workflow.py:332`
- Child prompt and child run creation: `backend/src/gateway/routers/workflow.py:434`
- Blocking execution endpoint: `backend/src/gateway/routers/workflow.py:601`

## Frontend

Primary implementation:

- `frontend/src/components/workspace/input-box.tsx`
- `frontend/src/components/workspace/plan-approval-overlay.tsx`
- `frontend/src/app/workspace/chats/[thread_id]/page.tsx`
- `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`
- `frontend/src/core/threads/slash-commands.ts`
- `frontend/src/core/workspace-io/api.ts`

The frontend is responsible for:

- Recognizing `/workflow` and `/workflow-exit`.
- Turning `/workflow` into a specialized Work Mode planning prompt.
- Showing a `WorkflowApprovalOverlay` similar to the plan approval overlay.
- Calling `execute-next` for each manual click.
- Repeating `execute-next` when auto-mode is enabled.
- Calling `stop` when the user presses the stop button during workflow execution.
- Refreshing workflow status after messages and after blocking execution responses.

Key code areas:

- Slash command list: `frontend/src/core/threads/slash-commands.ts:1`
- Workflow planning prompt: `frontend/src/components/workspace/input-box.tsx:174`
- Slash command dispatch: `frontend/src/components/workspace/input-box.tsx:970`
- Workflow overlay component: `frontend/src/components/workspace/plan-approval-overlay.tsx:194`
- Main chat workflow state and execution: `frontend/src/app/workspace/chats/[thread_id]/page.tsx:260`
- Main chat overlay rendering: `frontend/src/app/workspace/chats/[thread_id]/page.tsx:1249`
- Agent chat workflow state and execution: `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx:153`

## Source Of Truth

During execution, SQLite is the operational row store. `workflow.json` tracks user-editable execution settings and summary counters. The output CSV is a materialized artifact generated from SQLite plus source rows.

That means:

- Child output is not trusted until the backend parses and records it.
- CSV is not rewritten on every row unless flush settings make that true.
- `workflow.json.execution.max_parallel` can be edited by the user and is reread on the next execute call.
- `workflow.json.execution.current_row_index` is a hint for the next pending candidate, not an independent row lock.
