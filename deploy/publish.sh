#!/bin/bash
# publish.sh — deploy cc-term to remote server + install locally
#
# Usage:
#   ./deploy/publish.sh              # deploy to ttyd.ink + local install
#   ./deploy/publish.sh --remote     # remote only
#   ./deploy/publish.sh --local      # local only
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REMOTE_HOST="${CC_DEPLOY_HOST:-ttyd.ink}"
REMOTE_USER="${CC_DEPLOY_USER:-root}"
REMOTE_DIR="/opt/cc-term"

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${CYAN}[publish]${NC} $*"; }
ok()    { echo -e "${GREEN}[publish]${NC} $*"; }
err()   { echo -e "${RED}[publish]${NC} $*" >&2; }

DO_REMOTE=1
DO_LOCAL=1
for arg in "$@"; do
    case "$arg" in
        --remote) DO_LOCAL=0 ;;
        --local)  DO_REMOTE=0 ;;
        *)        err "Unknown flag: $arg"; exit 1 ;;
    esac
done

# ---- Syntax checks ----
info "Running syntax checks..."
bash -n "$PROJECT_DIR/bin/_cc-term-core"
python3 -m py_compile "$PROJECT_DIR/bin/cc-proxy-server.py"
python3 -m py_compile "$PROJECT_DIR/bin/cc-tunnel-client.py"
ok "Syntax checks passed."

# ---- Remote deploy ----
if [[ "$DO_REMOTE" -eq 1 ]]; then
    info "Deploying to ${REMOTE_USER}@${REMOTE_HOST}..."

    # Upload files
    scp -q "$PROJECT_DIR/bin/cc-proxy-server.py" \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/bin/cc-proxy-server.py"
    ok "  Uploaded cc-proxy-server.py"

    ssh "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}/config/ttyd"
    scp -q "$PROJECT_DIR/config/ttyd/index.html" \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/config/ttyd/index.html"
    ok "  Uploaded config/ttyd/index.html"

    ssh "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}/config/homepage"
    scp -q "$PROJECT_DIR/config/homepage/index.html" \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/config/homepage/index.html"
    scp -q "$PROJECT_DIR/config/homepage/docs.html" \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/config/homepage/docs.html"
    ok "  Uploaded config/homepage/"

    # Update systemd service and restart
    ssh "${REMOTE_USER}@${REMOTE_HOST}" bash -s <<'REMOTE_SCRIPT'
set -e

SERVICE_FILE="/etc/systemd/system/cc-term-proxy.service"

# Ensure service uses --data-dir and correct paths
cat > "$SERVICE_FILE" <<'EOF'
[Unit]
Description=cc-term Proxy Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/cc-term
ExecStart=/opt/cc-term/venv/bin/python /opt/cc-term/bin/cc-proxy-server.py --port 9999 --html /opt/cc-term/config/ttyd/index.html --data-dir /opt/cc-term/run --homepage-dir /opt/cc-term/config/homepage
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl restart cc-term-proxy
REMOTE_SCRIPT

    ok "  Service restarted."

    # Verify
    STATUS=$(ssh "${REMOTE_USER}@${REMOTE_HOST}" "systemctl is-active cc-term-proxy" 2>/dev/null || true)
    if [[ "$STATUS" == "active" ]]; then
        ok "Remote deploy complete — service is running."
    else
        err "Service status: $STATUS — check with: ssh ${REMOTE_USER}@${REMOTE_HOST} journalctl -u cc-term-proxy -n 20"
    fi
fi

# ---- Local install ----
if [[ "$DO_LOCAL" -eq 1 ]]; then
    info "Installing locally..."
    bash "$PROJECT_DIR/install.sh"
    ok "Local install complete."
fi

echo ""
ok "Done."
