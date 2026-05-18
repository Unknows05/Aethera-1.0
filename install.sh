#!/usr/bin/env bash
# Aethera v1.5 — Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/Unknows05/Aethera/main/install.sh | bash

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

REPO="https://github.com/Unknows05/Aethera-1.0.git"
INSTALL_DIR="${AETHERA_DIR:-$HOME/aethera}"
VERSION="1.5.0"

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║   Aethera v${VERSION} — Installer        ║"
echo "  ║   Autonomous AI Trading Agent        ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

# ── Checks ──────────────────────────────────────────────

check_command() {
    if ! command -v "$1" &>/dev/null; then
        echo -e "${RED}✗ $1 not found. Please install it first.${NC}"
        return 1
    fi
    echo -e "${GREEN}✓ $1 found${NC}"
}

echo "Checking dependencies..."
check_command "git" || exit 1
check_command "python3" || exit 1

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if [ "$(echo "$PYTHON_VERSION 3.10" | awk '{print ($1 >= $2)}')" != "1" ]; then
    echo -e "${RED}✗ Python 3.10+ required (found $PYTHON_VERSION)${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python $PYTHON_VERSION${NC}"

# Check pip
INSTALL_PIP=false
if ! command -v pip3 &>/dev/null && ! python3 -m pip --version &>/dev/null; then
    echo -e "${YELLOW}⚠ pip not found — attempting to install...${NC}"
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3-pip
        INSTALL_PIP=true
    elif command -v yum &>/dev/null; then
        sudo yum install -y -q python3-pip
        INSTALL_PIP=true
    else
        echo -e "${RED}✗ pip not found. Please install python3-pip manually.${NC}"
        exit 1
    fi
fi
echo -e "${GREEN}✓ pip${NC}"

# Check Node.js for TUI — auto-install if missing
HAS_NODE=false
if command -v node &>/dev/null; then
    NODE_VERSION=$(node -v | sed 's/v//')
    echo -e "${GREEN}✓ Node.js $NODE_VERSION (for TUI)${NC}"
    HAS_NODE=true
else
    echo -e "${YELLOW}⚠ Node.js not found — attempting to install...${NC}"
    if command -v apt-get &>/dev/null; then
        curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash - && sudo apt-get install -y -qq nodejs
        if command -v node &>/dev/null; then
            echo -e "${GREEN}✓ Node.js $(node -v | sed 's/v//') installed${NC}"
            HAS_NODE=true
        fi
    elif command -v yum &>/dev/null; then
        curl -fsSL https://rpm.nodesource.com/setup_18.x | sudo -E bash - && sudo yum install -y -q nodejs
        if command -v node &>/dev/null; then
            echo -e "${GREEN}✓ Node.js $(node -v | sed 's/v//') installed${NC}"
            HAS_NODE=true
        fi
    fi
    if [ "$HAS_NODE" = false ]; then
        echo -e "${YELLOW}⚠ Node.js could not be auto-installed. TUI will be skipped.${NC}"
        echo -e "  Install manually: https://nodejs.org/"
    fi
fi

# ── Install ─────────────────────────────────────────────

if [ -d "$INSTALL_DIR" ] && [ -d "$INSTALL_DIR/.git" ]; then
    echo -e "\n${YELLOW}Aethera already installed at $INSTALL_DIR${NC}"
    echo -e "Updating instead of fresh install..."
    cd "$INSTALL_DIR"
    git fetch origin main
    git reset --hard origin/main
    echo -e "${GREEN}✓ Updated to latest${NC}"
else
    echo -e "\n${CYAN}Installing Aethera to $INSTALL_DIR...${NC}"
    git clone "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    echo -e "${GREEN}✓ Cloned repository${NC}"
fi

# ── Python Dependencies ─────────────────────────────────

echo -e "\n${CYAN}Installing Python dependencies...${NC}"
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet -r requirements.txt 2>/dev/null || \
python3 -m pip install --quiet click rich requests openai ccxt pyyaml httpx apscheduler pynacl 2>/dev/null
echo -e "${GREEN}✓ Python dependencies installed${NC}"

# ── Build TUI ───────────────────────────────────────────

if [ "$HAS_NODE" = true ] && [ -d "tui" ]; then
    echo -e "\n${CYAN}Building TypeScript TUI...${NC}"
    cd tui
    npm install --silent 2>/dev/null
    npm run build 2>/dev/null
    cd ..
    echo -e "${GREEN}✓ TUI built${NC}"
else
    echo -e "\n${YELLOW}⚠ Skipping TUI build (Node.js not available)${NC}"
    echo -e "  Run 'cd tui && npm install && npm run build' later if needed"
fi

# ── Symlink CLI ─────────────────────────────────────────

echo -e "\n${CYAN}Setting up CLI...${NC}"
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

# Create wrapper script
cat > "$BIN_DIR/aethera" << 'WRAPPER'
#!/usr/bin/env bash
AETHERA_ROOT="${AETHERA_ROOT:-$HOME/aethera}"
exec python3 "$AETHERA_ROOT/cli.py" "$@"
WRAPPER
chmod +x "$BIN_DIR/aethera"

# Check if bin dir is in PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo -e "${YELLOW}⚠ $HOME/.local/bin not in PATH${NC}"
    echo -e "  Add this to ~/.bashrc or ~/.zshrc:"
    echo -e "  ${CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
else
    echo -e "${GREEN}✓ CLI symlink created${NC}"
fi

# ── Create data directory ───────────────────────────────

mkdir -p "$INSTALL_DIR/data"
mkdir -p "$INSTALL_DIR/vault"

# ── Done ────────────────────────────────────────────────

echo -e "\n${GREEN}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Aethera v${VERSION} installed!           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
echo -e ""
echo -e "  ${CYAN}Next steps:${NC}"
echo -e "    1. ${YELLOW}aethera init${NC}       — Setup wizard"
echo -e "    2. ${YELLOW}aethera start${NC}      — Launch TUI"
echo -e "    3. ${YELLOW}aethera --help${NC}     — All commands"
echo -e ""
echo -e "  ${CYAN}Update:${NC}"
echo -e "    ${YELLOW}aethera update${NC}       — Auto-update to latest"
echo -e ""

# Source PATH if needed
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

# Ask to run init
echo -ne "${CYAN}Run 'aethera init' now? [Y/n]: ${NC}"
read -r answer
if [[ "$answer" =~ ^[Nn]$ ]]; then
    echo -e "${YELLOW}Run 'aethera init' when ready.${NC}"
else
    cd "$INSTALL_DIR"
    exec python3 cli.py init
fi
