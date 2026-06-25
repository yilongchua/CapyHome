"""Read/write channel configuration overrides in extensions_config.json.

Channel base config lives in ``config.yaml`` under ``channels`` (with secrets as
``$ENV_VAR`` references). To let the user enter a bot token from the UI without
editing files, we persist an override block under the ``channels`` key of
``extensions_config.json`` (the same writable-via-API store used for MCP servers
and user LLM endpoints). The override is deep-merged **over** config.yaml at
channel-service startup/restart, so a UI-entered token wins over the env var.

Only the override block is exposed through the API — the env-resolved token from
config.yaml is never surfaced.
"""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def resolve_extensions_config_path() -> Path:
    """Resolve where extensions_config.json lives (creating-on-first-write friendly).

    Priority: env hint → existing resolved path → project root next to backend/.
    Mirrors the resolution used by the onboarding router.
    """
    env_hint = os.getenv("CAPYBARA_HOME_EXTENSIONS_CONFIG_PATH")
    if env_hint:
        return Path(env_hint)

    from src.config.extensions_config import ExtensionsConfig

    try:
        existing = ExtensionsConfig.resolve_config_path()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        return Path(existing)

    backend_dir = Path(__file__).resolve().parents[2]  # .../backend
    return backend_dir.parent / "extensions_config.json"


def _read_raw() -> dict[str, Any]:
    path = resolve_extensions_config_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("extensions_config.json is unreadable (%s); treating as empty", exc)
        return {}


def read_channels_override() -> dict[str, Any]:
    """Return the ``channels`` override block from extensions_config.json (may be empty)."""
    block = _read_raw().get("channels")
    return block if isinstance(block, dict) else {}


def read_telegram_override() -> dict[str, Any]:
    """Return the stored telegram override (enabled/bot_token/allowed_users), defaulted."""
    tg = read_channels_override().get("telegram")
    tg = tg if isinstance(tg, dict) else {}
    return {
        "enabled": bool(tg.get("enabled", False)),
        "bot_token": str(tg.get("bot_token", "") or ""),
        "allowed_users": list(tg.get("allowed_users", []) or []),
    }


def write_telegram_override(*, enabled: bool, bot_token: str, allowed_users: list[int]) -> None:
    """Persist the telegram override block, preserving all other keys in the file."""
    path = resolve_extensions_config_path()
    raw = _read_raw()

    channels = raw.get("channels")
    if not isinstance(channels, dict):
        channels = {}
    channels["telegram"] = {
        "enabled": bool(enabled),
        "bot_token": bot_token or "",
        "allowed_users": [int(u) for u in allowed_users],
    }
    raw["channels"] = channels

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2)
    tmp_path.replace(path)
    logger.info("Telegram channel override saved to %s (enabled=%s, token_set=%s)", path, enabled, bool(bot_token))


def merged_channels_config(base_channels: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge the extensions_config ``channels`` override over the config.yaml block.

    Per-channel dicts are merged key-by-key so a UI override only replaces the
    fields it sets (e.g. bot_token) while inheriting the rest from config.yaml.
    """
    merged = deepcopy(base_channels) if isinstance(base_channels, dict) else {}
    override = read_channels_override()
    for name, ov in override.items():
        if isinstance(ov, dict) and isinstance(merged.get(name), dict):
            merged[name] = {**merged[name], **ov}
        else:
            merged[name] = ov
    return merged
