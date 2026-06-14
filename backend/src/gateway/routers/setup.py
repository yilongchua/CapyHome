from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from src.config.app_config import get_app_config
from src.config.extensions_config import get_extensions_config


async def _require_local_origin(
    origin: Annotated[str | None, Header()] = None,
) -> None:
    if not origin:
        return
    hostname = (urlsplit(origin).hostname or "").lower()
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Managed setup actions are only available from the local CapyHome UI.")


router = APIRouter(
    prefix="/api/setup",
    tags=["setup"],
    dependencies=[Depends(_require_local_origin)],
)

SetupAction = Literal[
    "update_all",
    "websearch_enable_docker",
    "websearch_enable_podman",
    "websearch_disable",
    "websearch_repair",
]


class SetupComponentStatus(BaseModel):
    status: str
    message: str | None = None


class SetupJobResponse(BaseModel):
    job_id: str
    action: SetupAction | str
    status: str
    message: str | None = None
    updated_at: str | None = None


class SetupStatusResponse(BaseModel):
    managed_setup_enabled: bool
    docker: SetupComponentStatus
    podman: SetupComponentStatus
    daemon: SetupComponentStatus
    capyhome: SetupComponentStatus
    llm: SetupComponentStatus
    websearch: SetupComponentStatus
    websearch_replicas: int = Field(default=8, ge=1, le=32)
    websearch_runtime: Literal["docker", "podman"] | None = None
    latest_job: SetupJobResponse | None = None


class SetupActionRequest(BaseModel):
    action: SetupAction


class WebSearchTestResponse(BaseModel):
    ok: bool
    message: str
    query: str | None = None
    result_count: int | None = None


def _state_dir() -> Path:
    configured = os.getenv("CAPYHOME_SETUP_STATE_DIR")
    if configured:
        return Path(configured)
    return Path.cwd().parent / ".capyhome" / "setup"


def _manifest_path() -> Path:
    configured = os.getenv("CAPYHOME_MANAGED_INSTALL_PATH")
    if configured:
        return Path(configured)
    return Path.cwd().parent / ".capyhome-managed.json"


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _latest_job() -> SetupJobResponse | None:
    jobs_dir = _state_dir() / "jobs"
    if not jobs_dir.exists():
        return None
    candidates = sorted(
        jobs_dir.glob("*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        if path.name.endswith((".request.json", ".running.json")):
            continue
        payload = _read_json(path)
        if not payload.get("job_id"):
            continue
        return SetupJobResponse(
            job_id=str(payload["job_id"]),
            action=str(payload.get("action") or "update_all"),
            status=str(payload.get("status") or "unknown"),
            message=payload.get("message"),
            updated_at=payload.get("updated_at"),
        )
    return None


def _replica_count() -> int:
    payload = _read_json(_manifest_path())
    try:
        return min(32, max(1, int(payload.get("websearch_replicas", 8))))
    except (TypeError, ValueError):
        return 8


def _websearch_runtime() -> Literal["docker", "podman"] | None:
    value = _read_json(_manifest_path()).get("websearch_runtime")
    return value if value in {"docker", "podman"} else None


async def _probe_websearch_health(health_url: str | None) -> bool:
    if not health_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(health_url)
        return response.is_success
    except httpx.HTTPError:
        return False


def _websearch_config():
    config = get_extensions_config().mcp_servers.get("websearch")
    if not config:
        raise HTTPException(status_code=404, detail="WebSearch MCP is not configured.")
    if not config.url:
        raise HTTPException(status_code=409, detail="WebSearch has no MCP URL configured.")
    return config


@router.post("/websearch/test-connection", response_model=WebSearchTestResponse)
async def test_websearch_connection() -> WebSearchTestResponse:
    config = _websearch_config()
    if not config.health_url:
        raise HTTPException(status_code=409, detail="WebSearch has no health URL configured.")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            health_response = await client.get(config.health_url)
            health_response.raise_for_status()
            tools_response = await client.post(
                config.url,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            tools_response.raise_for_status()
        tools_payload = tools_response.json()
        tools = tools_payload.get("result", {}).get("tools", [])
        if not any(tool.get("name") == "websearch.search" for tool in tools):
            raise HTTPException(status_code=502, detail="WebSearch responded, but the websearch.search MCP tool is missing.")
        return WebSearchTestResponse(ok=True, message="Health endpoint and MCP tool discovery succeeded.")
    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="WebSearch connection test timed out.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"WebSearch connection test failed: {exc}") from exc


@router.post("/websearch/live-test", response_model=WebSearchTestResponse)
async def run_websearch_live_test() -> WebSearchTestResponse:
    config = _websearch_config()
    query = "latest news today"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                config.url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "websearch.search",
                        "arguments": {"query": query},
                    },
                },
            )
            response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise HTTPException(status_code=502, detail=f"WebSearch live test failed: {payload['error']}")
        content = payload.get("result", {}).get("structuredContent", {})
        results = content.get("results", [])
        if not isinstance(results, list) or not results:
            raise HTTPException(status_code=502, detail="WebSearch live test returned no news results.")
        return WebSearchTestResponse(
            ok=True,
            message=f'Live query "{query}" returned {len(results)} results.',
            query=query,
            result_count=len(results),
        )
    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="WebSearch live test timed out.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"WebSearch live test failed: {exc}") from exc


