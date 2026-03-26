#!/usr/bin/env bash
# connect-claude.sh — Automatically connect MusicMind MCP to Claude Desktop and/or Claude Code
#
# Usage: ./scripts/connect-claude.sh
#
# What it does:
#   1. Detects the absolute path to this repo
#   2. Checks for uv and installs deps if needed
#   3. Checks if Apple Music config exists (prompts to run setup if not)
#   4. Configures Claude Desktop (if installed) by updating claude_desktop_config.json
#   5. Configures Claude Code (if installed) by running `claude mcp add`
#   6. Verifies the server can start

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ── Detect project root ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
info "MusicMind project: ${BOLD}$PROJECT_DIR${NC}"

# ── Check uv ─────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    if [[ -x "$HOME/.local/bin/uv" ]]; then
        export PATH="$HOME/.local/bin:$PATH"
    else
        error "uv not found. Install it: ${BOLD}curl -LsSf https://astral.sh/uv/install.sh | sh${NC}"
        exit 1
    fi
fi
success "uv found: $(uv --version)"

# ── Install dependencies ─────────────────────────────────────────────
info "Installing dependencies..."
(cd "$PROJECT_DIR" && uv sync --all-extras 2>&1 | tail -1)
success "Dependencies installed"

# ── Check Apple Music config ─────────────────────────────────────────
CONFIG_FILE="$HOME/.config/musicmind/config.json"
if [[ ! -f "$CONFIG_FILE" ]]; then
    warn "Apple Music config not found at $CONFIG_FILE"
    echo ""
    echo -e "  Run: ${BOLD}cd $PROJECT_DIR && uv run python -m musicmind.setup${NC}"
    echo ""
    echo "  This will ask for your Apple Developer Team ID, Key ID,"
    echo "  .p8 key path, and open a browser for OAuth authorization."
    echo ""
    read -rp "Run setup now? [y/N] " answer
    if [[ "$answer" =~ ^[Yy]$ ]]; then
        (cd "$PROJECT_DIR" && uv run python -m musicmind.setup)
    else
        warn "Skipping setup — server will start in limited mode"
    fi
else
    success "Apple Music config found"
fi

# ── Verify server starts ─────────────────────────────────────────────
info "Verifying MusicMind server..."
SERVER_CHECK=$(cd "$PROJECT_DIR" && uv run python -c "from musicmind.server import mcp; print(f'OK: {len(mcp._tool_manager._tools)} tools')" 2>/dev/null) || true
if [[ "$SERVER_CHECK" == OK* ]]; then
    success "Server verified — $SERVER_CHECK"
else
    error "Server failed to load. Check your Python environment."
    exit 1
fi

# ── Configure Claude Desktop ─────────────────────────────────────────
CONNECTED=false

# Claude Desktop config path (macOS)
CLAUDE_DESKTOP_CONFIG="$HOME/.config/claude/claude_desktop_config.json"
# Also check Library path for macOS app
CLAUDE_DESKTOP_CONFIG_ALT="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

configure_desktop() {
    local config_path="$1"
    local config_dir
    config_dir="$(dirname "$config_path")"

    info "Configuring Claude Desktop at $config_path"

    # Create directory if needed
    mkdir -p "$config_dir"

    # Build the MCP server entry
    local mcp_entry
    mcp_entry=$(cat <<JSONEOF
{
  "command": "uv",
  "args": ["run", "--directory", "$PROJECT_DIR", "python", "-m", "musicmind"]
}
JSONEOF
)

    if [[ -f "$config_path" ]]; then
        # Check if musicmind already configured
        if grep -q '"musicmind"' "$config_path" 2>/dev/null; then
            success "Claude Desktop already has musicmind configured"
            return 0
        fi

        # Use Python to safely merge JSON
        uv run python -c "
import json, sys
config_path = '$config_path'
with open(config_path) as f:
    config = json.load(f)
if 'mcpServers' not in config:
    config['mcpServers'] = {}
config['mcpServers']['musicmind'] = {
    'command': 'uv',
    'args': ['run', '--directory', '$PROJECT_DIR', 'python', '-m', 'musicmind']
}
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
print('Updated')
" 2>/dev/null
    else
        # Create new config
        cat > "$config_path" <<JSONEOF
{
  "mcpServers": {
    "musicmind": {
      "command": "uv",
      "args": ["run", "--directory", "$PROJECT_DIR", "python", "-m", "musicmind"]
    }
  }
}
JSONEOF
    fi

    success "Claude Desktop configured — restart Claude Desktop to activate"
    CONNECTED=true
}

# Try both possible locations
if [[ -d "$HOME/Library/Application Support/Claude" ]] || [[ -f "$CLAUDE_DESKTOP_CONFIG_ALT" ]]; then
    configure_desktop "$CLAUDE_DESKTOP_CONFIG_ALT"
elif [[ -d "$HOME/.config/claude" ]] || command -v claude &>/dev/null; then
    configure_desktop "$CLAUDE_DESKTOP_CONFIG"
fi

# ── Configure Claude Code ────────────────────────────────────────────
if command -v claude &>/dev/null; then
    info "Configuring Claude Code..."

    # Check if already added
    if claude mcp list 2>/dev/null | grep -q musicmind; then
        success "Claude Code already has musicmind configured"
        CONNECTED=true
    else
        claude mcp add musicmind -- uv run --directory "$PROJECT_DIR" python -m musicmind 2>/dev/null && {
            success "Claude Code configured — musicmind MCP server added"
            CONNECTED=true
        } || {
            warn "Could not auto-add to Claude Code. Add manually:"
            echo ""
            echo -e "  ${BOLD}claude mcp add musicmind -- uv run --directory $PROJECT_DIR python -m musicmind${NC}"
            echo ""
        }
    fi
else
    info "Claude Code CLI not found (optional)"
fi

# ── Summary ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if $CONNECTED; then
    echo -e "${GREEN}${BOLD}MusicMind MCP is connected!${NC}"
    echo ""
    echo "  Try these prompts in Claude:"
    echo "    - \"Check MusicMind health\""
    echo "    - \"Show me my music taste profile\""
    echo "    - \"Find me 15 new songs I'd like\""
    echo "    - \"Create a playlist called 'Night Drive' with drill vibes\""
else
    echo -e "${YELLOW}${BOLD}No Claude integration detected.${NC}"
    echo ""
    echo "  To connect manually:"
    echo ""
    echo "  Claude Desktop:"
    echo "    Add to ~/.config/claude/claude_desktop_config.json:"
    echo "    {\"mcpServers\": {\"musicmind\": {\"command\": \"uv\","
    echo "      \"args\": [\"run\", \"--directory\", \"$PROJECT_DIR\", \"python\", \"-m\", \"musicmind\"]}}}"
    echo ""
    echo "  Claude Code:"
    echo "    claude mcp add musicmind -- uv run --directory $PROJECT_DIR python -m musicmind"
fi

echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
