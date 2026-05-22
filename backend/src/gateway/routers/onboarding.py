import ipaddress
import json
import logging
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


def _validate_probe_url(url: str) -> str | None:
    """Validate an outbound probe URL.

    Returns an error string if the URL is rejected, else None.
    Rejects non-http(s) schemes and resolves the host to ensure it is not a
    cloud-metadata endpoint. Localhost/loopback is allowed because most user
    LLM/ComfyUI endpoints are bound to 127.0.0.1.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return f"Unsupported URL scheme '{parsed.scheme}'. Use http:// or https://."
    if not parsed.hostname:
        return "URL is missing a host."

    host = parsed.hostname
    # Block well-known cloud metadata endpoints (AWS/GCP/Azure IMDS).
    if host in {"169.254.169.254", "metadata.google.internal", "metadata"}:
        return "Refusing to probe cloud metadata endpoint."

    try:
        # Resolve once; if any address is the metadata IP, reject.
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Let httpx raise the connection error normally — we just guard against
        # known dangerous targets here.
        return None

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip_str == "169.254.169.254":
            return "Refusing to probe cloud metadata IP."
        if ip.is_link_local and not ip.is_loopback:
            return "Refusing to probe link-local addresses."
    return None


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


def _resolve_save_path() -> Path:
    """Resolve where to persist extensions_config.json.

    Priority: env var hint → existing resolved path → project root next to backend/.
    The env-var hint is honored even if the file does not yet exist, so the very
    first save can create the file at the user-specified location.
    """
    import os as _os

    env_hint = _os.getenv("CAPYBARA_HOME_EXTENSIONS_CONFIG_PATH")
    if env_hint:
        return Path(env_hint)

    try:
        existing = ExtensionsConfig.resolve_config_path()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        return existing

    backend_dir = Path(__file__).resolve().parents[3]
    return backend_dir.parent / "extensions_config.json"


def _save_extensions_with_user_models(
    user_models: dict[str, UserLlmEndpointConfig],
) -> None:
    config_path = _resolve_save_path()
    if not config_path.exists():
        logger.info("No existing extensions config found; creating at %s", config_path)

    # Start from the raw on-disk JSON so any unknown/extra top-level keys survive.
    raw: dict[str, Any] = {}
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                raw = json.load(f) or {}
        except json.JSONDecodeError as exc:
            logger.warning("Existing extensions config is not valid JSON (%s); overwriting", exc)
            raw = {}

    raw["userModels"] = {name: m.model_dump() for name, m in user_models.items()}

    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2)
    tmp_path.replace(config_path)

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
    models_url = f"{base_url}/models" if base_url.endswith("/v1") else f"{base_url}/v1/models"

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
    rejection = _validate_probe_url(request.url)
    if rejection is not None:
        return TestGenericResponse(ok=False, error=rejection)
    try:
        async with httpx.AsyncClient(timeout=request.timeout_seconds, follow_redirects=False) as client:
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
