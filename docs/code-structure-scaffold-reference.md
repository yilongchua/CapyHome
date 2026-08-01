# CapyHome Code Structure And Scaffolding Reference

This document extracts the reusable structure behind CapyHome's conversation runtime, persistent memory, tools, slash commands, collapsible side panels, and sandbox environment. It is written as a reference for scaffolding another project with the same core shape.

## 1. System Shape

CapyHome is a full-stack agent app with four runtime layers:

- `frontend/`: Next.js chat workspace, thread list, settings, artifacts, slash commands, and collapsible panels.
- `backend/src/agents/`: LangGraph agent graphs, state schema, prompts, middleware chain, planning, memory, todo tracking, and streaming events.
- `backend/src/gateway/`: FastAPI gateway for memory, tools, models, uploads, artifacts, threads, runs, settings, workflow controls, and local integrations.
- `backend/src/sandbox/`: sandbox provider abstraction plus local and container-ready execution tools.

The root service split is:

- LangGraph server: graph runtime on port `2024`.
- Gateway API: FastAPI on port `8001`.
- Frontend: Next.js on port `3000`.
- Nginx unified entry: port `2026`.

## 2. Conversation Runtime

### Backend Graphs

Primary entry points:

- `backend/langgraph.json`
  - Registers `work_agent` as `src.agents:make_work_agent`.
  - Registers `plan_agent` as `src.agents:make_plan_agent`.
  - Registers an async checkpointer factory at `src/agents/checkpointer/async_provider.py:make_checkpointer`.
- `backend/src/agents/work_agent/agent.py`
  - Builds the main agent with model selection, tools, state schema, and middleware.
  - Uses `create_agent(...)` from LangChain/LangGraph.
  - Loads tools through `get_available_tools(...)`.
  - Uses `ThreadState` as the shared schema.
- `backend/src/agents/plan_agent/agent.py`
  - Thin wrapper around the work-agent builder.
  - Forces runtime config fields: `current_mode = "plan"`, `is_plan_mode = true`, `mode = "plan"`, `plan_behavior = "plan_foreground"`.

The project keeps Work Mode and Plan Mode as separate graph IDs while sharing most infrastructure. That is a useful scaffold pattern: route users to distinct graphs for distinct behavior, but reuse tool loading, memory, sandbox, and middleware where possible.

### Thread State

Canonical state lives in `backend/src/agents/thread_state.py`.

Important state groups:

- `messages`: inherited from LangChain `AgentState`.
- `sandbox`: contains `sandbox_id`.
- `thread_data`: contains host paths for workspace, uploads, outputs, and mounted folders.
- `title`: generated thread title.
- `artifacts`: presented files for the UI.
- `todos` and `todo_graph`: execution steps, dependencies, and readiness.
- `plan`: plan metadata, approval status, clarification state, handoff state, and plan paths.
- `uploaded_files`, `viewed_images`: transient file/vision bookkeeping.
- `activity_timeline`, `execution_trace`, `trajectory`, `metrics`: UI/debug timeline state.
- `scratchpad`, `task_memory`, `memory_version_ref`: long-running task and memory metadata.

For another project, start with a minimal state schema:

```python
class ThreadState(AgentState):
    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    plan: NotRequired[PlanState | None]
    todos: NotRequired[list[dict]]
    activity_timeline: Annotated[list[dict], merge_activity_timeline]
    memory_version_ref: NotRequired[MemoryVersionRefState | None]
```

Add reducers for any list-like state that can be updated by parallel tool calls.

### Middleware Chain

The conversation behavior is mostly middleware-driven. The reusable scaffold is:

1. `ThreadDataMiddleware`: establish per-thread file paths.
2. `UploadsMiddleware`: inject uploaded file metadata into the current turn.
3. `SandboxMiddleware`: acquire or reuse execution sandbox.
4. Permission and tool-disclosure middlewares.
5. Summarization and skill/memory injection.
6. Planning/todo middleware.
7. Title, follow-up questions, and memory queueing.
8. Vision, error boundary, retry, timeout, output truncation.
9. Activity timeline, execution trace, metrics.
10. Clarification interrupt middleware last.

