# User Deployment Guide

## Problem Statement
Your system works perfectly in a developer terminal, but a school administrator,
hospital IT staff, or security company cannot use it without technical knowledge.

## Solution: One-Command Deployment

### Option A: Docker (Recommended for Server/Cloud)
```bash
git clone https://github.com/yourusername/cctv-compression.git
cd cctv-compression
docker-compose up -d
# Open http://localhost:3000 in browser
```

### Option B: Raspberry Pi Edge Node (For each camera)
```bash
# On Raspberry Pi connected to camera
pip install -r requirements.txt
python3 capture_stream.py --server http://YOUR_SERVER_IP:5000 --cam 0
```

### Option C: Web Dashboard (No installation needed)
After server is running, anyone on the network can open:
- Dashboard: http://YOUR_SERVER_IP:3000
- Controls: Change compression, view live feed, see alerts

## User Scenarios

### Scenario 1: School Administrator
"I have 8 cameras in my school. Internet is slow. I want to save bandwidth
but still catch intruders."

Steps:
1. Install Docker on a PC in the office
2. Run docker-compose up -d
3. Connect cameras to the system via dashboard
4. Set profile to "Balanced Mode"
5. Done — bandwidth drops 60%, intruder alerts work

### Scenario 2: Hospital Security
"Patient privacy matters. I need monitoring but faces must be blurred."

Steps:
1. Deploy system (Docker or server)
2. Click "Privacy Shield" profile in dashboard
3. Ethical mode + face masking activates automatically
4. GDPR-compliant surveillance achieved

### Scenario 3: Smart City (100+ cameras)
"Bandwidth costs are destroying our budget."

Steps:
1. Deploy server on cloud (AWS/Azure)
2. Connect edge nodes to each camera
3. System auto-adapts: busy roads get high quality, empty streets get low
4. Scalability tested up to 32 cameras per server
