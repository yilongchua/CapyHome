# Independent Review

## Findings

### High: Failed rows are terminal, but progress counts only successes

`record_row_result()` records failed rows as `status = 'failed'`, and `claim_rows()` only claims rows where `status = 'pending'`. That means failed rows are skipped forever unless manually reset. At the same time, `execution.completed_rows` is calculated as `COUNT(*) WHERE status = 'success'`.

References:

- `backend/src/gateway/routers/workflow.py:240`
- `backend/src/gateway/routers/workflow.py:332`
- `backend/src/gateway/routers/workflow.py:364`
- `backend/src/gateway/routers/workflow.py:378`

Impact:

The UI label can show `1/2 rows` even when both rows have been processed and one failed. This also makes `completed_rows` ambiguous: it is really `successful_rows`, not processed rows.

Recommendation:

Rename the counter or add separate counters: `processed_rows`, `successful_rows`, and `failed_rows_count`. For v1, the fastest correction is to compute terminal progress as `success + failed` while preserving `failure_rows` for details.

### High: Final all-failed batch can require an extra Execute Workflow click

When the final claimed row or batch fails, there may be no `pending` rows left, but `completed_rows` remains below `source.row_count` because it counts successes only. `record_row_result()` then leaves status as `ready`. The next `execute-next` call sees no pending rows and only then marks the workflow `done`.

References:

- `backend/src/gateway/routers/workflow.py:378`
- `backend/src/gateway/routers/workflow.py:382`
- `backend/src/gateway/routers/workflow.py:618`
- `backend/src/gateway/routers/workflow.py:620`

Impact:

The overlay can reappear once more after the final actual row work is already finished. In auto-mode this becomes an extra harmless request; in manual mode it is confusing.

Recommendation:

After recording a row, determine terminal state from the absence of `pending` and `running` rows, not from success count alone.

### Medium: Stop/cancel active run tracking is process-local

Active workflow state lives in the module global `_ACTIVE_RUNS`. `/workflow/stop` can only see child runs registered in the same gateway process.

References:

- `backend/src/gateway/routers/workflow.py:69`
- `backend/src/gateway/routers/workflow.py:529`
- `backend/src/gateway/routers/workflow.py:582`
- `backend/src/gateway/routers/workflow.py:601`

Impact:

This is acceptable for a single gateway process, but it will be unreliable with multiple workers, process restart, or horizontal scaling. A stop request can miss active child runs and leave cancellation dependent on request completion.

Recommendation:

Persist active execution leases and child run IDs in SQLite. Treat `_ACTIVE_RUNS` as an optimization, not the only control plane.

### Medium: Successful child cleanup writes audit metadata into `error`

Successful child deletion is marked by setting `error = 'child_thread_deleted'`.

References:

- `backend/src/gateway/routers/workflow.py:503`
- `backend/src/gateway/routers/workflow.py:523`

Impact:

The `error` column no longer means only error. This weakens debugging and can confuse later reporting if successful rows appear to have an error-like value.

Recommendation:

Add a dedicated column such as `child_thread_deleted_at` or `child_thread_status`.

### Medium: Existing SQLite import is not rebuilt when the source changes

`initialize_runtime()` imports the CSV only when the SQLite table is empty. If the user edits `workflow.json.source.path`, replaces the CSV, or changes the file after initialization, existing SQLite rows remain.

References:

- `backend/src/gateway/routers/workflow.py:211`
- `backend/src/gateway/routers/workflow.py:213`
- `backend/src/gateway/routers/workflow.py:214`

Impact:

The workflow can execute stale rows while `workflow.json.source` appears to point at a new or changed CSV.

Recommendation:

Store a source fingerprint in SQLite or workflow metadata. Reinitialize only after explicit confirmation when the source path, size, mtime, header, or row count changes.

### Medium: Frontend workflow logic is duplicated across chat surfaces

The main chat page and agent chat page both implement workflow status polling, execute, stop, edit, auto-mode, and overlay wiring.

References:

- `frontend/src/app/workspace/chats/[thread_id]/page.tsx:291`
- `frontend/src/app/workspace/chats/[thread_id]/page.tsx:610`
- `frontend/src/app/workspace/chats/[thread_id]/page.tsx:915`
- `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx:191`
- `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx:334`
- `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx:373`

Impact:

Behavior can drift between normal chats and agent chats. The agent page already has a smaller status handling branch than the main chat page.

Recommendation:

Extract a `useWorkflowExecution()` hook and share overlay state/action wiring between both surfaces.

### Low: No-result rows are indistinguishable from successful found rows

The plan intentionally treats `""` as a successful no-result, but the SQLite row status remains `success` and there is no separate no-result marker.

References:

- `backend/src/gateway/routers/workflow.py:326`
- `backend/src/gateway/routers/workflow.py:329`
- `backend/tests/test_workflow_router.py:121`

Impact:

CSV output is correct, but workflow analytics cannot distinguish found address rows from no-result rows without inspecting output values.

Recommendation:

Optionally add a `result_status` value such as `success`, `no_result`, `failed`.

## Positive Notes

- The blocking request model is simple and aligns with the requested workflow.
- Child threads are correctly constrained to return JSON and avoid file writes.
- CSV export is atomic through a temp file replace.
- SQLite row claiming uses `BEGIN IMMEDIATE`, which is a reasonable v1 guard against duplicate claims.
- The child recursion/step policy is left as normal Work Mode behavior, as requested.