The ordering matters because path setup must happen before sandbox tools, memory should see final conversation messages, and clarification interrupts should be the last interceptor.

### Frontend Thread Streaming

Primary files:

- `frontend/src/core/threads/hooks.ts`
- `frontend/src/core/threads/types.ts`
- `frontend/src/components/workspace/chats/use-thread-chat.ts`
- `frontend/src/app/workspace/chats/[thread_id]/page.tsx`

The frontend uses LangGraph SDK's `useStream<AgentThreadState>()`:

- `assistantId: "work_agent"` by default.
- `threadId` is the current thread ID.
- `fetchStateHistory: true`.
- `reconnectOnMount: false`; the app has its own rejoin logic.
- `onCreated`: captures new thread ID and refreshes thread lists.
- `onLangChainEvent`: watches `on_tool_end` events and refreshes artifacts/uploads when write-like tools complete.
- `onUpdateEvent`: patches thread-list state for `title`, `plan`, `work_mode`, `phase_execution`, and handoff metadata.
- `onCustomEvent`: handles domain events such as plan creation, phase start/completion, compaction, traces, and activity.
- `onMetadataEvent`: stores the active `run_id`.
- `onFinish`: caches final messages, updates timestamps, refreshes workspace, and drains the queued-message list.

Submission flow:

1. Resolve browser attachment blobs to `File` objects.
2. Upload files through Gateway `POST /api/threads/{thread_id}/uploads`.
3. Create optimistic user/upload messages.
4. Build `runConfigurable` context:
   - `model_name`
   - `mode`
   - `is_plan_mode`
   - `thinking_enabled`
   - `subagent_enabled`
   - `plan_behavior`
   - `auto_mode`
   - `thread_id`
   - `current_turn_text`
   - `original_user_request`
5. Call `thread.submit({ messages }, { threadId, config, context, streamResumable: true })`.

For another project, keep the distinction between:

- `config`: LangGraph execution config such as recursion limits.
- `context`: your application runtime fields used by tools and middleware.
- `values`: persisted thread state.

## 3. Persistent Memory

### Backend Memory Files

Primary files:

- `backend/src/agents/middlewares/memory_middleware.py`
- `backend/src/agents/memory/queue.py`
- `backend/src/agents/memory/updater.py`
- `backend/src/agents/memory/store.py`
- `backend/src/agents/memory/vector_store.py`
- `backend/src/agents/memory/prompt.py`
- `backend/src/gateway/routers/memory.py`
- `backend/src/config/memory_config.py`
- `backend/src/config/memory_versioning_config.py`

### Memory Data Model

Memory version `2.0` contains:

- `scope`: `global` or `workspace`.
- `scopeId`: `global` or a thread/workspace ID.
- `lastUpdated`.
- `user.workContext`, `user.personalContext`, `user.topOfMind`.
- `history.recentMonths`, `history.earlierContext`, `history.longTermBackground`.
- `facts`: structured facts with `id`, `content`, `category`, `confidence`, `createdAt`, `source`.
- `behaviorRules`: instructions with `id`, `instruction`, `active`, `scope`, `scopeId`, timestamps.

Storage locations are resolved in `backend/src/config/paths.py`:

- Global memory: `{base_dir}/memory.json`.
- Per-agent memory: `{base_dir}/agents/{agent}/memory.json`.
- Workspace memory: `{base_dir}/threads/{thread_id}/memory.json`.
- Optional version records: configured by `memory_versioning.storage_dir`.

### Memory Update Flow

`MemoryMiddleware.after_agent(...)` queues a memory update after a run:

1. Skip if memory disabled or runtime context has `add_to_memory = false`.
2. Require `thread_id`.
3. Filter messages:
   - keep human messages,
   - keep final AI messages without tool calls,
   - remove tool messages,
   - strip ephemeral `<uploaded_files>` blocks.
4. Queue both global and workspace memory updates through `MemoryUpdateQueue`.
5. Debounce updates by `memory.debounce_seconds`.
6. `MemoryUpdater` asks a model to produce JSON memory updates.
7. Save updated memory and sync vector-store facts.

There is also a slash-like memory command handled server-side: a user message beginning with `/memory ` is captured by `MemoryMiddleware.before_agent(...)` and saved as a workspace behavior rule.

