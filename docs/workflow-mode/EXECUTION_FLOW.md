# Execution Flow

## Planning

1. User sends a prompt such as:

   ```text
   @address_coy.csv /workflow for each row search websearch the full company full address in 1 liner, no addition text and add it to column 'full_address'
   ```

2. The input box recognizes `/workflow`.
3. The frontend submits a normal Work Mode message with a specialized planning prompt.
4. The assistant inspects the attached/source CSV and writes:

   ```text
   /mnt/user-data/workspace/runtime/workflow.json
   ```

5. The frontend polls workflow status after message completion.
6. If `workflow.json` exists and the workflow is not terminal, the user sees the Workflow Approval overlay.

## Manual Execution

1. User clicks Execute Workflow.
2. Frontend calls:

   ```text
   POST /api/threads/{thread_id}/workflow/execute-next
   ```

3. Backend reads fresh `workflow.json`.
4. Backend initializes SQLite from CSV if needed.
5. Backend claims up to `execution.max_parallel` pending rows.
6. Backend creates one normal Work Mode child thread/run per claimed row.
7. Backend waits for all claimed child runs to finish.
8. Backend parses final assistant output as JSON.
9. Backend writes row results to SQLite.
10. Backend updates `workflow.json.execution`.
11. Backend exports output CSV when flush conditions are met.
12. Blocking request returns to the frontend.
13. Frontend refreshes workflow status and shows the overlay again if rows remain.

With `max_parallel: 1`, each click processes the next row. With `max_parallel > 1`, each click processes one batch.

## Auto-Mode

When auto-mode is enabled, the frontend schedules another `execute-next` call after each successful blocking response as long as:

- The workflow exists.
- The workflow is not `done`.
- The workflow is not `stopped`.
- The workflow is not `stopped_failed_threshold`.
- No workflow request is already in flight.
- The overlay was not dismissed with `/workflow-exit`.

This preserves the blocking backend design while allowing unattended repetition.

## Stop

If the stop button is pressed while a workflow request is in flight:

1. Frontend calls:

   ```text
   POST /api/threads/{thread_id}/workflow/stop
   ```

2. Backend marks the active in-memory workflow run as stop requested.
3. Backend attempts to cancel active child LangGraph runs.
4. Running rows are reset to `pending`.
5. `workflow.json.execution.status` becomes `stopped`.
6. Backend exports the current output CSV if SQLite exists.

The stopped workflow is retryable because cancelled/running rows return to `pending`.

## Failure Handling

These outcomes are failures:

- Child run error.
- Invalid JSON.
- Non-object JSON.
- Missing required output fields.
- Configured failure value, normally `"failed run"`.

Failure effects:

- SQLite row status becomes `failed`.
- Row number is appended to `execution.failure_rows`.
- `execution.consecutive_failures` increments.
- At 5 consecutive failures, `execution.status` becomes `stopped_failed_threshold`.

The configured no-result value, normally `""`, is a successful no-result and resets consecutive failures.
