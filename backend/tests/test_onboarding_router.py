"""Tests for the onboarding Gateway router.

Covers the bug fixes from the post-evaluation:
- /v1/models URL construction (no double-/v1, no missing-/v1)
- save preserves unknown top-level keys in extensions_config.json
- save uses a resolvable path even when no config exists yet
- /test-generic rejects non-http schemes and metadata endpoints
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.config import extensions_config as ext_module
from src.gateway.routers import onboarding

# ─── /v1/models URL construction ─────────────────────────────────────────────


def test_test_llm_appends_v1_models_when_missing():
    """base_url without /v1 should be probed at /v1/models."""
    captured: dict[str, Any] = {}

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"data": [{"id": "llama3"}]}

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get(self, url: str, headers: dict | None = None) -> _Response:
            captured["url"] = url
            return _Response()

    with patch("src.gateway.routers.onboarding.httpx.AsyncClient", _Client):
        result = asyncio.run(
            onboarding.test_llm_endpoint(
                onboarding.TestLlmRequest(base_url="http://localhost:11434", api_key="")
            )
        )

    assert result.ok is True
    assert result.models == ["llama3"]
    assert captured["url"] == "http://localhost:11434/v1/models"


def test_test_llm_does_not_double_v1():
    """base_url ending in /v1 should hit /v1/models exactly once."""
    captured: dict[str, Any] = {}

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"data": []}

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get(self, url: str, headers: dict | None = None) -> _Response:
            captured["url"] = url
            return _Response()

    with patch("src.gateway.routers.onboarding.httpx.AsyncClient", _Client):
        asyncio.run(
            onboarding.test_llm_endpoint(
                onboarding.TestLlmRequest(base_url="http://localhost:11434/v1/", api_key="")
            )
        )

    assert captured["url"] == "http://localhost:11434/v1/models"


# ─── save_user_models preserves on-disk extras ───────────────────────────────


def test_save_preserves_unknown_top_level_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Saving user models must not drop unknown/extra keys already in the file."""
    cfg_path = tmp_path / "extensions_config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "mcpServers": {"foo": {"enabled": True, "type": "stdio", "command": "echo"}},
                "skills": {"sk": {"enabled": True}},
                "experimental_future_field": {"keep_me": 1},
            }
        )
    )

    monkeypatch.setenv("CAPYBARA_HOME_EXTENSIONS_CONFIG_PATH", str(cfg_path))
    ext_module.reset_extensions_config()

    user_models = {
        "ollama": onboarding.UserLlmEndpointConfig(
            provider="ollama",
            display_name="Local Ollama",
            base_url="http://localhost:11434/v1",
        )
    }

    with patch.object(onboarding, "reload_extensions_config", lambda *a, **k: None):
        onboarding._save_extensions_with_user_models(user_models)

    written = json.loads(cfg_path.read_text())
    assert "experimental_future_field" in written
    assert written["experimental_future_field"] == {"keep_me": 1}
    assert written["mcpServers"]["foo"]["command"] == "echo"
    assert written["userModels"]["ollama"]["base_url"] == "http://localhost:11434/v1"


def test_save_creates_file_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When no config exists, save should create one at the resolved path."""
    cfg_path = tmp_path / "nested" / "extensions_config.json"
    monkeypatch.setenv("CAPYBARA_HOME_EXTENSIONS_CONFIG_PATH", str(cfg_path))
    ext_module.reset_extensions_config()

    user_models = {
        "lm": onboarding.UserLlmEndpointConfig(
            provider="lm-studio",
            display_name="LM",
            base_url="http://localhost:1234/v1",
        )
    }

    with patch.object(onboarding, "reload_extensions_config", lambda *a, **k: None):
        onboarding._save_extensions_with_user_models(user_models)

    assert cfg_path.exists()
    written = json.loads(cfg_path.read_text())
    assert written["userModels"]["lm"]["display_name"] == "LM"


# ─── /test-generic SSRF guard ────────────────────────────────────────────────


def test_test_generic_rejects_non_http_scheme():
    result = asyncio.run(
        onboarding.test_generic_endpoint(
            onboarding.TestGenericRequest(url="file:///etc/passwd")
        )
    )
    assert result.ok is False
    assert result.error is not None
    assert "scheme" in result.error.lower()


def test_test_generic_rejects_aws_metadata_host():
    result = asyncio.run(
        onboarding.test_generic_endpoint(
            onboarding.TestGenericRequest(url="http://169.254.169.254/latest/meta-data/")
        )
    )
    assert result.ok is False
    assert result.error is not None
    assert "metadata" in result.error.lower()


def test_test_generic_rejects_gcp_metadata_host():
    result = asyncio.run(
        onboarding.test_generic_endpoint(
            onboarding.TestGenericRequest(url="http://metadata.google.internal/computeMetadata/v1/")
        )
    )
    assert result.ok is False
    assert result.error is not None
    assert "metadata" in result.error.lower()


def test_test_generic_allows_loopback():
    """Localhost probes must still work — that's the whole point of this tool."""

    class _Response:
        status_code = 200
        is_success = True

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get(self, url: str) -> _Response:
            return _Response()

    with patch("src.gateway.routers.onboarding.httpx.AsyncClient", _Client):
        result = asyncio.run(
            onboarding.test_generic_endpoint(
                onboarding.TestGenericRequest(url="http://127.0.0.1:8188/system_stats")
            )
        )

    assert result.ok is True
    assert result.status_code == 200