### Memory Gateway API

Routes in `backend/src/gateway/routers/memory.py`:

- `GET /api/memory?scope=global|workspace&workspace_id=...`
- `POST /api/memory/reload`
- `GET /api/memory/config`
- `GET /api/memory/status`
- `GET /api/memory/versions`
- `GET /api/memory/versions/{version_id}`
- `POST /api/memory/redact`
- `POST /api/memory/facts/{fact_id}`
- `DELETE /api/memory/facts/{fact_id}`
- `POST /api/memory/rules`
- `PATCH /api/memory/rules/{rule_id}`
- `DELETE /api/memory/rules/{rule_id}`
- `POST /api/memory/forget-thread`
- `GET /api/memory/compactions`
- `POST /api/memory/clear`

Frontend bindings:

- `frontend/src/core/memory/api.ts`
- `frontend/src/core/memory/hooks.ts`
- `frontend/src/components/workspace/settings/memory-settings-page.tsx`

### Minimal Memory Config

```yaml
memory:
  enabled: true
  storage_path: memory.json
  max_facts: 100
  fact_confidence_threshold: 0.7
  injection_enabled: true
  max_injection_tokens: 2000
  model_name: null
  debounce_seconds: 30
  global_scope_enabled: true
  workspace_scope_enabled: true
  behavior_rules_enabled: true

memory_versioning:
  enabled: false
  require_expected_sha: false
  storage_dir: .capyhome/memory_versions
```

## 4. Tools

### Tool Sources

Primary files:

- `backend/src/tools/tools.py`
- `backend/src/tools/loader.py`
- `backend/src/tools/schema.py`
- `backend/src/tools/internal_tools_work.json`
- `backend/src/tools/internal_tools_plan.json`
- `backend/src/tools/external_tools.json`
- `backend/src/tools/builtins/`
- `backend/src/sandbox/tools.py`
- `backend/src/mcp/`
- `extensions_config.json`

Tool categories:

- Sandbox tools: `bash`, `ls`, `grep`, `read_file`, `write_file`, `str_replace`.
- Built-in interaction/artifact tools: `ask_user_for_clarification`, `present_files`, `view_image`.
- Planning tools: `write_plan`, `write_todos`.
- Memory tools: `recall`, knowledge-vault query/save.
- Subagent tool: `task`.
- MCP tools: loaded from enabled MCP servers.
- Community tools: toggled from `extensions_config.json`.

### Tool Loading Flow

`get_available_tools(...)` combines:

1. Config-defined tools from `config.yaml`.
2. Cached MCP tools, filtered by `external_tools.json` policy when enabled.
3. JSON-driven built-in/sandbox tools from either:
   - `internal_tools_work.json`
   - `internal_tools_plan.json`
4. Optional vision tool if selected model supports vision.
5. Optional `task` tool if `subagent_enabled = true`.
6. Mode filtering so Plan Mode and Work Mode expose different tool contracts.
7. Deduplication by tool name.

The JSON catalog pattern is worth copying. Each entry declares:

- `name`
- `description`
- `handler`
- `mode`
- `phase`
- `groups`
- `endpoint`
- feature gates such as `requires_vision` and `requires_subagent_enabled`
- return contract
- examples
- parameter schema

`loader.py` resolves the `handler` to a `BaseTool`, copies descriptions into the tool and args schema, attaches policy metadata, and validates drift between JSON and the Python handler.

### Minimal Tool Config

```yaml
tool_groups:
  - name: file:read
  - name: file:write
  - name: bash

tools:
  - group: file:read
    name: ls
    use: src.sandbox.tools:ls_tool
  - group: file:read
    name: read_file
    use: src.sandbox.tools:read_file_tool
  - group: file:write
    name: write_file
    use: src.sandbox.tools:write_file_tool
  - group: file:write
    name: str_replace
    use: src.sandbox.tools:str_replace_tool
  - group: bash
    name: bash
    use: src.sandbox.tools:bash_tool

permissions:
  default_mode: auto
  allow: []
  deny: []
  ask: []
```

### MCP Config

`extensions_config.json` owns dynamic external tools:

