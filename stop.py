#!/usr/bin/env python3
"""Stop all CCTV system processes (cross-platform)."""
import subprocess
import sys
import os
import time

print("[STOP] Stopping CCTV system...")

if sys.platform != 'win32':
    # Unix: use pkill
    for pattern in ["camera_manager.py", "capture_stream.py", "node server.js"]:
        result = subprocess.run(["pkill", "-f", pattern], capture_output=True)
        name = pattern.split()[0].replace(".py", "")
        if result.returncode == 0:
            print(f"  ✓ {name} stopped")
        else:
            print(f"  - {name} was not running")

    # Kill orphaned FFmpeg
    subprocess.run(["pkill", "-f", "udp://127.0.0.1:123"], capture_output=True)

    # Free webcam
    subprocess.run(["fuser", "-k", "/dev/video0"], capture_output=True)

    # Force kill remaining on ports
    for port in [5000, 3000]:
        result = subprocess.run(["lsof", "-i", f":{port}", "-t"], capture_output=True, text=True)
        if result.stdout.strip():
            for pid in result.stdout.strip().split('\n'):
                subprocess.run(["kill", "-9", pid], capture_output=True)
            print(f"  ✓ Port {port} freed")
else:
    # Windows: use taskkill
    for proc in ["node.exe", "ffmpeg.exe"]:
        subprocess.run(["taskkill", "/F", "/IM", proc], capture_output=True)
    print("  ✓ Processes killed")

time.sleep(1)
print("\nAll stopped.")
