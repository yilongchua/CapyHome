from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage

from src.agents.middlewares.todo_failure_retry_middleware import TodoFailureRetryMiddleware


def _runtime(mode: str = "work") -> SimpleNamespace:
    return SimpleNamespace(context={"mode": mode})


def test_injects_schema_recovery_prompt_once_for_validation_failure():
    mw = TodoFailureRetryMiddleware()
    state = {
        "todo_graph": {"nodes": [{"id": "todo-1", "status": "pending"}]},
        "messages": [AIMessage(content="[todo_update_validation_failed:validation_failed] bad payload")],
    }
    update = mw.after_model(state, _runtime())
    assert update is not None
    assert update["jump_to"] == "model"
    assert "strict schema" in update["messages"][0].content
    assert update["todo_schema_recovery_attempts"] == 1

    state2 = {
        "todo_graph": state["todo_graph"],
        "messages": [AIMessage(content="[todo_update_validation_failed:validation_failed] bad payload")],
        "todo_schema_recovery_attempts": 1,
    }
    update2 = mw.after_model(state2, _runtime())
    assert update2 is not None
    assert "reconcile invalid statuses/dependencies" in update2["messages"][0].content