```json
{
  "mcpServers": {
    "websearch": {
      "enabled": false,
      "type": "http",
      "url": "http://localhost:9000/mcp",
      "health_url": "http://localhost:9000/health",
      "timeout_seconds": 25
    },
    "filesystem": {
      "enabled": false,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/files"],
      "env": {}
    }
  },
  "skills": {},
  "communityTools": {}
}
```

Frontend management lives in:

- `frontend/src/core/mcp/api.ts`
- `frontend/src/core/mcp/hooks.ts`
- `frontend/src/components/workspace/settings/tool-settings-page.tsx`

## 5. Slash Commands

### Frontend Parser

Primary files:

- `frontend/src/core/threads/slash-commands.ts`
- `frontend/src/components/workspace/chat-ui/slash-command-dropdown.tsx`
- `frontend/src/components/workspace/input-box.tsx`

The parser only treats leading slash text as a command:

- trims leading whitespace,
- requires `/`,
- extracts the first whitespace-delimited token,
- lowercases the command name,
- returns `args`, `query`, `isRecognized`, and `showMenu`.

Supported frontend commands:

- `/compact`
- `/recover`
- `/handoff`
- `/new`
- `/mount`
- `/analyse`
- `/publishdocs`
- `/rename`
- `/workflow`
- `/workflow-recover`
- `/workflow-exit`

The dropdown filters command options by `query` and calls `onExecute(name)`. It is purely UI; execution is in `InputBox`.

### Frontend Command Execution

`InputBox.executeSlashCommand(...)` handles:

- `/compact`: `POST /api/threads/{thread_id}/compact`, clears local caches, emits workspace refresh.
- `/recover` or `/recover -todo`: cancels stale running LangGraph runs, builds a recovery prompt from incomplete todos, and submits it with `extraContext.recover_todo_command = true`.
- `/new`: routes to a fresh chat.
- `/mount`: opens native folder picker and persists mounted folder through workspace IO API.
- `/rename <title>`: updates thread title; without args opens a rename dialog.
- `/analyse`: stages `.docs` and `.analyse` artifacts for mounted folders.
- `/publishdocs`: copies staged docs to the mounted folder.
- `/workflow <task>`: submits a workflow-planning prompt with `extraContext.workflow_planning = true`.
- `/workflow-recover`: dispatches a browser `workflow-recover` event.
- `/workflow-exit`: dispatches a browser `workflow-exit` event.

### Backend Slash-Like Commands

There are two backend-side command surfaces:

- `MemoryMiddleware.before_agent(...)`: captures `/memory ...` as a behavior-rule injection.
- `backend/src/channels/manager.py`: external IM channels support `/new`, `/status`, `/models`, `/memory`, `/approvals`, `/pending`, `/approve`, `/reject`, and `/help`.

For another project, separate commands into:

- UI-only commands: navigation, dialogs, folder picker.
- Gateway commands: compaction, uploads, mounted folders, workflows.
- Agent commands: slash text converted into a normal user prompt plus runtime context flags.
- Channel commands: commands for Slack/Telegram/etc. that do not require the web UI.

## 6. Collapsible Side Panels

### Workspace Sidebar

Primary files:

- `frontend/src/app/workspace/layout.tsx`
- `frontend/src/components/workspace/workspace-sidebar.tsx`
- `frontend/src/components/ui/sidebar`
- `frontend/src/core/settings/local.ts`

Pattern:

- Wrap workspace in `SidebarProvider`.
- Store open/collapsed state in local settings: `settings.layout.sidebar_collapsed`.
- Use `Sidebar collapsible="icon"` for icon-only collapse.
- Render navigation, recent chats, and footer menu in the sidebar.
- Use `SidebarInset` for the main page.

### Settings Side Panel/Dialog

Primary file:

- `frontend/src/components/workspace/settings/settings-dialog.tsx`

Pattern:

- Dialog content uses a two-column layout: `220px` nav plus scrollable detail pane.
- Section metadata is an array of `{ id, label, icon }`.
- Active section is local React state.
- Each settings domain is its own component:
  - memory
  - tools
  - skills
  - models/LLM
  - browser
  - channels
  - cleanup
  - performance

This is a clean scaffold for any app settings surface: one registry array, one active-section state, independent section components.

