"""Core behavior tests for MCP client server config building."""

from datetime import timedelta

import pytest

from src.config.extensions_config import ExtensionsConfig, McpServerConfig
from src.mcp.client import build_server_params, build_servers_config


def test_build_server_params_stdio_success():
    config = McpServerConfig(
        type="stdio",
        command="npx",
        args=["-y", "my-mcp-server"],
        env={"API_KEY": "secret"},
    )

    params = build_server_params("my-server", config)

    assert params == {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "my-mcp-server"],
        "env": {"API_KEY": "secret"},
    }


def test_build_server_params_stdio_requires_command():
    config = McpServerConfig(type="stdio", command=None)

    with pytest.raises(ValueError, match="requires 'command' field"):
        build_server_params("broken-stdio", config)


@pytest.mark.parametrize("transport", ["sse", "http"])
def test_build_server_params_http_like_success(transport: str):
    config = McpServerConfig(
        type=transport,
        url="https://example.com/mcp",
        headers={"Authorization": "Bearer token"},
    )

    params = build_server_params("remote-server", config)

    assert params == {
        "transport": transport,
        "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer token"},
    }


def test_build_server_params_http_timeout_uses_timedelta():
    # streamable-HTTP ("http") expects timedelta for timeout/sse_read_timeout.
    config = McpServerConfig(type="http", url="http://localhost:9000/mcp", timeout_seconds=40)

    params = build_server_params("websearch", config)

    assert params["timeout"] == timedelta(seconds=40)
    assert params["sse_read_timeout"] == timedelta(seconds=40)


def test_build_server_params_sse_timeout_uses_float_seconds():
    # SSE transport expects float seconds, not timedelta.
    config = McpServerConfig(type="sse", url="http://example.com/sse", timeout_seconds=40)

    params = build_server_params("remote", config)

    assert params["timeout"] == 40.0
    assert params["sse_read_timeout"] == 40.0


@pytest.mark.parametrize("transport", ["sse", "http"])
def test_build_server_params_no_timeout_leaves_defaults(transport: str):
    # Null timeout_seconds must not inject keys, so adapter defaults apply.
    config = McpServerConfig(type=transport, url="http://example.com/mcp")

    params = build_server_params("remote", config)

    assert "timeout" not in params
    assert "sse_read_timeout" not in params


def test_build_server_params_stdio_ignores_timeout():
    # timeout_seconds is only meaningful for sse/http; stdio must not carry it.
    config = McpServerConfig(type="stdio", command="npx", args=["server"], timeout_seconds=40)

    params = build_server_params("local", config)

    assert "timeout" not in params
    assert "sse_read_timeout" not in params


@pytest.mark.parametrize("transport", ["sse", "http"])
def test_build_server_params_http_like_requires_url(transport: str):
    config = McpServerConfig(type=transport, url=None)

    with pytest.raises(ValueError, match="requires 'url' field"):
        build_server_params("broken-remote", config)


def test_build_server_params_rejects_unsupported_transport():
    config = McpServerConfig(type="websocket")

    with pytest.raises(ValueError, match="unsupported transport type"):
        build_server_params("bad-transport", config)


def test_build_servers_config_returns_empty_when_no_enabled_servers():
    extensions = ExtensionsConfig(
        mcp_servers={
            "disabled-a": McpServerConfig(enabled=False, type="stdio", command="echo"),
            "disabled-b": McpServerConfig(enabled=False, type="http", url="https://example.com"),
        },
        skills={},
    )

    assert build_servers_config(extensions) == {}


def test_build_servers_config_skips_invalid_server_and_keeps_valid_ones():
    extensions = ExtensionsConfig(
        mcp_servers={
            "valid-stdio": McpServerConfig(enabled=True, type="stdio", command="npx", args=["server"]),
            "invalid-stdio": McpServerConfig(enabled=True, type="stdio", command=None),
            "disabled-http": McpServerConfig(enabled=False, type="http", url="https://disabled.example.com"),
        },
        skills={},
    )

    result = build_servers_config(extensions)

    assert "valid-stdio" in result
    assert result["valid-stdio"]["transport"] == "stdio"
    assert "invalid-stdio" not in result
    assert "disabled-http" not in result
