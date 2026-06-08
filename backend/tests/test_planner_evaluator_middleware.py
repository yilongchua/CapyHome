"""Tests for planner clarification-resume orchestration + evaluator middleware.

Plan *generation* moved to the ``write_plan`` tool (see
``test_planner_middleware.py``), so this file only covers what
``PlannerMiddleware`` still does — advancing/resolving inline clarifications and
spawning the work handoff — plus the unrelated ``EvaluatorMiddleware``.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents.middlewares.evaluator_middleware import EvaluatorMiddleware
from src.agents.middlewares.planner_middleware import PlannerMiddleware
from src.config.app_config import AppConfig
from src.config.evaluator_config import EvaluatorConfig
from src.config.handoffs_config import HandoffsConfig
from src.config.model_config import ModelConfig
from src.config.routing_config import RoutingConfig
from src.config.sandbox_config import SandboxConfig
from src.models.router import ModelRouter


def _router() -> ModelRouter:
    cfg = AppConfig(
        models=[
            ModelConfig(
                name="primary",
                display_name="primary",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="primary",
                supports_thinking=True,
            )
        ],
        sandbox=SandboxConfig(use="src.sandbox.local:LocalSandboxProvider"),
    )
    cfg.routing = RoutingConfig(stages={"planner": "primary", "evaluator": "primary"}, fallback="primary")
    return ModelRouter(app_config=cfg)


def _runtime(*, auto_mode: bool = False):
    return SimpleNamespace(context={"thread_id": "thread-1", "model_name": "primary", "auto_mode": auto_mode})


# --------------------------------------------------------------------------- #
# PlannerMiddleware — inline clarification resume                             #
# --------------------------------------------------------------------------- #


def test_planner_clears_clarification_pending_after_user_answer(tmp_path: Path):
    middleware = PlannerMiddleware()
    state = {
        "plan": {
            "plan_id": "plan-1",
            "status": "draft",
            "clarification_pending": True,
            "clarification_index": 0,
            "clarification_answers": [],
            "clarifications": [
                {
                    "question": "What years should this cover?",
                    "options": [
                        {"label": "2024 to 2026", "recommended": True},
                        {"label": "Last 12 months", "recommended": False},
                    ],
                }
            ],
            "clarification_question": "What years should this cover?",
        },
        "messages": [
            HumanMessage(content="Create AI trends report"),
            ToolMessage(content="What years should this cover?", tool_call_id="tc-1", name="ask_user_for_clarification"),
            HumanMessage(content="Use 2024 to 2026."),
        ],
        "thread_data": {"workspace_path": str(tmp_path)},
    }
    update = middleware.before_model(state, _runtime())
    assert update is not None
    assert update["plan"]["clarification_pending"] is False
    assert isinstance(update["plan"]["clarification_answered_at"], str)


def test_planner_auto_mode_clarification_resolution_spawns_work_handoff(tmp_path: Path, monkeypatch):
    spawn_calls: list[dict] = []
    monkeypatch.setattr(
        "src.agents.middlewares.planner_middleware.spawn_work_mode_handoff",
        lambda **kwargs: spawn_calls.append(kwargs),
    )
    middleware = PlannerMiddleware()
    state = {
        "plan": {
            "plan_id": "plan-1",
            "status": "draft",
            "clarification_pending": True,
            "clarification_index": 0,
            "clarification_answers": [],
            "clarifications": [
                {
                    "question": "Which islands best match your pace?",
                    "options": [
                        {"label": "Santorini & Crete", "recommended": True},
                        {"label": "Rhodes only", "recommended": False},
                    ],
                }
            ],
            "clarification_question": "Which islands best match your pace?",
            "awaiting_execution_approval": True,
        },
        "messages": [
            HumanMessage(content="Plan Greece trip"),
            ToolMessage(content="[Auto Mode] Selected: Santorini & Crete", tool_call_id="tc-1", name="ask_user_for_clarification"),
        ],
        "thread_data": {"workspace_path": str(tmp_path)},
    }
    runtime = _runtime(auto_mode=True)
    runtime.context["plan_behavior"] = "plan_foreground"
    update = middleware.before_model(state, runtime)
    assert update is not None
    assert update["plan"]["clarification_pending"] is False
    assert update["plan"]["status"] == "approved"
    assert len(spawn_calls) == 1


def test_planner_clears_clarification_pending_after_auto_mode_selection(tmp_path: Path):
    middleware = PlannerMiddleware()
    state = {
        "plan": {
            "plan_id": "plan-1",
            "status": "draft",
            "clarification_pending": True,
            "clarification_index": 0,
            "clarification_answers": [],
            "clarifications": [
                {
                    "question": "Which islands best match your pace?",
                    "options": [
                        {"label": "Santorini & Crete", "recommended": True},
                        {"label": "Rhodes only", "recommended": False},
                    ],
                }
            ],
            "clarification_question": "Which islands best match your pace?",
            "awaiting_execution_approval": True,
        },
        "messages": [
            HumanMessage(content="Plan Greece trip"),
            ToolMessage(content="[Auto Mode] Selected: Santorini & Crete", tool_call_id="tc-1", name="ask_user_for_clarification"),
        ],
        "thread_data": {"workspace_path": str(tmp_path)},
    }
    update = middleware.before_model(state, _runtime(auto_mode=True))
    assert update is not None
    assert update["plan"]["clarification_pending"] is False
    assert update["plan"]["status"] == "approved"
    assert update["plan"]["awaiting_execution_approval"] is False
    assert isinstance(update["plan"]["approved_at"], str)


# --------------------------------------------------------------------------- #
# EvaluatorMiddleware (unchanged by the planner-as-tool refactor)             #
# --------------------------------------------------------------------------- #


def test_evaluator_defers_when_todos_incomplete(tmp_path: Path):
    middleware = EvaluatorMiddleware(
        router=_router(),
        requested_model="primary",
        max_attempts=EvaluatorConfig().max_attempts,
        handoffs_config=HandoffsConfig(enabled=True, dir=".handoffs"),
    )
    state = {
        "plan": {"title": "Plan", "summary": "Summary"},
        "eval_attempts": 0,
        "todo_graph": {"nodes": [{"id": "todo-1", "status": "pending"}]},
        "messages": [AIMessage(content="I am done")],
        "thread_data": {"workspace_path": str(tmp_path)},
    }
    update = middleware.after_model(state, _runtime())
    assert update is None


def test_evaluator_marks_plan_passed_on_llm_pass(monkeypatch, tmp_path: Path):
    class _Model:
        def invoke(self, prompt):  # noqa: ARG002
            return SimpleNamespace(content="VERDICT: PASS\nCRITIQUE: Looks good.")

    monkeypatch.setattr("src.agents.middlewares.evaluator_middleware.create_chat_model", lambda **kwargs: _Model())
    workspace = tmp_path / "workspace"
    plans = workspace / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    plan_file = plans / "plan-20260521-000000-sample.md"
    plan_file.write_text("# Plan", encoding="utf-8")
    alias_file = workspace / "plan.md"
    alias_file.write_text("# Plan", encoding="utf-8")
    middleware = EvaluatorMiddleware(
        router=_router(),
        requested_model="primary",
        max_attempts=EvaluatorConfig().max_attempts,
        handoffs_config=HandoffsConfig(enabled=True, dir=".handoffs"),
    )
    state = {
        "plan": {
            "title": "Plan",
            "summary": "Summary",
            "plan_path": "/mnt/user-data/workspace/plans/plan-20260521-000000-sample.md",
            "latest_alias_path": "/mnt/user-data/workspace/plan.md",
        },
        "eval_attempts": 0,
        "todo_graph": {"nodes": [{"id": "todo-1", "status": "completed"}]},
        "messages": [AIMessage(content="Final answer")],
        "thread_data": {"workspace_path": str(workspace)},
    }
    update = middleware.after_model(state, _runtime())
    assert update is not None
    assert update["plan"]["evaluation_status"] == "passed"
    assert (workspace / ".handoffs" / "report.md").exists()


def test_evaluator_async_after_model_runs_sync_path_off_event_loop(monkeypatch):
    middleware = EvaluatorMiddleware(
        router=_router(),
        requested_model="primary",
        max_attempts=EvaluatorConfig().max_attempts,
        handoffs_config=HandoffsConfig(enabled=False),
    )
    event_loop_thread = threading.get_ident()
    observed_thread = None

    def fake_after_model(_state, _runtime):
        nonlocal observed_thread
        observed_thread = threading.get_ident()
        return {"eval_attempts": 1}

    monkeypatch.setattr(middleware, "after_model", fake_after_model)
    update = asyncio.run(middleware.aafter_model({"plan": {"title": "Plan"}}, _runtime()))

    assert update == {"eval_attempts": 1}
    assert observed_thread is not None
    assert observed_thread != event_loop_thread


def test_evaluator_resolves_virtual_plan_path(monkeypatch, tmp_path: Path):
    class _Model:
        def invoke(self, prompt):  # noqa: ARG002
            return SimpleNamespace(content="VERDICT: PASS\nCRITIQUE: Looks good.")

    monkeypatch.setattr("src.agents.middlewares.evaluator_middleware.create_chat_model", lambda **kwargs: _Model())
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    plans = workspace / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "plan-20260521-000000-sample.md").write_text("# Plan", encoding="utf-8")
    (workspace / "plan.md").write_text("# Plan", encoding="utf-8")
    middleware = EvaluatorMiddleware(
        router=_router(),
        requested_model="primary",
        max_attempts=EvaluatorConfig().max_attempts,
        handoffs_config=HandoffsConfig(enabled=True, dir=".handoffs"),
    )
    state = {
        "plan": {
            "title": "Plan",
            "summary": "Summary",
            "plan_path": "/mnt/user-data/workspace/plans/plan-20260521-000000-sample.md",
            "latest_alias_path": "/mnt/user-data/workspace/plan.md",
        },
        "eval_attempts": 0,
        "todo_graph": {"nodes": [{"id": "todo-1", "status": "completed"}]},
        "messages": [AIMessage(content="Final answer")],
        "thread_data": {
            "workspace_path": str(workspace),
            "outputs_path": str(tmp_path / "outputs"),
            "uploads_path": str(tmp_path / "uploads"),
            "mounted_path": None,
        },
    }
    update = middleware.after_model(state, _runtime())
    assert update is not None
    assert update["plan"]["evaluation_status"] == "passed"
