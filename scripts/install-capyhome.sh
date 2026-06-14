#!/usr/bin/env bash
set -euo pipefail

CAPYHOME_REPOSITORY="${CAPYHOME_REPOSITORY:-https://github.com/yilongchua/CapyHome.git}"
WEBSEARCH_REPOSITORY="${WEBSEARCH_REPOSITORY:-https://github.com/yilongchua/websearch.git}"
INSTALL_ROOT="${CAPYHOME_INSTALL_ROOT:-$HOME/Desktop}"
CAPYHOME_ROOT="$INSTALL_ROOT/CapyHome"
WEBSEARCH_ROOT="$INSTALL_ROOT/websearch"

for command in git docker python3; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "$command is required before installing CapyHome." >&2
        exit 1
    fi
done

if ! docker info >/dev/null 2>&1; then
    echo "Docker is not running. Start Docker Desktop and run this command again." >&2
    exit 1
fi

mkdir -p "$INSTALL_ROOT"

clone_or_validate() {
    local repository="$1"
    local directory="$2"
    local label="$3"
    if [ -d "$directory/.git" ]; then
        if [ "$(git -C "$directory" remote get-url origin)" != "$repository" ]; then
            echo "$label already exists with a different Git origin: $directory" >&2
            exit 1
        fi
        return
    fi
    if [ -e "$directory" ]; then
        echo "$label install path already exists and is not a Git checkout: $directory" >&2
        exit 1
    fi
    git clone "$repository" "$directory"
}

clone_or_validate "$CAPYHOME_REPOSITORY" "$CAPYHOME_ROOT" "CapyHome"
clone_or_validate "$WEBSEARCH_REPOSITORY" "$WEBSEARCH_ROOT" "WebSearch"

export WEBSEARCH_ROOT
"$CAPYHOME_ROOT/scripts/bootstrap-config.sh"
"$CAPYHOME_ROOT/scripts/local-prod.sh" start

echo ""
echo "CapyHome is starting at http://localhost:2026"
