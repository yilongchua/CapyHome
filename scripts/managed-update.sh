#!/usr/bin/env bash
set -euo pipefail

REQUEST_FILE="$1"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$ROOT/.capyhome/setup"
JOBS_DIR="$STATE_DIR/jobs"
MANIFEST="$ROOT/.capyhome-managed.json"
JOB_ID="$(basename "$REQUEST_FILE" .running.json)"
STATUS_FILE="$JOBS_DIR/$JOB_ID.json"
LOG_FILE="$JOBS_DIR/$JOB_ID.log"
ACTION="update_all"
WEBSEARCH_ENABLED="false"
WEBSEARCH_RUNTIME="docker"

exec >>"$LOG_FILE" 2>&1

status() {
    python3 - "$STATUS_FILE" "$1" "${2:-}" "$ACTION" <<'PY'
import json
import sys
from datetime import datetime, timezone

payload = {
    "job_id": sys.argv[1].rsplit("/", 1)[-1].removesuffix(".json"),
    "status": sys.argv[2],
    "message": sys.argv[3] or None,
    "action": sys.argv[4],
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
PY
}

fail() {
    trap - ERR
    status failed "$1"
    rm -f "$REQUEST_FILE"
    exit 1
}

trap 'fail "Update failed. Open diagnostics for details."' ERR

ACTION="$(python3 - "$REQUEST_FILE" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle).get("action", "update_all"))
PY
)"

if ! docker info >/dev/null 2>&1; then
    fail "Docker is not running. Start Docker Desktop and try again."
fi

values=()
while IFS= read -r line; do
    values+=("$line")
done < <(python3 - "$MANIFEST" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
for name in ("capyhome", "websearch"):
    item = data[name]
    print(item["path"])
    print(item["remote"])
    print(item["branch"])
print(data.get("websearch_replicas", 8))
PY
)

CAPY_PATH="${values[0]}"
CAPY_REMOTE="${values[1]}"
CAPY_BRANCH="${values[2]}"
WEB_PATH="${values[3]}"
WEB_REMOTE="${values[4]}"
WEB_BRANCH="${values[5]}"
REPLICAS="${values[6]}"

check_repo() {
    local name="$1"
    local path="$2"
    local remote="$3"
    local branch="$4"
    [ -d "$path/.git" ] || fail "$name checkout is missing."
    [ "$(git -C "$path" remote get-url origin)" = "$remote" ] || fail "$name origin does not match the managed install."
    [ -z "$(git -C "$path" status --porcelain)" ] || fail "$name has uncommitted changes. Commit or stash them before updating."
    git -C "$path" fetch --prune origin "$branch"
    git -C "$path" merge-base --is-ancestor HEAD "origin/$branch" || fail "$name cannot be fast-forwarded."
}
WEBSEARCH_ENABLED="$(python3 - "$REQUEST_FILE" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print("true" if json.load(handle).get("websearch_enabled", False) else "false")
PY
)"
WEBSEARCH_RUNTIME="$(python3 - "$REQUEST_FILE" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle).get("websearch_runtime")
print(value if value in {"docker", "podman"} else "docker")
PY
)"

if [ "$WEBSEARCH_RUNTIME" = "podman" ] && { [ "$WEBSEARCH_ENABLED" = "true" ] || [ "$ACTION" = "websearch_enable_podman" ] || [ "$ACTION" = "websearch_repair" ]; }; then
    if ! command -v podman >/dev/null 2>&1 || ! podman info >/dev/null 2>&1; then
        fail "Podman is not running. Start the Podman machine and try again."
    fi
    if ! podman compose version >/dev/null 2>&1 && ! command -v podman-compose >/dev/null 2>&1; then
        fail "Podman Compose is unavailable. Install podman-compose and try again."
    fi
fi

podman_compose() {
    if podman compose version >/dev/null 2>&1; then
        podman compose -p capyhome-websearch -f "$CAPY_PATH/docker/docker-compose.websearch.podman.yaml" "$@"
    else
        podman-compose -p capyhome-websearch -f "$CAPY_PATH/docker/docker-compose.websearch.podman.yaml" "$@"
    fi
}

if [ "$ACTION" = "update_all" ]; then
    check_repo capyhome "$CAPY_PATH" "$CAPY_REMOTE" "$CAPY_BRANCH"
    check_repo websearch "$WEB_PATH" "$WEB_REMOTE" "$WEB_BRANCH"
    python3 - "$STATE_DIR/update-state.json" "$CAPY_PATH" "$CAPY_BRANCH" "$WEB_PATH" "$WEB_BRANCH" <<'PY'
import json
import subprocess
import sys
from datetime import datetime, timezone

