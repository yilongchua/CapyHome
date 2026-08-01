# CapyHome — Scaffolding Reference

Code structure and patterns for **Conversation**, **Persistent Memory**, **Tools**, **Skills (slash commands)**, **Side Panel (collapsible UI)**, and **Sandbox Environment**. Intended as a copy-reference for a new project.

---

## 1. Conversation

### 1.1 Thread State (`src/agents/thread_state.py`)

All LangGraph agent state lives in `ThreadState`, a typed dict that accumulates across turns via reducer functions. Key sub-schemas:

| Schema | Purpose |
|---|---|
| `SandboxState` | Holds `sandbox_id` for the active execution environment |
| `ThreadDataState` | Paths: `workspace_path`, `uploads_path`, `outputs_path`, `mounted_path` |
| `PlanState` | Full plan lifecycle: id, status, todos, clarifications, evaluation, approval |
| `MemoryState` | In-context memory snapshot injected into system prompt |
| `TodoState` | Task list items and their statuses |
| `TrajectoryRuntimeState` | Run-id and file-path for trajectory logging |
| `SkillDisclosureState` | Tracks which skills are active and when they were injected |

`ThreadState` composes all sub-schemas via `Annotated` reducers:

```python
class ThreadState(
    AgentState,
    SandboxState,
    ThreadDataState,
    MemoryState,
    TodoState,
    PlanState,
    ActivityTimelineState,
    ContextMetricsState,
    ExecutionTraceState,
    ...
):
    artifacts: Annotated[list[str], merge_artifacts]
    viewed_images: Annotated[dict, merge_viewed_images]
    clarifications: Annotated[list[dict], merge_clarifications]
    ...
```

Reducers like `merge_artifacts` deduplicate lists; `merge_viewed_images` merges dicts. This is the LangGraph pattern for safe concurrent state updates.

---

### 1.2 Work Agent (`src/agents/work_agent/agent.py`)

Entry point for task execution. Built with LangGraph's `create_react_agent`:

```python
agent = create_react_agent(
    model=model,
    tools=get_available_tools(mode="work", subagent_enabled=True),
    state_schema=ThreadState,
    checkpointer=get_checkpointer(),
    store=get_memory_store(),
)
```

The agent is wrapped by a middleware stack (see 1.4) before it is served. Each turn:
1. Middleware `before_agent` hooks run (sandbox init, skill injection, memory injection, etc.)
2. The React loop runs: model generates → tool calls → tool results → repeat
3. Middleware `after_agent` hooks run (sandbox release, title generation, memory update, etc.)

The system prompt is assembled in `src/agents/work_agent/prompt.py` and includes:
- Current date/time
- SOUL.md (persona definition)
- Enabled skills injected inline
- Memory injection point (`<!--__MEMORY_INJECTION_POINT__-->` sentinel replaced at runtime)
- Subagent instructions when `subagent_enabled=True`

---

### 1.3 Plan Agent (`src/agents/plan_agent/agent.py`)

Handles plan mode. Uses the same `create_react_agent` pattern with:
- `mode="plan"` → loads `internal_tools_plan.json` instead of work catalog
- `forced_plan_draft=True` → exposes only `write_plan` (recovery mode)
- Same middleware stack minus execution-only middlewares

---

### 1.4 Middleware Registry (`src/agents/common/middleware_registry.py`)

Middleware is layered in order. Each `AgentMiddleware` has `before_agent` and `after_agent` hooks. Key middlewares (work agent order):

| Middleware | What it does |
|---|---|
| `SandboxMiddleware` | Acquires/releases sandbox; lazy by default (first tool call) |
| `ThreadDataMiddleware` | Injects workspace/uploads/outputs paths into state |
| `MemoryMiddleware` | Loads memory snapshot into state before each turn |
| `SummarizationMiddleware` | Compacts old messages when context grows large |
| `ClarificationMiddleware` | Intercepts `ask_user_for_clarification` tool calls, enqueues questions |
| `PlannerMiddleware` | Orchestrates plan creation (slim orchestrator, calls `write_plan` tool) |
| `PlanExecutionGateMiddleware` | Blocks work runs until plan is approved |
| `TodoMiddleware` | Manages todo list lifecycle |
| `TodoDagMiddleware` | Reads todo dependency graph, blocks todos on unanswered clarifications |
| `SteeringMiddleware` | Injects mid-run user guidance from the steering queue |
| `TitleMiddleware` | Generates conversation title after first turn |
| `ActivityTimelineMiddleware` | Tracks which tools ran and when |
| `SkillDisclosureMiddleware` | Injects active skill SKILL.md content into system prompt |
| `ToolDisclosureMiddleware` | Adds tool descriptions to the context |
| `AutoresearchMiddleware` | Triggers background knowledge vault ingestion |
| `WorkModeMiddleware` | Routes between plan and work agents based on mode |

