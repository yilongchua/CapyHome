#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    enabled = len(sys.argv) > 1 and sys.argv[1] == "enable"
    runtime = sys.argv[2] if len(sys.argv) > 2 else "docker"
    root = Path(os.environ.get("CAPYHOME_ROOT") or Path(__file__).resolve().parents[1])
    path = root / "extensions_config.json"
    data: dict = {}
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)

    servers = data.setdefault("mcpServers", {})
    current = dict(servers.get("websearch") or {})
    current.update(
        {
            "enabled": enabled,
            "type": "http",
            "url": (
                "http://host.docker.internal:9000/mcp"
                if runtime == "podman"
                else "http://websearch-proxy:9000/mcp"
            ),
            "health_url": (
                "http://host.docker.internal:9000/health"
                if runtime == "podman"
                else "http://websearch-proxy:9000/health"
            ),
            "timeout_seconds": 25,
            "description": "Local WebSearch MCP managed by CapyHome",
        }
    )
    servers["websearch"] = current

    tmp_path = path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(tmp_path, path)

    manifest_path = root / ".capyhome-managed.json"
    if enabled and runtime in {"docker", "podman"} and manifest_path.exists():
        with manifest_path.open(encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["websearch_runtime"] = runtime
        manifest_tmp = manifest_path.with_suffix(".json.tmp")
        with manifest_tmp.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
            handle.write("\n")
        os.replace(manifest_tmp, manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
