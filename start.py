#!/usr/bin/env python3
"""
AI Risk-Aware CCTV Compression — Cross-Platform Launcher
Works on Windows, macOS, and Linux.
"""
import os
import sys
import subprocess
import signal
import time
import shutil

# ── Colors ──
class C:
    RED = '\033[0;31m' if sys.platform != 'win32' or os.environ.get('ANSICON') else ''
    GREEN = '\033[0;32m' if sys.platform != 'win32' or os.environ.get('ANSICON') else ''
    YELLOW = '\033[1;33m' if sys.platform != 'win32' or os.environ.get('ANSICON') else ''
    CYAN = '\033[0;36m' if sys.platform != 'win32' or os.environ.get('ANSICON') else ''
    BOLD = '\033[1m' if sys.platform != 'win32' or os.environ.get('ANSICON') else ''
    NC = '\033[0m' if sys.platform != 'win32' or os.environ.get('ANSICON') else ''

def log(msg):  print(f"{C.CYAN}[START]{C.NC} {msg}")
def ok(msg):   print(f"{C.GREEN}  ✓{C.NC} {msg}")
def warn(msg): print(f"{C.YELLOW}  ⚠{C.NC} {msg}")
def fail(msg): print(f"{C.RED}  ✗{C.NC} {msg}")

# ── Project paths ──
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSES = []

def cleanup(sig=None, frame=None):
    """Kill all child processes on exit."""
    print()
    log("Shutting down...")
    for p in PROCESSES:
        try:
            p.terminate()
            p.wait(timeout=3)
        except:
            try:
                p.kill()
            except:
                pass
    # Kill orphaned FFmpeg on UDP ports
    if sys.platform != 'win32':
        subprocess.run(["pkill", "-f", "udp://127.0.0.1:123"], capture_output=True)
        subprocess.run(["pkill", "-f", "node server.js"], capture_output=True)
    ok("All stopped.")
    sys.exit(0)