---

### 1.5 Runtime Context (`src/agents/common/runtime_context.py`)

Passed through `runtime.context` to every tool. Standard keys:

```python
{
    "thread_id": str,       # Current conversation thread
    "agent_name": str,      # Which agent is running
    "sandbox_id": str,      # Set by SandboxMiddleware after acquisition
    "run_id": str,          # LangGraph run identifier
}
```

Tools access this via `runtime.context.get("thread_id")` etc.

---

### 1.6 Checkpointer (`src/agents/checkpointer/`)

Persists LangGraph state across turns using SQLite.

```
checkpointer/
├── provider.py          # get_checkpointer() → singleton ExtendedSqliteSaver
├── async_provider.py    # async variant for async graph runs
└── extended_sqlite_saver.py  # Adds None-safe fix + WAL mode for SQLite
```

`get_checkpointer()` returns a process-singleton backed by `.capyhome/checkpoints.db`. WAL mode is enabled for concurrent read access.

The checkpointer is passed to `create_react_agent(..., checkpointer=checkpointer)` — LangGraph then auto-saves/restores `ThreadState` by `thread_id` between turns.

---

### 1.7 Thread & Run API Routes

**Threads** (`src/gateway/routers/threads.py`):
- `GET /api/threads` — list all threads
- `POST /api/threads` — create thread
- `GET /api/threads/{id}` — get thread state
- `DELETE /api/threads/{id}` — delete thread + its checkpoint + filesystem data

**Runs** (`src/gateway/routers/runs.py`):
- `POST /api/threads/{id}/runs` — start a new agent run (returns SSE stream)
- `GET /api/threads/{id}/runs/{run_id}` — poll run status
- `POST /api/threads/{id}/runs/{run_id}/cancel` — cancel in-flight run

Runs stream SSE events: `message_chunk`, `tool_call`, `tool_result`, `run_complete`, `run_error`, `plan_adapted`.

---

## 2. Persistent Memory

### 2.1 Architecture Overview

```
Memory System
├── store.py          # File-based JSON persistence (versioned snapshots)
├── vector_store.py   # Embedding index for semantic recall queries
├── queue.py          # Pending memory writes buffered between turns
├── updater.py        # LLM-driven memory extraction from conversation
├── summarization_hook.py  # Triggers summarization when messages grow
├── compaction_archive.py  # Archives old messages after summarization
└── prompt.py         # Assembles memory block for system prompt injection
```

---

### 2.2 Memory Store (`src/agents/memory/store.py`)

Version-aware JSON file store. Two scopes:

| Scope | Key | Path |
|---|---|---|
| `global` | `"global"` | `.capyhome/memory/<agent>/global/` |
| `workspace` | `thread_id` | `.capyhome/memory/<agent>/workspace/<thread_id>/` |

Each scope directory holds:
- `<sha256>.json` — immutable versioned snapshot
- `latest.json` — pointer to current version SHA

Core operations:

```python
def load_memory(agent_name, scope, workspace_id) -> dict | None
def save_memory(agent_name, data, scope, workspace_id) -> Path
def get_memory_versions(agent_name, scope, workspace_id) -> list[dict]
def rollback_memory(agent_name, version_id, scope, workspace_id) -> dict
```

Memory data format:
```json
{
  "entries": [
    {
      "id": "uuid",
      "content": "The user prefers bullet points over prose.",
      "category": "preference",
      "confidence": 0.9,
      "source": "conversation",
      "created_at": "2026-07-31T00:00:00Z",
      "updated_at": "2026-07-31T00:00:00Z"
    }
  ],
  "updated_at": "2026-07-31T00:00:00Z"
}
```

Automatic PII redaction on save: credit cards (`CARD_RE`), emails (`EMAIL_RE`), phone numbers (`PHONE_RE`).

---

### 2.3 Vector Store (`src/agents/memory/vector_store.py`)

Wraps a sentence-transformer embedding model + FAISS (or numpy fallback) for semantic search.

