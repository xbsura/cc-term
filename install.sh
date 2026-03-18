#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# cc-term installer
# A curated terminal environment for AI-powered coding on macOS
# ============================================================

CC_HOME="$HOME/.cc-term"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FORCE_INSTALL=false

# Parse arguments
for arg in "$@"; do
    if [[ "$arg" == "-f" || "$arg" == "--force" ]]; then
        FORCE_INSTALL=true
    fi
done

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[cc-term]${NC} $*"; }
ok()    { echo -e "${GREEN}[cc-term]${NC} $*"; }
warn()  { echo -e "${YELLOW}[cc-term]${NC} $*"; }
err()   { echo -e "${RED}[cc-term]${NC} $*" >&2; }

# ----------------------------------------------------------
# 1. Pre-flight checks
# ----------------------------------------------------------
if [[ "$(uname)" != "Darwin" ]]; then
    err "cc-term is designed for macOS only."
    exit 1
fi

# brew_run: always use the correct brew binary, handling Rosetta 2
brew_run() {
    if [[ -f /opt/homebrew/bin/brew ]]; then
        # ARM Homebrew (works natively or under Rosetta via arch -arm64)
        arch -arm64 /opt/homebrew/bin/brew "$@"
    elif [[ -f /usr/local/bin/brew ]]; then
        /usr/local/bin/brew "$@"
    else
        brew "$@"
    fi
}

# ----------------------------------------------------------
# 2. Homebrew
# ----------------------------------------------------------
if ! command -v brew &>/dev/null && [[ ! -f /opt/homebrew/bin/brew ]] && [[ ! -f /usr/local/bin/brew ]]; then
    warn "Homebrew is not installed."
    echo ""
    read -rp "$(echo -e "${CYAN}[cc-term]${NC} Install Homebrew now? [Y/n] ")" answer
    answer="${answer:-Y}"
    if [[ "$answer" =~ ^[Yy]$ ]]; then
        info "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add brew to current session PATH
        if [[ -f /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -f /usr/local/bin/brew ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
        ok "Homebrew installed."
    else
        err "Homebrew is required. Please install it first:"
        err "  https://brew.sh"
        exit 1
    fi
else
    ok "Homebrew already installed."
fi

# ----------------------------------------------------------
# 3. Install packages
# ----------------------------------------------------------

# --- Cask: iTerm2 ---
# Check by .app existence (handles non-brew installs)
if [[ -d "/Applications/iTerm.app" ]]; then
    ok "  iterm2 (already installed)"
else
    info "  Installing iterm2..."
    brew_run install --cask iterm2 || warn "  iterm2 install failed (install manually from https://iterm2.com)"
fi

# --- Formulae ---
FORMULAE=(bash tmux vim bat btop duf tig lazygit qrencode python@3)

# Map package names to binary names for fast detection (bash 3.2 compatible)
pkg_to_bin() {
    case "$1" in
        python@3) echo "python3" ;;
        *)        echo "$1" ;;
    esac
}

info "Installing formulae..."
for pkg in "${FORMULAE[@]}"; do
    bin_name="$(pkg_to_bin "$pkg")"

    if [[ "$FORCE_INSTALL" == "true" ]]; then
        if brew_run list "$pkg" &>/dev/null; then
            ok "  $pkg (already installed)"
        else
            info "  Installing $pkg..."
            brew_run install "$pkg" || warn "  $pkg install failed, skipping."
        fi
    else
        if command -v "$bin_name" &>/dev/null; then
            ok "  $pkg (already installed)"
        else
            info "  Installing $pkg..."
            brew_run install "$pkg" || warn "  $pkg install failed, skipping."
        fi
    fi
done

# ----------------------------------------------------------
# 3.1 Detect brew prefix and Python path
# ----------------------------------------------------------
BREW_PREFIX="$(brew_run --prefix)"
BREW_PYTHON3="${BREW_PREFIX}/bin/python3"
if [[ ! -x "$BREW_PYTHON3" ]]; then
    # Fallback: find python3 from brew's python@3 package
    BREW_PYTHON3="$(brew_run --prefix python@3)/bin/python3"