### Chat Activity And Directory Panel

Primary files:

- `frontend/src/components/workspace/chats/chat-box.tsx`
- `frontend/src/components/workspace/chats/chat-activity-panel.tsx`
- `frontend/src/components/workspace/artifacts/artifact-list-tray.tsx`
- `frontend/src/components/workspace/artifacts/context.tsx`

`ChatBox` uses `react-resizable-panels`:

- Main panel: conversation content.
- Right panel: activity timeline and directory/artifact tabs.
- Nested directory explorer can collapse independently.
- Panel sizes are remembered in refs and restored on expand.
- Artifacts poll only while the directory tab is visible and the document is visible.

`ChatActivityPanel` provides collapsible information:

- workflow status card,
- phase progress,
- activity timeline,
- grouped timeline rows with per-group collapse,
- whole-timeline collapse toggle.

`ArtifactListTray` is a smaller bottom tray with:

- local `collapsed` state,
- clickable header,
- height transition,
- file list body.

For another project, use three levels:

- Global navigation sidebar: app-wide routes and thread list.
- Thread right panel: run-specific activity, files, traces, todos.
- Inline trays/popovers: contextual files, slash commands, attachments.

## 7. Sandbox Environment

### Path Model

Primary files:

- `backend/src/config/paths.py`
- `backend/src/agents/middlewares/thread_data_middleware.py`
- `backend/src/sandbox/path_mapping.py`
- `backend/src/sandbox/tools.py`

Virtual paths shown to agents:

- `/mnt/user-data/workspace`
- `/mnt/user-data/workspace/uploads`
- `/mnt/user-data/mounted`
- `/mnt/skills`

Host paths:

- `{base_dir}/threads/{thread_id}/user-data/workspace`
- `{base_dir}/threads/{thread_id}/user-data/workspace/uploads`
- `{base_dir}/threads/{thread_id}/user-data/workspace` for outputs, because outputs are a legacy alias.
- `{base_dir}/threads/{thread_id}/memory.json` for workspace memory.

`ThreadDataMiddleware` computes paths per run and stores them in state. In lazy mode it does not create folders immediately; sandbox tools call `ensure_thread_directories_exist(...)` on first use.

### Provider Abstraction

Primary files:

- `backend/src/sandbox/sandbox.py`
- `backend/src/sandbox/sandbox_provider.py`
- `backend/src/sandbox/local/local_sandbox.py`
- `backend/src/sandbox/local/local_sandbox_provider.py`
- Docker/container provider lives under `backend/src/community/` when enabled.

Provider contract:

```python
class SandboxProvider(ABC):
    def acquire(self, thread_id: str | None = None) -> str: ...
    def get(self, sandbox_id: str) -> Sandbox | None: ...
    def release(self, sandbox_id: str) -> None: ...
```

Sandbox contract:

```python
class Sandbox(ABC):
    def execute_command(self, command: str) -> str: ...
    def read_file(self, path: str) -> str: ...
    def write_file(self, path: str, content: str) -> None: ...
    def list_dir(self, path: str) -> str: ...
```

Config picks the provider:

```yaml
sandbox:
  use: src.sandbox.local:LocalSandboxProvider
```

The local provider returns a singleton sandbox with ID `local`. Container providers can use the same interface but isolate per thread, mount `/mnt/user-data`, and release at shutdown or idle timeout.

### Sandbox Tools

`backend/src/sandbox/tools.py` wraps sandbox operations as LangChain tools:

- `bash`: translates virtual paths for local sandbox, injects `CAPYBARA_HOME_THREAD_ID` and `THREAD_ID`, executes command.
- `ls`: returns a capped directory tree.
- `grep`: read-only recursive regex search.
- `read_file`: reads optional line ranges.
- `write_file`: writes files into workspace.
- `str_replace`: structured file edit.

Critical helper functions:

- `replace_virtual_path(path, thread_data)`
- `to_virtual_path(path, thread_data)`
- `replace_virtual_paths_in_command(command, thread_data)`
- `ensure_sandbox_initialized(runtime)`
- `ensure_thread_directories_exist(runtime)`
- `is_local_sandbox(runtime)`