```python
store = get_memory_vector_store()  # process singleton

# Index entries
store.index(entries=[{"id": ..., "content": ..., "scope": ..., "scope_id": ...}])

# Query across multiple scopes
results = store.query(
    query="user prefers dark mode",
    scopes=[("workspace", thread_id), ("global", "global")],
    top_k=5,
)
# returns list of dicts with score, content, category, etc.
```

The index is rebuilt from the JSON store on startup and after each save.

---

### 2.4 Memory Queue (`src/agents/memory/queue.py`)

Buffers pending memory write operations between agent turns. The updater enqueues extracted facts; the middleware drains the queue after each turn.

```python
queue = get_memory_queue()
queue.enqueue(agent_name, scope, workspace_id, entries)
pending = queue.drain()
```

---

### 2.5 Memory Updater (`src/agents/memory/updater.py`)

Sends conversation messages to the LLM to extract new memory facts, then merges them into the store:
- Deduplicates by content similarity (cosine distance < threshold)
- Resolves conflicts: newer higher-confidence entry wins
- Categorizes entries: `preference`, `fact`, `task`, `feedback`, `context`

Called from `MemoryMiddleware.after_agent()`.

---

### 2.6 Memory Config (`src/config/memory_config.py`)

```python
@dataclass
class MemoryConfig:
    enabled: bool = True
    global_scope_enabled: bool = True
    workspace_scope_enabled: bool = True
    recall_top_k: int = 10
    auto_update: bool = True
    update_model: str = "claude-haiku-4-5-20251001"
    similarity_threshold: float = 0.85
```

---

### 2.7 Memory Middleware (`src/agents/middlewares/memory_middleware.py`)

`before_agent`: loads the current memory snapshot and injects it into `ThreadState.memory`.
`after_agent`: calls `updater.update()` to extract and persist new facts from the completed turn.

The memory block is rendered by `src/agents/memory/prompt.py` and inserted at `<!--__MEMORY_INJECTION_POINT__-->` in the system prompt.

---

### 2.8 Summarization Middleware (`src/agents/middlewares/summarization_middleware.py`)

When `len(messages) > max_messages` (configurable), calls the LLM to produce a summary, replaces old messages with a single summary `HumanMessage`, and archives the originals via `compaction_archive.py`.

Config lives in `src/config/summarization_config.py`:
```python
@dataclass
class SummarizationConfig:
    enabled: bool = True
    max_messages: int = 40
    summary_model: str = "claude-haiku-4-5-20251001"
    keep_recent_n: int = 6   # messages kept verbatim after summary
```

---

### 2.9 Memory API Routes (`src/gateway/routers/memory.py`)

- `GET /api/memory` — fetch current memory (scope + workspace_id params)
- `POST /api/memory` — upsert a memory entry
- `DELETE /api/memory/{entry_id}` — delete one entry
- `GET /api/memory/versions` — list version history
- `POST /api/memory/rollback` — restore a previous version

---

## 3. Tools

### 3.1 Tool Loading Pipeline

```
config.yaml tools: []        ← user-declared tools (community modules)
internal_tools_work.json     ← JSON catalog for work mode (LLM-facing descriptions)
internal_tools_plan.json     ← JSON catalog for plan mode
external_tools.json          ← MCP server policy (filter rules)
extensions_config.json       ← enabled/disabled overrides per tool
```

`get_available_tools(mode, subagent_enabled, model_name)` in `src/tools/tools.py`:
1. Loads config-declared tools, filtered by group/mode/enabled-override
2. Loads cached MCP tools (initialized at startup), filtered by `external_tools.json` policy
3. Loads JSON-catalog builtins via `_build_builtin_tools_from_json()` (or legacy `BUILTIN_TOOLS` list)
4. Deduplicates by name — JSON-built wins on collision
5. Returns merged `list[BaseTool]`

Mode controls which JSON file is loaded:
- `mode="plan"` → `internal_tools_plan.json`
- `mode="work"` or unset → `internal_tools_work.json`

---

### 3.2 Tool Definition Schema (`src/tools/schema.py`)

```python
class ToolDefinition(BaseModel):
    name: str
    description: str           # LLM-facing; overrides handler's docstring
    handler: str               # "src.tools.builtins.recall_tool:recall_tool"
    mode: list[ToolMode]       # ["plan", "work", "auto"]
    phase: list[ToolPhase]     # ["draft", "approved"]
    groups: list[str]
    endpoint: ToolEndpoint     # "primary" | "helper" | "any"
    requires_vision: bool
    requires_subagent_enabled: bool
    parameters: ToolParameters # JSON Schema for LLM
    deprecated: bool
```

