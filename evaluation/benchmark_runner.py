"""
benchmark_runner.py — Real Configuration Benchmark Runner

Benchmarks each YAML configuration profile against a test video:
  - ultra_low_bandwidth (5fps, 640x480, 200kbps, HEVC)
  - balanced (10fps, 1280x720, 800kbps, HEVC)
  - high_quality (24fps, 1920x1080, 2000kbps, HEVC)
  - privacy_mode (5fps, 1280x720, 500kbps, HEVC + blur)

Measures: PSNR, SSIM, VMAF (if available), bitrate, encode time, detection recall.

Usage:
    python3 benchmark_runner.py --input /path/to/video.mp4
    python3 benchmark_runner.py --input 0 --capture-seconds 30
"""

import os
import sys
import subprocess
import json
import csv
import time
import argparse
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import yaml

try:
    from skimage.metrics import structural_similarity as sk_ssim
    from skimage.metrics import peak_signal_noise_ratio as sk_psnr
    _SKIMAGE = True
except ImportError:
    _SKIMAGE = False

try:
    from ultralytics import YOLO
    _YOLO = True
except ImportError:
    _YOLO = False

# ── Configuration ──────────────────────────────────────────────────────────

CONFIGS_DIR = os.path.join(os.path.dirname(__file__), "..", "configs")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "yolov8n.pt")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "benchmark_results")


