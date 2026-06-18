"""NotificationService — dispatches proactive outbound notifications.

Reuses the channels ``MessageBus`` (which Telegram/Slack already subscribe to),
so a notification is just an ``OutboundMessage`` published onto the bus. The
public ``notify(...)`` entry point is **synchronous and fire-and-forget**: it
schedules the async publish onto the gateway event loop captured at startup,
which makes it safe to call from synchronous control-plane code running in a
background thread (mirroring how ``TelegramChannel`` bridges threads).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.channels.message_bus import MessageBus, OutboundMessage
from src.notifications.targets import NotificationTargetStore

logger = logging.getLogger(__name__)


class NotificationService:
    """Publishes notification messages to IM channels via the shared MessageBus."""

    def __init__(
        self,
        *,
        bus: MessageBus | None,
        config: dict[str, Any] | None = None,
        store: NotificationTargetStore | None = None,
    ) -> None:
        self._bus = bus
        self._config = dict(config or {})
        self._store = store or NotificationTargetStore()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- lifecycle ---------------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the event loop notifications should be dispatched on."""
        self._loop = loop

    @property
    def store(self) -> NotificationTargetStore:
        return self._store

    @property
    def enabled(self) -> bool:
        return bool(self._bus is not None and self._config.get("enabled", False))

    def scope_enabled(self, scope: str) -> bool:
        """Whether a specific notification scope is enabled (defaults to True)."""
        return self.enabled and bool(self._config.get(scope, True))

    @property
    def default_channel(self) -> str:
        return str(self._config.get("channel", "telegram"))

    # -- recording targets -------------------------------------------------

    def record_target(self, channel_name: str, chat_id: str, *, user_id: str = "") -> None:
        """Remember a chat as the primary destination for proactive pings."""
        self._store.set_primary(channel_name, chat_id, user_id=user_id)

    # -- dispatch ----------------------------------------------------------

    def notify(
        self,
        text: str,
        *,
        scope: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        thread_id: str | None = None,
    ) -> bool:
        """Send a notification (fire-and-forget).

        Returns True if the message was scheduled for dispatch, False if it was
        skipped (disabled, scope off, or no target chat). Never raises — a
        notification failure must not break the event that triggered it.
        """
        try:
            if scope is not None:
                if not self.scope_enabled(scope):
                    return False
            elif not self.enabled:
                return False

            ch = channel or self.default_channel
            target_chat = chat_id or self._store.get_chat_id(ch)
            if not target_chat:
                logger.info("[Notifications] skipped (no target chat for channel=%s)", ch)
                return False

            msg = OutboundMessage(
                channel_name=ch,
                chat_id=str(target_chat),
                thread_id=thread_id or "",
                text=text,
                is_final=True,
                metadata={"source": "notification", "scope": scope or ""},
            )
            return self._dispatch(msg)
        except Exception:
            logger.exception("[Notifications] failed to dispatch notification")
            return False

    def _dispatch(self, msg: OutboundMessage) -> bool:
        if self._bus is None:
            return False
        coro = self._bus.publish_outbound(msg)
        loop = self._loop
        if loop is not None and loop.is_running():
            # Safe from any thread, including the loop's own thread.
            asyncio.run_coroutine_threadsafe(coro, loop)
            return True
        # No bound running loop (e.g. embedded/test context): best-effort.
        try:
            asyncio.run(coro)
            return True
        except RuntimeError:
            logger.warning("[Notifications] no event loop available to dispatch notification")
            coro.close()
            return False


# -- singleton access -------------------------------------------------------

_notification_service: NotificationService | None = None


def get_notification_service() -> NotificationService | None:
    """Get the singleton NotificationService (if started)."""
    return _notification_service


def start_notification_service() -> NotificationService:
    """Create and start the global NotificationService from app config.

    Wires it to the running channel service's MessageBus and binds the current
    event loop for thread-safe dispatch. Must be called after the channel
    service has started and from within the gateway event loop.
    """
    global _notification_service
    if _notification_service is not None:
        return _notification_service

    from src.channels.service import get_channel_service
    from src.config.app_config import get_app_config

    config = (get_app_config().model_extra or {}).get("notifications", {})
    if not isinstance(config, dict):
        config = {}

    channel_service = get_channel_service()
    bus = channel_service.bus if channel_service is not None else None

    service = NotificationService(bus=bus, config=config)
    try:
        service.bind_loop(asyncio.get_running_loop())
    except RuntimeError:
        logger.warning("[Notifications] no running loop at startup; dispatch will fall back")

    _notification_service = service
    logger.info("[Notifications] service started (enabled=%s, channel=%s)", service.enabled, service.default_channel)
    return service


def stop_notification_service() -> None:
    """Tear down the global NotificationService."""
    global _notification_service
    _notification_service = None