def sha(path: str, ref: str) -> str:
    return subprocess.check_output(["git", "-C", path, "rev-parse", ref], text=True).strip()

payload = {
    "status": "prepared",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "capyhome": {
        "old_sha": sha(sys.argv[2], "HEAD"),
        "target_sha": sha(sys.argv[2], f"origin/{sys.argv[3]}"),
    },
    "websearch": {
        "old_sha": sha(sys.argv[4], "HEAD"),
        "target_sha": sha(sys.argv[4], f"origin/{sys.argv[5]}"),
    },
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
PY
    status updating_repositories "Updating CapyHome and WebSearch."
    git -C "$CAPY_PATH" merge --ff-only "origin/$CAPY_BRANCH"
    git -C "$WEB_PATH" merge --ff-only "origin/$WEB_BRANCH"
fi

export CAPYHOME_ROOT="$CAPY_PATH"
export WEBSEARCH_ROOT="$WEB_PATH"
export WEBSEARCH_REPLICAS="$REPLICAS"

if [ "$ACTION" = "websearch_disable" ]; then
    docker compose --env-file "$CAPY_PATH/.env" -p capyhome \
        -f "$CAPY_PATH/docker/docker-compose.prod.yaml" \
        -f "$CAPY_PATH/docker/docker-compose.websearch.yaml" \
        stop websearch websearch-proxy dashboard-logs
    if command -v podman >/dev/null 2>&1; then
        podman_compose stop websearch websearch-proxy dashboard-logs >/dev/null 2>&1 || true
    fi
    "$CAPY_PATH/scripts/configure-websearch-mcp.py" disable
    status succeeded "WebSearch disabled."
    rm -f "$REQUEST_FILE"
    exit 0
fi

status building "Building and restarting services."
if [ "$ACTION" = "update_all" ] && [ "$WEBSEARCH_ENABLED" != "true" ]; then
    docker compose --env-file "$CAPY_PATH/.env" -p capyhome \
        -f "$CAPY_PATH/docker/docker-compose.prod.yaml" \
        up -d --build --remove-orphans
else
    if [ "$WEBSEARCH_RUNTIME" = "podman" ]; then
        docker compose --env-file "$CAPY_PATH/.env" -p capyhome \
            -f "$CAPY_PATH/docker/docker-compose.prod.yaml" \
            -f "$CAPY_PATH/docker/docker-compose.websearch.yaml" \
            stop websearch websearch-proxy dashboard-logs >/dev/null 2>&1 || true
        podman_compose up -d --build --scale "websearch=$REPLICAS" --remove-orphans
        podman_compose up -d --no-deps --force-recreate websearch-proxy
    else
        if command -v podman >/dev/null 2>&1; then
            podman_compose stop websearch websearch-proxy dashboard-logs >/dev/null 2>&1 || true
        fi
        docker compose --env-file "$CAPY_PATH/.env" -p capyhome \
            -f "$CAPY_PATH/docker/docker-compose.prod.yaml" \
            -f "$CAPY_PATH/docker/docker-compose.websearch.yaml" \
            up -d --build --scale "websearch=$REPLICAS" --remove-orphans
    fi
    "$CAPY_PATH/scripts/configure-websearch-mcp.py" enable "$WEBSEARCH_RUNTIME"
fi

status checking_health "Waiting for CapyHome and WebSearch."
for _ in $(seq 1 90); do
    capyhome_healthy=false
    websearch_healthy=false
    if curl -fsS http://localhost:2026/health >/dev/null 2>&1; then
        capyhome_healthy=true
    fi
    if curl -fsS http://localhost:9000/health >/dev/null 2>&1; then
        websearch_healthy=true
    fi
    if [ "$capyhome_healthy" = "true" ] && { [ "$WEBSEARCH_ENABLED" != "true" ] && [ "$ACTION" = "update_all" ] || [ "$websearch_healthy" = "true" ]; }; then
        if [ "$websearch_healthy" = "true" ]; then
            curl -fsS \
                -H "Content-Type: application/json" \
                -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
                http://localhost:9000/mcp | grep -q "websearch.search"
            python3 "$CAPY_PATH/scripts/check-websearch-replicas.py" \
                --url http://localhost:9000/health \
                --replicas "$REPLICAS" \
                --requests "$((REPLICAS * 8))"
        fi
        if [ -f "$STATE_DIR/update-state.json" ]; then
            python3 - "$STATE_DIR/update-state.json" <<'PY'
import json
import sys
from datetime import datetime, timezone
path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
payload["status"] = "succeeded"
payload["completed_at"] = datetime.now(timezone.utc).isoformat()
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
PY
        fi
        status succeeded "Update complete."
        rm -f "$REQUEST_FILE"
        exit 0
    fi
    sleep 2
done

fail "Services did not become healthy after the update."