def load_config(config_path):
    """Load a YAML configuration profile."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_available_configs():
    """List all YAML config files."""
    configs = []
    for f in sorted(Path(CONFIGS_DIR).glob("*.yaml")):
        if f.name == "cameras.yaml":
            continue
        configs.append(str(f))
    return configs


# ── Encoding ───────────────────────────────────────────────────────────────

def encode_with_config(input_path, config, output_path):
    """Encode video using settings from a config profile."""
    edge = config.get("edge_node", {})
    encoder = edge.get("encoder", {})

    codec = encoder.get("codec", "libx265")
    preset = encoder.get("preset", "medium")
    bitrate_str = encoder.get("bitrate", "800k")
    maxrate_str = encoder.get("maxrate", "1000k")
    bufsize_str = encoder.get("bufsize", "2000k")
    gop_size = encoder.get("gop_size", 60)
    resolution = edge.get("resolution", "1280x720")

    # Parse resolution
    try:
        w, h = [int(x) for x in resolution.split("x")]
    except ValueError:
        w, h = 1280, 720

    # Parse bitrate strings
    bitrate = int(str(bitrate_str).replace("k", ""))
    maxrate = int(str(maxrate_str).replace("k", ""))
    bufsize = int(str(bufsize_str).replace("k", ""))

    # Build FFmpeg command
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", f"scale={w}:{h}",
        "-c:v", codec,
        "-preset", preset,
        "-b:v", f"{bitrate}k",
        "-maxrate", f"{maxrate}k",
        "-bufsize", f"{bufsize}k",
        "-g", str(gop_size),
        "-keyint_min", str(max(1, gop_size // 2)),
    ]

    # Codec-specific flags
    if codec in ("hevc_nvenc", "h264_nvenc"):
        cmd.extend(["-tune", encoder.get("tune", "ll")])

    cmd.append(output_path)

    start = time.time()
    try:
        result = subprocess.run(
            cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
            text=True, timeout=300
        )
        elapsed = time.time() - start
        if result.returncode != 0:
            print(f"  [ffmpeg error]: {result.stderr[-300:]}")
            return None, elapsed
        return output_path, elapsed
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"  [encode error]: {e}")
        return None, 0


# ── Metrics ────────────────────────────────────────────────────────────────

def compute_psnr_ssim(orig_path, recon_path, max_frames=100):
    """Compute PSNR and SSIM between two videos."""
    cap_a = cv2.VideoCapture(orig_path)
    cap_b = cv2.VideoCapture(recon_path)

    psnrs, ssims = [], []
    frame_idx = 0

    while frame_idx < max_frames:
        ok_a, fa = cap_a.read()
        ok_b, fb = cap_b.read()
        if not ok_a or not ok_b:
            break

        if fa.shape != fb.shape:
            fb = cv2.resize(fb, (fa.shape[1], fa.shape[0]))

        if _SKIMAGE:
            psnrs.append(float(sk_psnr(fa, fb, data_range=255)))
            ga = cv2.cvtColor(fa, cv2.COLOR_BGR2GRAY)
            gb = cv2.cvtColor(fb, cv2.COLOR_BGR2GRAY)
            ssims.append(float(sk_ssim(ga, gb, data_range=255)))
        else:
            mse = np.mean((fa.astype(np.float32) - fb.astype(np.float32)) ** 2)
            psnrs.append(100.0 if mse == 0 else 10 * np.log10(255**2 / mse))

        frame_idx += 1

    cap_a.release()
    cap_b.release()

    return {
        "psnr": float(np.mean(psnrs)) if psnrs else None,
        "ssim": float(np.mean(ssims)) if ssims else None,
        "frames_evaluated": frame_idx,
    }


def compute_vmaf(orig_path, recon_path):
    """Compute VMAF if ffmpeg has libvmaf available."""
    try:
        cmd = [
            "ffmpeg", "-i", recon_path, "-i", orig_path,
            "-lavfi", "libvmaf", "-f", "null", "-"
        ]
        result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, timeout=60)
        import re
        match = re.search(r"VMAF score: ([\d\.]+)", result.stderr)
        if match:
            return float(match.group(1))
    except Exception:
        pass
    return None


def compute_detection_recall(orig_path, recon_path, model, max_frames=30):
    """Check object detection recall after compression."""
    if model is None:
        return None

    cap_a = cv2.VideoCapture(orig_path)
    cap_b = cv2.VideoCapture(recon_path)

    orig_counts, recon_counts = [], []
    frame_idx = 0

    while frame_idx < max_frames:
        ok_a, fa = cap_a.read()
        ok_b, fb = cap_b.read()
        if not ok_a or not ok_b:
            break

        r_a = model(fa, verbose=False, conf=0.3)
        r_b = model(fb, verbose=False, conf=0.3)
        orig_counts.append(len(r_a[0].boxes))
        recon_counts.append(len(r_b[0].boxes))
        frame_idx += 1

    cap_a.release()
    cap_b.release()

    if not orig_counts or sum(orig_counts) == 0:
        return 1.0

    return min(np.mean(recon_counts) / max(np.mean(orig_counts), 1e-6), 1.0)


# ── Main Benchmark ────────────────────────────────────────────────────────

def run_benchmark(config_path, test_video, yolo_model=None):
    """Run benchmark for a single config profile."""
    config_name = Path(config_path).stem
    print(f"\n{'─'*60}")
    print(f"  Config: {config_name}")
    print(f"{'─'*60}")

    config = load_config(config_path)
    edge = config.get("edge_node", {})
    encoder = edge.get("encoder", {})

    print(f"  Codec: {encoder.get('codec', 'N/A')} | "
          f"Bitrate: {encoder.get('bitrate', 'N/A')} | "
          f"Resolution: {edge.get('resolution', 'N/A')} | "
          f"FPS: {edge.get('base_fps', 'N/A')}")

    # Get video duration
    cap = cv2.VideoCapture(test_video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s = total_frames / fps
    cap.release()

    # Encode
    output_path = os.path.join(OUTPUT_DIR, f"{config_name}_encoded.mp4")
    encoded, elapsed = encode_with_config(test_video, config, output_path)

    if encoded is None or not os.path.exists(output_path):
        print(f"  FAILED to encode")
        return None

    # File metrics
    file_size = os.path.getsize(output_path)
    actual_kbps = (file_size * 8 / 1000) / duration_s

    # Quality metrics
    quality = compute_psnr_ssim(test_video, output_path)
    vmaf = compute_vmaf(test_video, output_path)
    det_recall = compute_detection_recall(test_video, output_path, yolo_model)

    result = {
        "config": config_name,
        "codec": encoder.get("codec", "N/A"),
        "target_bitrate": encoder.get("bitrate", "N/A"),
        "resolution": edge.get("resolution", "N/A"),
        "fps_target": edge.get("base_fps", 15),
        "video": os.path.basename(test_video),
        "duration_s": round(duration_s, 1),
        "actual_bitrate_kbps": round(actual_kbps, 1),
        "psnr_db": round(quality["psnr"], 3) if quality["psnr"] else "",
        "ssim": round(quality["ssim"], 5) if quality["ssim"] else "",
        "vmaf": round(vmaf, 2) if vmaf else "",
        "detection_recall": round(det_recall, 4) if det_recall is not None else "",
        "encode_time_s": round(elapsed, 2),
        "file_size_bytes": file_size,
        "frames_evaluated": quality["frames_evaluated"],
        "timestamp": datetime.utcnow().isoformat(),
    }

    print(f"  PSNR: {quality['psnr']:.2f} dB | SSIM: {quality['ssim']:.4f} | "
          f"Bitrate: {actual_kbps:.0f} kbps | Time: {elapsed:.1f}s")
    if vmaf:
        print(f"  VMAF: {vmaf:.2f}")
    if det_recall is not None:
        print(f"  Detection Recall: {det_recall:.2%}")

    # Cleanup encoded file
    try:
        os.remove(output_path)
    except Exception:
        pass

    return result


def main():
    parser = argparse.ArgumentParser(description="Configuration Benchmark Runner")
    parser.add_argument("--input", "-i", required=True,
                        help="Test video path or camera index (e.g., 0)")
    parser.add_argument("--output", "-o", default=OUTPUT_DIR,
                        help="Output directory")
    parser.add_argument("--capture-seconds", type=int, default=30,
                        help="Seconds to capture from camera")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    # Get test video
    test_video = args.input
    try:
        cam_idx = int(args.input)
        raw_path = os.path.join(args.output, "captured_reference.avi")
        print(f"Capturing {args.capture_seconds}s from camera {cam_idx}...")
        cap = cv2.VideoCapture(cam_idx)
        fps = cap.get(cv2.CAP_PROP_FPS) or 15
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
        out = cv2.VideoWriter(raw_path, cv2.VideoWriter_fourcc(*'XVID'), fps, (w, h))
        start = time.time()
        while time.time() - start < args.capture_seconds:
            ok, frame = cap.read()
            if not ok:
                break
            out.write(frame)
        cap.release()
        out.release()
        test_video = raw_path
    except ValueError:
        if not os.path.exists(test_video):
            print(f"Input not found: {test_video}")
            sys.exit(1)

    # Load YOLO
    yolo_model = None
    if _YOLO and os.path.exists(MODEL_PATH):
        print(f"Loading YOLO: {MODEL_PATH}")
        yolo_model = YOLO(MODEL_PATH)

    # Run benchmarks
    configs = get_available_configs()
    print(f"\nRunning benchmarks on {len(configs)} config profiles...")
    print(f"Test video: {test_video}")

    results = []
    for config_path in configs:
        result = run_benchmark(config_path, test_video, yolo_model)
        if result:
            results.append(result)

    # Save CSV
    if results:
        csv_path = os.path.join(args.output, "benchmark_results.csv")
        fieldnames = list(results[0].keys())
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f"\nResults saved to: {csv_path}")

        # Summary table
        print(f"\n{'═'*80}")
        print(f"{'CONFIG':<25} {'CODEC':<12} {'BITRATE':>8} {'PSNR':>8} {'SSIM':>8} {'TIME':>6}")
        print(f"{'─'*80}")
        for r in results:
            print(f"{r['config']:<25} {r['codec']:<12} {str(r['actual_bitrate_kbps']):>7}k "
                  f"{str(r['psnr_db']):>8} {str(r['ssim']):>8} {r['encode_time_s']:>5.1f}s")
        print(f"{'═'*80}")


if __name__ == "__main__":
    main()
