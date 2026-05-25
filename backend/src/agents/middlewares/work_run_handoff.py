"""Helpers for transitioning an approved plan into a fresh Work Mode run."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from src.config.handoffs_config import get_handoffs_config

logger = logging.getLogger(__name__)

_HANDOFF_GUARD = threading.Lock()
_IN_FLIGHT_HANDOFFS: set[str] = set()


def _langgraph_url() -> str:
    return os.getenv("CAPYBARA_LANGGRAPH_URL") or os.getenv("LANGGRAPH_URL") or "http://localhost:2024"


def _normalize_title(raw: object) -> str:
    return str(raw or "").strip()


def _derive_title_from_state(values: dict) -> str:
    title = _normalize_title(values.get("title"))
    if title:
        return title
    plan = values.get("plan")
    if isinstance(plan, dict):
        plan_title = _normalize_title(plan.get("title"))
        if plan_title:
            return plan_title
    return "New Conversation"


def _run_title_handoff_if_missing(*, thread_id: str, delay_seconds: float) -> None:
    from langgraph_sdk import get_client

    time.sleep(delay_seconds)
    try:
        client = get_client(url=_langgraph_url())
        state = client.threads.get_state(thread_id)
        # Async SDK methods are awaited at call sites in routers; here we are in
        # a daemon thread, so we use the sync-like wrappers exposed by the SDK.
        if hasattr(state, "__await__"):
            import asyncio

            state = asyncio.run(state)
        values = state.get("values") if isinstance(state, dict) else getattr(state, "values", {}) or {}
        current_title = _normalize_title(values.get("title")) if isinstance(values, dict) else ""
        if current_title:
            return
        fallback_title = _derive_title_from_state(values if isinstance(values, dict) else {})
        # Thread may still be near a checkpoint boundary; retry briefly on conflict.
        for _ in range(4):
            try:
                result = client.threads.update_state(thread_id, {"title": fallback_title})
                if hasattr(result, "__await__"):
                    import asyncio

                    asyncio.run(result)
                return
            except Exception:
                time.sleep(0.4)
    except Exception:
        logger.exception("Automatic title handoff failed for thread %s", thread_id)


def spawn_title_handoff_if_missing(*, thread_id: str, delay_seconds: float = 0.6, thread_name_suffix: str = "") -> None:
    worker = threading.Thread(
        target=_run_title_handoff_if_missing,
        kwargs={"thread_id": thread_id, "delay_seconds": delay_seconds},
        name=f"title-handoff-{thread_id[:8]}{thread_name_suffix}",
        daemon=True,
    )
    worker.start()


def _handoff_context_message(original_user_request: str | None, clarification_block: str) -> list[Any]:
    from langchain_core.messages import HumanMessage

    messages: list[Any] = []
    if original_user_request and original_user_request.strip():
        messages.append(
            HumanMessage(
                name="work_handoff_context",
                content=f"<work_handoff_context>\nOriginal request: {original_user_request.strip()}\n</work_handoff_context>",
            )
        )
    if clarification_block.strip():
        messages.append(
            HumanMessage(
                name="clarification_resolved",
                content=f"<clarification_resolved>\n{clarification_block.strip()}\n</clarification_resolved>",
            )
        )
    return messages


def _read_thread_values(client: Any, thread_id: str) -> dict[str, Any]:
    state = client.threads.get_state(thread_id)
    if hasattr(state, "__await__"):
        import asyncio

        state = asyncio.run(state)
    raw_values = state.get("values") if isinstance(state, dict) else getattr(state, "values", {}) or {}
    return raw_values if isinstance(raw_values, dict) else {}


def _run_work_mode_handoff(
    *,
    thread_id: str,
    requested_model_name: str | None,
    auto_mode: bool,
    original_user_request: str | None,
    delay_seconds: float,
) -> None:
    from langgraph_sdk import get_client

    from src.agents.middlewares.daemon_agent_invoke import invoke_client_agent_async
    from src.agents.middlewares.plan_execution import (
        format_clarification_context_for_work,
        mark_handoff_failed,
        mark_handoff_succeeded,
    )
    from src.client import CapyHomeClient

    time.sleep(delay_seconds)
    lg_client = get_client(url=_langgraph_url())
    # Ensure the thread has a visible title before work execution begins.
    spawn_title_handoff_if_missing(thread_id=thread_id, thread_name_suffix="-pre-work")
    handoff_cfg = get_handoffs_config()
    max_attempts = 1 + int(handoff_cfg.work_handoff_retry_attempts)
    recursion_limit = int(handoff_cfg.work_handoff_recursion_limit)
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        values: dict[str, Any] = {}
        try:
            try:
                values = _read_thread_values(lg_client, thread_id)
            except Exception:
                logger.debug("Could not read thread state before work handoff for %s", thread_id, exc_info=True)
            client = CapyHomeClient(
                model_name=requested_model_name,
                thinking_enabled=True,
                subagent_enabled=True,
                plan_mode=False,
                auto_mode=auto_mode,
            )
            config = client._get_runnable_config(  # noqa: SLF001
                thread_id,
                model_name=requested_model_name,
                thinking_enabled=True,
                subagent_enabled=True,
                auto_mode=auto_mode,
                mode="work",
                current_turn_text=original_user_request or "",
                original_user_request=original_user_request or "",
            )
            config["recursion_limit"] = recursion_limit
            config["configurable"].update(
                {
                    "mode": "work",
                    "is_plan_mode": False,
                    "background_followup": False,
                    "plan_behavior": "work_interactive",
                }
            )
            clarification_block = ""
            if isinstance(values, dict):
                plan = values.get("plan")
                if isinstance(plan, dict):
                    clarification_block = format_clarification_context_for_work(plan)
            handoff_messages = _handoff_context_message(original_user_request, clarification_block)
            invoke_client_agent_async(
                client,
                {"messages": handoff_messages},
                config=config,
                context={
                    "thread_id": thread_id,
                    "mode": "work",
                    "is_plan_mode": False,
                    "background_followup": False,
                    "plan_behavior": "work_interactive",
                    "model_name": requested_model_name,
                    "auto_mode": auto_mode,
                    "current_turn_text": original_user_request or "",
                    "original_user_request": original_user_request or "",
                },
            )
            try:
                latest_values = _read_thread_values(lg_client, thread_id)
            except Exception:
                latest_values = values
            plan = latest_values.get("plan") if isinstance(latest_values, dict) else None
            if isinstance(plan, dict):
                try:
                    result = lg_client.threads.update_state(thread_id, {"plan": mark_handoff_succeeded(plan)})
                    if hasattr(result, "__await__"):
                        import asyncio

                        asyncio.run(result)
                except Exception:
                    logger.exception("Failed to persist successful work handoff for thread %s", thread_id)
            return
        except Exception as exc:
            last_error = exc
            logger.exception(
                "Automatic work-mode handoff attempt %s/%s failed for thread %s",
                attempt,
                max_attempts,
                thread_id,
            )
            if attempt < max_attempts:
                time.sleep(0.8)
                continue
            break

    if last_error is not None:
        exc = last_error
        logger.exception("Automatic work-mode handoff failed for thread %s", thread_id)
        try:
            values = _read_thread_values(lg_client, thread_id)
        except Exception:
            values = {}
        plan = values.get("plan") if isinstance(values, dict) else None
        if isinstance(plan, dict):
            try:
                result = lg_client.threads.update_state(
                    thread_id,
                    {"plan": mark_handoff_failed(plan, error=str(exc))},
                )
                if hasattr(result, "__await__"):
                    import asyncio

                    asyncio.run(result)
            except Exception:
                logger.exception("Failed to persist failed work handoff for thread %s", thread_id)


def spawn_work_mode_handoff(
    *,
    thread_id: str,
    requested_model_name: str | None,
    auto_mode: bool,
    original_user_request: str | None = None,
    delay_seconds: float = 1.0,
    thread_name_suffix: str = "",
) -> None:
    """Spawn a daemon that starts a fresh Work Mode run on the same thread."""

    with _HANDOFF_GUARD:
        if thread_id in _IN_FLIGHT_HANDOFFS:
            logger.info("Skipping duplicate work-mode handoff for thread %s", thread_id)
            return
        _IN_FLIGHT_HANDOFFS.add(thread_id)

    def _run_with_cleanup() -> None:
        try:
            _run_work_mode_handoff(
                thread_id=thread_id,
                requested_model_name=requested_model_name,
                auto_mode=auto_mode,
                original_user_request=original_user_request,
                delay_seconds=delay_seconds,
            )
        finally:
            with _HANDOFF_GUARD:
                _IN_FLIGHT_HANDOFFS.discard(thread_id)

    worker = threading.Thread(
        target=_run_with_cleanup,
        name=f"work-mode-handoff-{thread_id[:8]}{thread_name_suffix}",
        daemon=True,
    )
    worker.start()