fi
if [[ ! -x "$BREW_PYTHON3" ]]; then
    err "python3 not found in brew. Please run: brew install python@3"
    exit 1
fi
ok "Using Python: $BREW_PYTHON3"

# ----------------------------------------------------------
# 3.2 Create isolated Python venv
# ----------------------------------------------------------
CC_VENV="$CC_HOME/venv"
CC_PYTHON="$CC_VENV/bin/python"
if [[ ! -f "$CC_PYTHON" ]]; then
    info "Creating Python venv at $CC_VENV ..."
    "$BREW_PYTHON3" -m venv "$CC_VENV"
    ok "Python venv created."
else
    ok "Python venv already exists."
fi

# ----------------------------------------------------------
# 4. Install imgcat (iTerm2 shell utility)
# ----------------------------------------------------------
IMGCAT_PATH="$CC_HOME/bin/imgcat"
mkdir -p "$CC_HOME/bin"
if [[ ! -f "$IMGCAT_PATH" ]]; then
    info "Downloading imgcat..."
    curl -fsSL "https://iterm2.com/utilities/imgcat" -o "$IMGCAT_PATH"
    chmod +x "$IMGCAT_PATH"
    ok "imgcat installed."
else
    ok "imgcat already installed."
fi

# ----------------------------------------------------------
# 5. Install TPM (Tmux Plugin Manager)
# ----------------------------------------------------------
TPM_DIR="$CC_HOME/tmux/plugins/tpm"
if [[ ! -d "$TPM_DIR" ]]; then
    info "Installing Tmux Plugin Manager (TPM)..."
    git clone https://github.com/tmux-plugins/tpm "$TPM_DIR"
    ok "TPM installed."
else
    ok "TPM already installed."
fi

# ----------------------------------------------------------
# 6. Deploy config files
# ----------------------------------------------------------
info "Deploying configuration to $CC_HOME ..."

mkdir -p "$CC_HOME/config"

cp "$SCRIPT_DIR/config/bash_profile" "$CC_HOME/config/bash_profile"
cp "$SCRIPT_DIR/config/tmux.conf"    "$CC_HOME/config/tmux.conf"
cp "$SCRIPT_DIR/config/vimrc"        "$CC_HOME/config/vimrc"

# Deploy remote access files
mkdir -p "$CC_HOME/config/remote"
cp "$SCRIPT_DIR/config/remote/index.html" "$CC_HOME/config/remote/index.html"

# Deploy ttyd access files
mkdir -p "$CC_HOME/config/ttyd"
cp "$SCRIPT_DIR/config/ttyd/index.html" "$CC_HOME/config/ttyd/index.html"
cp "$SCRIPT_DIR/config/ttyd/ttyd.conf" "$CC_HOME/config/ttyd/ttyd.conf"

# Deploy homepage files
mkdir -p "$CC_HOME/config/homepage"
if [[ -f "$SCRIPT_DIR/config/homepage/index.html" ]]; then
    cp "$SCRIPT_DIR/config/homepage/index.html" "$CC_HOME/config/homepage/index.html"
fi
if [[ -f "$SCRIPT_DIR/config/homepage/docs.html" ]]; then
    cp "$SCRIPT_DIR/config/homepage/docs.html" "$CC_HOME/config/homepage/docs.html"
fi

