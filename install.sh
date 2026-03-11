#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# cc-terminal installer
# A curated terminal environment for AI-powered coding on macOS
# ============================================================

CC_HOME="$HOME/.cc-terminal"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[cc-terminal]${NC} $*"; }
ok()    { echo -e "${GREEN}[cc-terminal]${NC} $*"; }
warn()  { echo -e "${YELLOW}[cc-terminal]${NC} $*"; }
err()   { echo -e "${RED}[cc-terminal]${NC} $*" >&2; }

# ----------------------------------------------------------
# 1. Pre-flight checks
# ----------------------------------------------------------
if [[ "$(uname)" != "Darwin" ]]; then
    err "cc-terminal is designed for macOS only."
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
    read -rp "$(echo -e "${CYAN}[cc-terminal]${NC} Install Homebrew now? [Y/n] ")" answer
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
FORMULAE=(bash tmux vim bat btop duf tig lazygit qrencode)

info "Installing formulae..."
for pkg in "${FORMULAE[@]}"; do
    if brew_run list "$pkg" &>/dev/null; then
        ok "  $pkg (already installed)"
    else
        info "  Installing $pkg..."
        brew_run install "$pkg" || warn "  $pkg install failed, skipping."
    fi
done

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

# Deploy tmate access files
mkdir -p "$CC_HOME/config/tmate"
cp "$SCRIPT_DIR/config/tmate/index.html" "$CC_HOME/config/tmate/index.html"
cp "$SCRIPT_DIR/config/tmate/tmate.conf" "$CC_HOME/config/tmate/tmate.conf"

# Copy launchers and servers
cp "$SCRIPT_DIR/bin/cc-terminal" "$CC_HOME/bin/cc-terminal"
cp "$SCRIPT_DIR/bin/cc-term" "$CC_HOME/bin/cc-term"
cp "$SCRIPT_DIR/bin/cc-remote-server.py" "$CC_HOME/bin/cc-remote-server.py"
cp "$SCRIPT_DIR/bin/cc-relay-server.py" "$CC_HOME/bin/cc-relay-server.py"
cp "$SCRIPT_DIR/bin/cc-proxy-server.py" "$CC_HOME/bin/cc-proxy-server.py"
cp "$SCRIPT_DIR/bin/cc-tmate-manager.py" "$CC_HOME/bin/cc-tmate-manager.py"
cp "$SCRIPT_DIR/bin/cc-state-manager.py" "$CC_HOME/bin/cc-state-manager.py"
cp "$SCRIPT_DIR/bin/cc-provider-manager.py" "$CC_HOME/bin/cc-provider-manager.py"
chmod +x "$CC_HOME/bin/cc-terminal" "$CC_HOME/bin/cc-term"

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
python3 "$CC_HOME/bin/cc-provider-manager.py" seed >/dev/null 2>&1 || true

# ----------------------------------------------------------
# 8. Install tmux plugins via TPM
# ----------------------------------------------------------
info "Installing tmux plugins..."
TMUX_PLUGIN_MANAGER_PATH="$CC_HOME/tmux/plugins" "$TPM_DIR/bin/install_plugins" || warn "TPM plugin install skipped (tmux may not be running)."

# ----------------------------------------------------------
# 9. Create symlinks in /usr/local/bin
# ----------------------------------------------------------
PRIMARY_LINK="/usr/local/bin/cc-term"
LEGACY_LINK="/usr/local/bin/cc-terminal"

mkdir -p /usr/local/bin 2>/dev/null || true
for link_target in "$PRIMARY_LINK" "$LEGACY_LINK"; do
    if [[ -L "$link_target" || -f "$link_target" ]]; then
        rm -f "$link_target"
    fi
done

if ln -sf "$CC_HOME/bin/cc-term" "$PRIMARY_LINK" 2>/dev/null; then
    ok "Symlinked cc-term -> $PRIMARY_LINK"
else
    warn "Could not create symlink at $PRIMARY_LINK (may need sudo)."
    warn "You can run cc-term directly: $CC_HOME/bin/cc-term"
fi

if ln -sf "$CC_HOME/bin/cc-terminal" "$LEGACY_LINK" 2>/dev/null; then
    ok "Compatibility symlinked cc-terminal -> $LEGACY_LINK"
else
    warn "Could not create compatibility symlink at $LEGACY_LINK."
fi

# ----------------------------------------------------------
# Done
# ----------------------------------------------------------
echo ""
ok "============================================"
ok " cc-terminal installed successfully!"
ok "============================================"
echo ""
info "Launch with:  cc-term"
info "Named tab:   cc-term -new <name>"
info "Remote:      cc-term <name> -r"
info "Providers:   cc-term -provider -ls"
info "Config dir:  $CC_HOME"
echo ""
info "Your global shell/vim/tmux configs are NOT modified."
info "All cc-terminal settings are isolated under $CC_HOME."
echo ""
