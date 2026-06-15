import assert from "node:assert/strict";
import test from "node:test";

const { decideRejoinAction } = await import(
  new URL("./rejoin-policy.ts", import.meta.url).href
);

const defaults = {
  pollLoading: false,
  pollFailed: false,
  runId: "run-1",
  streamLoading: false,
  streamError: false,
  ownsLocalStream: false,
  lastJoinedRunId: null,
};

void test("does not clear local ownership before initial polling completes", () => {
  assert.equal(
    decideRejoinAction({
      ...defaults,
      pollLoading: true,
      runId: null,
      ownsLocalStream: true,
    }),
    "wait",
  );
});

void test("does not mark or join a discovered run while another stream is loading", () => {
  assert.equal(
    decideRejoinAction({
      ...defaults,
      streamLoading: true,
    }),
    "wait",
  );
});

void test("does not immediately retry an already attempted run after an error", () => {
  assert.equal(
    decideRejoinAction({
      ...defaults,
      streamError: true,
      lastJoinedRunId: "run-1",
    }),
    "wait",
  );
});

void test("joins an unowned running stream and clears ownership after a successful empty poll", () => {
  assert.equal(decideRejoinAction(defaults), "join");
  assert.equal(
    decideRejoinAction({
      ...defaults,
      runId: null,
    }),
    "clear-local-owner",
  );
});
