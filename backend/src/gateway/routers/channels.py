"""Gateway router for IM channel management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])


class ChannelStatusResponse(BaseModel):
    service_running: bool
    channels: dict[str, dict]


class ChannelRestartResponse(BaseModel):
    success: bool
    message: str


class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    allowed_users: list[int] = Field(default_factory=list)


class ChannelsConfigResponse(BaseModel):
    telegram: TelegramConfig


class SaveChannelsConfigResponse(BaseModel):
    telegram: TelegramConfig
    restarted: bool
    message: str


@router.get("/", response_model=ChannelStatusResponse)
async def get_channels_status() -> ChannelStatusResponse:
    """Get the status of all IM channels."""
    from src.channels.service import get_channel_service

    service = get_channel_service()
    if service is None:
        return ChannelStatusResponse(service_running=False, channels={})
    status = service.get_status()
    return ChannelStatusResponse(**status)


@router.post("/{name}/restart", response_model=ChannelRestartResponse)
async def restart_channel(name: str) -> ChannelRestartResponse:
    """Restart a specific IM channel."""
    from src.channels.service import get_channel_service

    service = get_channel_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Channel service is not running")

    success = await service.restart_channel(name)
    if success:
        logger.info("Channel %s restarted successfully", name)
        return ChannelRestartResponse(success=True, message=f"Channel {name} restarted successfully")
    else:
        logger.warning("Failed to restart channel %s", name)
        return ChannelRestartResponse(success=False, message=f"Failed to restart channel {name}")


@router.get("/config", response_model=ChannelsConfigResponse)
async def get_channels_config() -> ChannelsConfigResponse:
    """Return the UI-editable channel overrides (stored in extensions_config.json).

    Only the override block is returned; a token supplied via the config.yaml
    ``$TELEGRAM_BOT_TOKEN`` env var is intentionally not surfaced here.
    """
    from src.channels.config_store import read_telegram_override

    return ChannelsConfigResponse(telegram=TelegramConfig(**read_telegram_override()))


@router.put("/config", response_model=SaveChannelsConfigResponse)
async def save_channels_config(request: ChannelsConfigResponse) -> SaveChannelsConfigResponse:
    """Persist channel overrides and apply them by restarting the affected channel."""
    from src.channels.config_store import write_telegram_override
    from src.channels.service import get_channel_service

    tg = request.telegram
    write_telegram_override(
        enabled=tg.enabled,
        bot_token=tg.bot_token,
        allowed_users=tg.allowed_users,
    )

    # Apply immediately when the channel service is running (picks up the new
    # config via reload). When not running, the override applies on next startup.
    restarted = False
    message = "Saved. Restart the gateway to apply."
    service = get_channel_service()
    if service is not None:
        try:
            restarted = await service.restart_channel("telegram")
            message = "Saved and applied." if restarted else "Saved, but the Telegram channel did not start (check the bot token)."
        except Exception as exc:
            logger.exception("Failed to apply telegram channel config")
            message = f"Saved, but applying failed: {exc}"

    return SaveChannelsConfigResponse(telegram=tg, restarted=restarted, message=message)
