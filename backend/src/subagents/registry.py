"""Subagent registry for managing available subagents."""

import logging

from src.subagents.builtins import BUILTIN_SUBAGENTS
from src.subagents.config import SubagentConfig

logger = logging.getLogger(__name__)


def get_subagent_config(name: str) -> SubagentConfig | None:
    """Get a subagent configuration by name, with config.yaml overrides applied.

    Args:
        name: The name of the subagent.

    Returns:
        SubagentConfig if found (with any config.yaml overrides applied), None otherwise.
    """
    config = BUILTIN_SUBAGENTS.get(name)
    if config is None:
        return None

    # Apply timeout override from config.yaml (lazy import to avoid circular deps)
    from src.config.subagents_config import get_subagents_app_config

    app_config = get_subagents_app_config()
    updates: dict = {}
    effective_timeout = app_config.get_timeout_for(name)
    if effective_timeout != config.timeout_seconds:
        logger.debug(f"Subagent '{name}': timeout overridden by config.yaml ({config.timeout_seconds}s -> {effective_timeout}s)")
        updates["timeout_seconds"] = effective_timeout

    effective_max_turns = app_config.get_max_turns_for(name)
    if effective_max_turns != config.max_turns:
        logger.debug(f"Subagent '{name}': max_turns overridden by config.yaml ({config.max_turns} -> {effective_max_turns})")
        updates["max_turns"] = effective_max_turns

    if updates:
        config = config.model_copy(update=updates)

    return config


def list_subagents() -> list[SubagentConfig]:
    """List all available subagent configurations (with config.yaml overrides applied).

    Returns:
        List of all registered SubagentConfig instances.
    """
    return [get_subagent_config(name) for name in BUILTIN_SUBAGENTS]


def get_subagent_names() -> list[str]:
    """Get all available subagent names.

    Returns:
        List of subagent names.
    """
    return list(BUILTIN_SUBAGENTS.keys())


def get_subagent_names_for_mode(mode: str) -> list[str]:
    """Get the names of subagents spawnable in the given runtime mode.

    A subagent is spawnable in ``mode`` iff ``mode`` is listed in its ``modes``.
    Used by ``task_tool`` to gate delegation and to build helpful error text.
    """
    return [name for name, config in BUILTIN_SUBAGENTS.items() if mode in config.modes]
