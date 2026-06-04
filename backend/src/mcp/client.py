"""MCP client using langchain-mcp-adapters."""

import logging
from datetime import timedelta
from typing import Any

from src.config.extensions_config import ExtensionsConfig, McpServerConfig

logger = logging.getLogger(__name__)


def build_server_params(server_name: str, config: McpServerConfig) -> dict[str, Any]:
    """Build server parameters for MultiServerMCPClient.

    Args:
        server_name: Name of the MCP server.
        config: Configuration for the MCP server.

    Returns:
        Dictionary of server parameters for langchain-mcp-adapters.
    """
    transport_type = config.type or "stdio"
    params: dict[str, Any] = {"transport": transport_type}

    if transport_type == "stdio":
        if not config.command:
            raise ValueError(f"MCP server '{server_name}' with stdio transport requires 'command' field")
        params["command"] = config.command
        params["args"] = config.args
        # Add environment variables if present
        if config.env:
            params["env"] = config.env
    elif transport_type in ("sse", "http"):
        if not config.url:
            raise ValueError(f"MCP server '{server_name}' with {transport_type} transport requires 'url' field")
        params["url"] = config.url
        # Add headers if present
        if config.headers:
            params["headers"] = config.headers
        # Per-server timeout: bound both the HTTP request and the SSE read wait
        # so a hung server (e.g. websearch.search) can't block a tool call past
        # this many seconds. Without this the langchain-mcp-adapters defaults
        # apply (HTTP 5-30s but sse_read_timeout 300s), which is what let a
        # 231s websearch.search call slip past. langchain-mcp-adapters expects
        # timedelta for streamable-HTTP ("http") and float seconds for "sse".
        if config.timeout_seconds is not None:
            if transport_type == "http":
                params["timeout"] = timedelta(seconds=config.timeout_seconds)
                params["sse_read_timeout"] = timedelta(seconds=config.timeout_seconds)
            else:  # sse
                params["timeout"] = float(config.timeout_seconds)
                params["sse_read_timeout"] = float(config.timeout_seconds)
    else:
        raise ValueError(f"MCP server '{server_name}' has unsupported transport type: {transport_type}")

    return params


def build_servers_config(extensions_config: ExtensionsConfig) -> dict[str, dict[str, Any]]:
    """Build servers configuration for MultiServerMCPClient.

    Args:
        extensions_config: Extensions configuration containing all MCP servers.

    Returns:
        Dictionary mapping server names to their parameters.
    """
    enabled_servers = extensions_config.get_enabled_mcp_servers()

    if not enabled_servers:
        logger.info("No enabled MCP servers found")
        return {}

    servers_config = {}
    for server_name, server_config in enabled_servers.items():
        try:
            servers_config[server_name] = build_server_params(server_name, server_config)
            logger.info(f"Configured MCP server: {server_name}")
        except Exception as e:
            logger.error(f"Failed to configure MCP server '{server_name}': {e}")

    return servers_config