`ToolParameters` mirrors the JSON Schema `object` format with `required` and `properties`.

---

### 3.3 Tool Loader (`src/tools/loader.py`)

`build_structured_tool(defn)`:
1. Resolves `defn.handler` string to a Python `BaseTool` via reflection
2. Asserts `tool.name == defn.name` (fail-fast on mismatch)
3. Overwrites `tool.description` with `defn.description`
4. Patches `args_schema.model_fields[arg].description` for each parameter
5. Attaches `_capyhome_policy = defn` for audit/diagnostics

`schema_drift_report(defn, tool)` — used in tests to detect JSON/handler arg mismatch.

`filter_mcp_tools_by_policy(tools, policy, mode, phase, subagent)` — applies `external_tools.json` MCP server rules.

---

### 3.4 Built-in Tools (`src/tools/builtins/`)

| Tool name | File | What it does |
|---|---|---|
| `ask_user_for_clarification` | `clarification_tool.py` | Non-blocking question queue; intercepted by `ClarificationMiddleware` |
| `recall` | `recall_tool.py` | Semantic search over long-term memory |
| `present_files` | `present_file_tool.py` | Exposes workspace files to the frontend artifact viewer |
| `view_image` | `view_image_tool.py` | Reads image → base64 → stored in `ThreadState.viewed_images` |
| `task` | `task_tool.py` | Dispatches a subagent; returns `Command` to fork a child run |
| `write_plan` | `write_plan_tool.py` | Creates/updates a structured plan in state + on disk |
| `write_todos` | `write_todos_tool.py` | Creates/updates the todo list in state |
| `setup_agent` | `setup_agent_tool.py` | Writes SOUL.md + config.yaml for a custom agent |

All builtins use `@tool("name", parse_docstring=True)` from LangChain. Tools that need thread state receive `runtime: ToolRuntime[ContextT, ThreadState]` as an injected parameter.

Tools that must update state return `Command(update={...})` — LangGraph merges these via state reducers.

---

### 3.5 Sandbox Tools (`src/sandbox/tools.py`)

Sandbox-backed tools available in work mode (declared in `internal_tools_work.json`):

| Tool | Description |
|---|---|
| `bash` | Run any shell command in the sandbox |
| `ls` | List directory (tree, 2 levels) |
| `read_file` | Read text file, optional line range |
| `write_file` | Write/append text file |
| `str_replace` | In-place string replace in a file |
| `grep` | Recursive regex search across files |

All sandbox tools call `ensure_sandbox_initialized(runtime)` (lazy acquisition) then `ensure_thread_directories_exist(runtime)`. Virtual paths (`/mnt/user-data/workspace/*`) are translated to host paths before execution and back in output.

---

### 3.6 Mode-scoped Community Tools

```python
_COMMUNITY_TOOL_MODES = {
    "query_knowledge_vault": frozenset({"work", "auto"}),
    "save_to_knowledge_vault": frozenset({"work", "auto"}),
    "write_todos": frozenset({"work", "auto"}),
    "bash": frozenset({"work", "auto"}),
    "write_file": frozenset({"work", "auto"}),
    "str_replace": frozenset({"work", "auto"}),
}
```

Tools absent from this map are available in every mode.

---

### 3.7 Tool Audit CLI (`src/tools/audit.py`)

```bash
PYTHONPATH=. uv run python -m src.tools.audit --mode work --subagent
PYTHONPATH=. uv run python -m src.tools.audit --mode plan
```

Renders a Markdown table of the resolved tool surface for any mode/phase/vision/subagent combination.

---

## 4. Skills (Slash Commands)

### 4.1 What a Skill Is

A **Skill** is a directory containing a `SKILL.md` file. When a skill is enabled, `SkillDisclosureMiddleware` injects its full content into the system prompt before each agent turn. The LLM reads the SKILL.md instructions as if they were part of its operating context.

Users activate skills via the frontend or by referencing them with `/skill-name`.

---

### 4.2 Directory Layout

```
skills/
├── public/               # Bundled skills
│   ├── coding/
│   │   └── SKILL.md
│   └── research/
│       └── SKILL.md
└── custom/               # User-created skills
    └── my-skill/
        └── SKILL.md
```

A flat layout (no `public/` subdirectory) is also supported — the root acts as the public category.

---

