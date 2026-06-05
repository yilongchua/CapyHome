# Workflow Mode Review

This folder documents the current blocking `/workflow` implementation and records an independent implementation review.

## Files

- [ARCHITECTURE.md](ARCHITECTURE.md) explains how the feature is wired through frontend and backend.
- [API.md](API.md) records endpoint contracts, runtime file locations, `workflow.json`, and SQLite shape.
- [EXECUTION_FLOW.md](EXECUTION_FLOW.md) walks through planning, manual execution, auto-mode, stop, and failure handling.
- [REVIEW.md](REVIEW.md) lists review findings with severity and code references.
- [TESTING.md](TESTING.md) records verification performed and remaining test gaps.
- [workflow-flow.png](workflow-flow.png) is the flow diagram for the blocking workflow lifecycle.

## Current Assessment

The implementation follows the requested v1 architecture: `/workflow` stays in normal Work Mode, planning creates `/mnt/user-data/workspace/runtime/workflow.json`, and execution is a blocking gateway request that owns SQLite and CSV writes. Child Work Mode threads only return JSON.

The most important review concerns are:

- Failed rows are terminal but `completed_rows` counts only successful rows, so progress and finalization semantics can be misleading.
- A workflow whose final claimed batch fails can need one extra Execute Workflow click before it transitions to `done`.
- Stop/cancel relies on in-process gateway memory, so it is not robust across multiple workers or process restart.
- Successful child cleanup stores deletion metadata in the `error` column, which weakens later auditability.

See [REVIEW.md](REVIEW.md) for details.
