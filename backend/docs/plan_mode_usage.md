# Plan Mode with TodoList Middleware

This document describes how to enable and use the Plan Mode feature with TodoList middleware in CapyHome 2.0.

## Overview

Plan Mode adds a TodoList middleware to the agent, which provides a `write_todos` tool that helps the agent:
- Break down complex tasks into smaller, manageable steps
- Track progress as work progresses
- Provide visibility to users about what's being done

The TodoList middleware is built on LangChain's `TodoListMiddleware`.

## Configuration

### Enabling Plan Mode

Plan Mode is its own LangGraph graph, `plan_agent` (`make_plan_agent`), registered alongside `work_agent` (`make_work_agent`) in `langgraph.json`. In the UI, entry is **user-initiated** via the manual toggle (Shift+Tab) — Work Mode never auto-escalates into Plan Mode. The selected mode travels in `config.configurable.current_mode` (`"work"` | `"plan"`); the legacy `is_plan_mode` boolean and `mode` string are dual-written for back-compat.

```python
from langchain_core.runnables import RunnableConfig
from src.agents.plan_agent.agent import make_plan_agent

# Plan Mode = the plan_agent graph. make_plan_agent forces current_mode="plan".
config = RunnableConfig(
    configurable={
        "thread_id": "example-thread",
        "thinking_enabled": True,
    }
)

agent = make_plan_agent(config)
```

For Work Mode, call `make_work_agent(config)` instead (the default graph).

### Configuration Options

- **current_mode** (`"work"` | `"plan"`): canonical mode field, resolved by `resolve_current_mode()` in `src/agents/common/mode.py`. `make_plan_agent` forces `"plan"`; `make_work_agent` defaults to `"work"`.
  - Legacy `is_plan_mode` (bool) and `mode` (str) are still accepted and dual-written during the transition.
  - Mode is settled **up-front** at agent build time (it selects the tool catalog and which graph runs), not flipped mid-run.

## Default Behavior

When plan mode is enabled with default settings, the agent will have access to a `write_todos` tool with the following behavior:

### When to Use TodoList

The agent will use the todo list for:
1. Complex multi-step tasks (3+ distinct steps)
2. Non-trivial tasks requiring careful planning
3. When user explicitly requests a todo list
4. When user provides multiple tasks

### When NOT to Use TodoList

The agent will skip using the todo list for:
1. Single, straightforward tasks
2. Trivial tasks (< 3 steps)
3. Purely conversational or informational requests

### Task States

- **pending**: Task not yet started
- **in_progress**: Currently working on (can have multiple parallel tasks)
- **completed**: Task finished successfully

## Usage Examples

### Basic Usage

```python
from langchain_core.runnables import RunnableConfig
from src.agents.plan_agent.agent import make_plan_agent
from src.agents.work_agent.agent import make_work_agent

base = {"thread_id": "example-thread", "thinking_enabled": True}

# Plan Mode: the plan_agent graph (plan-mode middlewares + planning tool catalog)
plan_agent = make_plan_agent(RunnableConfig(configurable={**base}))

# Work Mode: the default work_agent graph (full execution catalog)
work_agent = make_work_agent(RunnableConfig(configurable={**base, "thread_id": "another-thread"}))
```

### Selecting the graph per request

In the running app the frontend picks the graph (Shift+Tab toggles Plan/Work). Programmatically, choose the factory that matches the mode you want:

```python
from langchain_core.runnables import RunnableConfig
from src.agents.plan_agent.agent import make_plan_agent
from src.agents.work_agent.agent import make_work_agent

def create_agent_for_task(plan_first: bool):
    """Plan Mode for tasks the user wants to scope first; Work Mode otherwise."""
    config = RunnableConfig(configurable={"thread_id": "task", "thinking_enabled": True})
    return make_plan_agent(config) if plan_first else make_work_agent(config)
```

## How It Works

