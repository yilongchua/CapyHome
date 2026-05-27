"""Canonical runtime-mode resolution.

The agent runtime has a single ``current_mode`` field with values ``"work"`` or
``"plan"``. Older config payloads may use ``mode`` (string alias) or
``is_plan_mode`` (boolean). ``resolve_current_mode`` reads any of those forms
and returns the canonical string. ``normalize_runtime_mode`` validates a raw
string.
"""

from typing import Literal

CurrentMode = Literal["work", "plan"]

_ALLOWED_RUNTIME_MODES: frozenset[str] = frozenset({"work", "plan"})


def normalize_runtime_mode(raw_mode: object) -> CurrentMode:
    """Validate and normalize a raw mode value to ``"work"`` or ``"plan"``.

    Empty/missing values default to ``"work"``. Legacy aliases ``"pro"`` and
    ``"fast"`` raise — callers must update the client.
    """
    mode = str(raw_mode or "").strip().lower()
    if not mode:
        return "work"
    if mode in {"pro", "fast"}:
        raise ValueError(
            "Invalid runtime mode '" + mode + "'. Supported modes are 'work' and 'plan'. "
            "Please update the client to send mode='plan' for planning runs.",
        )
    if mode not in _ALLOWED_RUNTIME_MODES:
        raise ValueError(
            "Invalid runtime mode '" + mode + "'. Supported modes are 'work' and 'plan'.",
        )
    return mode  # type: ignore[return-value]


def resolve_current_mode(cfg: dict) -> CurrentMode:
    """Resolve the canonical ``current_mode`` from a configurable dict.

    Precedence: ``current_mode`` (canonical) > ``mode`` (legacy alias) >
    ``is_plan_mode`` (legacy boolean). Returns one of ``"work"`` or ``"plan"``.
    Default is ``"work"`` when nothing is set.
    """
    raw_current = cfg.get("current_mode")
    if raw_current:
        return normalize_runtime_mode(raw_current)
    raw_legacy_mode = cfg.get("mode")
    if raw_legacy_mode:
        return normalize_runtime_mode(raw_legacy_mode)
    if cfg.get("is_plan_mode"):
        return "plan"
    return "work"
