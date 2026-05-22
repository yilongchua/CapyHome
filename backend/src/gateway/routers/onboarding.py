import json
import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config.extensions_config import (
    ExtensionsConfig,
    UserLlmEndpointConfig,
    get_extensions_config,
    reload_extensions_config,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


# ─── Request / Response models ───────────────────────────────────────────────

class TestLlmRequest(BaseModel):
    base_url: str = Field(..., description="OpenAI-compatible base URL (e.g. http://localhost:11434/v1)")
    api_key: str = Field(default="", description="Optional API key")


class TestLlmResponse(BaseModel):
    ok: bool
    models: list[str] = Field(default_factory=list, description="Discovered model IDs")
    error: str | None = None


class TestComfyuiRequest(BaseModel):
    base_url: str = Field(..., description="ComfyUI base URL (e.g. http://127.0.0.1:8188)")


class TestComfyuiResponse(BaseModel):
    ok: bool
    error: str | None = None


class TestGenericRequest(BaseModel):
    url: str = Field(..., description="URL to health-check via GET")
    timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)


class TestGenericResponse(BaseModel):
    ok: bool
    status_code: int | None = None
    error: str | None = None


class LlmEndpointsMap(BaseModel):
    user_models: dict[str, UserLlmEndpointConfig] = Field(
        ...,
        description="Map of endpoint name to configuration",
        alias="userModels",
    )


class LlmEndpointsResponse(BaseModel):
    user_models: dict[str, UserLlmEndpointConfig] = Field(
        default_factory=dict,
        alias="userModels",
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _load_current_config() -> ExtensionsConfig:
    try:
        return get_extensions_config()
    except Exception as exc:
        logger.warning("Failed to load extensions config: %s", exc)
        return ExtensionsConfig(mcp_servers={}, skills={})


def _save_extensions_with_user_models(
    user_models: dict[str, UserLlmEndpointConfig],
) -> None:
    config_path = ExtensionsConfig.resolve_config_path()
    if config_path is None:
        config_path = Path.cwd().parent / "extensions_config.json"
        logger.info("No existing extensions config found; creating at %s", config_path)

    current = _load_current_config()

    config_data: dict[str, Any] = {
        "mcpServers": {name: s.model_dump() for name, s in current.mcp_servers.items()},
        "skills": {name: {"enabled": s.enabled} for name, s in current.skills.items()},
        "communityTools": {name: {"enabled": ct.enabled} for name, ct in current.community_tools.items()},
        "userModels": {name: m.model_dump() for name, m in user_models.items()},
    }

    with open(config_path, "w") as f:
        json.dump(config_data, f, indent=2)

    logger.info("User LLM endpoints saved to: %s", config_path)
    reload_extensions_config()
    logger.info("Extensions config reloaded after saving user models.")


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post(
    "/test-llm",
    response_model=TestLlmResponse,
    summary="Test LLM Endpoint",
    description="Send a GET /v1/models request to verify an OpenAI-compatible endpoint and discover available models.",
)
async def test_llm_endpoint(request: TestLlmRequest) -> TestLlmResponse:
    base_url = request.base_url.rstrip("/")
    models_url = f"{base_url}/models" if not base_url.endswith("/v1") else f"{base_url}/models"

    headers = {}
    if request.api_key:
        headers["Authorization"] = f"Bearer {request.api_key}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(models_url, headers=headers)
            response.raise_for_status()
            data = response.json()

        model_ids: list[str] = []
        models_data = data.get("data") or data.get("models") or []
        for m in models_data:
            if isinstance(m, dict):
                mid = m.get("id") or m.get("name") or m.get("model")
                if mid:
                    model_ids.append(str(mid))
            elif isinstance(m, str):
                model_ids.append(m)

        return TestLlmResponse(ok=True, models=model_ids)
    except httpx.TimeoutException:
        return TestLlmResponse(ok=False, error="Connection timed out")
    except httpx.ConnectError:
        return TestLlmResponse(ok=False, error="Connection refused — is the server running?")
    except httpx.HTTPStatusError as exc:
        return TestLlmResponse(ok=False, error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
    except Exception as exc:
        return TestLlmResponse(ok=False, error=str(exc))


@router.post(
    "/test-comfyui",
    response_model=TestComfyuiResponse,
    summary="Test ComfyUI Endpoint",
    description="Hit the /system_stats endpoint to verify a ComfyUI server is reachable.",
)
async def test_comfyui_endpoint(request: TestComfyuiRequest) -> TestComfyuiResponse:
    base_url = request.base_url.rstrip("/")
    health_url = f"{base_url}/system_stats"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(health_url)
            response.raise_for_status()
        return TestComfyuiResponse(ok=True)
    except httpx.TimeoutException:
        return TestComfyuiResponse(ok=False, error="Connection timed out")
    except httpx.ConnectError:
        return TestComfyuiResponse(ok=False, error="Connection refused — is the ComfyUI server running?")
    except httpx.HTTPStatusError as exc:
        return TestComfyuiResponse(ok=False, error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
    except Exception as exc:
        return TestComfyuiResponse(ok=False, error=str(exc))


@router.post(
    "/test-generic",
    response_model=TestGenericResponse,
    summary="Generic Health Check",
    description="Send a GET request to any URL and report reachability and status code.",
)
async def test_generic_endpoint(request: TestGenericRequest) -> TestGenericResponse:
    try:
        async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
            response = await client.get(request.url)
            return TestGenericResponse(ok=response.is_success, status_code=response.status_code)
    except httpx.TimeoutException:
        return TestGenericResponse(ok=False, error="Connection timed out")
    except httpx.ConnectError:
        return TestGenericResponse(ok=False, error="Connection refused")
    except Exception as exc:
        return TestGenericResponse(ok=False, error=str(exc))


@router.get(
    "/llm-endpoints",
    response_model=LlmEndpointsResponse,
    summary="List User LLM Endpoints",
    description="Return all user-added LLM endpoints from extensions config.",
)
async def list_llm_endpoints() -> LlmEndpointsResponse:
    config = _load_current_config()
    return LlmEndpointsResponse(user_models=config.user_models)


@router.put(
    "/llm-endpoints",
    response_model=LlmEndpointsResponse,
    summary="Save User LLM Endpoints",
    description="Save user-added LLM endpoints to extensions config, preserving MCP servers and community tools.",
)
async def save_llm_endpoints(request: LlmEndpointsMap) -> LlmEndpointsResponse:
    try:
        _save_extensions_with_user_models(request.user_models)
        reloaded = get_extensions_config()
        return LlmEndpointsResponse(user_models=reloaded.user_models)
    except Exception as exc:
        logger.error("Failed to save LLM endpoints: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to save LLM endpoints: {exc}")
