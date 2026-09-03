"""Shared system-prompt memory-context builder.

Both the work agent and the plan agent inject the same ``<memory>`` block into
their system prompts.  This module owns the single implementation; the two
prompt modules keep thin wrappers so their own loggers stay attached to any
failure (``src.agents.work_agent.prompt`` / ``src.agents.plan_agent.prompt``).

Imports inside :func:`build_memory_context` are intentionally lazy: callers and
tests patch ``src.agents.memory.get_memory_data`` and
``src.config.memory_config.get_memory_config`` at module level, which only takes
effect when the lookup happens at call time.
"""

from __future__ import annotations

import logging

_fallback_logger = logging.getLogger(__name__)


def build_memory_context(
    agent_name: str | None = None,
    *,
    current_turn_text: str = "",
    logger: logging.Logger | None = None,
) -> str:
    """Build the ``<memory>`` block for a system prompt.

    Args:
        agent_name: If provided, loads per-agent memory. If None, loads global memory.
        current_turn_text: The user's current turn, used for relevance filtering.
            Falls back to the runtime config when empty.
        logger: Logger used to report failures. Defaults to this module's logger.

    Returns:
        Formatted memory context wrapped in ``<memory>`` tags, or an empty string
        when memory is disabled, unavailable, or yields no content.
    """
    log = logger or _fallback_logger
    try:
        from langgraph.config import get_config

        from src.agents.memory import format_memory_for_injection, get_memory_data
        from src.config.memory_config import get_memory_config

        config = get_memory_config()
        if not config.enabled or not config.injection_enabled:
            return ""

        cfg = get_config()
        configurable = cfg.get("configurable", {}) if isinstance(cfg, dict) else {}
        workspace_id = str(configurable.get("thread_id") or "") or None

        memory_data = get_memory_data(agent_name, scope="global") if config.global_scope_enabled else {}
        workspace_memory_data = None
        if config.workspace_scope_enabled and workspace_id:
            workspace_memory_data = get_memory_data(
                agent_name,
                scope="workspace",
                workspace_id=workspace_id,
            )

        current_turn_text = current_turn_text.strip() or str(
            configurable.get("current_turn_text")
            or configurable.get("original_user_request")
            or configurable.get("user_prompt")
            or ""
        ).strip()
        memory_content = format_memory_for_injection(
            memory_data,
            max_tokens=config.max_injection_tokens,
            current_turn_text=current_turn_text,
            workspace_memory_data=workspace_memory_data,
            workspace_id=workspace_id,
        )

        if not memory_content.strip():
            return ""

        return f"""<memory>
{memory_content}
</memory>
"""
    except Exception:
        log.exception("Failed to load memory context")
        return ""


def build_subagent_rules_context(
    thread_id: str | None = None,
    *,
    agent_name: str | None = None,
    logger: logging.Logger | None = None,
) -> str:
    """Build a ``<behavior_rules>`` block for a delegated (subagent) run.

    Subagents get **rules only, never facts**. Rules are short, user-authored
    directives that must apply everywhere — a user who sets "always answer in
    Dutch" via ``/memory`` reasonably expects delegated work to honour it too.
    Facts are deliberately excluded: they are retrieved evidence, and injecting
    them into a research subagent risks passing personal context off as sourced
    findings. Subagents that should search memory get the ``recall`` tool
    instead, which is an explicit per-subagent decision.

    Returns an empty string when memory or behavior rules are disabled, when no
    active rules exist, or on any failure — this must never break delegation.
    """
    log = logger or _fallback_logger
    try:
        from src.agents.memory.backend import MemoryScopes, get_memory_backend
        from src.config.memory_config import get_memory_config

        config = get_memory_config()
        if not config.enabled or not config.injection_enabled or not config.behavior_rules_enabled:
            return ""

        backend = get_memory_backend()
        rules: list[dict] = []
        seen: set[str] = set()

        targets = []
        if config.workspace_scope_enabled and thread_id:
            targets.append(MemoryScopes.resolve("workspace", thread_id, agent_name))
        if config.global_scope_enabled:
            targets.append(MemoryScopes.resolve("global", None, agent_name))

        for scopes in targets:
            profile = backend.get_profile(scopes=scopes) or {}
            for rule in profile.get("behaviorRules") or []:
                if not isinstance(rule, dict) or not bool(rule.get("active", True)):
                    continue
                instruction = str(rule.get("instruction") or "").strip()
                if not instruction or instruction in seen:
                    continue
                seen.add(instruction)
                rules.append(rule)

        if not rules:
            return ""

        lines = "\n".join(f"- {str(r.get('instruction')).strip()}" for r in rules[:10])
        return f"""

<behavior_rules>
Standing instructions from the user. They apply to your work and your output
format. They do not license you to broaden your assigned scope.
{lines}
</behavior_rules>
"""
    except Exception:
        log.exception("Failed to load subagent behavior rules")
        return ""