@router.get("/status", response_model=SetupStatusResponse)
async def get_setup_status() -> SetupStatusResponse:
    enabled = os.getenv("CAPYHOME_MANAGED_SETUP_ENABLED", "0") == "true"
    daemon_payload = _read_json(_state_dir() / "daemon-status.json")
    daemon_running = daemon_payload.get("status") == "running"
    docker_state = str(daemon_payload.get("docker") or ("unknown" if enabled else "unsupported"))
    podman_state = str(daemon_payload.get("podman") or ("unknown" if enabled else "unsupported"))
    websearch_runtime = str(daemon_payload.get("websearch") or "unreachable")

    try:
        models = get_app_config().models
        llm_status = SetupComponentStatus(
            status="configured" if models else "missing",
            message=None if models else "Configure an LLM provider to start chatting.",
        )
    except Exception as exc:
        llm_status = SetupComponentStatus(status="unhealthy", message=str(exc))

    mcp = get_extensions_config().mcp_servers.get("websearch")
    mcp_enabled = bool(mcp and mcp.enabled)
    websearch_reachable = websearch_runtime == "healthy" or await _probe_websearch_health(mcp.health_url if mcp else None)
    if websearch_reachable and mcp_enabled:
        websearch_status = SetupComponentStatus(status="healthy")
    elif websearch_reachable:
        websearch_status = SetupComponentStatus(status="needs_registration", message="WebSearch is running but its MCP server is disabled.")
    elif mcp_enabled:
        websearch_status = SetupComponentStatus(status="unhealthy", message="WebSearch MCP is enabled but the service is unreachable.")
    else:
        websearch_status = SetupComponentStatus(status="disabled")

    return SetupStatusResponse(
        managed_setup_enabled=enabled,
        docker=SetupComponentStatus(
            status=docker_state,
            message="Start Docker Desktop and try again." if docker_state == "stopped" else None,
        ),
        podman=SetupComponentStatus(
            status=podman_state,
            message=(
                "Start the Podman machine and try again."
                if podman_state == "stopped"
                else "Install podman-compose and try again."
                if podman_state == "compose_missing"
                else None
            ),
        ),
        daemon=SetupComponentStatus(
            status="running" if daemon_running else ("unavailable" if enabled else "unsupported"),
            message=None if daemon_running else "Run make local-prod to start the managed setup service.",
        ),
        capyhome=SetupComponentStatus(status="healthy"),
        llm=llm_status,
        websearch=websearch_status,
        websearch_replicas=_replica_count(),
        websearch_runtime=_websearch_runtime(),
        latest_job=_latest_job(),
    )


@router.post("/actions", response_model=SetupJobResponse, status_code=202)
async def create_setup_action(request: SetupActionRequest) -> SetupJobResponse:
    if os.getenv("CAPYHOME_MANAGED_SETUP_ENABLED", "0") != "true":
        raise HTTPException(status_code=409, detail="Managed setup is unavailable in this deployment.")

    state_dir = _state_dir()
    daemon_payload = _read_json(state_dir / "daemon-status.json")
    if daemon_payload.get("status") != "running":
        raise HTTPException(status_code=503, detail="Setup service is not running. Run make local-prod and try again.")
    if daemon_payload.get("docker") != "running":
        raise HTTPException(status_code=503, detail="Docker is not running. Start Docker Desktop and try again.")
    websearch_mcp = get_extensions_config().mcp_servers.get("websearch")
    websearch_enabled = (
        daemon_payload.get("websearch") == "healthy"
        or bool(websearch_mcp and websearch_mcp.enabled)
    )
    selected_runtime = (
        "podman"
        if request.action == "websearch_enable_podman"
        else "docker"
        if request.action == "websearch_enable_docker"
        else _websearch_runtime()
    )
    podman_required = selected_runtime == "podman" and (
        request.action in {"websearch_enable_podman", "websearch_repair"}
        or (request.action == "update_all" and websearch_enabled)
    )
    if podman_required and daemon_payload.get("podman") != "running":
        raise HTTPException(status_code=503, detail="Podman is not running. Start the Podman machine and try again.")

    jobs_dir = state_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    if any(jobs_dir.glob("*.request.json")) or any(jobs_dir.glob("*.running.json")):
        raise HTTPException(status_code=409, detail="Another setup operation is already running.")

    job_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    request_payload = {
        "job_id": job_id,
        "action": request.action,
        "status": "queued",
        "created_at": now,
        "websearch_enabled": websearch_enabled,
        "websearch_runtime": selected_runtime,
    }
    request_path = jobs_dir / f"{job_id}.request.json"
    tmp_path = jobs_dir / f".{job_id}.request.tmp"
    tmp_path.write_text(json.dumps(request_payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, request_path)

    status_payload = dict(request_payload)
    status_payload["updated_at"] = now
    status_path = jobs_dir / f"{job_id}.json"
    status_path.write_text(json.dumps(status_payload, indent=2), encoding="utf-8")
    return SetupJobResponse(**status_payload)


@router.get("/jobs/{job_id}", response_model=SetupJobResponse)
async def get_setup_job(job_id: str) -> SetupJobResponse:
    if not job_id.isalnum():
        raise HTTPException(status_code=400, detail="Invalid job id.")
    payload = _read_json(_state_dir() / "jobs" / f"{job_id}.json")
    if not payload:
        raise HTTPException(status_code=404, detail="Setup job not found.")
    return SetupJobResponse(
        job_id=str(payload.get("job_id") or job_id),
        action=str(payload.get("action") or "update_all"),
        status=str(payload.get("status") or "unknown"),
        message=payload.get("message"),
        updated_at=payload.get("updated_at"),
    )
