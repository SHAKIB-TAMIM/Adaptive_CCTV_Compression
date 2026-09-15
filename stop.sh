#!/bin/bash
# ──────────────────────────────────────────────
#  Stop all CCTV system processes
# ──────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}[STOP]${NC} Stopping CCTV system..."

# Stop by process name
pkill -f "camera_manager.py" 2>/dev/null && echo -e "${GREEN}  ✓${NC} Edge-node stopped" || echo -e "${YELLOW}  -${NC} Edge-node was not running"
pkill -f "capture_stream.py" 2>/dev/null || true
pkill -f "node server.js" 2>/dev/null && echo -e "${GREEN}  ✓${NC} Server stopped" || echo -e "${YELLOW}  -${NC} Server was not running"

# Kill orphaned FFmpeg processes on our UDP ports
pkill -f "udp://127.0.0.1:123[0-9]" 2>/dev/null && echo -e "${GREEN}  ✓${NC} FFmpeg processes killed" || true

# Free webcam
fuser -k /dev/video0 2>/dev/null && echo -e "${GREEN}  ✓${NC} Webcam freed" || true

sleep 1

# Verify
if lsof -i :5000 -t &>/dev/null; then
    echo -e "${RED}  !${NC} Port 5000 still in use — killing forcefully"
    lsof -i :5000 -t | xargs kill -9 2>/dev/null || true
fi

if lsof -i :3000 -t &>/dev/null; then
    echo -e "${RED}  !${NC} Port 3000 still in use — killing forcefully"
    lsof -i :3000 -t | xargs kill -9 2>/dev/null || true
fi

echo ""
echo -e "${GREEN}All stopped.${NC}"
