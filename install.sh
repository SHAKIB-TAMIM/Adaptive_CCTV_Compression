#!/bin/bash
# install.sh — One-command installation for end users
# Usage: curl -sL https://raw.githubusercontent.com/YOUR_REPO/install.sh | bash
# Or: chmod +x install.sh && ./install.sh

set -e

echo "╔══════════════════════════════════════════════════════════╗"
echo "║    NEXUS Surveillance — Adaptive CCTV Compression       ║"
echo "║    One-Command Installer                                 ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

INSTALL_DIR="$HOME/nexus-surveillance"
DOCKER_AVAILABLE=false
PYTHON_AVAILABLE=false

# ── Step 1: Check prerequisites ──
echo "Step 1: Checking prerequisites..."
echo ""

# Check Docker
if command -v docker &> /dev/null; then
    echo -e "  ${GREEN}✓${NC} Docker found: $(docker --version | head -1)"
    DOCKER_AVAILABLE=true
else
    echo -e "  ${YELLOW}✗${NC} Docker not found (optional — can use Python instead)"
fi

# Check Docker Compose
if command -v docker-compose &> /dev/null || docker compose version &> /dev/null; then
    echo -e "  ${GREEN}✓${NC} Docker Compose found"
else
    echo -e "  ${YELLOW}✗${NC} Docker Compose not found"
fi

# Check Python
if command -v python3 &> /dev/null; then
    echo -e "  ${GREEN}✓${NC} Python found: $(python3 --version)"
    PYTHON_AVAILABLE=true
else
    echo -e "  ${YELLOW}✗${NC} Python3 not found"
fi

# Check Node.js
if command -v node &> /dev/null; then
    echo -e "  ${GREEN}✓${NC} Node.js found: $(node --version)"
else
    echo -e "  ${YELLOW}✗${NC} Node.js not found"
fi

# Check ffmpeg
if command -v ffmpeg &> /dev/null; then
    echo -e "  ${GREEN}✓${NC} FFmpeg found: $(ffmpeg -version 2>&1 | head -1)"
else
    echo -e "  ${RED}✗${NC} FFmpeg not found (REQUIRED — install with: sudo apt install ffmpeg)"
    echo ""
    echo "Please install FFmpeg first:"
    echo "  Ubuntu/Debian: sudo apt install ffmpeg"
    echo "  macOS: brew install ffmpeg"
    echo "  Windows: https://ffmpeg.org/download.html"
    exit 1
fi

echo ""

# ── Step 2: Choose installation method ──
echo "Step 2: Choose installation method"
echo ""
echo "  1) Docker (Recommended — easiest, isolated)"
echo "  2) Python (Manual — for Raspberry Pi or single machine)"
echo "  3) Quick Demo (Just show the dashboard, no cameras)"
echo ""
read -p "Enter choice [1/2/3]: " CHOICE

case $CHOICE in
    1)
        if [ "$DOCKER_AVAILABLE" = false ]; then
            echo -e "${RED}Docker is not installed. Please install Docker first:${NC}"
            echo "  curl -fsSL https://get.docker.com | sh"
            echo "  sudo usermod -aG docker \$USER"
            exit 1
        fi
        INSTALL_METHOD="docker"
        ;;
    2)
        if [ "$PYTHON_AVAILABLE" = false ]; then
            echo -e "${RED}Python3 is not installed.${NC}"
            exit 1
        fi
        INSTALL_METHOD="python"
        ;;
    3)
        INSTALL_METHOD="demo"
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

echo ""

# ── Step 3: Clone / Download ──
echo "Step 3: Downloading system..."
echo ""

if [ -d "$INSTALL_DIR" ]; then
    echo "  Directory exists: $INSTALL_DIR"
    read -p "  Update to latest version? [y/N]: " UPDATE
    if [ "$UPDATE" = "y" ] || [ "$UPDATE" = "Y" ]; then
        cd "$INSTALL_DIR"
        git pull
    fi
else
    # Try git clone, fallback to zip download
    if command -v git &> /dev/null; then
        echo "  Cloning repository..."
        git clone https://github.com/YOUR_USERNAME/cctv-compression.git "$INSTALL_DIR"
    else
        echo "  Downloading zip..."
        mkdir -p "$INSTALL_DIR"
        curl -L "https://github.com/YOUR_USERNAME/cctv-compression/archive/refs/heads/main.zip" -o /tmp/nexus.zip
        unzip /tmp/nexus.zip -d /tmp/nexus-extract
        mv /tmp/nexus-extract/cctv-compression-main/* "$INSTALL_DIR"/
        rm -rf /tmp/nexus.zip /tmp/nexus-extract
    fi
fi

cd "$INSTALL_DIR"
echo "  Downloaded to: $INSTALL_DIR"
echo ""

# ── Step 4: Install based on method ──
echo "Step 4: Installing..."
echo ""

if [ "$INSTALL_METHOD" = "docker" ]; then
    echo "  Using Docker deployment..."
    docker-compose -f deployment/docker-compose.yml up -d --build
    echo ""
    echo -e "${GREEN}✓ System installed and running!${NC}"
    echo ""
    echo "  Dashboard: http://localhost:3000"
    echo "  Server API: http://localhost:5000"
    echo ""
    echo "  To connect cameras, edit: configs/cameras.yaml"
    echo "  Then restart: docker-compose restart"
    echo ""

elif [ "$INSTALL_METHOD" = "python" ]; then
    echo "  Installing Python dependencies..."
    pip3 install -r edge-node/requirements.txt
    pip3 install pyyaml scikit-image matplotlib pandas

    echo "  Installing Node.js dependencies..."
    cd server && npm install && cd ..
    cd dashboard && npm install && cd ..

    echo ""
    echo -e "${GREEN}✓ Dependencies installed!${NC}"
    echo ""
    echo "  To start the system:"
    echo "    Terminal 1 (Server):    cd server && node server.js"
    echo "    Terminal 2 (Dashboard): cd dashboard && npm run dev"
    echo "    Terminal 3 (Camera):    python3 edge-node/capture_stream.py --server http://localhost:5000 --cam 0"
    echo ""

elif [ "$INSTALL_METHOD" = "demo" ]; then
    echo "  Setting up demo mode..."
    cd server && npm install && cd ..
    cd dashboard && npm install && cd ..

    echo ""
    echo -e "${GREEN}✓ Demo mode ready!${NC}"
    echo ""
    echo "  To start the demo:"
    echo "    Terminal 1: cd server && node server.js"
    echo "    Terminal 2: cd dashboard && npm run dev"
    echo "    Open: http://localhost:3000"
    echo ""
    echo "  The dashboard will show simulated data."
    echo "  No cameras needed for the demo."
    echo ""
fi

# ── Step 5: Post-install instructions ──
echo "╔══════════════════════════════════════════════════════════╗"
echo "║                    INSTALLATION COMPLETE                 ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "  Quick Start Guide:"
echo "  ─────────────────"
echo ""
echo "  1. Open dashboard in browser: http://localhost:3000"
echo "  2. Select a camera from the dropdown"
echo "  3. Choose a compression profile:"
echo "     • Balanced — normal campus operation"
echo "     • Ultra Low BW — for 3G/satellite connections"
echo "     • High Quality — for forensic investigations"
echo "     • Privacy Shield — GDPR compliant"
echo "  4. The system adapts automatically when events are detected"
echo ""
echo "  Need help? Read: docs/USER_DEPLOYMENT.md"
echo ""
