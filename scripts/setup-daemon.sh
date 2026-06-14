#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$ROOT/.capyhome/setup"
JOBS_DIR="$STATE_DIR/jobs"
mkdir -p "$JOBS_DIR"

write_daemon_status() {
    local docker_status="missing"
    local podman_status="missing"
    local websearch_status="unreachable"
    if command -v docker >/dev/null 2>&1; then
        docker_status="stopped"
        if docker info >/dev/null 2>&1; then
            docker_status="running"
        fi
    fi
    if command -v podman >/dev/null 2>&1; then
        podman_status="stopped"
        if podman info >/dev/null 2>&1; then
            if podman compose version >/dev/null 2>&1 || command -v podman-compose >/dev/null 2>&1; then
                podman_status="running"
            else
                podman_status="compose_missing"
            fi
        fi
    fi
    if curl -fsS http://localhost:9000/health >/dev/null 2>&1; then
        websearch_status="healthy"
    fi
    printf '{"status":"running","pid":%s,"docker":"%s","podman":"%s","websearch":"%s","updated_at":"%s"}\n' \
        "$$" "$docker_status" "$podman_status" "$websearch_status" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_DIR/daemon-status.json"
}

trap 'printf "{\"status\":\"stopped\",\"updated_at\":\"%s\"}\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_DIR/daemon-status.json"' EXIT

while true; do
    write_daemon_status
    for request in "$JOBS_DIR"/*.request.json; do
        [ -e "$request" ] || break
        job_id="$(basename "$request" .request.json)"
        running="$JOBS_DIR/$job_id.running.json"
        if ! mv "$request" "$running" 2>/dev/null; then
            continue
        fi
        "$ROOT/scripts/managed-update.sh" "$running" || true
    done
    sleep 2
done
