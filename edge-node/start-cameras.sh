#!/usr/bin/env bash
# start-cameras.sh — Launch one edge-node process per camera defined in configs/cameras.yaml
# Usage: ./start-cameras.sh [--server http://localhost:5000] [--config ../configs/cameras.yaml]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SERVER="http://127.0.0.1:5000"
CAMERAS_YAML="${SCRIPT_DIR}/../configs/cameras.yaml"
SERVER_URL="${DEFAULT_SERVER}"

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) SERVER_URL="$2"; shift 2 ;;
    --config) CAMERAS_YAML="$2"; shift 2 ;;
    --help) echo "Usage: $0 [--server URL] [--config PATH]"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [ ! -f "$CAMERAS_YAML" ]; then
  echo "ERROR: Cameras config not found at $CAMERAS_YAML"
  exit 1
fi

# Parse YAML with a simple Python snippet (PyYAML is already a dependency)
python3 -c "
import yaml, os, sys, subprocess, signal

with open('${CAMERAS_YAML}') as f:
    config = yaml.safe_load(f)

cameras = config.get('cameras', [])
enabled = [c for c in cameras if c.get('enabled', True)]

if not enabled:
    print('No enabled cameras found in config')
    sys.exit(0)

print(f'Starting {len(enabled)} camera edge-node(s)...')
procs = []
try:
    for cam in enabled:
        cam_id   = cam['id']
        source   = cam['source']
        profile  = cam.get('profile', 'balanced')
        udp_port = cam.get('udp_port', 1234)
        fps      = cam.get('fps', 15)
        name     = cam.get('name', cam_id)

        config_path = os.path.abspath(os.path.join('${SCRIPT_DIR}', '..', 'configs', f'{profile}.yaml'))
        if not os.path.exists(config_path):
            config_path = 'None'

        cmd = [
            sys.executable, '${SCRIPT_DIR}/capture_stream.py',
            '--server', '${SERVER_URL}',
            '--cam', str(source),
            '--camera-id', cam_id,
            '--udp-port', str(udp_port),
            '--fps', str(fps),
        ]
        if config_path != 'None':
            cmd += ['--config', config_path]

        print(f'  [{cam_id}] {name} -> source={source}, port={udp_port}, profile={profile}')
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
        procs.append(proc)

    print(f'\nAll {len(enabled)} camera(s) running. Press Ctrl+C to stop all.')
    for proc in procs:
        proc.wait()

except KeyboardInterrupt:
    print('\nShutting down all cameras...')
    for proc in procs:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    for proc in procs:
        proc.wait(timeout=5)
    print('All cameras stopped.')
"
