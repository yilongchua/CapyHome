# Phase 3 — Chrome Web Clipper

**Status**: Not started  
**Effort**: Medium (Chrome extension + one new API endpoint)  
**Prerequisite**: Phase 2 (so clipped content goes through improved ingest)

---

## Problem

Currently the main ingestion paths into the vault are:
1. Agent-driven web search → `search_results_ingestion_queue.json` → scheduled ingest
2. Manual file drop into the raw sources directory
3. Autoresearch pipeline output

There is no browser-side capture. When a user is reading a relevant article in their browser, they have no way to route it into the vault without leaving the browser, downloading the page, and manually placing it.

---

## What llm_wiki Does

llm_wiki ships a Chrome extension (Manifest V3) that:

1. User clicks the extension icon on any web page
2. `Readability.js` strips ads/nav/sidebars → clean article HTML
3. `Turndown.js` converts HTML → Markdown
4. Extension POSTs `{title, url, content, projectPath}` to `localhost:19827/clip`
5. Rust `clip_server` holds the clip in a `PENDING_CLIPS` in-memory queue
6. React frontend polls `GET /clips/pending` every 3 seconds
7. Frontend adds clip to ingest queue → two-step CoT ingest triggers automatically

The key design element is not the local daemon itself but the lightweight capture flow:

- extract useful article content
- convert to markdown
- route it into the knowledge pipeline quickly

---

## What to Build in Capybara

### Routing decision

The updated direction is simpler:

**Rule**: the Chrome clipper writes into the existing single `knowledge_vault/`.

No multi-vault picker is required. Domain-specific login-gated systems should be handled through MCP or separate acquisition workflows, not through vault routing complexity in the clipper.

### Architecture

```
Chrome Extension                  Capybara Backend
────────────────                  ────────────────
User clicks icon
  → Readability.js extracts article
  → Turndown.js converts to Markdown
  → POST /api/vault/clip  ──────→  VaultClipEndpoint
  {                                  validates payload
    title,                           adds to search_results_ingestion_queue
    url,                             returns {queued: true, queue_id}
    content,                         and optional follow-up ingest metadata
    metadata: {
      selected_text,
      reading_time_minutes,
      domain
    }
  }
```

No dedicated clip daemon needed. Capybara's existing ingest queue (`03_ops/queues/search_results_ingestion_queue.json`) and `VaultLearningManager.claim_search_queue_items()` already handle queued content. The clip endpoint is just a new way to add items to that queue with `source_tool: "web_clipper"`.

### Backend: new endpoint

**File**: `backend/src/gateway/routers/vault.py`

```python
class VaultClipRequest(BaseModel):
    title: str
    url: str
    content: str                    # Markdown from Turndown.js
    metadata: dict = {}

@router.post("/clip")
async def clip_to_vault(request: VaultClipRequest):
    vault_manager = get_default_vault_manager()
    queue_report = vault_manager.enqueue_search_results(query=f"clip:{request.title}", results=[{
        "title": request.title,
        "url": request.url,
        "extracted_content": request.content,
        "source_tool": "web_clipper",
        "query": f"clip:{request.title}",
        "snippet": request.content[:200],
        "metadata": request.metadata,
    }])
    return {"queued": True, "queue": queue_report}
```

### Chrome Extension

Reuse llm_wiki's approach (both Readability.js and Turndown.js are MIT licensed). Key differences from llm_wiki:

| llm_wiki | Capybara clipper |
|---|---|
| POSTs to `localhost:19827` | POSTs to `localhost:8001/api/vault/clip` (Capybara Gateway port) |
| Routes to a local file path | Routes into the default `knowledge_vault` |
| Receives clips via polling | Writes directly to ingest queue via REST |
| Rust daemon required running | Only Capybara Gateway required running |

**Extension files**:

```
extension/
  manifest.json          # Manifest V3, host_permissions: localhost:8001
  popup.html
  popup.js               # Readability + Turndown + clip POST
  Readability.js         # copy from llm_wiki (MIT)
  Turndown.js            # copy from llm_wiki (MIT)
  icon-16.png
  icon-48.png
  icon-128.png
```

**manifest.json**:
```json
{
  "manifest_version": 3,
  "name": "Capybara Web Clipper",
  "version": "1.0.0",
  "description": "Clip web pages into your Capybara knowledge vault",
  "permissions": ["activeTab", "scripting"],
  "host_permissions": ["http://127.0.0.1:8001/*"],
  "action": {
    "default_popup": "popup.html",
    "default_icon": {
      "16": "icon-16.png",
      "48": "icon-48.png",
      "128": "icon-128.png"
    }
  }
}
```

**popup.js — core flow**:
```javascript
async function clip() {
  // 1. Inject Readability + Turndown into page context
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: extractArticle,   // uses Readability + Turndown
  });

  const { title, content, url } = results[0].result;

  // 2. POST to Capybara
  await fetch("http://127.0.0.1:8001/api/vault/clip", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, url, content }),
  });
  showSuccess();
}
```

---

## Frontend: clip status in vault tab

Add a small "Clipped" count to the vault status header (alongside existing sources / queued / objectives counts). This shows how many items entered via the clipper vs. autoresearch, giving the user visibility into the two ingestion paths.

No new page required — just an additional stat card in `frontend/src/app/workspace/vault/page.tsx` using the existing `useVaultStatus()` hook, once the `/api/vault/status` response includes a `clip_count` field from the manifest.

---

## CORS consideration

The Chrome extension communicates with `localhost:8001` (Capybara Gateway). Ensure CORS is configured to allow requests from Chrome extension origins (`chrome-extension://*`) on the `/api/vault/clip` endpoint. The existing Capybara FastAPI CORS middleware likely needs the extension origin added.

---

## Security note

The clip endpoint accepts arbitrary content from the browser. Apply the existing trust scoring (`VaultLearningManager._trust_score()`) to clip content, same as web search results. Low-trust clips (short content, suspicious URLs) should be rejected with the same threshold (0.55) and logged to the manifest's trust decisions.

This prevents the clipper from being used to inject garbage into the vault and ensures all ingestion paths go through the same quality gate.

---

## Optional Extension: Explicit Save From Chat

The same phase should also cover a non-browser entrypoint:

- user asks Capybara to "save this to the vault"
- the system writes the answer, sources, and metadata into the same queue or direct-ingest path

That makes the vault useful not only for captured pages but also for preserving especially good agent outputs for future reuse.
