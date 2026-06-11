"""Tests for lead and subagent prompt-capture attribution."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage

from src.models.prompt_logging import PromptLoggingCallback


def _capture(monkeypatch, tmp_path: Path, configurable: dict) -> list[Path]:
    monkeypatch.setenv("CAPYBARA_PROMPT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CAPYBARA_PROMPT_LOGGING_ENABLED", "1")
    monkeypatch.setattr("langgraph.config.get_config", lambda: {"configurable": configurable})

    PromptLoggingCallback().on_chat_model_start(
        {"name": "ChatOpenAI"},
        [[HumanMessage(content="hello")]],
    )
    return list(tmp_path.rglob("*.txt"))


def test_lead_prompt_capture_remains_flat(monkeypatch, tmp_path: Path) -> None:
    files = _capture(monkeypatch, tmp_path, {"thread_id": "thread-1"})

    assert len(files) == 1
    assert files[0].parent == tmp_path
    payload = json.loads(files[0].read_text(encoding="utf-8").split("\n\n", 1)[0])
    assert payload["actor"] == "work_agent"
    assert payload["subagent_type"] is None
    assert payload["task_id"] is None


def test_subagent_prompt_capture_is_nested_and_attributed(monkeypatch, tmp_path: Path) -> None:
    files = _capture(
        monkeypatch,
        tmp_path,
        {
            "thread_id": "thread-1",
            "subagent_type": "knowledge-researcher",
            "subagent_task_id": "task/123",
        },
    )

    assert len(files) == 1
    assert files[0].parent == tmp_path / "subagents" / "knowledge-researcher" / "task_123"
    payload = json.loads(files[0].read_text(encoding="utf-8").split("\n\n", 1)[0])
    assert payload["actor"] == "sub_agent"
    assert payload["subagent_type"] == "knowledge-researcher"
    assert payload["task_id"] == "task/123"
