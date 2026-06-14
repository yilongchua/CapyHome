from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.config.extensions_config import ExtensionsConfig, McpServerConfig
from src.gateway.routers import setup


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self, *, get_response: _FakeResponse | None = None, post_response: _FakeResponse | None = None) -> None:
        self.get_response = get_response
        self.post_response = post_response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def get(self, _url: str) -> _FakeResponse:
        assert self.get_response is not None
        return self.get_response

    async def post(self, _url: str, **_kwargs) -> _FakeResponse:
        assert self.post_response is not None
        return self.post_response


def _websearch_extensions() -> ExtensionsConfig:
    return ExtensionsConfig(
        mcp_servers={
            "websearch": McpServerConfig(
                enabled=True,
                type="http",
                url="http://localhost:9000/mcp",
                health_url="http://localhost:9000/health",
            )
        }
    )


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_dir = tmp_path / "setup"
    manifest = tmp_path / "managed.json"
    manifest.write_text(json.dumps({"websearch_replicas": 8}), encoding="utf-8")
    monkeypatch.setenv("CAPYHOME_SETUP_STATE_DIR", str(state_dir))
    monkeypatch.setenv("CAPYHOME_MANAGED_INSTALL_PATH", str(manifest))
    monkeypatch.setenv("CAPYHOME_MANAGED_SETUP_ENABLED", "true")
    return state_dir


def test_create_setup_action_writes_atomic_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = _configure(tmp_path, monkeypatch)
    state_dir.mkdir(parents=True)
    (state_dir / "daemon-status.json").write_text(
        json.dumps({"status": "running", "docker": "running", "websearch": "healthy"}),
        encoding="utf-8",
    )

    result = asyncio.run(setup.create_setup_action(setup.SetupActionRequest(action="update_all")))

    assert result.status == "queued"
    request_path = state_dir / "jobs" / f"{result.job_id}.request.json"
    assert request_path.exists()
    assert json.loads(request_path.read_text())["action"] == "update_all"


def test_create_setup_action_returns_simple_docker_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = _configure(tmp_path, monkeypatch)
    state_dir.mkdir(parents=True)
    (state_dir / "daemon-status.json").write_text(
        json.dumps({"status": "running", "docker": "stopped"}),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(setup.create_setup_action(setup.SetupActionRequest(action="update_all")))

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Docker is not running. Start Docker Desktop and try again."


def test_podman_action_requires_running_podman(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = _configure(tmp_path, monkeypatch)
    state_dir.mkdir(parents=True)
    (state_dir / "daemon-status.json").write_text(
        json.dumps({"status": "running", "docker": "running", "podman": "stopped"}),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            setup.create_setup_action(
                setup.SetupActionRequest(action="websearch_enable_podman")
            )
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Podman is not running. Start the Podman machine and try again."


def test_podman_action_records_selected_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = _configure(tmp_path, monkeypatch)
    state_dir.mkdir(parents=True)
    (state_dir / "daemon-status.json").write_text(
        json.dumps(
            {
                "status": "running",
                "docker": "running",
                "podman": "running",
                "websearch": "unreachable",
            }
        ),
        encoding="utf-8",
    )

    result = asyncio.run(
        setup.create_setup_action(
            setup.SetupActionRequest(action="websearch_enable_podman")
        )
    )

    payload = json.loads(
        (state_dir / "jobs" / f"{result.job_id}.request.json").read_text()
    )
    assert payload["websearch_runtime"] == "podman"


def test_replica_count_defaults_and_clamps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(tmp_path, monkeypatch)
    Path(setup._manifest_path()).write_text(json.dumps({"websearch_replicas": 99}), encoding="utf-8")
    assert setup._replica_count() == 32


def test_status_uses_direct_websearch_health_without_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(setup, "get_extensions_config", _websearch_extensions)
    probe = AsyncMock(return_value=True)
    monkeypatch.setattr(setup, "_probe_websearch_health", probe)

    result = asyncio.run(setup.get_setup_status())

    assert result.websearch.status == "healthy"
    probe.assert_awaited_once_with("http://localhost:9000/health")


def test_status_marks_enabled_unreachable_websearch_unhealthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(setup, "get_extensions_config", _websearch_extensions)
    monkeypatch.setattr(setup, "_probe_websearch_health", AsyncMock(return_value=False))

    result = asyncio.run(setup.get_setup_status())

    assert result.websearch.status == "unhealthy"


def test_websearch_connection_checks_health_and_mcp_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup, "get_extensions_config", _websearch_extensions)
    client = _FakeHttpClient(
        get_response=_FakeResponse({"ok": "true"}),
        post_response=_FakeResponse(
            {"result": {"tools": [{"name": "websearch.search"}]}}
        ),
    )
    monkeypatch.setattr(setup.httpx, "AsyncClient", lambda **_kwargs: client)

    result = asyncio.run(setup.test_websearch_connection())

    assert result.ok is True
    assert result.message == "Health endpoint and MCP tool discovery succeeded."


def test_websearch_live_test_requires_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup, "get_extensions_config", _websearch_extensions)
    client = _FakeHttpClient(
        post_response=_FakeResponse(
            {
                "result": {
                    "structuredContent": {
                        "query": "latest news today",
                        "results": [{"title": "News", "url": "https://example.com"}],
                    }
                }
            }
        )
    )
    monkeypatch.setattr(setup.httpx, "AsyncClient", lambda **_kwargs: client)

    result = asyncio.run(setup.run_websearch_live_test())

    assert result.ok is True
    assert result.query == "latest news today"
    assert result.result_count == 1
