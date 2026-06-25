"""Gateway router for proactive notifications.

Lets the frontend report a web-UI chat completion so the user can be pinged on
an IM channel (e.g. Telegram) when their browser tab isn't focused. Delivery is
handled by the NotificationService over the channels MessageBus.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

_PREVIEW_MAX_CHARS = 280


class ThreadCompleteRequest(BaseModel):
    thread_id: str
    title: str | None = None
    preview: str | None = None


class NotifyResponse(BaseModel):
    sent: bool


@router.post("/thread-complete", response_model=NotifyResponse)
async def notify_thread_complete(request: ThreadCompleteRequest) -> NotifyResponse:
    """Send a 'your chat finished' notification (scope: on_chat_complete).

    Best-effort: returns sent=False (never errors) when notifications are
    disabled, the scope is off, or no target chat has been recorded.
    """
    from src.notifications import get_notification_service

    service = get_notification_service()
    if service is None or not service.scope_enabled("on_chat_complete"):
        return NotifyResponse(sent=False)

    title = (request.title or "").strip() or "CapyHome"
    text = f"✅ {title} — reply ready."
    preview = (request.preview or "").strip()
    if preview:
        if len(preview) > _PREVIEW_MAX_CHARS:
            preview = preview[:_PREVIEW_MAX_CHARS] + "…"
        text += f"\n\n{preview}"

    sent = service.notify(text, scope="on_chat_complete", thread_id=request.thread_id)
    return NotifyResponse(sent=sent)
