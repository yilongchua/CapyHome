import assert from "node:assert/strict";
import test from "node:test";

const { hasPendingToolResultsInCurrentTurn } = await import(
  new URL("./utils.ts", import.meta.url).href
);

void test("detects unresolved tool calls in the current real user turn", () => {
  const messages = [
    { id: "h1", type: "human", content: "Research EVs" },
    {
      id: "a1",
      type: "ai",
      content: "I will research this.",
      tool_calls: [
        {
          id: "task-1",
          name: "task",
          args: { description: "Research used EVs" },
        },
      ],
    },
  ];

  assert.equal(hasPendingToolResultsInCurrentTurn(messages), true);
});

void test("clears pending tool calls when their tool result arrives", () => {
  const messages = [
    { id: "h1", type: "human", content: "Research EVs" },
    {
      id: "a1",
      type: "ai",
      content: "",
      tool_calls: [{ id: "task-1", name: "task", args: {} }],
    },
    {
      id: "t1",
      type: "tool",
      tool_call_id: "task-1",
      content: "Task Succeeded. Result: Done",
    },
  ];

  assert.equal(hasPendingToolResultsInCurrentTurn(messages), false);
});

void test("ignores terminal UI tool calls and older turns", () => {
  const messages = [
    { id: "h1", type: "human", content: "Old turn" },
    {
      id: "a1",
      type: "ai",
      content: "",
      tool_calls: [{ id: "old-search", name: "web_search", args: {} }],
    },
    { id: "h2", type: "human", content: "Show file" },
    {
      id: "a2",
      type: "ai",
      content: "Here is the file.",
      tool_calls: [{ id: "present-1", name: "present_files", args: {} }],
    },
  ];

  assert.equal(hasPendingToolResultsInCurrentTurn(messages), false);
});
