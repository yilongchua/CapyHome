"""Task tool for delegating work to subagents."""

import logging
import time
import uuid
from typing import Annotated

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.config import get_config, get_stream_writer
from langgraph.types import Command
from langgraph.typing import ContextT

from src.agents.activity_timeline import create_activity_event, stream_activity_event
from src.agents.execution_trace import (
    create_trace_event,
    extract_token_usage_from_message,
    make_summary_fallback,
    stream_trace_event,
)
from src.agents.middlewares.runtime_events import append_runtime_event
from src.agents.thread_state import ThreadState
from src.agents.work_agent.prompt import get_skills_prompt_section
from src.subagents import SubagentExecutor, get_subagent_config
from src.subagents.executor import SubagentStatus, cleanup_background_task, get_background_task_result
from src.subagents.registry import get_subagent_names, get_subagent_names_for_mode

logger = logging.getLogger(__name__)


def _normalize_plan_status(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if value in {"draft", "approved", "executing", "completed"}:
        return value
    return "draft"


def _parent_agent_name(runtime: ToolRuntime[ContextT, ThreadState] | None) -> str | None:
    if runtime is None:
        return None
    runtime_cfg = getattr(runtime, "config", None)
    configurable = runtime_cfg.get("configurable", {}) if isinstance(runtime_cfg, dict) else {}
    metadata = runtime_cfg.get("metadata", {}) if isinstance(runtime_cfg, dict) else {}
    context = getattr(runtime, "context", None) or {}
    for source in (configurable, metadata, context):
        if isinstance(source, dict):
            agent_name = source.get("agent_name")
            if isinstance(agent_name, str) and agent_name.strip() and agent_name != "default":
                return agent_name.strip()
    return None


def _parent_tool_groups(runtime: ToolRuntime[ContextT, ThreadState] | None) -> list[str] | None:
    agent_name = _parent_agent_name(runtime)
    if not agent_name:
        return None
    try:
        from src.config.agents_config import load_agent_config

        agent_config = load_agent_config(agent_name)
    except Exception:
        logger.exception("Failed to load parent agent config for subagent tool scoping: %s", agent_name)
        return []
    return list(agent_config.tool_groups or []) if agent_config and agent_config.tool_groups is not None else None


def _extract_reasoning_from_subagent_message(message: dict) -> str | None:
    additional_kwargs = message.get("additional_kwargs") or {}
    reasoning_content = additional_kwargs.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content.strip():
        return reasoning_content.strip()

    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                thinking = block.get("thinking")
                if isinstance(thinking, str) and thinking.strip():
                    return thinking.strip()
    return None


def _extract_token_usage_from_subagent_message(message: dict) -> dict[str, int] | None:
    class _Shim:
        def __init__(self, msg: dict):
            self.response_metadata = msg.get("response_metadata") or {}
            self.usage_metadata = msg.get("usage_metadata")

    usage = extract_token_usage_from_message(_Shim(message))
    if usage:
        return usage
    return None


def _summarize_subagent_activity(message: dict) -> str | None:
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_name = tool_call.get("name")
            args = tool_call.get("args")
            if isinstance(tool_name, str) and tool_name.strip():
                if isinstance(args, dict):
                    for key in ("query", "command", "prompt", "description", "path", "url"):
                        value = args.get(key)
                        if isinstance(value, str) and value.strip():
                            return f"{tool_name}: {value.strip()[:180]}"
                return f"{tool_name}"

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()[:180]
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str) and block.strip():
                text_parts.append(block.strip())
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())
        if text_parts:
            return " ".join(text_parts)[:180]
    return None


def _resolve_task_mode(runtime: object) -> str:
    """Resolve the canonical runtime mode ('work' or 'plan') for `task` gating.

    Reads the `configurable` dict from the tool runtime, falling back to the
    LangGraph runnable config. Defaults to 'work' when nothing is available
    (e.g. direct/unit invocation).
    """
    from src.agents.common.mode import resolve_current_mode

    configurable: dict = {}
    runtime_cfg = getattr(runtime, "config", None) if runtime is not None else None
    if isinstance(runtime_cfg, dict):
        configurable = runtime_cfg.get("configurable", {}) or {}
    if not configurable:
        try:
            from langgraph.config import get_config

            configurable = (get_config() or {}).get("configurable", {}) or {}
        except Exception:
            configurable = {}
    try:
        return resolve_current_mode(configurable)
    except Exception:
        return "work"