# Copy launchers and servers
cp "$SCRIPT_DIR/bin/_cc-term-core" "$CC_HOME/bin/_cc-term-core"
cp "$SCRIPT_DIR/bin/cc-term" "$CC_HOME/bin/cc-term"
cp "$SCRIPT_DIR/bin/cc-remote-server.py" "$CC_HOME/bin/cc-remote-server.py"
cp "$SCRIPT_DIR/bin/cc-relay-server.py" "$CC_HOME/bin/cc-relay-server.py"
cp "$SCRIPT_DIR/bin/cc-proxy-server.py" "$CC_HOME/bin/cc-proxy-server.py"
cp "$SCRIPT_DIR/bin/cc-tunnel-client.py" "$CC_HOME/bin/cc-tunnel-client.py"
cp "$SCRIPT_DIR/bin/cc-tmate-manager.py" "$CC_HOME/bin/cc-tmate-manager.py"
cp "$SCRIPT_DIR/bin/cc-state-manager.py" "$CC_HOME/bin/cc-state-manager.py"
cp "$SCRIPT_DIR/bin/cc-provider-manager.py" "$CC_HOME/bin/cc-provider-manager.py"
chmod +x "$CC_HOME/bin/_cc-term-core" "$CC_HOME/bin/cc-term"

# Deploy cc-remote-status.sh
if [[ -f "$SCRIPT_DIR/bin/cc-remote-status.sh" ]]; then
    cp "$SCRIPT_DIR/bin/cc-remote-status.sh" "$CC_HOME/bin/cc-remote-status.sh"
    chmod +x "$CC_HOME/bin/cc-remote-status.sh"
fi

# Deploy provider scripts
mkdir -p "$CC_HOME/providers"
for script in "$SCRIPT_DIR"/providers/*.sh; do
    [[ -f "$script" ]] && cp "$script" "$CC_HOME/providers/"
done

# Create run and state directories
mkdir -p "$CC_HOME/run"
mkdir -p "$CC_HOME/state"

ok "Configuration deployed."

# ----------------------------------------------------------
# 7. Preserve existing providers
# ----------------------------------------------------------
info "Preserving existing providers..."
if [[ -f "$CC_HOME/providers.json" ]]; then
    ok "Existing providers.json preserved."
else
    info "No providers.json yet — add one later with: cc-term -provider -new ..."
fi
"$CC_PYTHON" "$CC_HOME/bin/cc-provider-manager.py" seed >/dev/null 2>&1 || true

# ----------------------------------------------------------
# 8. Install tmux plugins via TPM
# ----------------------------------------------------------
info "Installing tmux plugins..."
TMUX_PLUGIN_MANAGER_PATH="$CC_HOME/tmux/plugins" "$TPM_DIR/bin/install_plugins" || warn "TPM plugin install skipped (tmux may not be running)."

# ----------------------------------------------------------
# 9. Create symlinks in brew prefix bin
# ----------------------------------------------------------
BREW_BIN="${BREW_PREFIX}/bin"
PRIMARY_LINK="${BREW_BIN}/cc-term"

if [[ -L "$PRIMARY_LINK" || -f "$PRIMARY_LINK" ]]; then
    rm -f "$PRIMARY_LINK"
fi

if ln -sf "$CC_HOME/bin/cc-term" "$PRIMARY_LINK" 2>/dev/null; then
    ok "Symlinked cc-term -> $PRIMARY_LINK"
else
    warn "Could not create symlink at $PRIMARY_LINK."
    warn "You can run cc-term directly: $CC_HOME/bin/cc-term"
fi

# ----------------------------------------------------------
# Done
# ----------------------------------------------------------
echo ""
ok "============================================"
ok " cc-term installed successfully!"
ok "============================================"
echo ""
info "List with:         cc-term -ls"
info "Launch with:       cc-term"
info "Named tab:         cc-term [-new] <name>"
info "Remote:            cc-term <name> -r"
info "List Providers:    cc-term -p -ls"
info "Add Providers:     cc-term -p -new -api <url> -key <key>"
info "Config dir:        ~/.cc-term"
echo ""

# ----------------------------------------------------------
# Auto-reload if running inside cc-term
# ----------------------------------------------------------
if [[ -n "${CC_TERM_PROFILE_LOADED:-}" ]]; then
    # shellcheck disable=SC1091
    source "$CC_HOME/config/bash_profile"
    ok "Auto backup & recovery enabled..."
    echo ""
fi

info "Enjoy your CC coding journey 🚀"
echo ""
