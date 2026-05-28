from typing import Literal, TypedDict

from langchain.tools import tool


class ClarificationOption(TypedDict, total=False):
    """Structured clarification option passed to ask_user_for_clarification."""

    label: str
    recommended: bool
    description: str | None


@tool("ask_user_for_clarification", parse_docstring=True, return_direct=True)
def ask_user_for_clarification_tool(
    question: str,
    clarification_type: Literal[
        "missing_info",
        "ambiguous_requirement",
        "approach_choice",
        "risk_confirmation",
        "suggestion",
    ],
    context: str | None = None,
    options: list[ClarificationOption] | None = None,
    blocks: list[str] | None = None,
    urgency: Literal["deferrable", "blocking"] = "deferrable",
) -> str:
    """Ask the user for clarification when you need their input.

    By default this call is non-blocking: the question is appended to a
    queue that the user sees as tabs in a side panel, and the run continues
    on any todos that are not gated by an unanswered clarification. Call
    this tool multiple times in one turn to surface several questions at
    once — the user prefers to answer them in a batch.

    Args:
        question: The clarification question to ask. Be specific and clear.
        clarification_type: missing_info, ambiguous_requirement, approach_choice, risk_confirmation, or suggestion.
        context: Optional one-sentence context explaining why you are asking.
        options: Optional structured choices. Each option may include `label`, `recommended`, and `description`.
        blocks: Optional list of todo ids whose readiness depends on this answer. The DAG gate hides them from `ready_ids` until the user answers. Pass `[]` (or omit) for questions that don't gate any specific todo.
        urgency: `deferrable` (default) queues the question without interrupting the run. `blocking` halts execution immediately until the user answers — use only when no useful progress is possible without the answer.
    """
    # Placeholder implementation. ClarificationMiddleware intercepts the call,
    # appends the question to ThreadState.clarifications, and decides whether
    # to interrupt the run.
    return "Clarification request processed by middleware"
