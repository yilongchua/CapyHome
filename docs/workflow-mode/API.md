# API And Runtime Contract

## Runtime Files

The implementation uses these virtual paths inside the parent thread workspace:

```text
/mnt/user-data/workspace/runtime/workflow.json
/mnt/user-data/workspace/runtime/workflow.sqlite
/mnt/user-data/workspace/uploads/<source_stem>_output<source_suffix>
```

For `address_coy.csv`, the output path is:

```text
/mnt/user-data/workspace/uploads/address_coy_output.csv
```

## Endpoints

```text
GET    /api/threads/{thread_id}/workflow
PATCH  /api/threads/{thread_id}/workflow
POST   /api/threads/{thread_id}/workflow/initialize
POST   /api/threads/{thread_id}/workflow/execute-next
POST   /api/threads/{thread_id}/workflow/stop
GET    /api/threads/{thread_id}/workflow/status
POST   /api/threads/{thread_id}/workflow/export
```

`execute-next` is intentionally blocking. It returns after all claimed rows in the current batch finish, fail, or are cancelled.

## `workflow.json`

```json
{
  "version": "1",
  "source": {
    "path": "/mnt/user-data/workspace/uploads/address_coy.csv",
    "type": "csv",
    "columns": [],
    "row_count": 0
  },
  "runtime": {
    "workflow_json": "/mnt/user-data/workspace/runtime/workflow.json",
    "sqlite": "/mnt/user-data/workspace/runtime/workflow.sqlite",
    "output_csv": "/mnt/user-data/workspace/uploads/address_coy_output.csv"
  },
  "row_task": {
    "instruction": "",
    "input_fields": [],
    "output_schema": {},
    "failure_value": "failed run",
    "no_result_value": ""
  },
  "execution": {
    "status": "ready",
    "max_parallel": 1,
    "flush_every_completed_rows": 20,
    "flush_all": false,
    "add_to_memory": false,
    "current_row_index": 0,
    "completed_rows": 0,
    "consecutive_failures": 0,
    "consecutive_failures_limit": 5,
    "failure_rows": []
  }
}
```

## SQLite Table

The current table is:

```sql
CREATE TABLE IF NOT EXISTS workflow_rows (
  row_index INTEGER PRIMARY KEY,
  row_number TEXT NOT NULL,
  source_json TEXT NOT NULL,
  result_json TEXT,
  status TEXT NOT NULL,
  child_thread_id TEXT,
  child_run_id TEXT,
  started_at TEXT,
  completed_at TEXT,
  error TEXT
);
```

Expected row statuses:

- `pending`: available to claim.
- `running`: claimed by an active `execute-next` request.
- `success`: child returned valid JSON and no failure sentinel.
- `failed`: child errored, returned invalid JSON, missed required fields, or returned the configured failure value.

Cancelled rows are reset to `pending` by the backend.

## Child Output Contract

The child prompt tells the child Work Mode run:

- Execute one row only.
- Return only valid JSON.
- Do not write files.
- Return `"failed run"` for the requested output field when websearch times out.
- Return `""` for the requested output field when no result exists.

Example:

```json
{
  "full_address": "1 Raffles Place, Singapore 048616"
}
```

Invalid JSON, missing required fields, and the configured failure value are recorded as failed rows.
