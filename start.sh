#!/bin/bash
# ──────────────────────────────────────────────────────────
#  AI Risk-Aware CCTV Compression — One-Command Launcher
#  Starts: Server + Edge-Node (auto-discovers cameras)
# ──────────────────────────────────────────────────────────

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${CYAN}[START]${NC} $*"; }
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
fail() { echo -e "${RED}  ✗${NC} $*"; }

echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║  AI Risk-Aware CCTV Compression System                  ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Kill previous ──
log "Stopping previous instances..."
pkill -f "node server.js" 2>/dev/null; pkill -f "camera_manager.py" 2>/dev/null
pkill -f "udp://127.0.0.1:123" 2>/dev/null; fuser -k /dev/video0 2>/dev/null
sleep 1
ok "Clean"

# ── Server ──
log "Starting server..."
cd "$PROJECT_DIR/server"
setsid node server.js > /tmp/cctv_server.log 2>&1 &
for i in {1..15}; do
    lsof -i :5000 -t &>/dev/null && break
    sleep 1
done
lsof -i :5000 -t &>/dev/null && ok "Server ready" || { fail "Server failed"; exit 1; }

# ── Edge-node (auto-discovers cameras) ──
log "Starting edge-node (auto-discovering cameras)..."
cd "$PROJECT_DIR/edge-node"
setsid python3 -u camera_manager.py \
    --server http://127.0.0.1:5000 \
    --config ../configs/cameras.yaml \
    --use-optimizer \
    --no-audio \
    --discover > /tmp/cctv_edge.log 2>&1 &
sleep 10
ok "Edge-node started"

# ── Dashboard ──
log "Starting dashboard..."
cd "$PROJECT_DIR/dashboard"
if ! lsof -i :3000 -t &>/dev/null; then
    setsid npm run dev > /tmp/cctv_dashboard.log 2>&1 &
    sleep 3
fi
ok "Dashboard at http://localhost:3000"

# ── Done ──
CAMERAS=$(grep -c "enabled: true" "$PROJECT_DIR/configs/cameras.yaml" 2>/dev/null || echo "?")
echo ""
echo -e "${GREEN}${BOLD}System running!${NC}"
echo -e "  Dashboard:  http://localhost:3000"
echo -e "  Settings:   http://localhost:3000/config"
echo -e "  Cameras:    $CAMERAS"
echo ""
echo -e "  Stop: ${YELLOW}./stop.sh${NC}"
echo ""
