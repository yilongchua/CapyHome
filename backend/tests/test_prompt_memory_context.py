from types import SimpleNamespace

from src.agents.lead_agent import prompt as prompt_module
from src.agents.lead_agent.prompt_cache import invalidate


def _memory_payload(summary: str) -> dict:
    return {
        "user": {
            "workContext": {
                "summary": summary,
            }
        },
        "history": {},
        "facts": [],
        "behaviorRules": [],
    }


def _patch_prompt_dependencies(monkeypatch, *, thread_id: str, memory_config: SimpleNamespace) -> list[tuple[str, str | None]]:
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr("src.config.get_app_config", lambda: SimpleNamespace(skills=SimpleNamespace(progressive_disclosure=False)))
    monkeypatch.setattr(prompt_module, "get_prompt_config", lambda: SimpleNamespace(componentized=True))
    monkeypatch.setattr(prompt_module, "get_skills_prompt_section", lambda _available_skills=None: "")
    monkeypatch.setattr(prompt_module, "get_agent_soul", lambda _agent_name=None: "<soul>static soul</soul>")
    monkeypatch.setattr("src.config.memory_config.get_memory_config", lambda: memory_config)
    monkeypatch.setattr("langgraph.config.get_config", lambda: {"configurable": {"thread_id": thread_id}})

    def fake_get_memory_data(_agent_name=None, *, scope="global", workspace_id=None):
        calls.append((scope, workspace_id))
        if scope == "workspace":
            return _memory_payload(f"workspace memory for {workspace_id}")
        return _memory_payload("global memory")

    monkeypatch.setattr("src.agents.memory.get_memory_data", fake_get_memory_data)
    return calls


def test_apply_prompt_template_injects_thread_scoped_memory_after_cache_hit(monkeypatch):
    invalidate()
    cfg = SimpleNamespace(
        enabled=True,
        injection_enabled=True,
        max_injection_tokens=2000,
        global_scope_enabled=True,
        workspace_scope_enabled=True,
        recall_top_k=5,
    )
    _patch_prompt_dependencies(monkeypatch, thread_id="thread-a", memory_config=cfg)
    first = prompt_module.apply_prompt_template()

    monkeypatch.setattr("langgraph.config.get_config", lambda: {"configurable": {"thread_id": "thread-b"}})
    second = prompt_module.apply_prompt_template()

    assert "workspace memory for thread-a" in first
    assert "workspace memory for thread-b" not in first
    assert "workspace memory for thread-b" in second
    assert "workspace memory for thread-a" not in second


def test_memory_context_honors_scope_flags(monkeypatch):
    invalidate()
    cfg = SimpleNamespace(
        enabled=True,
        injection_enabled=True,
        max_injection_tokens=2000,
        global_scope_enabled=False,
        workspace_scope_enabled=True,
        recall_top_k=5,
    )
    calls = _patch_prompt_dependencies(monkeypatch, thread_id="thread-scope", memory_config=cfg)

    rendered = prompt_module._get_memory_context()

    assert "workspace memory for thread-scope" in rendered
    assert "global memory" not in rendered
    assert calls == [("workspace", "thread-scope")]


def test_memory_context_empty_when_all_scopes_disabled(monkeypatch):
    invalidate()
    cfg = SimpleNamespace(
        enabled=True,
        injection_enabled=True,
        max_injection_tokens=2000,
        global_scope_enabled=False,
        workspace_scope_enabled=False,
        recall_top_k=5,
    )
    calls = _patch_prompt_dependencies(monkeypatch, thread_id="thread-disabled", memory_config=cfg)

    rendered = prompt_module._get_memory_context()

    assert rendered == ""
    assert calls == []
