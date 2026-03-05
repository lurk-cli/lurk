#!/usr/bin/env bash
set -e

# lurk installer — one command to set up the context broker for AI tools
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/zasanao/lurk/main/install.sh | bash
#   ./install.sh  (from repo root)

LURK_DIR="$HOME/.lurk"
LURK_SRC="$LURK_DIR/src"
LOCAL_BIN="$HOME/.local/bin"
REPO_URL="https://github.com/zasanao/lurk.git"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()  { echo -e "${BOLD}$1${RESET}"; }
ok()    { echo -e "  ${GREEN}✓${RESET} $1"; }
warn()  { echo -e "  ${YELLOW}!${RESET} $1"; }
fail()  { echo -e "  ${RED}✗${RESET} $1"; }
step()  { echo -e "\n${BOLD}$1${RESET}"; }

# ---------- Step 1: Pre-flight checks ----------

step "Checking requirements..."

# macOS only
if [[ "$(uname)" != "Darwin" ]]; then
    fail "lurk only supports macOS."
    exit 1
fi
ok "macOS detected"

# Python 3.11+
if ! command -v python3 &>/dev/null; then
    fail "Python 3 not found."
    echo "  Install it with: brew install python@3.11"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [[ "$PYTHON_MAJOR" -lt 3 ]] || [[ "$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -lt 11 ]]; then
    fail "Python 3.11+ required (found $PYTHON_VERSION)."
    echo "  Install it with: brew install python@3.11"
    exit 1
fi
ok "Python $PYTHON_VERSION"

# Swift
if ! command -v swift &>/dev/null; then
    fail "Swift not found. Install Xcode Command Line Tools:"
    echo "  xcode-select --install"
    exit 1
fi
ok "Swift $(swift --version 2>&1 | head -1 | sed 's/.*version //' | cut -d' ' -f1)"

# pipx
if ! command -v pipx &>/dev/null; then
    warn "pipx not found — installing..."
    if command -v brew &>/dev/null; then
        brew install pipx 2>/dev/null || python3 -m pip install --user pipx
    else
        python3 -m pip install --user pipx
    fi
    # Ensure pipx is on PATH
    python3 -m pipx ensurepath 2>/dev/null || true
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v pipx &>/dev/null; then
        fail "pipx installation failed. Install manually: brew install pipx"
        exit 1
    fi
fi
ok "pipx"

# ---------- Step 2: Clone or update source ----------

step "Getting lurk source..."

# Detect if running from inside a lurk repo
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IN_REPO=false
SRC_DIR=""

if [[ -f "$SCRIPT_DIR/daemon/Package.swift" && -d "$SCRIPT_DIR/lurk/src/lurk" ]]; then
    IN_REPO=true
    SRC_DIR="$SCRIPT_DIR"
    ok "Using local repo at $SRC_DIR"
elif [[ -d "$LURK_SRC/.git" ]]; then
    git -C "$LURK_SRC" pull --ff-only 2>/dev/null || true
    SRC_DIR="$LURK_SRC"
    ok "Updated $LURK_SRC"
else
    git clone --depth 1 "$REPO_URL" "$LURK_SRC"
    SRC_DIR="$LURK_SRC"
    ok "Cloned to $LURK_SRC"
fi

# ---------- Step 3: Build the Swift daemon ----------

step "Building the daemon (this may take a minute)..."

cd "$SRC_DIR/daemon"
swift build -c release 2>&1 | tail -1
cd - >/dev/null

mkdir -p "$LOCAL_BIN"
cp "$SRC_DIR/daemon/.build/release/lurk-daemon" "$LOCAL_BIN/lurk-daemon"
ok "Built and installed lurk-daemon to $LOCAL_BIN/lurk-daemon"

# ---------- Step 4: Install Python CLI via pipx ----------

step "Installing lurk CLI..."

# pipx install (or reinstall if already present)
pipx install "$SRC_DIR/lurk" --force 2>&1 | tail -1
ok "lurk CLI installed via pipx"

# Ensure ~/.local/bin is on PATH for the rest of this script
export PATH="$LOCAL_BIN:$HOME/.local/pipx/venvs/lurk/bin:$PATH"

# Install all optional extras
step "Installing optional extras..."

pipx inject lurk "lurk[mcp]" 2>/dev/null && ok "MCP server (Claude Code / Cursor)" || warn "MCP — install later with: pipx inject lurk \"lurk[mcp]\""
pipx inject lurk "lurk[http]" 2>/dev/null && ok "HTTP API" || warn "HTTP — install later with: pipx inject lurk \"lurk[http]\""
pipx inject lurk "lurk[llm]" 2>/dev/null && ok "LLM-enhanced context" || warn "LLM — install later with: pipx inject lurk \"lurk[llm]\""

# ---------- Step 5: Set up and start ----------

step "Configuring lurk..."

lurk install --daemon "$LOCAL_BIN/lurk-daemon" 2>/dev/null || true
ok "Launch agent configured"

lurk start 2>/dev/null || true
ok "Daemon started"

# ---------- Step 6: Accessibility permission ----------

step "Accessibility permission needed"

echo ""
echo -e "  lurk needs Accessibility access to read window titles."
echo -e "  Opening System Settings — add ${CYAN}lurk-daemon${RESET} and toggle it on."
echo ""

open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null || true

echo -e "  After granting permission, verify with: ${CYAN}lurk status${RESET}"

# ---------- Step 7: Success ----------

echo ""
echo -e "${GREEN}${BOLD}lurk is installed and running.${RESET}"
echo ""
echo -e "  ${CYAN}lurk status${RESET}      Check daemon status"
echo -e "  ${CYAN}lurk context${RESET}     See what lurk observes"
echo -e "  ${CYAN}lurk agents${RESET}      See active AI agents"
echo ""
echo -e "${BOLD}Connect to your AI tools:${RESET}"
echo -e "  ${CYAN}claude mcp add lurk -- lurk serve-mcp${RESET}   Claude Code"
echo -e "  ${CYAN}lurk serve-http${RESET}                        HTTP API at :4141"