For another project, keep virtual paths stable and never expose raw host paths to the model or frontend.

## 8. Gateway Scaffolding

Primary file:

- `backend/src/gateway/app.py`

Gateway setup:

- Load app config during startup.
- Start optional background services in FastAPI lifespan.
- Include routers for each domain.
- Expose `GET /health`.
- Let nginx proxy `/api/langgraph/*` to LangGraph and app-specific `/api/*` to Gateway.

Routers relevant to this reference:

- `memory.py`: persistent memory and behavior rules.
- `mcp.py`: MCP server config.
- `community_tools.py`: built-in tool toggles.
- `uploads.py`: per-thread file upload.
- `artifacts.py`: serve and list workspace artifacts.
- `threads.py`: thread cleanup, compaction, hard stop.
- `runs.py`: run config and non-blocking resume.
- `workflow.py` and `workspace_io.py`: slash-command-backed mounted-folder workflows.
- `clarifications.py`: answer agent clarification interrupts.

## 9. Recommended Scaffold Order

Build a new project in this order:

1. Create a LangGraph `ThreadState` with `messages`, `thread_data`, `sandbox`, `artifacts`, `title`, and optional `plan`.
2. Add `Paths` and virtual path mapping.
3. Add `ThreadDataMiddleware`.
4. Add a local `SandboxProvider` and sandbox tools.
5. Add `get_available_tools(...)` with JSON catalogs for work/plan mode.
6. Add the work-agent graph and checkpointing.
7. Add FastAPI Gateway routes for uploads, artifacts, memory, threads, runs, tools.
8. Add frontend `useThreadStream(...)` around LangGraph SDK `useStream`.
9. Add the chat page and `InputBox`.
10. Add slash command parser/dropdown and route commands to UI, Gateway, or agent prompt submission.
11. Add memory middleware, queue, updater, and memory settings page.
12. Add collapsible sidebar, activity panel, and artifact directory panel.
13. Add Plan Mode only if the app needs explicit plan approval.

## 10. Minimal Portable Directory Layout

```text
my-agent-app/
├── backend/
│   ├── langgraph.json
│   └── src/
│       ├── agents/
│       │   ├── thread_state.py
│       │   ├── work_agent/agent.py
│       │   ├── plan_agent/agent.py
│       │   ├── middlewares/
│       │   └── memory/
│       ├── config/
│       │   ├── app_config.py
│       │   ├── paths.py
│       │   └── sandbox_config.py
│       ├── gateway/
│       │   ├── app.py
│       │   └── routers/
│       ├── sandbox/
│       │   ├── sandbox.py
│       │   ├── sandbox_provider.py
│       │   ├── path_mapping.py
│       │   ├── tools.py
│       │   └── local/
│       └── tools/
│           ├── tools.py
│           ├── loader.py
│           ├── internal_tools_work.json
│           └── internal_tools_plan.json
├── frontend/
│   └── src/
│       ├── app/workspace/
│       ├── components/workspace/
│       │   ├── input-box.tsx
│       │   ├── workspace-sidebar.tsx
│       │   ├── chats/chat-box.tsx
│       │   ├── chats/chat-activity-panel.tsx
│       │   ├── chat-ui/slash-command-dropdown.tsx
│       │   └── settings/
│       └── core/
│           ├── api/
│           ├── threads/
│           ├── memory/
│           ├── mcp/
│           ├── uploads/
│           ├── artifacts/
│           └── settings/
├── config.example.yaml
└── extensions_config.example.json
```

## 11. Copy Versus Simplify

Copy directly when building a similar app:

- Thread state pattern and reducers.
- Thread data path middleware.
- Sandbox provider interface.
- JSON-driven tool catalogs.
- LangGraph `useStream` frontend pattern.
- Memory queue and scope split.
- Settings-dialog section registry.
- Slash command parser/dropdown split.

Simplify first in a smaller project:

- Start with only `work_agent`; add `plan_agent` later.
- Start with local sandbox only.
- Start with no MCP, then add `extensions_config.json`.
- Start with memory JSON files only; add vector search and versioning later.
- Start with a single right panel for activity/files before adding nested resizable panels.
- Start with three slash commands: `/new`, `/compact`, `/memory`.