### 4.3 SKILL.md Format (`src/skills/parser.py`)

```markdown
---
name: my-skill
description: One-line description shown in the skills list
license: MIT           # optional
paths:                 # optional glob patterns for auto-activation
  - "*.py"
  - "src/**"
workflow: false        # true = intentional batch workflow skill
---

# Skill instructions here

The agent reads everything below the frontmatter and follows it.
```

---

### 4.4 Skill Types (`src/skills/types.py`)

```python
class Skill(CapyBaseModel):
    name: str
    description: str
    skill_dir: Path         # Host path to skill directory
    skill_file: Path        # Host path to SKILL.md
    category: Literal["public", "custom"]
    enabled: bool           # Controlled via extensions_config.json
    paths: list[str] | None # Auto-activation glob patterns
    workflow: bool          # Batch workflow flag
```

---

### 4.5 Skill Loader (`src/skills/loader.py`)

```python
skills = load_skills(enabled_only=True)
```

- Scans `skills/public/` and `skills/custom/` (or flat root)
- Parses each `SKILL.md` via `parse_skill_file()`
- Reads enabled state from `extensions_config.json` (live disk read, not cached)
- Returns sorted list of `Skill` objects

---

## 5. Side Panel — Collapsible UI

### 5.1 Sidebar Architecture (Frontend)

The workspace uses shadcn/ui's `Sidebar` component with collapsible icon mode:

```tsx
// workspace-sidebar.tsx
<Sidebar variant="sidebar" collapsible="icon">
  <SidebarHeader>
    <WorkspaceHeader />
  </SidebarHeader>
  <SidebarContent>
    <WorkspaceNavChatList />
    {isSidebarOpen && <RecentChatList />}   {/* hidden when collapsed */}
  </SidebarContent>
  <SidebarFooter>
    <WorkspaceNavMenu />
  </SidebarFooter>
  <SidebarRail />   {/* drag handle */}
</Sidebar>
```

`useSidebar()` provides `open` (boolean) and `setOpen` to control collapse state.
`collapsible="icon"` collapses to icon-only width; `collapsible="offcanvas"` slides fully off screen.

---

### 5.2 Collapsible Primitive (`src/components/ui/collapsible.tsx`)

Wraps `@radix-ui/react-collapsible` for individual collapsible sections:

```tsx
<Collapsible>
  <CollapsibleTrigger className="cursor-pointer">
    Toggle section
  </CollapsibleTrigger>
  <CollapsibleContent>
    Hidden content revealed on click
  </CollapsibleContent>
</Collapsible>
```

Three exports: `Collapsible` (root), `CollapsibleTrigger`, `CollapsibleContent`.
Uses `data-slot` attributes for targeting in CSS.

---

### 5.3 Workspace Layout (`src/components/workspace/workspace-container.tsx`)

Three layout primitives:

```tsx
<WorkspaceContainer>          {/* flex h-screen w-full flex-col */}
  <WorkspaceHeader            {/* h-16, breadcrumb + rightSlot */}
    rightSlot={<ActionButtons />}
  >
    <BreadcrumbItem />        {/* children append to breadcrumb trail */}
  </WorkspaceHeader>
  <WorkspaceBody>             {/* flex min-h-0 flex-1, scrollable */}
    {children}
  </WorkspaceBody>
</WorkspaceContainer>
```

`WorkspaceHeader` auto-builds a breadcrumb from the current `pathname` (first 2 segments). Pass `rightSlot` for header-right actions. Pass `children` to extend the breadcrumb.

---

## 6. Sandbox Environment

### 6.1 Abstract Interface (`src/sandbox/sandbox.py`)

```python
class Sandbox(ABC):
    def execute_command(self, command: str) -> str: ...
    def read_file(self, path: str) -> str: ...
    def list_dir(self, path: str, max_depth=2) -> list[str]: ...
    def write_file(self, path: str, content: str, append=False) -> None: ...
    def update_file(self, path: str, content: bytes) -> None: ...
```

Two concrete implementations ship:
- `LocalSandbox` — runs commands in a subprocess on the host machine
- `AioSandbox` (community) — runs commands in a remote Docker container

---

### 6.2 Local Sandbox (`src/sandbox/local/local_sandbox.py`)