def _normalize_subagent_label(value: str) -> str:
    label = value.strip()
    if not label:
        return "task"
    return label


def _normalize_description(description: str | None) -> str:
    value = str(description or "").strip()
    return value or "delegated task"


def _build_group_title(subagent_type: str, description: str) -> str:
    return f"{_normalize_subagent_label(subagent_type)}: {description}"


def _research_report_path(task_id: str) -> str:
    return f"/mnt/user-data/workspace/research/{task_id}.md"


def _research_prompt(prompt: str, report_path: str) -> str:
    return (
        f"{prompt.rstrip()}\n\n"
        "<research_report_contract>\n"
        f"Write the completed research report to `{report_path}` using `write_file`.\n"
        "Use `str_replace` only to refine that same report after it has been created.\n"
        "Your final response must briefly state the status, report path, major findings, source count, and remaining uncertainty.\n"
        "</research_report_contract>"
    )


def _terminal_error_command(
    *,
    tool_call_id: str,
    task_id: str,
    subagent_type: str,
    terminal_status: str,
    error_type: str,
    error: str,
) -> Command:
    artifact = {
        "task_id": task_id,
        "terminal_status": terminal_status,
        "subagent_type": subagent_type,
        "error_type": error_type,
        "error": error,
    }
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"Task {terminal_status}. Error: {error}",
                    tool_call_id=tool_call_id,
                    name="task",
                    status="error",
                    artifact=artifact,
                )
            ]
        }
    )


