from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from src.agents.middlewares.autoresearch_middleware import AutoresearchMiddleware


class _Service:
    def __init__(self) -> None:
        self.activity: list[tuple[str | None, str]] = []

    def record_workspace_activity(self, *, thread_id, message):
        self.activity.append((thread_id, message))

    def list_templates(self):
        return [SimpleNamespace(id="knowledge-vault-autoresearch-loop")]

    def start_autoresearch_objective(self, **_kwargs):
        return {
            "scheduled_time": "09:00",
            "objective": SimpleNamespace(objective_id="objective-1", scheduler_job_id="job-1"),
            "bootstrap_run": SimpleNamespace(id="run-1"),
        }


def test_autoresearch_trigger_state_is_runtime_scoped(monkeypatch):
    service = _Service()
    monkeypatch.setattr(
        "src.agents.middlewares.autoresearch_middleware.get_control_plane_service",
        lambda: service,
    )
    middleware = AutoresearchMiddleware()
    runtime_a = SimpleNamespace(context={"thread_id": "thread-a"})
    runtime_b = SimpleNamespace(context={"thread_id": "thread-b"})

    request_a = SimpleNamespace(
        messages=[HumanMessage(content="autoresearch - battery recycling")],
        runtime=runtime_a,
    )
    request_b = SimpleNamespace(
        messages=[HumanMessage(content="normal workspace activity")],
        runtime=runtime_b,
    )

    middleware.wrap_model_call(request_a, lambda _request: None)
    middleware.wrap_model_call(request_b, lambda _request: SimpleNamespace(result=[AIMessage(content="ok")]))

    middleware.after_agent({"messages": request_a.messages}, runtime_a)
    middleware.after_agent({"messages": request_b.messages}, runtime_b)

    assert service.activity == [
        ("thread-a", "autoresearch - battery recycling"),
        ("thread-b", "normal workspace activity"),
    ]
