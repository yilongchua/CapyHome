from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from src.agents.middlewares.memory_middleware import MemoryMiddleware


def _runtime(add_to_memory):
    return SimpleNamespace(context={"thread_id": "thread-1", "add_to_memory": add_to_memory})


def _state():
    return {"messages": [HumanMessage(content="Find the address"), AIMessage(content='{"full_address": "1 Full Street"}')]}


def test_memory_middleware_skips_queue_when_add_to_memory_false(monkeypatch):
    queued: list[dict] = []

    class _Queue:
        def add(self, **kwargs):
            queued.append(kwargs)

    monkeypatch.setattr("src.agents.middlewares.memory_middleware.get_memory_config", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr("src.agents.middlewares.memory_middleware.get_memory_queue", lambda: _Queue())

    MemoryMiddleware().after_agent(_state(), _runtime(False))

    assert queued == []


def test_memory_middleware_queues_when_add_to_memory_true(monkeypatch):
    queued: list[dict] = []

    class _Queue:
        def add(self, **kwargs):
            queued.append(kwargs)

    monkeypatch.setattr("src.agents.middlewares.memory_middleware.get_memory_config", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr("src.agents.middlewares.memory_middleware.get_memory_queue", lambda: _Queue())

    MemoryMiddleware().after_agent(_state(), _runtime(True))

    assert len(queued) == 1
    assert queued[0]["thread_id"] == "thread-1"