Executes commands via `subprocess.run` with shell=True. Key behaviors:
- Detects shell: tries `/bin/zsh` → `/bin/bash` → `/bin/sh` → `shutil.which("sh")`
- `timeout=600` seconds per command
- Path mapping: container paths (e.g. `/mnt/skills`) ↔ host paths; resolved before execution, reverse-resolved in output
- `_resolve_paths_in_command()` — regex-replaces container path prefixes in command strings before running
- `_reverse_resolve_paths_in_output()` — strips host paths from stdout/stderr to keep virtual paths user-visible

---

### 6.3 Sandbox Provider (`src/sandbox/sandbox_provider.py`)

```python
provider = get_sandbox_provider()   # process singleton

sandbox_id = provider.acquire(thread_id)   # get or reuse sandbox for thread
sandbox = provider.get(sandbox_id)
provider.release(sandbox_id)               # return to pool / destroy
provider.shutdown()                        # at process exit
```

For `LocalSandbox`: one sandbox per thread (keyed by `thread_id`), reused across turns. No pooling needed.
For `AioSandbox`: acquires a Docker container slot, releases it when the run completes.

---

### 6.4 Sandbox Middleware (`src/sandbox/middleware.py`)

`SandboxMiddleware(lazy_init=True)`:
- `lazy_init=True` (default): sandbox acquired on first `ensure_sandbox_initialized()` call inside a tool
- `lazy_init=False`: acquired eagerly in `before_agent()`
- `after_agent()`: releases the sandbox unless `release_on_exit=False` (used by subagents that inherit parent's sandbox)

State update on acquisition:
```python
{"sandbox": {"sandbox_id": sandbox_id}}
```

---

### 6.5 Virtual Path System

All user-facing paths use the `/mnt/user-data/` virtual prefix:

| Virtual path | Maps to |
|---|---|
| `/mnt/user-data/workspace/*` | `thread_data["workspace_path"]/*` |
| `/mnt/user-data/uploads/*` | `thread_data["uploads_path"]/*` (legacy alias) |
| `/mnt/user-data/outputs/*` | `thread_data["workspace_path"]/*` (canonicalized) |

Functions in `src/sandbox/tools.py`:
- `replace_virtual_path(path, thread_data)` — virtual → host
- `to_virtual_path(path, thread_data)` — host → virtual (for storing artifact URLs)
- `replace_virtual_paths_in_command(command, thread_data)` — regex-replaces all occurrences in a command string
- `inject_thread_env_in_command(command, thread_id)` — prepends `CAPYBARA_HOME_THREAD_ID` and `THREAD_ID` env vars

---

## Quick-Reference: Key File Paths

| Area | File |
|---|---|
| Conversation state schema | `backend/src/agents/thread_state.py` |
| Work agent | `backend/src/agents/work_agent/agent.py` |
| Plan agent | `backend/src/agents/plan_agent/agent.py` |
| Middleware registry | `backend/src/agents/common/middleware_registry.py` |
| Checkpointer | `backend/src/agents/checkpointer/provider.py` |
| Thread/Run API routes | `backend/src/gateway/routers/threads.py`, `runs.py` |
| Memory store | `backend/src/agents/memory/store.py` |
| Memory vector store | `backend/src/agents/memory/vector_store.py` |
| Memory middleware | `backend/src/agents/middlewares/memory_middleware.py` |
| Memory config | `backend/src/config/memory_config.py` |
| Memory API routes | `backend/src/gateway/routers/memory.py` |
| Tool assembly | `backend/src/tools/tools.py` |
| Tool JSON schema | `backend/src/tools/schema.py` |
| Tool loader | `backend/src/tools/loader.py` |
| Built-in tools | `backend/src/tools/builtins/` |
| Sandbox tools | `backend/src/sandbox/tools.py` |
| Work tool catalog | `backend/src/tools/internal_tools_work.json` |
| Plan tool catalog | `backend/src/tools/internal_tools_plan.json` |
| Skills loader | `backend/src/skills/loader.py` |
| Skill type | `backend/src/skills/types.py` |
| Skill parser | `backend/src/skills/parser.py` |
| Sidebar component | `frontend/src/components/workspace/workspace-sidebar.tsx` |
| Collapsible primitive | `frontend/src/components/ui/collapsible.tsx` |
| Workspace layout | `frontend/src/components/workspace/workspace-container.tsx` |
| Sandbox abstract | `backend/src/sandbox/sandbox.py` |
| Local sandbox | `backend/src/sandbox/local/local_sandbox.py` |
| Sandbox middleware | `backend/src/sandbox/middleware.py` |
| Sandbox provider | `backend/src/sandbox/sandbox_provider.py` |
