import assert from "node:assert/strict";
import test from "node:test";

const { normalizeApiUrlForCache } = await import(
  new URL("./client-cache-key.ts", import.meta.url).href
);

void test("normalizeApiUrlForCache treats trailing slashes as same client", () => {
  assert.equal(
    normalizeApiUrlForCache("http://localhost:2026/api/langgraph/"),
    "http://localhost:2026/api/langgraph",
  );
  assert.equal(
    normalizeApiUrlForCache("http://localhost:2026/api/langgraph///"),
    "http://localhost:2026/api/langgraph",
  );
});

void test("normalizeApiUrlForCache keeps mock and production URLs distinct", () => {
  assert.notEqual(
    normalizeApiUrlForCache("http://localhost:3000/mock/api"),
    normalizeApiUrlForCache("http://localhost:2026/api/langgraph"),
  );
});
