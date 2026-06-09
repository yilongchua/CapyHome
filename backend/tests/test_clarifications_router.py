"""Tests for batch clarification answer routing."""

from __future__ import annotations

import asyncio

from src.gateway.routers.clarifications import ClarificationAnswer, ClarifyBatchRequest, clarify_batch


class _ThreadsClient:
    def __init__(self, values: dict):
        self._values = values
        self.calls: list[tuple[str, dict]] = []

    async def get_state(self, thread_id: str):  # noqa: ARG002
        return {"values": self._values}

    async def update_state(self, thread_id: str, values: dict):
        self.calls.append((thread_id, values))
        self._values = {**self._values, **values}


class _RunsClient:
    def __init__(self):
        self.create_calls: list[tuple[tuple, dict]] = []

    async def create(self, *args, **kwargs):
        self.create_calls.append((args, kwargs))
        return {"run_id": "run-replan-1"}

    async def get(self, *args):  # noqa: ARG002
        return {"assistant_id": "plan_agent"}


class _Client:
    def __init__(self, threads: _ThreadsClient, runs: _RunsClient):
        self.threads = threads
        self.runs = runs


class _AppConfig:
    def get_default_run_config(self):
        return {"recursion_limit": 1000}


def _pending_clarification() -> dict:
    return {
        "id": "clarif-1",
        "question": "What transport mode should the itinerary use?",
        "status": "pending",
        "options": [{"label": "Train + rental car"}],
    }


def _pending_clarification_two() -> dict:
    return {
        "id": "clarif-2",
        "question": "How many travel days should stay flexible?",
        "status": "pending",
        "options": [{"label": "Two flexible days"}],
    }


def test_clarify_batch_starts_plan_turn_when_no_plan_exists(monkeypatch):
    threads = _ThreadsClient(
        {
            "messages": [{"type": "human", "content": "Plan a 12-day Netherlands coast trip"}],
            "clarifications": [_pending_clarification()],
            "clarification_pending": True,
        }
    )
    runs = _RunsClient()
    monkeypatch.setattr("langgraph_sdk.get_client", lambda url: _Client(threads, runs))
    monkeypatch.setattr("src.gateway.routers.clarifications.get_app_config", lambda: _AppConfig())

    response = asyncio.run(
        clarify_batch(
            "thread-1",
            ClarifyBatchRequest(answers=[ClarificationAnswer(clarification_id="clarif-1", answer="Train + rental car")]),
        )
    )

    assert response.resumed_run_id == "run-replan-1"
    assert response.clarification_pending is False
    assert threads.calls[-1][1]["clarification_pending"] is False
    assert "messages" not in threads.calls[-1][1]
    args, kwargs = runs.create_calls[-1]
    assert args[:2] == ("thread-1", "plan_agent")
    prompt = kwargs["input"]["messages"][0]["content"]
    assert prompt.startswith("Resolved planning request:")
    assert "Plan a 12-day Netherlands coast trip" in prompt
    assert "Existing plan reference:" in prompt
    assert "plan.md: /mnt/user-data/workspace/plan.md" in prompt
    assert "State: no structured plan is currently recorded" in prompt
    assert "What transport mode should the itinerary use?: Train + rental car" in prompt
    assert "settled user constraints" in prompt
    assert "Prior planning context:" not in prompt
    assert "write_plan" in prompt
    assert kwargs["config"] == {"recursion_limit": 1000}
    assert kwargs["context"]["mode"] == "plan"
    assert kwargs["context"]["model_call_phase"] == "planner"


def test_clarify_batch_starts_plan_turn_and_resolves_existing_draft_plan_with_all_answers(monkeypatch):
    threads = _ThreadsClient(
        {
            "messages": [{"type": "human", "content": "Plan a Netherlands coast trip"}],
            "plan": {
                "plan_id": "plan-1",
                "status": "draft",
                "title": "Coast Trip",
                "objective": "Build a coastal itinerary.",
                "summary": "A draft route along the Netherlands coast.",
                "plan_path": "/mnt/user-data/workspace/custom-plan.md",
                "clarification_pending": True,
                "clarifications": [_pending_clarification(), _pending_clarification_two()],
                "clarification_answers": [],
            },
            "clarifications": [_pending_clarification(), _pending_clarification_two()],
        }
    )
    runs = _RunsClient()
    monkeypatch.setattr("langgraph_sdk.get_client", lambda url: _Client(threads, runs))
    monkeypatch.setattr("src.gateway.routers.clarifications.get_app_config", lambda: _AppConfig())

    response = asyncio.run(
        clarify_batch(
            "thread-1",
            ClarifyBatchRequest(
                answers=[
                    ClarificationAnswer(clarification_id="clarif-1", answer="Train + rental car"),
                    ClarificationAnswer(clarification_id="clarif-2", answer="Two flexible days"),
                ]
            ),
        )
    )

    assert response.resumed_run_id == "run-replan-1"
    updated_plan = threads.calls[-1][1]["plan"]
    assert updated_plan["clarification_pending"] is False
    assert updated_plan["clarification_resolved"] is True
    assert [answer["selected_label"] for answer in updated_plan["clarification_answers"]] == ["Train + rental car", "Two flexible days"]
    prompt = runs.create_calls[-1][1]["input"]["messages"][0]["content"]
    assert "Existing plan reference:" in prompt
    assert "plan.md: /mnt/user-data/workspace/custom-plan.md" in prompt
    assert "Coast Trip" in prompt
    assert "A draft route along the Netherlands coast." in prompt
    assert "What transport mode should the itinerary use?: Train + rental car" in prompt
    assert "How many travel days should stay flexible?: Two flexible days" in prompt
    assert runs.create_calls[-1][1]["config"] == {"recursion_limit": 1000}


def test_clarify_batch_active_work_mode_does_not_start_plan_turn(monkeypatch):
    threads = _ThreadsClient(
        {
            "messages": [{"type": "human", "content": "Do the task"}],
            "work_mode": {"active": True},
            "clarifications": [_pending_clarification()],
            "clarification_pending": True,
        }
    )
    runs = _RunsClient()
    monkeypatch.setattr("langgraph_sdk.get_client", lambda url: _Client(threads, runs))

    response = asyncio.run(
        clarify_batch(
            "thread-1",
            ClarifyBatchRequest(answers=[ClarificationAnswer(clarification_id="clarif-1", answer="Train + rental car")]),
        )
    )

    assert response.resumed_run_id is None
    assert runs.create_calls == []
    assert threads.calls[-1][1]["messages"][0].name == "clarifications_resolved"


def test_clarify_batch_resume_uses_default_run_config(monkeypatch):
    threads = _ThreadsClient(
        {
            "messages": [{"type": "human", "content": "Do the task"}],
            "work_mode": {"active": True},
            "clarifications": [_pending_clarification()],
            "clarification_pending": True,
        }
    )
    runs = _RunsClient()
    monkeypatch.setattr("langgraph_sdk.get_client", lambda url: _Client(threads, runs))
    monkeypatch.setattr("src.gateway.routers.clarifications.get_app_config", lambda: _AppConfig())

    response = asyncio.run(
        clarify_batch(
            "thread-1",
            ClarifyBatchRequest(
                answers=[ClarificationAnswer(clarification_id="clarif-1", answer="Train + rental car")],
                run_id="paused-run-1",
            ),
        )
    )

    assert response.resumed_run_id == "run-replan-1"
    args, kwargs = runs.create_calls[-1]
    assert args[:2] == ("thread-1", "plan_agent")
    assert kwargs["command"] == {"resume": {"run_id": "paused-run-1"}}
    assert kwargs["config"] == {"recursion_limit": 1000}
