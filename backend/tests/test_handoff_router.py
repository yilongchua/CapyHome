"""Tests for the thread handoff gateway route."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from src.config.paths import Paths
from src.gateway.routers.handoff import create_thread_handoff


class _ThreadsClient:
    def __init__(self, values: dict):
        self.values = values
        self.created = 0
        self.updated: list[tuple[str, dict]] = []

    async def get_state(self, thread_id: str):  # noqa: ARG002
        return {"values": self.values}

    async def create(self):
        self.created += 1
        return {"thread_id": f"thread-new-{self.created}"}

    async def update_state(self, thread_id: str, values: dict):
        self.updated.append((thread_id, values))


class _Client:
    def __init__(self, threads: _ThreadsClient):
        self.threads = threads


def test_create_thread_handoff_generates_package_and_copies_workspace(monkeypatch, tmp_path):
    paths = Paths(tmp_path)
    source_thread_id = "thread-source"
    paths.ensure_thread_dirs(source_thread_id)
    source_workspace = paths.sandbox_work_dir(source_thread_id)
    (source_workspace / "src").mkdir(parents=True, exist_ok=True)
    (source_workspace / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (source_workspace / ".runtime").mkdir(parents=True, exist_ok=True)
    (source_workspace / ".runtime" / "report.md").write_text("runtime report\n", encoding="utf-8")
    (source_workspace / ".handoff" / "old-package").mkdir(parents=True, exist_ok=True)
    (source_workspace / ".handoff" / "old-package" / "index.md").write_text("old handoff\n", encoding="utf-8")

    values = {
        "title": "Build Handoff",
        "dreamy_mode": False,
        "messages": [
            {"id": "m1", "type": "human", "content": "Please continue the current implementation carefully."},
            {"id": "m2", "type": "ai", "content": "I updated the plan and changed app.py."},
            {"id": "m3", "type": "human", "content": "Fork this into a fresh thread when ready."},
        ],
        "todos": [
            {"id": "todo-1", "content": "Implement the API", "status": "completed"},
            {"id": "todo-2", "content": "Polish the frontend", "status": "pending"},
        ],
        "artifacts": ["/mnt/user-data/workspace/src/app.py"],
        "handoff_artifacts": ["/mnt/user-data/workspace/.runtime/report.md"],
        "plan": {
            "title": "Execution Plan",
            "summary": "Ship the new handoff flow.",
            "objective": "Create a reusable thread fork package.",
            "status": "executing",
            "assumptions": ["Plan.md remains the main execution record."],
            "constraints": ["Do not write handoff data into .docs."],
        },
    }
    threads = _ThreadsClient(values)

    monkeypatch.setattr("langgraph_sdk.get_client", lambda url: _Client(threads))
    monkeypatch.setattr("src.gateway.routers.handoff.get_paths", lambda: paths)

    response = asyncio.run(create_thread_handoff(source_thread_id))

    assert response.new_thread_id == "thread-new-1"
    assert response.handoff_root_virtual_path.startswith("/mnt/user-data/workspace/.handoff/")
    assert response.package_manifest_virtual_path
    assert response.copied_file_count and response.copied_file_count >= 3

    handoff_dir_name = response.handoff_root_virtual_path.rsplit("/", 1)[-1]
    source_handoff_root = source_workspace / ".handoff" / handoff_dir_name
    assert (source_handoff_root / "index.md").exists()
    assert (source_handoff_root / "plan.md").exists()
    assert (source_handoff_root / "workspace_manifest.md").exists()

    manifest = json.loads((source_handoff_root / "handoff_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_thread_id"] == source_thread_id
    assert manifest["new_thread_id"] == "thread-new-1"
    assert not (source_workspace / ".handoff" / "old-package").exists()

    dest_workspace = paths.sandbox_work_dir("thread-new-1")
    assert (dest_workspace / "src" / "app.py").read_text(encoding="utf-8") == "print('hello')\n"
    assert (dest_workspace / ".runtime" / "report.md").read_text(encoding="utf-8") == "runtime report\n"
    assert (dest_workspace / ".handoff" / handoff_dir_name / "index.md").exists()
    assert not (dest_workspace / ".handoff" / "old-package").exists()
    assert any(
        thread_id == "thread-new-1" and "handoff_meta" in values
        for thread_id, values in threads.updated
    )


def test_create_thread_handoff_blocks_dreamy(monkeypatch):
    threads = _ThreadsClient({"dreamy_mode": True})
    monkeypatch.setattr("langgraph_sdk.get_client", lambda url: _Client(threads))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(create_thread_handoff("thread-source"))

    assert exc.value.status_code == 409
    assert "/dreamy-exit" in str(exc.value.detail)
