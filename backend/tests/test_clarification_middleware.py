"""Tests for ClarificationMiddleware queue + DAG-aware interrupt behaviour."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.types import Command

from src.agents.middlewares.clarification_middleware import ClarificationMiddleware


def _runtime(*, auto_mode_in_context: bool = False, auto_mode_in_config: bool | None = None):
    config = {}
    if auto_mode_in_config is not None:
        config = {"configurable": {"auto_mode": auto_mode_in_config}}
    return SimpleNamespace(
        context={"auto_mode": auto_mode_in_context},
        config=config,
    )


def _request(
    *,
    state: dict | None = None,
    context: dict | None = None,
    options: list[dict] | None = None,
    blocks: list[str] | None = None,
    urgency: str = "deferrable",
    question: str = "Which option?",
):
    args: dict = {
        "question": question,
        "clarification_type": "approach_choice",
        "options": options
        or [
            {"label": "Recommended", "recommended": True, "description": "Best default"},
            {"label": "Fallback", "recommended": False, "description": "Alternative"},
        ],
        "urgency": urgency,
    }
    if blocks is not None:
        args["blocks"] = blocks
    return SimpleNamespace(
        tool_call={"name": "ask_user_for_clarification", "id": "tc-1", "args": args},
        runtime=SimpleNamespace(context=context or {}, state=state or {}),
        state=state or {},
    )


# --- auto-mode bypass -------------------------------------------------------


def test_before_model_does_not_mutate_runtime_context():
    middleware = ClarificationMiddleware()
    runtime = _runtime(auto_mode_in_context=True, auto_mode_in_config=None)
    middleware.before_model({"auto_mode": False}, runtime)
    assert "_clarification_auto_mode" not in runtime.context


def test_auto_mode_pre_answers_clarification_without_interrupting():
    middleware = ClarificationMiddleware()
    request = _request(context={"auto_mode": True})
    result = middleware.wrap_tool_call(
        request,
        lambda _req: ToolMessage(content="unused", tool_call_id="tc-1", name="ask_user_for_clarification"),
    )
    assert isinstance(result, Command)
    # Auto mode pre-answers and does NOT interrupt.
    assert not result.goto  # empty/None goto means no interrupt
    appended = result.update["clarifications"][0]
    assert appended["status"] == "answered"
    assert appended["answer"] == "Recommended"
    assert result.update["messages"][0].content == "[Auto Mode] Selected: Recommended"


def test_auto_mode_pre_answers_duplicate_planner_inline_clarification():
    middleware = ClarificationMiddleware()
    request = _request(
        state={
            "plan": {
                "clarifications": [
                    {
                        "question": "Which option?",
                        "options": [{"label": "Recommended", "recommended": True}],
                    }
                ]
            }
        },
        context={"auto_mode": True},
    )
    result = middleware.wrap_tool_call(
        request,
        lambda _req: ToolMessage(content="unused", tool_call_id="tc-1", name="ask_user_for_clarification"),
    )
    assert isinstance(result, Command)
    assert result.update["clarifications"][0]["status"] == "answered"
    assert result.update["messages"][0].content == "[Auto Mode] Selected: Recommended"


# --- deferrable: queue and continue ----------------------------------------


def test_deferrable_call_queues_question_without_interrupting():
    middleware = ClarificationMiddleware()
    # No todo_graph yet → "had_ready" is False, so the DAG-starved branch
    # cannot trigger. Deferrable + no blocking urgency = no interrupt.
    request = _request(state={"clarifications": [], "todo_graph": None})
    result = middleware.wrap_tool_call(
        request,
        lambda _req: ToolMessage(content="unused", tool_call_id="tc-1", name="ask_user_for_clarification"),
    )
    assert isinstance(result, Command)
    assert not result.goto  # empty/None goto means no interrupt
    entry = result.update["clarifications"][0]
    assert entry["status"] == "pending"
    assert entry["urgency"] == "deferrable"
    assert result.update["clarification_pending"] is True


def test_deferrable_continues_when_unblocked_todos_remain():
    """Indonesia case: 3 clarifications all gate todo-recommend, but
    todo-top-companies has no blocks. Agent must keep working."""
    middleware = ClarificationMiddleware()
    todo_graph = {
        "nodes": [
            {"id": "todo-top-companies", "status": "pending", "depends_on": []},
            {"id": "todo-recommend", "status": "pending", "depends_on": []},
        ],
    }
    request = _request(
        state={"clarifications": [], "todo_graph": todo_graph},
        blocks=["todo-recommend"],
        question="How much do you intend to invest?",
    )
    result = middleware.wrap_tool_call(
        request,
        lambda _req: ToolMessage(content="unused", tool_call_id="tc-1", name="ask_user_for_clarification"),
    )
    assert isinstance(result, Command)
    assert not result.goto  # empty/None goto means no interrupt  # todo-top-companies still ready
    entry = result.update["clarifications"][0]
    assert entry["blocks"] == ["todo-recommend"]


# --- urgency=blocking always interrupts ------------------------------------


def test_blocking_urgency_interrupts_immediately():
    middleware = ClarificationMiddleware()
    todo_graph = {
        "nodes": [{"id": "todo-x", "status": "pending", "depends_on": []}],
    }
    request = _request(state={"clarifications": [], "todo_graph": todo_graph}, urgency="blocking")
    result = middleware.wrap_tool_call(
        request,
        lambda _req: ToolMessage(content="unused", tool_call_id="tc-1", name="ask_user_for_clarification"),
    )
    assert isinstance(result, Command)
    assert result.goto == END


# --- DAG-starved auto-interrupt --------------------------------------------


def test_deferrable_call_interrupts_when_every_ready_todo_is_blocked():
    """Once every ready todo is gated by a pending clarification, the
    DAG has nothing useful to do — halt and surface the questions."""
    middleware = ClarificationMiddleware()
    todo_graph = {
        "nodes": [{"id": "todo-only", "status": "pending", "depends_on": []}],
    }
    # Existing clarification already gates the only todo; the new deferrable
    # call adds another gating clarification — the run must halt.
    existing = [
        {"id": "clarif-prior", "status": "pending", "blocks": ["todo-only"]},
    ]
    request = _request(
        state={"clarifications": existing, "todo_graph": todo_graph},
        blocks=["todo-only"],
        urgency="deferrable",
    )
    result = middleware.wrap_tool_call(
        request,
        lambda _req: ToolMessage(content="unused", tool_call_id="tc-1", name="ask_user_for_clarification"),
    )
    assert isinstance(result, Command)
    assert result.goto == END


def test_deferrable_does_not_interrupt_when_no_ready_todos_existed():
    """If the DAG had no ready todos to start with (empty plan), a deferrable
    clarification should not be treated as a 'starved' halt signal — the
    agent might be in the early discovery phase before any plan exists."""
    middleware = ClarificationMiddleware()
    request = _request(
        state={"clarifications": [], "todo_graph": {"nodes": []}},
        urgency="deferrable",
    )
    result = middleware.wrap_tool_call(
        request,
        lambda _req: ToolMessage(content="unused", tool_call_id="tc-1", name="ask_user_for_clarification"),
    )
    assert isinstance(result, Command)
    assert not result.goto  # empty/None goto means no interrupt


# --- multiple questions in one turn ----------------------------------------


def test_three_deferrable_calls_in_one_turn_all_queue_without_halting():
    """Indonesia scenario: agent surfaces budget, region, sector questions
    sequentially. Two parallel todos remain unblocked. Run continues."""
    middleware = ClarificationMiddleware()
    todo_graph = {
        "nodes": [
            {"id": "todo-top-companies", "status": "pending", "depends_on": []},
            {"id": "todo-macro-overview", "status": "pending", "depends_on": []},
            {"id": "todo-recommend", "status": "pending", "depends_on": []},
        ],
    }
    accumulated: list[dict] = []
    for blocks, question in [
        (["todo-recommend"], "How much do you intend to invest?"),
        (["todo-recommend"], "Which region of Indonesia?"),
        (["todo-recommend"], "Which sectors are in scope?"),
    ]:
        request = _request(
            state={"clarifications": accumulated, "todo_graph": todo_graph},
            blocks=blocks,
            question=question,
        )
        result = middleware.wrap_tool_call(
            request,
            lambda _req: ToolMessage(content="unused", tool_call_id="tc-1", name="ask_user_for_clarification"),
        )
        assert isinstance(result, Command)
        assert not result.goto  # empty/None goto means no interrupt
        accumulated.extend(result.update["clarifications"])

    assert len(accumulated) == 3
    assert all(c["status"] == "pending" for c in accumulated)
    # All three gate todo-recommend; macro/top-companies are still independent.
    for c in accumulated:
        assert c["blocks"] == ["todo-recommend"]
