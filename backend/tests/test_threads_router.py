"""Tests for thread deletion gateway routes."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

from src.config.paths import Paths
from src.gateway.routers import threads


class _ThreadsClient:
    def __init__(self, existing_thread_ids: set[str] | None = None, failing_thread_ids: set[str] | None = None, values: dict | None = None):
        self.deleted: list[str] = []
        self.existing_thread_ids = existing_thread_ids or set()
        self.failing_thread_ids = failing_thread_ids or set()
        self.values = values or {}
        self.updated: list[dict] = []

    async def delete(self, thread_id: str):
        if thread_id in self.failing_thread_ids:
            raise RuntimeError(f"boom:{thread_id}")
        if thread_id not in self.existing_thread_ids:
            raise _NotFoundError("missing")
        self.deleted.append(thread_id)
        self.existing_thread_ids.remove(thread_id)

    async def search(self, *, limit: int, offset: int):  # noqa: ARG002
        items = sorted(self.existing_thread_ids)
        page = items[offset : offset + limit]
        return [{"thread_id": thread_id} for thread_id in page]

    async def get_state(self, thread_id: str):  # noqa: ARG002
        return {"values": self.values}

    async def update_state(self, thread_id: str, values: dict):  # noqa: ARG002
        self.updated.append(values)
        self.values.update(values)
        return {"values": self.values}


class _Client:
    def __init__(self, thread_client: _ThreadsClient):
        self.threads = thread_client


class _NotFoundError(Exception):
    status_code = 404


@pytest.fixture()
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    paths = Paths(tmp_path)
    monkeypatch.setattr(threads, "get_paths", lambda: paths)
    return paths


def test_delete_thread_removes_langgraph_history_and_local_files(paths: Paths, monkeypatch: pytest.MonkeyPatch):
    thread_id = "thread-1"
    thread_dir = paths.thread_dir(thread_id)
    (thread_dir / "user-data" / "workspace").mkdir(parents=True)
    (thread_dir / "user-data" / "workspace" / "plan.md").write_text("test", encoding="utf-8")

    client = _ThreadsClient(existing_thread_ids={thread_id})
    monkeypatch.setattr("langgraph_sdk.get_client", lambda url: _Client(client))

    response = asyncio.run(threads.delete_thread(thread_id))

    assert response.thread_id == thread_id
    assert response.deleted is True
    assert response.files_deleted is True
    assert client.deleted == [thread_id]
    assert not thread_dir.exists()


def test_delete_thread_is_idempotent_when_langgraph_or_files_are_missing(paths: Paths, monkeypatch: pytest.MonkeyPatch):
    thread_id = "thread-missing"
    client = _ThreadsClient(existing_thread_ids=set())
    monkeypatch.setattr("langgraph_sdk.get_client", lambda url: _Client(client))

    response = asyncio.run(threads.delete_thread(thread_id))

    assert response.deleted is False
    assert response.files_deleted is False


def test_delete_thread_handles_legacy_prefixed_thread_id(paths: Paths, monkeypatch: pytest.MonkeyPatch):
    canonical_thread_id = "6317b60a-75a8-4ba8-9537-712a388d850b"
    prefixed_thread_id = f"chats/{canonical_thread_id}"
    thread_dir = paths.thread_dir(canonical_thread_id)
    (thread_dir / "user-data" / "workspace").mkdir(parents=True)

    client = _ThreadsClient(existing_thread_ids={canonical_thread_id})
    monkeypatch.setattr("langgraph_sdk.get_client", lambda url: _Client(client))

    response = asyncio.run(threads.delete_thread(prefixed_thread_id))

    assert response.thread_id == prefixed_thread_id
    assert response.deleted is True
    assert response.files_deleted is True
    assert client.deleted == [canonical_thread_id]
    assert not thread_dir.exists()


def test_delete_all_threads_deletes_each_thread_and_reports_failures(paths: Paths, monkeypatch: pytest.MonkeyPatch):
    for thread_id in ("thread-a", "thread-b", "thread-c"):
        (paths.thread_dir(thread_id) / "user-data" / "workspace").mkdir(parents=True)

    client = _ThreadsClient(
        existing_thread_ids={"thread-a", "thread-b", "thread-c"},
        failing_thread_ids={"thread-b"},
    )
    monkeypatch.setattr("langgraph_sdk.get_client", lambda url: _Client(client))

    response = asyncio.run(threads.delete_all_threads())

    assert response.deleted_count == 2
    assert response.files_deleted_count == 2
    assert response.failed_thread_ids == ["thread-b"]
    assert not paths.thread_dir("thread-a").exists()
    assert paths.thread_dir("thread-b").exists()
    assert not paths.thread_dir("thread-c").exists()


def test_hard_stop_patches_dangling_tool_calls(monkeypatch: pytest.MonkeyPatch):
    executor_module = importlib.import_module("src.subagents.executor")
    client = _ThreadsClient(
        values={
            "messages": [
                {"id": "h1", "type": "human", "content": "run"},
                {
                    "id": "a1",
                    "type": "ai",
                    "content": "",
                    "tool_calls": [
                        {"id": "tc-1", "name": "write_todos", "args": {}},
                        {"id": "tc-present", "name": "present_files", "args": {}},
                    ],
                },
            ],
            "work_mode": {"active": True, "current_phase_index": 2},
        }
    )
    monkeypatch.setattr("langgraph_sdk.get_client", lambda url: _Client(client))
    monkeypatch.setattr(executor_module, "cancel_background_tasks_for_thread", lambda thread_id: 2)

    response = asyncio.run(threads.hard_stop_thread("thread-1"))

    assert response.cancelled_subagents == 2
    assert response.patched_tool_calls == 1
    assert response.state_patched is True
    updated = client.updated[0]
    assert updated["work_mode"]["active"] is False
    assert updated["work_mode"]["stopped"] is True
    patched_messages = updated["messages"]
    assert patched_messages[-1]["type"] == "tool"
    assert patched_messages[-1]["tool_call_id"] == "tc-1"
    assert "[run_stopped]" in patched_messages[-1]["content"]


def test_hard_stop_does_not_duplicate_existing_tool_results(monkeypatch: pytest.MonkeyPatch):
    executor_module = importlib.import_module("src.subagents.executor")
    client = _ThreadsClient(
        values={
            "messages": [
                {"id": "h1", "type": "human", "content": "run"},
                {
                    "id": "a1",
                    "type": "ai",
                    "content": "",
                    "tool_calls": [{"id": "tc-1", "name": "write_todos", "args": {}}],
                },
                {"id": "t1", "type": "tool", "tool_call_id": "tc-1", "name": "write_todos", "content": "done"},
            ]
        }
    )
    monkeypatch.setattr("langgraph_sdk.get_client", lambda url: _Client(client))
    monkeypatch.setattr(executor_module, "cancel_background_tasks_for_thread", lambda thread_id: 0)

    response = asyncio.run(threads.hard_stop_thread("thread-1"))

    assert response.patched_tool_calls == 0
    assert response.state_patched is False
    assert client.updated == []