1. `make_plan_agent(config)` forces `current_mode="plan"` (plus legacy `is_plan_mode=True` / `mode="plan"`) and delegates to the shared `_build_work_agent(config, prompt_template_fn=plan_apply_prompt_template)` builder in `work_agent/agent.py`.
2. `get_available_tools(mode="plan")` selects the planning tool catalog (`internal_tools_plan.json`) up-front — read-only/planning tools, no `bash`/`write_file`/`task`.
3. The shared middleware registry activates the plan-mode middlewares when `is_plan_mode=True`: `PlannerMiddleware`, `PlanEvaluatorMiddleware`, `PlanExecutionGateMiddleware`, `PlanFileSyncMiddleware`, `TodoDagMiddleware` (the latter provides `write_todos`).
4. The plan-mode agent produces a canonical `plan.md` (`serialize_plan_md`, `plan_version: 5`) in the workspace and terminates.
5. On handoff, the `work_agent` graph parses `plan.md` (picking up any manual user edits) and executes the todo graph, delegating to subagents via `task`.

## Architecture

```
make_plan_agent(config)                make_work_agent(config)
   │  forces current_mode="plan"          │  default current_mode="work"
   │  prompt = plan overlay               │  prompt = work prompt
   └──────────────┬───────────────────────┘
                  ▼
        _build_work_agent(config, prompt_template_fn=…)
                  │
                  ├─> get_available_tools(mode=…) selects per-mode catalog
                  │     plan → internal_tools_plan.json (no execution tools)
                  │     work → internal_tools_work.json (full execution surface)
                  │
                  └─> shared middleware registry
                        ├─ ThreadDataMiddleware / SandboxMiddleware (plumbing)
                        ├─ Plan-mode middlewares (when is_plan_mode=True):
                        │    PlannerMiddleware, PlanEvaluatorMiddleware,
                        │    PlanExecutionGateMiddleware, PlanFileSyncMiddleware,
                        │    TodoDagMiddleware (write_todos)
                        ├─ Work-mode middlewares (WorkModeMiddleware, …)
                        └─ ClarificationMiddleware (last)
```

## Implementation Details

### Agent Modules
- **Plan Mode**: `src/agents/plan_agent/agent.py` — `make_plan_agent(config)`, a thin wrapper that forces plan mode and injects the plan-mode prompt.
- **Work Mode / shared builder**: `src/agents/work_agent/agent.py` — `make_work_agent(config)` and the internal `_build_work_agent(...)` that both graphs share; `_build_middlewares(config)` builds the mode-aware middleware chain.
- **Mode resolution**: `src/agents/common/mode.py` — `resolve_current_mode(cfg)`.

### Runtime Configuration
Plan mode is controlled via the `is_plan_mode` parameter in `RunnableConfig.configurable`:
```python
config = RunnableConfig(
    configurable={
        "is_plan_mode": True,  # Enable plan mode
        # ... other configurable options
    }
)
```

## Key Benefits

1. **Dynamic Control**: Enable/disable plan mode per request without global state
2. **Flexibility**: Different conversations can have different plan mode settings
3. **Simplicity**: No need for global configuration management
4. **Context-Aware**: Plan mode decision can be based on task complexity, user preferences, etc.

## Custom Prompts

CapyHome uses custom `system_prompt` and `tool_description` for the TodoListMiddleware that match the overall CapyHome prompt style:

### System Prompt Features
- Uses XML tags (`<todo_list_system>`) for structure consistency with CapyHome's main prompt
- Emphasizes CRITICAL rules and best practices
- Clear "When to Use" vs "When NOT to Use" guidelines
- Focuses on real-time updates and immediate task completion

### Tool Description Features
- Detailed usage scenarios with examples
- Strong emphasis on NOT using for simple tasks
- Clear task state definitions (pending, in_progress, completed)
- Comprehensive best practices section
- Task completion requirements to prevent premature marking

The plan-mode prompt overlay lives in `src/agents/plan_agent/prompt.py` (`PLAN_MODE_SECTION`); the todo middleware and its prompts are wired in `src/agents/work_agent/agent.py` and `src/agents/work_agent/todo_prompts.py`.

## Notes

- TodoList middleware uses LangChain's built-in `TodoListMiddleware` with **custom CapyHome-style prompts**
- Plan mode is **disabled by default** (`is_plan_mode=False`) to maintain backward compatibility
- The middleware is positioned before `ClarificationMiddleware` to allow todo management during clarification flows
- Custom prompts emphasize the same principles as CapyHome's main system prompt (clarity, action-oriented, critical rules)
