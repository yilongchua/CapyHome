#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$ROOT/.capyhome/setup"
MANIFEST="$ROOT/.capyhome-managed.json"
WEBSEARCH_ROOT="${WEBSEARCH_ROOT:-$(cd "$ROOT/.." && pwd)/websearch}"
WEBSEARCH_REPLICAS="${WEBSEARCH_REPLICAS:-8}"
CORE_COMPOSE=(docker compose --env-file "$ROOT/.env" -p capyhome -f "$ROOT/docker/docker-compose.prod.yaml")
FULL_COMPOSE=("${CORE_COMPOSE[@]}" -f "$ROOT/docker/docker-compose.websearch.yaml")

export CAPYHOME_ROOT="$ROOT"
export WEBSEARCH_ROOT
export WEBSEARCH_REPLICAS

require_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        echo "Docker is not installed. Install Docker Desktop and try again." >&2
        exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
        echo "Docker is not running. Start Docker Desktop and try again." >&2
        exit 1
    fi
    if ! docker compose version >/dev/null 2>&1; then
        echo "Docker Compose is unavailable. Update Docker Desktop and try again." >&2
        exit 1
    fi
}

require_podman() {
    if ! command -v podman >/dev/null 2>&1; then
        echo "Podman is not installed. Install Podman Desktop and try again." >&2
        exit 1
    fi
    if ! podman info >/dev/null 2>&1; then
        echo "Podman is not running. Start the Podman machine and try again." >&2
        exit 1
    fi
    if ! podman compose version >/dev/null 2>&1 && ! command -v podman-compose >/dev/null 2>&1; then
        echo "Podman Compose is unavailable. Install podman-compose and try again." >&2
        exit 1
    fi
}

podman_compose() {
    if podman compose version >/dev/null 2>&1; then
        podman compose -p capyhome-websearch -f "$ROOT/docker/docker-compose.websearch.podman.yaml" "$@"
    else
        podman-compose -p capyhome-websearch -f "$ROOT/docker/docker-compose.websearch.podman.yaml" "$@"
    fi
}

write_manifest() {
    mkdir -p "$STATE_DIR"
    if [ ! -f "$MANIFEST" ]; then
        python3 - "$MANIFEST" "$ROOT" "$WEBSEARCH_ROOT" "$WEBSEARCH_REPLICAS" <<'PY'
import json
import subprocess
import sys

def remote(path: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", path, "remote", "get-url", "origin"], text=True).strip()
    except Exception:
        return ""

def branch(path: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", path, "branch", "--show-current"], text=True).strip() or "main"
    except Exception:
        return "main"

manifest = {
    "capyhome": {"path": sys.argv[2], "remote": remote(sys.argv[2]), "branch": branch(sys.argv[2])},
    "websearch": {"path": sys.argv[3], "remote": remote(sys.argv[3]), "branch": branch(sys.argv[3])},
    "websearch_replicas": int(sys.argv[4]),
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2)
PY
    fi
}

start_daemon() {
    mkdir -p "$STATE_DIR"
    if [ -f "$STATE_DIR/daemon.pid" ] && kill -0 "$(cat "$STATE_DIR/daemon.pid")" >/dev/null 2>&1; then
        return
    fi
    nohup "$ROOT/scripts/setup-daemon.sh" >"$STATE_DIR/daemon.log" 2>&1 &
    echo $! > "$STATE_DIR/daemon.pid"
}

stop_daemon() {
    if [ -f "$STATE_DIR/daemon.pid" ]; then
        pid="$(cat "$STATE_DIR/daemon.pid")"
        if kill -0 "$pid" >/dev/null 2>&1; then
            kill "$pid" >/dev/null 2>&1 || true
        fi
        rm -f "$STATE_DIR/daemon.pid"
    fi
}

doctor() {
    require_docker
    "$ROOT/scripts/bootstrap-config.sh"
    if [ ! -d "$ROOT/.git" ]; then
        echo "CapyHome is not a Git checkout: $ROOT." >&2
        exit 1
    fi
    if [ ! -d "$WEBSEARCH_ROOT/.git" ]; then
        echo "WebSearch checkout not found at $WEBSEARCH_ROOT." >&2
        exit 1
    fi
    test -f "$WEBSEARCH_ROOT/Dockerfile"
    test -f "$WEBSEARCH_ROOT/config.yaml"
    test -f "$WEBSEARCH_ROOT/searxng-settings.yml"
    write_manifest
    echo "Local production prerequisites are ready."
}

case "${1:-start}" in
    start)
        doctor
        start_daemon
        "${CORE_COMPOSE[@]}" up -d --build
        echo "CapyHome is starting at http://localhost:2026"
        ;;
    stop)
        require_docker
        "${FULL_COMPOSE[@]}" down --remove-orphans
        if command -v podman >/dev/null 2>&1; then
            podman_compose down --remove-orphans >/dev/null 2>&1 || true
        fi
        stop_daemon
        ;;
    logs)
        require_docker
        "${FULL_COMPOSE[@]}" logs -f
        ;;
    doctor)
        doctor
        ;;
    websearch-enable)
        doctor
        podman_compose down --remove-orphans >/dev/null 2>&1 || true
        "${FULL_COMPOSE[@]}" up -d --build --scale "websearch=$WEBSEARCH_REPLICAS" websearch websearch-proxy dashboard-logs
        "$ROOT/scripts/configure-websearch-mcp.py" enable docker
        ;;
    websearch-enable-podman)
        doctor
        require_podman
        "${FULL_COMPOSE[@]}" stop websearch websearch-proxy dashboard-logs >/dev/null 2>&1 || true
        podman_compose up -d --build --scale "websearch=$WEBSEARCH_REPLICAS" websearch websearch-proxy dashboard-logs
        podman_compose up -d --no-deps --force-recreate websearch-proxy
        "$ROOT/scripts/configure-websearch-mcp.py" enable podman
        ;;
    websearch-disable)
        require_docker
        "${FULL_COMPOSE[@]}" stop websearch websearch-proxy dashboard-logs
        if command -v podman >/dev/null 2>&1; then
            podman_compose stop websearch websearch-proxy dashboard-logs >/dev/null 2>&1 || true
        fi
        "$ROOT/scripts/configure-websearch-mcp.py" disable
        ;;
    *)
        echo "Usage: $0 {start|stop|logs|doctor|websearch-enable|websearch-enable-podman|websearch-disable}" >&2
        exit 2
        ;;
esac
