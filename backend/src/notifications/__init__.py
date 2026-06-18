"""Proactive outbound notifications.

Bridges server-side events (scheduled/autoresearch run completion, approvals,
chat completion) to IM channels via the existing channels MessageBus, so the
user can be pinged on Telegram even when no browser is open.
"""

from src.notifications.service import (
    NotificationService,
    get_notification_service,
    start_notification_service,
    stop_notification_service,
)

__all__ = [
    "NotificationService",
    "get_notification_service",
    "start_notification_service",
    "stop_notification_service",
]