def is_port_free(port):
    """Check if a port is free."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) != 0

def wait_for_port(port, timeout=15):
    """Wait until a port is listening."""
    for _ in range(timeout):
        if not is_port_free(port):
            return True
        time.sleep(1)
    return False

def check_cmd(cmd):
    """Check if a command exists."""
    return shutil.which(cmd) is not None

# ── Main ──
def main():
    # Register cleanup
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════════╗{C.NC}
{C.CYAN}{C.BOLD}║  AI Risk-Aware CCTV Compression System                  ║{C.NC}
{C.CYAN}{C.BOLD}║  Adaptive Encoding • Risk Detection • Forensic Evidence  ║{C.NC}
{C.CYAN}{C.BOLD}╚══════════════════════════════════════════════════════════╝{C.NC}
""")

    # ── Check prerequisites ──
    log("Checking prerequisites...")

    if not check_cmd("node"):
        fail("Node.js not found!")
        print("  Install from: https://nodejs.org/")
        sys.exit(1)
    ok(f"Node.js found")

    if not check_cmd("python3") and not check_cmd("python"):
        fail("Python not found!")
        print("  Install from: https://python.org/")
        sys.exit(1)
    ok("Python found")

    if not check_cmd("ffmpeg"):
        fail("FFmpeg not found!")
        if sys.platform == 'darwin':
            print("  Install: brew install ffmpeg")
        elif sys.platform == 'win32':
            print("  Install from: https://ffmpeg.org/download.html")
        else:
            print("  Install: sudo apt install ffmpeg")
        sys.exit(1)
    ok("FFmpeg found")

    # ── Check Python packages ──
    log("Checking Python packages...")
    missing = []
    for pkg in ["cv2", "numpy", "socketio", "yaml", "requests", "PIL"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        warn(f"Missing packages: {', '.join(missing)}")
        log("Installing...")
        pip = "pip3" if check_cmd("pip3") else "pip"
        pkg_map = {"cv2": "opencv-python", "socketio": "python-socketio", "yaml": "PyYAML", "PIL": "Pillow"}
        install_names = [pkg_map.get(p, p) for p in missing]
        subprocess.run([pip, "install"] + install_names, check=False)
        ok("Packages installed")
    else:
        ok("Python packages OK")

    # ── Stop previous instances ──
    log("Stopping previous instances...")
    if sys.platform != 'win32':
        subprocess.run(["pkill", "-f", "node server.js"], capture_output=True)
        subprocess.run(["pkill", "-f", "camera_manager.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "capture_stream.py"], capture_output=True)
    else:
        subprocess.run(["taskkill", "/F", "/IM", "node.exe"], capture_output=True)
    time.sleep(1)
    ok("Clean slate")

    # ── Start Server ──
    log("Starting server on port 5000...")
    server_log = open("/tmp/cctv_server.log" if sys.platform != 'win32' else "cctv_server.log", "w")
    server_proc = subprocess.Popen(
        ["node", "server.js"],
        cwd=os.path.join(PROJECT_DIR, "server"),
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    PROCESSES.append(server_proc)

    if not wait_for_port(5000):
        fail("Server failed to start! Check logs.")
        cleanup()
    ok(f"Server ready (PID {server_proc.pid})")

    # ── Start Edge-Node ──
    log("Starting edge-node (auto-discovering cameras)...")
    edge_log = open("/tmp/cctv_edge.log" if sys.platform != 'win32' else "cctv_edge.log", "w")
    edge_proc = subprocess.Popen(
        [sys.executable, "-u", "camera_manager.py",
         "--server", "http://127.0.0.1:5000",
         "--config", "../configs/cameras.yaml",
         "--use-optimizer",
         "--no-audio",
         "--discover"],
        cwd=os.path.join(PROJECT_DIR, "edge-node"),
        stdout=edge_log,
        stderr=subprocess.STDOUT,
    )
    PROCESSES.append(edge_proc)
    time.sleep(5)
    if edge_proc.poll() is None:
        ok(f"Edge-node started (PID {edge_proc.pid})")
    else:
        warn("Edge-node exited early. Check: tail cctv_edge.log")

    # ── Start Dashboard ──
    log("Starting dashboard on port 3000...")
    if not is_port_free(3000):
        ok("Dashboard already running")
    else:
        dash_log = open("/tmp/cctv_dashboard.log" if sys.platform != 'win32' else "cctv_dashboard.log", "w")
        dash_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=os.path.join(PROJECT_DIR, "dashboard"),
            stdout=dash_log,
            stderr=subprocess.STDOUT,
        )
        PROCESSES.append(dash_proc)
        time.sleep(3)
        ok("Dashboard starting at http://localhost:3000")

    # ── Count cameras ──
    cam_count = 0
    try:
        import yaml
        with open(os.path.join(PROJECT_DIR, "configs", "cameras.yaml")) as f:
            cfg = yaml.safe_load(f)
            cam_count = len([c for c in cfg.get("cameras", []) if c.get("enabled", True)])
    except:
        pass

    # ── Status ──
    print(f"""
{C.CYAN}{C.BOLD}══════════════════════════════════════════════════════════{C.NC}
{C.GREEN}{C.BOLD}  System is running!{C.NC}
{C.CYAN}{C.BOLD}══════════════════════════════════════════════════════════{C.NC}

  {C.BOLD}Dashboard:{C.NC}  http://localhost:3000
  {C.BOLD}Server API:{C.NC} http://localhost:5000
  {C.BOLD}Settings:{C.NC}   http://localhost:3000/config
  {C.BOLD}Cameras:{C.NC}    {cam_count} configured

  {C.BOLD}Stop:{C.NC}       Press Ctrl+C or run: python stop.py
{C.CYAN}{C.BOLD}══════════════════════════════════════════════════════════{C.NC}
""")

    # ── Wait ──
    try:
        server_proc.wait()
    except KeyboardInterrupt:
        cleanup()

if __name__ == "__main__":
    main()
