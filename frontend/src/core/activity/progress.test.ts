import assert from "node:assert/strict";
import test from "node:test";

const { buildProgressOperations } = await import(
  new URL("./progress.ts", import.meta.url).href
);

void test("title start and completion collapse into one operation", () => {
  const operations = buildProgressOperations([
    {
      id: "run-1:1",
      run_id: "run-1",
      timestamp: 1,
      actor: "system",
      kind: "title_generation_start",
      line: "Generating chat title...",
    },
    {
      id: "run-1:2",
      run_id: "run-1",
      timestamp: 2,
      actor: "system",
      kind: "title_generation_completed",
      line: 'Title generated: "Setting up coffee at home in a tiny kitchen"',
    },
  ]);

  assert.equal(operations.length, 1);
  assert.equal(operations[0]?.operationId, "title:run-1");
  assert.equal(operations[0]?.status, "completed");
  assert.equal(
    operations[0]?.label,
    'Title generated: "Setting up coffee at home in a tiny kitchen"',
  );
});

void test("tool start and end collapse into one operation", () => {
  const operations = buildProgressOperations([
    {
      id: "run-1:1",
      run_id: "run-1",
      timestamp: 1,
      actor: "capyhome",
      kind: "tool_call_start",
      line: "Writing file: /mnt/user-data/workspace/report.md...",
      task_id: "tool-1",
      payload: { tool: "write_file" },
    },
    {
      id: "run-1:2",
      run_id: "run-1",
      timestamp: 2,
      actor: "capyhome",
      kind: "tool_call_end",
      line: "Wrote: /mnt/user-data/workspace/report.md",
      task_id: "tool-1",
      payload: { tool: "write_file" },
    },
  ]);

  assert.equal(operations.length, 1);
  assert.equal(operations[0]?.operationId, "tool:tool-1");
  assert.equal(operations[0]?.status, "completed");
  assert.equal(operations[0]?.label, "Wrote: /mnt/user-data/workspace/report.md");
});

void test("planner start creates complexity completion and plan-created completes todos", () => {
  const operations = buildProgressOperations([
    {
      id: "run-1:1",
      run_id: "run-1",
      timestamp: 1,
      actor: "capyhome",
      kind: "planning_started",
      line: "Planner is evaluating request complexity...",
    },
    {
      id: "run-1:2",
      run_id: "run-1",
      timestamp: 2,
      actor: "capyhome",
      kind: "plan_created",
      line: "Plan created with 3 todo(s)",
      payload: { todo_count: 3 },
    },
  ]);

  assert.equal(operations.length, 2);
  assert.equal(operations[0]?.operationId, "planner:complexity:run-1");
  assert.equal(operations[0]?.status, "completed");
  assert.equal(operations[0]?.label, "Planner evaluated request complexity");
  assert.equal(operations[1]?.operationId, "planner:todos:run-1");
  assert.equal(operations[1]?.status, "completed");
  assert.equal(operations[1]?.label, "Planner created 3 todo(s)");
});

void test("subagent task lifecycle collapses by task id", () => {
  const operations = buildProgressOperations([
    {
      id: "run-1:1",
      run_id: "run-1",
      timestamp: 1,
      actor: "baby_capy",
      kind: "task_started",
      line: "Baby Capy - source-researcher is working on restaurants...",
      task_id: "task-1",
    },
    {
      id: "run-1:2",
      run_id: "run-1",
      timestamp: 2,
      actor: "baby_capy",
      kind: "task_completed",
      line: "Baby Capy - source-researcher finished restaurants",
      task_id: "task-1",
    },
  ]);

  assert.equal(operations.length, 1);
  assert.equal(operations[0]?.operationId, "subagent:task-1");
  assert.equal(operations[0]?.status, "completed");
  assert.equal(operations[0]?.label, "Baby Capy - source-researcher finished restaurants");
});

void test("generic model response events do not render as progress operations", () => {
  const operations = buildProgressOperations([
    {
      id: "run-1:1",
      run_id: "run-1",
      timestamp: 1,
      actor: "capyhome",
      kind: "model_response",
      line: "CapyHome is working on choosing the next actions...",
      assistant_message_id: "message-1",
      payload: { tool_names: ["write_file"], tool_calls_count: 1 },
    },
    {
      id: "run-1:2",
      run_id: "run-1",
      timestamp: 2,
      actor: "capyhome",
      kind: "model_response",
      line: "CapyHome is working on finalizing the response...",
      assistant_message_id: "message-2",
      payload: {},
    },
  ]);

  assert.deepEqual(operations, []);
});

void test("todo and file presentation progress hides internal output details", () => {
  const operations = buildProgressOperations([
    {
      id: "run-1:1",
      run_id: "run-1",
      timestamp: 1,
      actor: "capyhome",
      kind: "tool_call_start",
      line: "Updating todo list...",
      task_id: "todos-1",
      payload: { tool: "write_todos" },
    },
    {
      id: "run-1:2",
      run_id: "run-1",
      timestamp: 2,
      actor: "capyhome",
      kind: "tool_call_end",
      line: "Updated todo list",
      task_id: "todos-1",
      tool_summary: "Updated todo graph with 3 item(s); ready=[]",
      payload: { tool: "write_todos" },
    },
    {
      id: "run-1:3",
      run_id: "run-1",
      timestamp: 3,
      actor: "capyhome",
      kind: "tool_call_end",
      line: "Preparing files",
      task_id: "present-1",
      tool_summary: "Successfully presented files",
      payload: { tool: "present_files" },
    },
  ]);

  assert.equal(operations.length, 2);
  assert.equal(operations[0]?.label, "Updated todo list");
  assert.equal(operations[0]?.detail, undefined);
  assert.equal(operations[1]?.label, "Presented files");
  assert.equal(operations[1]?.detail, undefined);
});
