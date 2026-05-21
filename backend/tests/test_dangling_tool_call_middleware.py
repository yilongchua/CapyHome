from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from src.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware
from src.agents.middlewares.runtime_events import RUNTIME_EVENTS_KEY


def test_emits_runtime_event_for_dangling_write_todos():
    mw = DanglingToolCallMiddleware()
    runtime = SimpleNamespace(context={})
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "write_todos", "args": {"todos": [{"id": "todo-1", "status": "completed"}]}}],
        )
    ]
    request = SimpleNamespace(runtime=runtime)
    patched = mw._build_patched_messages(messages, request)  # noqa: SLF001
    assert patched is not None
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "tc-1" for m in patched)
    events = runtime.context.get(RUNTIME_EVENTS_KEY, [])
    assert any(isinstance(evt, dict) and evt.get("event") == "todo_update_dangling" for evt in events)
