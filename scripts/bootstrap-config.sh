#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

copy_if_missing() {
    local source="$1"
    local target="$2"
    if [ ! -e "$target" ]; then
        cp "$source" "$target"
        echo "Created ${target#$ROOT/}"
    fi
}

copy_if_missing "$ROOT/config.example.yaml" "$ROOT/config.yaml"
copy_if_missing "$ROOT/extensions_config.example.json" "$ROOT/extensions_config.json"
copy_if_missing "$ROOT/.env.example" "$ROOT/.env"
copy_if_missing "$ROOT/frontend/.env.example" "$ROOT/frontend/.env"

if ! grep -q '^BETTER_AUTH_SECRET=' "$ROOT/.env"; then
    if command -v openssl >/dev/null 2>&1; then
        secret="$(openssl rand -hex 32)"
    else
        secret="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
    fi
    printf '\nBETTER_AUTH_SECRET=%s\n' "$secret" >> "$ROOT/.env"
fi

chmod 600 "$ROOT/.env"

python3 - "$ROOT/extensions_config.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    json.load(handle)
PY

test -s "$ROOT/config.yaml"

echo "Configuration is ready."
