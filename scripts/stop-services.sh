#!/usr/bin/env bash
#
# stop-services.sh - Stop local CapyHome development services and orphaned workers.
#
# Must be run from the repo root or from within the repo.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTS=(2026 2024 8001 3000)
SANDBOX_PREFIXES=(capyhome-sandbox capybara-home-sandbox)

pid_command() {
    ps -p "$1" -o command= 2>/dev/null || true
}

pid_cwd() {
    lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1
}

is_repo_pid() {
    local pid="$1"
    local cmd cwd
    cmd="$(pid_command "$pid")"
    cwd="$(pid_cwd "$pid")"

    case "$cmd" in
        *"$REPO_ROOT"*|*"src.gateway.app:app"*|*"langgraph dev"*|*"next dev"*|*"next-server"*|*"docker/nginx/nginx.local.conf"*)
            return 0
            ;;
    esac

    case "$cwd" in
        "$REPO_ROOT"|"$REPO_ROOT"/*)
            return 0
            ;;
    esac

    return 1
}

append_pid() {
    local pid="$1"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        STOP_PIDS="${STOP_PIDS:-} $pid"
    fi
}

append_matching_repo_pids() {
    local pattern="$1"
    local pid
    while IFS= read -r pid; do
        if is_repo_pid "$pid"; then
            append_pid "$pid"
        fi
    done < <(pgrep -f "$pattern" 2>/dev/null || true)
}

append_port_listener_pids() {
    local port pid
    for port in "${PORTS[@]}"; do
        while IFS= read -r pid; do
            if is_repo_pid "$pid"; then
                append_pid "$pid"
            fi
        done < <(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
    done
}

append_lmstudio_repo_client_pids() {
    local line pid
    while IFS= read -r line; do
        pid="$(awk '{print $2}' <<<"$line")"
        if [ -n "$pid" ] && is_repo_pid "$pid"; then
            append_pid "$pid"
        fi
    done < <(lsof -nP -iTCP:1234 -sTCP:ESTABLISHED 2>/dev/null | awk 'NR > 1 && $1 != "LM Stu" && $1 != "LM\\x20Stu"')
}

dedupe_pids() {
    tr ' ' '\n' <<<"${STOP_PIDS:-}" | awk 'NF && !seen[$0]++'
}

kill_collected_pids() {
    local pids
    pids="$(dedupe_pids)"
    if [ -z "$pids" ]; then
        return 0
    fi

    echo "$pids" | xargs kill 2>/dev/null || true
    sleep 1

    local still_running=()
    local pid
    while IFS= read -r pid; do
        if kill -0 "$pid" 2>/dev/null; then
            still_running+=("$pid")
        fi
    done <<<"$pids"

    if [ "${#still_running[@]}" -gt 0 ]; then
        kill -9 "${still_running[@]}" 2>/dev/null || true
    fi
}

stop_nginx() {
    nginx -c "$REPO_ROOT/docker/nginx/nginx.local.conf" -p "$REPO_ROOT" -s quit 2>/dev/null || true
    sleep 1
    append_matching_repo_pids "nginx.*$REPO_ROOT"
}

cleanup_containers() {
    local prefix
    for prefix in "${SANDBOX_PREFIXES[@]}"; do
        "$REPO_ROOT/scripts/cleanup-containers.sh" "$prefix" 2>/dev/null || true
    done
}

main() {
    echo "Stopping all services..."

    STOP_PIDS=""
    append_matching_repo_pids "make dev"
    append_matching_repo_pids "make start"
    append_matching_repo_pids "scripts/serve.sh"
    append_matching_repo_pids "scripts/start-daemon.sh"
    append_matching_repo_pids "langgraph dev"
    append_matching_repo_pids "uvicorn src.gateway.app:app"
    append_matching_repo_pids "next dev"
    append_matching_repo_pids "next start"
    append_matching_repo_pids "next-server"
    append_port_listener_pids
    append_lmstudio_repo_client_pids
    stop_nginx
    kill_collected_pids

    STOP_PIDS=""
    append_port_listener_pids
    append_lmstudio_repo_client_pids
    kill_collected_pids

    echo "Cleaning up sandbox containers..."
    cleanup_containers
    echo "✓ All services stopped"
}

main "$@"