@tool("task", parse_docstring=True)
def task_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> str | Command:
    """Delegate a task to a specialized subagent that runs in its own context.

    Subagents help you:
    - Preserve context by keeping exploration and implementation separate
    - Handle complex multi-step tasks autonomously
    - Execute commands or operations in isolated contexts

    Available subagent types:
    - **general-purpose**: A capable agent for complex, multi-step tasks that require
      both exploration and action. Use when the task requires complex reasoning,
      multiple dependent steps, or would benefit from isolated context.
    - **bash**: Command execution specialist for running bash commands. Use for
      git operations, build processes, or when command output would be verbose.
    - **knowledge-researcher**: Researches one coherent web/knowledge-vault topic,
      writes a Markdown report, and returns the report path.
    - **docs-explorer**: Local corpus explorer for uploaded or mounted documents
      mirrored into `/mnt/user-data/workspace/.docs`.
    - **comparison-dimension-researcher**: Researches one comparison dimension
      across a fixed set of options.
    - **synthesis-reviewer**: Reviews collected findings or drafts for coverage,
      contradictions, citations, and freshness.

    When to use this tool:
    - Complex tasks requiring multiple steps or tools
    - Tasks that produce verbose output
    - When you want to isolate context from the main conversation
    - Parallel research or exploration tasks

    When NOT to use this tool:
    - Simple, single-step operations (use tools directly)
    - Tasks requiring user interaction or clarification

    Args:
        description: A short (3-5 word) description of the task for logging/display. ALWAYS PROVIDE THIS PARAMETER FIRST.
        prompt: The task description for the subagent. Be specific and clear about what needs to be done. ALWAYS PROVIDE THIS PARAMETER SECOND.
        subagent_type: The type of subagent to use. ALWAYS PROVIDE THIS PARAMETER THIRD.
    """
    # Get subagent configuration
    config = get_subagent_config(subagent_type)
    if config is None:
        available = ", ".join(get_subagent_names())
        return f"Error: Unknown subagent type '{subagent_type}'. Available: {available}"

    # Mode gating: a subagent may only be spawned in a runtime mode listed in its
    # `modes`. Plan Mode exposes only planning helpers (scope-researcher,
    # finder-agent); Work/Auto exposes the execution subagents.
    current_mode = _resolve_task_mode(runtime)
    if current_mode not in config.modes:
        available = ", ".join(sorted(get_subagent_names_for_mode(current_mode)))
        return (
            f"Error: subagent '{subagent_type}' is not available in {current_mode} mode "
            f"(its allowed modes: {', '.join(config.modes)}). "
            f"Available in {current_mode} mode: {available or '(none)'}."
        )

    # Draft-plan gate: execution subagents are gated until the plan is approved.
    # Planning subagents (modes include 'plan') are exempt — they run *during*
    # drafting, which is the whole point of the plan-mode finder tier.
    plan_state = runtime.state.get("plan") if runtime and runtime.state else None
    if isinstance(plan_state, dict) and "plan" not in config.modes:
        plan_status = _normalize_plan_status(plan_state.get("status"))
        if plan_status == "draft":
            return (
                "Task execution is gated because the current plan is still in draft state. "
                "Use the explicit execute-plan action first, then retry."
            )

    skills_section = get_skills_prompt_section()
    if skills_section:
        config = config.model_copy(update={"system_prompt": config.system_prompt + "\n\n" + skills_section})

    normalized_description = _normalize_description(description)
    normalized_subagent_type = _normalize_subagent_label(subagent_type)
    report_path = _research_report_path(str(tool_call_id)) if normalized_subagent_type == "knowledge-researcher" else None
    if report_path:
        prompt = _research_prompt(prompt, report_path)

    group_title = _build_group_title(normalized_subagent_type, normalized_description)
    dispatch_event = create_activity_event(
        runtime,
        actor="baby_capy",
        kind="subagent_dispatch_started",
        line=f"Baby Capy - {normalized_subagent_type} is dispatching {normalized_description}...",
        task_id=str(tool_call_id),
        group_id=str(tool_call_id),
        group_kind="subagent_task",
        group_title=group_title,
        group_role="step",
        subagent_type=normalized_subagent_type,
        description=normalized_description,
        payload={
            "source": "task_tool",
            "event": "subagent_dispatch_started",
            "tool_call_id": str(tool_call_id),
            "task_id": str(tool_call_id),
            "description": normalized_description,
            "subagent_type": normalized_subagent_type,
            "group_id": str(tool_call_id),
            "group_kind": "subagent_task",
            "group_title": group_title,
        },
    )
    stream_activity_event(dispatch_event)
    append_runtime_event(runtime, {**dispatch_event["payload"], "activity_already_streamed": True})

    # Extract parent context from runtime
    sandbox_state = None
    thread_data = None
    thread_id = None
    parent_model = None
    trace_id = None

    if runtime is not None:
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")
        thread_id = (getattr(runtime, "context", None) or {}).get("thread_id")

        # Prefer runtime-carried metadata (available in tool runtime), then
        # fall back to langgraph get_config() when running inside a runnable
        # context. This keeps direct/unit invocation paths working.
        runtime_cfg = getattr(runtime, "config", None)
        metadata = runtime_cfg.get("metadata", {}) if isinstance(runtime_cfg, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        if not metadata:
            try:
                metadata = get_config().get("metadata", {})
            except RuntimeError:
                metadata = {}
        parent_model = metadata.get("model_name")

        # Get or generate trace_id for distributed tracing
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]

    # Get available tools (excluding task tool to prevent nesting)
    # Lazy import to avoid circular dependency
    from src.tools import get_available_tools

    # Subagents inherit the parent custom agent's tool-group boundary, but never
    # receive `task` to prevent recursive delegation.
    tools = get_available_tools(
        model_name=parent_model,
        groups=_parent_tool_groups(runtime),
        subagent_enabled=False,
    )

    # Create executor
    executor = SubagentExecutor(
        config=config,
        tools=tools,
        parent_model=parent_model,
        sandbox_state=sandbox_state,
        thread_data=thread_data,
        thread_id=thread_id,
        trace_id=trace_id,
    )

    # Start background execution (always async to prevent blocking)
    # Use tool_call_id as task_id for better traceability
    task_id = executor.execute_async(prompt, task_id=tool_call_id)

    # Poll for task completion in backend (removes need for LLM to poll)
    poll_count = 0
    last_status = None
    last_message_count = 0  # Track how many AI messages we've already sent
    # Polling timeout: execution timeout + 60s buffer, checked every 5s
    max_poll_count = (config.timeout_seconds + 60) // 5

    logger.info(f"[trace={trace_id}] Started background task {task_id} (subagent={subagent_type}, timeout={config.timeout_seconds}s, polling_limit={max_poll_count} polls)")

    writer = get_stream_writer()
    assistant_message_id = None
    for msg in reversed(runtime.state.get("messages", []) if runtime and runtime.state else []):
        if getattr(msg, "type", None) == "ai":
            assistant_message_id = getattr(msg, "id", None)
            break

    started_trace = create_trace_event(
        runtime,
        stage="subagent",
        event_type="task_started",
        status="running",
        payload={
            "description": normalized_description,
            "subagent_type": normalized_subagent_type,
            "max_turns": config.max_turns,
            "group_id": str(task_id),
            "group_kind": "subagent_task",
            "group_title": group_title,
            "report_path": report_path,
        },
        thinking={
            "source": "summary",
            "content": make_summary_fallback(
                event_type="task_started",
                payload={"description": normalized_description, "subagent_type": subagent_type},
            ),
        },
        turn_id=str(tool_call_id),
        assistant_message_id=str(assistant_message_id) if assistant_message_id is not None else None,
        task_id=str(task_id),
    )
    # Send task-started event and a first-class trace event for real-time UI.
    writer(
        {
            "type": "task_started",
            "task_id": task_id,
            "description": normalized_description,
            "subagent_type": normalized_subagent_type,
            "report_path": report_path,
            "group_id": str(task_id),
            "group_kind": "subagent_task",
            "group_title": group_title,
            "trace": started_trace,
        }
    )
    stream_trace_event(started_trace)
    append_runtime_event(
        runtime,
        {
            "source": "task_tool",
            "event": "task_started",
            "status": "running",
            "task_id": str(task_id),
            "turn_id": str(tool_call_id),
            "assistant_message_id": str(assistant_message_id) if assistant_message_id is not None else None,
            "description": normalized_description,
            "original_description": description,
            "subagent_type": normalized_subagent_type,
            "report_path": report_path,
            "group_id": str(task_id),
            "group_kind": "subagent_task",
            "group_title": group_title,
            "trace_event": started_trace,
            "trace_already_streamed": True,
        },
    )

    while True:
        result = get_background_task_result(task_id)

        if result is None:
            logger.error(f"[trace={trace_id}] Task {task_id} not found in background tasks")
            error = "Task disappeared from background tasks"
            writer({"type": "task_failed", "task_id": task_id, "error": error})
            failed_trace = create_trace_event(
                runtime,
                stage="subagent",
                event_type="task_failed",
                status="failed",
                payload={
                    "error": error,
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                },
                thinking={"source": "summary", "content": make_summary_fallback(event_type="task_failed", payload={"subagent_type": subagent_type})},
                turn_id=str(tool_call_id),
                assistant_message_id=str(assistant_message_id) if assistant_message_id is not None else None,
                task_id=str(task_id),
            )
            stream_trace_event(failed_trace)
            append_runtime_event(
                runtime,
                {
                    "source": "task_tool",
                    "event": "task_failed",
                    "status": "failed",
                    "task_id": str(task_id),
                    "turn_id": str(tool_call_id),
                    "assistant_message_id": str(assistant_message_id) if assistant_message_id is not None else None,
                    "error": error,
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                    "trace_event": failed_trace,
                    "trace_already_streamed": True,
                },
            )
            cleanup_background_task(task_id)
            return _terminal_error_command(
                tool_call_id=str(tool_call_id),
                task_id=str(task_id),
                subagent_type=normalized_subagent_type,
                terminal_status="failed",
                error_type="task_disappeared",
                error=error,
            )

        # Log status changes for debugging
        if result.status != last_status:
            logger.info(f"[trace={trace_id}] Task {task_id} status: {result.status.value}")
            last_status = result.status

        # Check for new AI messages and send task_running events
        current_message_count = len(result.ai_messages)
        if current_message_count > last_message_count:
            # Send task_running event for each new message
            for i in range(last_message_count, current_message_count):
                message = result.ai_messages[i]
                reasoning = _extract_reasoning_from_subagent_message(message)
                token_usage = _extract_token_usage_from_subagent_message(message)
                tool_summary = _summarize_subagent_activity(message)
                thinking = (
                    {"source": "raw", "content": reasoning}
                    if reasoning
                    else {
                        "source": "summary",
                        "content": make_summary_fallback(
                            event_type="task_running",
                            payload={"message_index": i + 1, "subagent_type": subagent_type},
                        ),
                    }
                )
                running_trace = create_trace_event(
                    runtime,
                    stage="subagent",
                    event_type="task_running",
                    status="running",
                    payload={
                        "message_index": i + 1,
                        "total_messages": current_message_count,
                        "subagent_type": normalized_subagent_type,
                        "description": normalized_description,
                        "group_id": str(task_id),
                        "group_kind": "subagent_task",
                        "group_title": group_title,
                    },
                    token_usage=token_usage,
                    thinking=thinking,
                    turn_id=str(tool_call_id),
                    assistant_message_id=str(assistant_message_id) if assistant_message_id is not None else None,
                    task_id=str(task_id),
                )
                writer(
                    {
                        "type": "task_running",
                        "task_id": task_id,
                        "message": message,
                        "message_index": i + 1,  # 1-based index for display
                        "total_messages": current_message_count,
                        "group_id": str(task_id),
                        "group_kind": "subagent_task",
                        "group_title": group_title,
                        "subagent_type": normalized_subagent_type,
                        "description": normalized_description,
                        "tool_summary": tool_summary,
                        "trace": running_trace,
                    }
                )
                stream_trace_event(running_trace)
                append_runtime_event(
                    runtime,
                    {
                        "source": "task_tool",
                        "event": "task_running",
                        "status": "running",
                        "task_id": str(task_id),
                        "turn_id": str(tool_call_id),
                        "assistant_message_id": str(assistant_message_id) if assistant_message_id is not None else None,
                        "message_index": i + 1,
                        "total_messages": current_message_count,
                        "subagent_type": normalized_subagent_type,
                        "description": normalized_description,
                        "group_id": str(task_id),
                        "group_kind": "subagent_task",
                        "group_title": group_title,
                        "tool_summary": tool_summary,
                        "trace_event": running_trace,
                        "trace_already_streamed": True,
                    },
                )
                logger.info(f"[trace={trace_id}] Task {task_id} sent message #{i + 1}/{current_message_count}")
            last_message_count = current_message_count

        # Check if task completed, failed, or timed out
        if result.status == SubagentStatus.COMPLETED:
            completed_trace = create_trace_event(
                runtime,
                stage="subagent",
                event_type="task_completed",
                status="completed",
                payload={
                    "result_preview": str(result.result or "")[:400],
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                },
                thinking={
                    "source": "summary",
                    "content": make_summary_fallback(
                        event_type="task_completed",
                        payload={"subagent_type": subagent_type},
                    ),
                },
                turn_id=str(tool_call_id),
                assistant_message_id=str(assistant_message_id) if assistant_message_id is not None else None,
                task_id=str(task_id),
            )
            writer(
                {
                    "type": "task_completed",
                    "task_id": task_id,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "result": result.result,
                    "trace": completed_trace,
                }
            )
            stream_trace_event(completed_trace)
            append_runtime_event(
                runtime,
                {
                    "source": "task_tool",
                    "event": "task_completed",
                    "status": "completed",
                    "task_id": str(task_id),
                    "turn_id": str(tool_call_id),
                    "assistant_message_id": str(assistant_message_id) if assistant_message_id is not None else None,
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                    "trace_event": completed_trace,
                    "trace_already_streamed": True,
                },
            )
            logger.info(f"[trace={trace_id}] Task {task_id} completed after {poll_count} polls")
            cleanup_background_task(task_id)
            report_notice = f" Report: {report_path}." if report_path else ""
            return f"Task Succeeded.{report_notice} Result: {result.result}"
        elif result.status == SubagentStatus.FAILED:
            failed_trace = create_trace_event(
                runtime,
                stage="subagent",
                event_type="task_failed",
                status="failed",
                payload={
                    "error": str(result.error or "")[:400],
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                },
                thinking={
                    "source": "summary",
                    "content": make_summary_fallback(
                        event_type="task_failed",
                        payload={"subagent_type": subagent_type},
                    ),
                },
                turn_id=str(tool_call_id),
                assistant_message_id=str(assistant_message_id) if assistant_message_id is not None else None,
                task_id=str(task_id),
            )
            writer(
                {
                    "type": "task_failed",
                    "task_id": task_id,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "error": result.error,
                    "trace": failed_trace,
                }
            )
            stream_trace_event(failed_trace)
            append_runtime_event(
                runtime,
                {
                    "source": "task_tool",
                    "event": "task_failed",
                    "status": "failed",
                    "task_id": str(task_id),
                    "turn_id": str(tool_call_id),
                    "assistant_message_id": str(assistant_message_id) if assistant_message_id is not None else None,
                    "error": str(result.error or "")[:400],
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                    "trace_event": failed_trace,
                    "trace_already_streamed": True,
                },
            )
            logger.error(f"[trace={trace_id}] Task {task_id} failed: {result.error}")
            cleanup_background_task(task_id)
            return _terminal_error_command(
                tool_call_id=str(tool_call_id),
                task_id=str(task_id),
                subagent_type=normalized_subagent_type,
                terminal_status="failed",
                error_type="subagent_failed",
                error=str(result.error or "Unknown subagent failure"),
            )
        elif result.status == SubagentStatus.TIMED_OUT:
            timed_out_trace = create_trace_event(
                runtime,
                stage="subagent",
                event_type="task_timed_out",
                status="failed",
                payload={
                    "error": str(result.error or "")[:400],
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                },
                thinking={
                    "source": "summary",
                    "content": make_summary_fallback(
                        event_type="task_timed_out",
                        payload={"subagent_type": subagent_type},
                    ),
                },
                turn_id=str(tool_call_id),
                assistant_message_id=str(assistant_message_id) if assistant_message_id is not None else None,
                task_id=str(task_id),
            )
            writer(
                {
                    "type": "task_timed_out",
                    "task_id": task_id,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "error": result.error,
                    "trace": timed_out_trace,
                }
            )
            stream_trace_event(timed_out_trace)
            append_runtime_event(
                runtime,
                {
                    "source": "task_tool",
                    "event": "task_timed_out",
                    "status": "failed",
                    "task_id": str(task_id),
                    "turn_id": str(tool_call_id),
                    "assistant_message_id": str(assistant_message_id) if assistant_message_id is not None else None,
                    "error": str(result.error or "")[:400],
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                    "trace_event": timed_out_trace,
                    "trace_already_streamed": True,
                },
            )
            logger.warning(f"[trace={trace_id}] Task {task_id} timed out: {result.error}")
            cleanup_background_task(task_id)
            return _terminal_error_command(
                tool_call_id=str(tool_call_id),
                task_id=str(task_id),
                subagent_type=normalized_subagent_type,
                terminal_status="timed_out",
                error_type="execution_timeout",
                error=str(result.error or "Task execution timed out"),
            )

        # Still running, wait before next poll
        time.sleep(5)  # Poll every 5 seconds
        poll_count += 1

        # Polling timeout as a safety net (in case thread pool timeout doesn't work)
        # Set to execution timeout + 60s buffer, in 5s poll intervals
        # This catches edge cases where the background task gets stuck
        # Note: We don't call cleanup_background_task here because the task may
        # still be running in the background. The cleanup will happen when the
        # executor completes and sets a terminal status.
        if poll_count > max_poll_count:
            timeout_minutes = config.timeout_seconds // 60
            logger.error(f"[trace={trace_id}] Task {task_id} polling timed out after {poll_count} polls (should have been caught by thread pool timeout)")
            timeout_trace = create_trace_event(
                runtime,
                stage="subagent",
                event_type="task_timed_out",
                status="failed",
                payload={
                    "error": "Task polling timed out",
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                },
                thinking={
                    "source": "summary",
                    "content": make_summary_fallback(
                        event_type="task_timed_out",
                        payload={"subagent_type": subagent_type},
                    ),
                },
                turn_id=str(tool_call_id),
                assistant_message_id=str(assistant_message_id) if assistant_message_id is not None else None,
                task_id=str(task_id),
            )
            writer(
                {
                    "type": "task_timed_out",
                    "task_id": task_id,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "trace": timeout_trace,
                }
            )
            stream_trace_event(timeout_trace)
            append_runtime_event(
                runtime,
                {
                    "source": "task_tool",
                    "event": "task_timed_out",
                    "status": "failed",
                    "task_id": str(task_id),
                    "turn_id": str(tool_call_id),
                    "assistant_message_id": str(assistant_message_id) if assistant_message_id is not None else None,
                    "error": "Task polling timed out",
                    "subagent_type": normalized_subagent_type,
                    "description": normalized_description,
                    "group_id": str(task_id),
                    "group_kind": "subagent_task",
                    "group_title": group_title,
                    "trace_event": timeout_trace,
                    "trace_already_streamed": True,
                },
            )
            return _terminal_error_command(
                tool_call_id=str(tool_call_id),
                task_id=str(task_id),
                subagent_type=normalized_subagent_type,
                terminal_status="timed_out",
                error_type="polling_timeout",
                error=(
                    f"Task polling timed out after {timeout_minutes} minutes. "
                    f"This may indicate the background task is stuck. Status: {result.status.value}"
                ),
            )
