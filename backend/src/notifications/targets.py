"""NotificationTargetStore — persists where proactive notifications should be sent.

Per channel, we keep a single "primary" target (chat_id) for the user. The
target is auto-recorded the first time the user interacts with the bot (so
"message your bot once and it knows where to reach you"). This is intentionally
a single-user model; swap the JSON file for a per-user table if multi-user
support is ever needed.
"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class NotificationTargetStore:
    """JSON-file-backed store mapping ``channel_name -> {chat_id, user_id}``."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            from src.config.paths import get_paths

            path = Path(get_paths().base_dir) / "notifications" / "targets.json"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt notification target store at %s, starting fresh", self._path)
        return {}

    def _save(self) -> None:
        fd = tempfile.NamedTemporaryFile(mode="w", dir=self._path.parent, suffix=".tmp", delete=False)
        try:
            json.dump(self._data, fd, indent=2)
            fd.close()
            Path(fd.name).replace(self._path)
        except BaseException:
            fd.close()
            Path(fd.name).unlink(missing_ok=True)
            raise

    # -- public API --------------------------------------------------------

    def get_chat_id(self, channel_name: str) -> str | None:
        """Return the primary chat_id for a channel, if one has been recorded."""
        entry = self._data.get(channel_name)
        return entry.get("chat_id") if entry else None

    def set_primary(self, channel_name: str, chat_id: str, *, user_id: str = "") -> None:
        """Record/refresh the primary notification target for a channel."""
        with self._lock:
            now = time.time()
            existing = self._data.get(channel_name)
            self._data[channel_name] = {
                "chat_id": str(chat_id),
                "user_id": user_id or (existing.get("user_id", "") if existing else ""),
                "created_at": existing["created_at"] if existing else now,
                "updated_at": now,
            }
            self._save()
        logger.info("[Notifications] primary target for %s set to chat_id=%s", channel_name, chat_id)
