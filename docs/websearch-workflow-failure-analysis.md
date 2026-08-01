# WebSearch / Workflow Row-Failure Analysis

**Date:** 2026-08-01
**Context:** COSCO Fleet Review CSV workflow (thread `d5359bc2-...`), 382 rows, per-row
`websearch_search` + `query_knowledge_vault` lookups run as child Work-Mode threads.
**Status:** Diagnosis complete. Fixes below are *not yet applied* — this document exists
to capture the root cause before choosing between a patch and a redesign.

---

## 1. Symptoms

Child threads ("wf rN") returned successful results but many rows failed to append to
the output CSV. Breakdown of the analysed batch:

| Rows | Behaviour | Tool actually used |
|------|-----------|--------------------|
| 1, 6, 67 | clean success | `websearch_search` + `query_knowledge_vault` only |
| 58–63 | called websearch, every call errored | `websearch_search` (all raised) |
| 64, 68 | 2 messages, instant empty JSON | **no tool at all** |
| 65, 66, 67, 69, 70 | 30–98 messages of `curl`/`python3` scraping | **`bash` only, never websearch** |

Two `invalid_json` symptoms were also seen — these were a *separate* parser bug (prose
before the JSON), fixed independently in `parse_child_result`.

Dashboard (3h window) at the time:

```
Queries: total=30 ok=30 fail=0 avg=6.2s
Sources: total=99 ok=66 fail=33
Containers: 8
```

The mismatch is the crux: **the websearch server reported 30/30 queries OK**, but the
client (langgraph) saw `httpx.ReadTimeout` / `BrokenResourceError` / `ExceptionGroup`
and even `Failed to load MCP tools (bulk)`.

---

## 2. The 33 failed sources are a red herring

- **Query** = one `websearch_search` call. **Source** = each individual URL crawled
  inside that query (a query fans out to several).
- 33/99 failed sources = normal per-URL crawl attrition (bot-blocks, paywalls, slow
  pages). These are **agnostic to the child request** and do **not** fail a row — the
  query still returns `ok` with the surviving sources (hence Queries 30/30 ok).

The row failures came from a different layer entirely (below).

---

## 3. Two distinct failure layers

| Layer | What timed out | Was `websearch_search` in the toolset? | Recovery |
|-------|----------------|----------------------------------------|----------|
| **A. Invocation** (rows 58–63) | a `websearch_search` *call* | Yes | Automatic — `ToolErrorBoundaryMiddleware` converts to a recoverable error `ToolMessage` |
| **B. Tool-list handshake** (rows 64–70) | the MCP *tool listing* at agent-build | **No — tool absent** | Only on config-mtime change / process restart |

Layer B is the important one: when the tool is absent, the model has no web-search
capability, so it improvises with `bash`/`curl`/`python` scraping (rows 65,66,67,69,70)
or gives up and emits empty JSON (rows 64,68).

---

## 4. The exact "drop" condition (client side)

`websearch_search` disappears from a child agent's toolset when **`await client.get_tools()`
raises during MCP cache initialization**:

