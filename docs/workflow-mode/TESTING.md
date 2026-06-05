# Testing And Verification

## Existing Tests Added

`backend/tests/test_workflow_router.py` covers:

- Output virtual path generation.
- CSV import into SQLite.
- Stable row claim order.
- Result accounting for success and failure.
- Output CSV export.
- Child JSON parsing for success, no-result, failure sentinel, and invalid JSON.

## Verification Performed

These checks passed during implementation:

```text
uvx ruff check src/gateway/app.py src/gateway/routers/__init__.py src/gateway/routers/workflow.py tests/test_workflow_router.py
uv run python -m py_compile src/gateway/routers/workflow.py tests/test_workflow_router.py
```

A direct backend smoke script also passed:

- Created a sample CSV.
- Wrote a workflow payload.
- Initialized SQLite.
- Recorded a result.
- Exported `<source>_output.csv`.

Gateway import smoke confirmed the workflow routes are mounted.

## Not Verified

Full backend pytest was not run because the local backend environment did not expose `pytest` through `uv run pytest`.

Frontend checks were not run because `node`, `pnpm`, and `corepack` were not available in the shell environment.

## Recommended Additional Tests

- Final batch all-failed should transition terminally without requiring a second execute click.
- `completed_rows` or replacement counters should represent intended progress semantics.
- Stop during active child execution should leave claimed rows retryable.
- `max_parallel > 1` should claim rows without duplicates and update counters deterministically.
- Reinitialization behavior should be explicit when source CSV changes after SQLite exists.
- Frontend auto-mode should stop on `done`, `stopped`, and `stopped_failed_threshold`.
- Normal chat and agent chat surfaces should have shared workflow behavior.
