#!/usr/bin/env bash
set -euo pipefail

# ==========================================
#  Tool Bootstrap — installs system-level
#  prerequisites if they are missing.
#  Called by: make install
# ==========================================

OS="$(uname -s)"

# ---------- helpers ----------

has() { command -v "$1" >/dev/null 2>&1; }

node_ok() {
    has node || return 1
    local major
    major=$(node -v | sed 's/v//' | cut -d. -f1)
    [ "$major" -ge 22 ]
}

brew_install() {
    if has brew; then
        brew install "$1"
    else
        echo "  ✗ Homebrew not found — cannot auto-install $1"
        echo "    Install Homebrew first: https://brew.sh"
        echo "    Then re-run: make install"
        exit 1
    fi
}

echo "=========================================="
echo "  Checking & Installing System Tools"
echo "=========================================="
echo ""

# ---------- uv ----------

echo "Checking uv..."
if has uv; then
    echo "  ✓ uv $(uv --version | awk '{print $2}') already installed"
else
    echo "  Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Make uv available in this session
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if has uv; then
        echo "  ✓ uv installed"
    else
        echo "  ✗ uv install failed — please install manually:"
        echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
fi

echo ""

# ---------- node ----------

echo "Checking Node.js (>=22)..."
if node_ok; then
    echo "  ✓ Node.js $(node -v) already installed"
else
    if has node; then
        echo "  Node.js $(node -v) found but version 22+ is required — upgrading..."
    else
        echo "  Node.js not found — installing..."
    fi

    if [ "$OS" = "Darwin" ]; then
        brew_install node@22
        # Homebrew installs node@22 as keg-only; link it
        brew link --overwrite node@22 2>/dev/null || true
        export PATH="$(brew --prefix node@22)/bin:$PATH"
    else
        # Linux — use NodeSource
        echo "  Downloading NodeSource setup script for Node 22..."
        curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
        sudo apt-get install -y nodejs
    fi

    if node_ok; then
        echo "  ✓ Node.js $(node -v) installed"
    else
        echo "  ✗ Node.js 22+ install failed — please install manually:"
        echo "    macOS:  brew install node@22"
        echo "    Linux:  https://nodejs.org/en/download"
        exit 1
    fi
fi

echo ""

# ---------- pnpm ----------

echo "Checking pnpm..."
if has pnpm; then
    echo "  ✓ pnpm $(pnpm -v) already installed"
else
    echo "  Installing pnpm via npm..."
    npm install -g pnpm
    if has pnpm; then
        echo "  ✓ pnpm $(pnpm -v) installed"
    else
        echo "  ✗ pnpm install failed — please install manually:"
        echo "    npm install -g pnpm"
        exit 1
    fi
fi

echo ""

# ---------- nginx ----------

echo "Checking nginx..."
if has nginx; then
    echo "  ✓ nginx already installed"
else
    echo "  Installing nginx..."
    if [ "$OS" = "Darwin" ]; then
        brew_install nginx
    else
        sudo apt-get install -y nginx
    fi

    if has nginx; then
        echo "  ✓ nginx installed"
    else
        echo "  ✗ nginx install failed — please install manually:"
        echo "    macOS:  brew install nginx"
        echo "    Linux:  sudo apt install nginx"
        exit 1
    fi
fi

echo ""
echo "=========================================="
echo "  ✓ All system tools ready"
echo "=========================================="
echo ""