1. websearch has `excluded_tools: []` → it is in the **bulk group**
   ([`tools.py:133`](../backend/src/mcp/tools.py#L133)), fetched via a single
   `MultiServerMCPClient.get_tools()`.
2. `get_tools()` performs the MCP handshake + tool-list over HTTP to
   `localhost:9000/mcp`, bounded by `timeout_seconds: 40`
   ([`client.py:46-49`](../backend/src/mcp/client.py#L46)). If it exceeds 40s or the
   stream breaks → `ReadTimeout` / `BrokenResourceError` / `ExceptionGroup`.
3. The `except Exception` at [`tools.py:164`](../backend/src/mcp/tools.py#L164) **swallows
   it** — logs `Failed to load MCP tools (bulk)`, appends nothing. websearch is now absent.
4. `get_mcp_tools()` returns the websearch-less list — it does **not** re-raise.

### Why it is sticky (cache poisoning)

A single 40s timeout drops websearch for the **whole process**, not one row:

```python
# cache.py initialize_mcp_tools()
_mcp_tools_cache = await get_mcp_tools()   # line 74 → websearch-less list
_cache_initialized = True                   # line 75 → marked done unconditionally
```

- Every later `get_cached_mcp_tools()` sees `_cache_initialized=True` and returns the
  poisoned list ([`cache.py:103`](../backend/src/mcp/cache.py#L103),
  [`cache.py:122`](../backend/src/mcp/cache.py#L122)) — **it never retries**.
- It self-heals only when `_is_cache_stale()` fires (`extensions_config.json` mtime
  changes) or the langgraph process restarts.

This is why rows 58–63 had websearch but 64–70 did not: the cache was poisoned at the
`03:13:55` bulk-load timeout and stayed poisoned.

### Design flaws that amplify it

- **No `asyncio.wait_for` on the bulk path** — the 10s preview path has one
  ([`tools.py:44`](../backend/src/mcp/tools.py#L44)); the bulk load relies solely on the
  per-server httpx `timeout=40`.
- **Bulk load is all-or-nothing** — only websearch is enabled today, but if more bulk
  servers are added, one timing out drops *all* of them (TaskGroup → ExceptionGroup).
- **Failure is cached as success** — "load failed" is treated identically to "no tools".

---

## 5. Why doesn't the websearch server respond? (server side — CONFIRMED from `~/Desktop/websearch`)

Topology: `docker/websearch-nginx.conf` load-balances 8 replicas with `least_conn`,
`proxy_read_timeout 120s`, `proxy_next_upstream error timeout ... tries 3`. Health is
`GET /health` → `{"ok":"true","service":"websearch","crawler_mode":"cli"}`.

### `/health` does NOT make a search call — confirmed

`main.py:86-92` — the handler returns a static dict (service name + `crawler.mode` config
value). No SearXNG call, no crawl. `/mcp` `tools/list` (`main.py:149-150`) is likewise a
**static schema** (`_mcp_tool_schema()`, pure dict, no I/O). So neither endpoint does any
real work. Health failing is **not** health-does-work.

### It is NOT request-volume overload — it is event-loop starvation

Root cause = **a single-worker event loop blocked by synchronous I/O and CPU work on the
search hot path.** Confirmed facts:

1. **One event loop per container.** `main.py:221` runs `uvicorn.run("main:app", …)` with
   **no `workers=`** → 1 worker. Each of the 8 replicas is a single asyncio event loop.
2. **Up to 8 concurrent searches admitted per container.** `server.max_concurrent_requests: 8`
   (`config.yaml:19`) gates `/search` via a semaphore (`main.py:23-46`); the 9th waits only
   `queue_timeout_seconds: 2.0` then gets `503`. So one event loop juggles up to 8 in-flight
   searches.
3. **The pipeline is `async` but runs blocking work directly on the loop:**
   - `append_event` (`utils/events.py:91-103`) is **synchronous** `os.open`/`os.write`/`os.close`,
     called on the hot path for *every* source event (attempted/succeeded/failed) and every
     query start/end — many blocking file appends per query.
   - Every `run_query` starts with **synchronous filesystem scans**: `maybe_prune_markdown_daily`
     / `maybe_prune_event_logs_daily` (`pipeline.py:459-466`) and `failed_domains` →
     `iter_events` glob+read of the event logs (`pipeline.py:475`, `events.py:114-133`).
   - **CPU-bound** `assess_content_quality` (regex scoring, `utils/cleanup.py`) and
     `_html_to_text` regex run on every extracted page (up to `extract_top_k: 2` per query,
     up to 6000 chars each).

With 8 searches on one loop, these synchronous file writes + directory scans + CPU regex
**serialize and monopolize the event loop**. A trivial `/health` or `/mcp tools/list`
coroutine can't be scheduled until the loop yields, so it queues for seconds — long enough
to exceed the client's probe / 40s handshake timeout. `/health` does zero work; it is
**starved, not overloaded.**

This is also exactly what drops `websearch_search`: the MCP `initialize` + `tools/list`
handshake at agent-build (§4) is a fast static response, but it's scheduled on the same
blocked loop → it exceeds the 40s client bound → `client.get_tools()` raises → cache
poisoned.

### Why the dashboard shows queries OK while the client sees failures

The blocking work delays completion; the server's own `queue_succeeded` event still fires
once the loop gets around to it (dashboard 30/30 ok, avg 6.2s), but the **client already
gave up** at 40s / the SSE stream broke (`BrokenResourceError`). Server-success and
client-timeout are measured at different ends of the same slow loop.

---

## 6. Why patching is not enough

The obvious patches — (a) don't mark the cache initialized on failure so the next child
retries, (b) cache the tool schema client-side, (c) harden the prompt to never use bash —
all *contain* the damage but leave the core coupling intact: **a slow crawl still blocks
the whole replica (queries, handshakes, and health), and burst concurrency still saturates
all replicas at once.** Patches make failure graceful; they do not make websearch responsive
under load.

---

## 7. Redesign directions (to discuss)

The confirmed root cause is **blocking work on a single event loop**, so the
highest-leverage fixes attack that directly:

1. **Get blocking work off the event loop (biggest win, smallest change).** Offload the
   synchronous file I/O (`append_event`, pruning scans, `failed_domains`) and CPU work
   (`assess_content_quality`, HTML→text regex) via `asyncio.to_thread(...)` / a thread
   pool, or batch event logging through an in-memory queue flushed by a background task so
   the hot path never does inline `os.write`. This alone stops `/health` and `tools/list`
   starvation.

2. **Run more than one worker per container.** `uvicorn --workers N` (or gunicorn +
   uvicorn workers) so one busy crawl loop can't monopolize the container. With #1, removes
   head-of-line blocking.

3. **Serve `/health` and `/mcp tools/list` from a path that can't be starved.** They are
   static — a reserved worker / separate lightweight process keeps liveness and the tool
   handshake always answerable, directly preventing the `websearch_search` tool-drop.

4. **Decouple search from crawl.** SearXNG metasearch is sub-second; crawl4ai is the slow
   part. Return hits + snippets immediately, make full-page crawl optional/async. Many row
   lookups (name, address, website) don't need a full crawl.

5. **Async job model.** `submit → poll → fetch`, so the MCP call returns in <1s and never
   holds a connection for 40s — removes the timeout class entirely.

6. **Client-side (CapyHome) resilience regardless of the server fix.** Only mark the MCP
   cache initialized on a *successful* bulk load (no poisoning); cache the tool schema so
   agent-build never depends on the live server; prompt-harden so a websearch failure
   returns the failure value instead of falling back to `bash`/`curl`/`python`.

---

## 8. Open questions

- Why did rows overlap when `max_parallel: 1`? (Frontend overlapping `execute-next`? A
  separate concurrency source?) — self-inflicted burst is worth eliminating regardless.
- With #1+#2, is `max_concurrent_requests: 8` per container still the right ceiling, or
  should it be tuned against CPU cores once blocking work is off the loop?
- Do we even need per-page crawl for this workflow's fields, or is SearXNG snippet data
  (option 4) enough for name/address/website?
