#!/usr/bin/env python3
"""
run_av1_benchmark.py — Comparative codec benchmark.

Encodes a test video clip (or camera capture) with H.264, H.265, AV1, and VP9
at multiple bitrates and measures:
  - Output file size / effective bitrate
  - PSNR (dB)
  - SSIM
  - Encoding time (seconds)
  - Bitrate efficiency vs H.264 baseline

Results are saved to:
  evaluation/codec_benchmark_results.csv
  evaluation/codec_benchmark_plots.png

Usage:
  python3 run_av1_benchmark.py --input /path/to/clip.mp4
  python3 run_av1_benchmark.py --input 0          # live camera (30s capture)
  python3 run_av1_benchmark.py --help
"""

import os
import sys
import csv
import time
import subprocess
import argparse
import json
import tempfile
from datetime import datetime

import cv2
import numpy as np

# Optional: matplotlib for plots
try:
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    _PLOT = True
except ImportError:
    _PLOT = False
    print("[bench] matplotlib not available — skipping plots")

# Optional: scikit-image for SSIM
try:
    from skimage.metrics import structural_similarity as sk_ssim
    from skimage.metrics import peak_signal_noise_ratio as sk_psnr
    _SKIMAGE = True
except ImportError:
    _SKIMAGE = False

# ── Configuration ──────────────────────────────────────────────────────────────

CODECS = [
    {"name": "H.264",  "codec": "libx264",   "ext": "mp4",  "extra": ["-preset", "medium"]},
    {"name": "H.265",  "codec": "libx265",   "ext": "mp4",  "extra": ["-preset", "medium"]},
    {"name": "AV1",    "codec": "libsvtav1", "ext": "mp4",  "extra": ["-preset", "6"]},
    {"name": "VP9",    "codec": "libvpx-vp9","ext": "webm", "extra": ["-deadline", "good", "-cpu-used", "4"]},
]

BITRATES_KBPS = [300, 600, 1000, 2000, 4000]

OUTPUT_DIR  = os.path.join(os.path.dirname(__file__), "codec_benchmark")
OUTPUT_CSV  = os.path.join(os.path.dirname(__file__), "codec_benchmark_results.csv")
OUTPUT_PLOT = os.path.join(os.path.dirname(__file__), "codec_benchmark_plots.png")

CAPTURE_SECONDS = 30   # seconds to capture from camera if --input is an int

# ── Helpers ────────────────────────────────────────────────────────────────────

def run_ffmpeg(cmd, description=""):
    start = time.time()
    try:
        res = subprocess.run(
            cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
            text=True, timeout=300
        )
        elapsed = time.time() - start
        if res.returncode != 0:
            print(f"  [ffmpeg error] {description}: {res.stderr[-300:]}")
            return None, elapsed
        return elapsed, elapsed
    except subprocess.TimeoutExpired:
        print(f"  [timeout] {description}")
        return None, 0.0
    except Exception as e:
        print(f"  [error] {description}: {e}")
        return None, 0.0


def compute_psnr_ssim(orig_path, recon_path):
    """Frame-by-frame PSNR and SSIM between two video files."""
    cap_a = cv2.VideoCapture(orig_path)
    cap_b = cv2.VideoCapture(recon_path)

    psnrs, ssims = [], []

    while True:
        ok_a, fa = cap_a.read()
        ok_b, fb = cap_b.read()
        if not ok_a or not ok_b:
            break
        # Resize b to match a if needed
        if fa.shape != fb.shape:
            fb = cv2.resize(fb, (fa.shape[1], fa.shape[0]))

        if _SKIMAGE:
            psnrs.append(float(sk_psnr(fa, fb, data_range=255)))
            gray_a = cv2.cvtColor(fa, cv2.COLOR_BGR2GRAY)
            gray_b = cv2.cvtColor(fb, cv2.COLOR_BGR2GRAY)
            ssims.append(float(sk_ssim(gray_a, gray_b, data_range=255)))
        else:
            mse = np.mean((fa.astype(np.float32) - fb.astype(np.float32)) ** 2)
            psnr = 100.0 if mse == 0 else 10 * np.log10(255**2 / mse)
            psnrs.append(psnr)

    cap_a.release()
    cap_b.release()

    mean_psnr = float(np.mean(psnrs)) if psnrs else None
    mean_ssim = float(np.mean(ssims)) if ssims else None
    return mean_psnr, mean_ssim


def capture_from_camera(source, seconds=30, out_path=None):
    """Capture N seconds from a camera source and save to out_path (raw frames .avi)."""
    if out_path is None:
        out_path = os.path.join(OUTPUT_DIR, "captured_reference.avi")
    print(f"[bench] Capturing {seconds}s from camera '{source}' -> {out_path}")
    try:
        src = int(source)
    except ValueError:
        src = source

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        print(f"[bench] Cannot open camera: {source}")
        sys.exit(1)

    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)  or 640)
    h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
    out = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*'XVID'), fps, (w, h))

    start = time.time()
    frames = 0
    while time.time() - start < seconds:
        ok, frame = cap.read()
        if not ok:
            break
        out.write(frame)
        frames += 1
        if frames % int(fps) == 0:
            print(f"  {int(time.time() - start)}s / {seconds}s", end='\r', flush=True)

    cap.release()
    out.release()
    print(f"\n[bench] Captured {frames} frames ({frames/fps:.1f}s) to {out_path}")
    return out_path


# ── Main benchmark ─────────────────────────────────────────────────────────────

def run_benchmark(input_path):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    rows = []
    fieldnames = [
        "codec", "bitrate_target_kbps", "bitrate_actual_kbps",
        "encode_time_s", "file_size_bytes",
        "psnr_db", "ssim",
        "bd_rate_vs_h264",   # placeholder
        "timestamp",
    ]

    # Check which codecs are available
    available_codecs = []
    for c in CODECS:
        check = subprocess.run(
            ["ffmpeg", "-encoders"], capture_output=True, text=True, timeout=5
        )
        if c["codec"] in check.stdout:
            available_codecs.append(c)
        else:
            print(f"[bench] ⚠ Codec {c['codec']} ({c['name']}) not available in FFmpeg — skipping")

    if not available_codecs:
        print("[bench] No codecs available. Install FFmpeg with --enable-libx265 --enable-libsvtav1")
        sys.exit(1)

    print(f"\n[bench] Testing {len(available_codecs)} codec(s) × {len(BITRATES_KBPS)} bitrates = "
          f"{len(available_codecs)*len(BITRATES_KBPS)} experiments\n")

    # Get input duration for bitrate calculation
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True, text=True, timeout=10
    )
    try:
        duration_s = float(probe.stdout.strip())
    except Exception:
        duration_s = CAPTURE_SECONDS

    for codec_def in available_codecs:
        codec_name = codec_def["name"]
        codec      = codec_def["codec"]
        ext        = codec_def["ext"]
        extra      = codec_def["extra"]

        print(f"\n{'─'*50}")
        print(f"  Codec: {codec_name} ({codec})")
        print(f"{'─'*50}")

        for br in BITRATES_KBPS:
            out_file = os.path.join(OUTPUT_DIR, f"{codec}_{br}kbps.{ext}")
            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-c:v", codec,
                *extra,
                "-b:v", f"{br}k",
                "-maxrate", f"{int(br*1.5)}k",
                "-bufsize", f"{br*2}k",
                out_file
            ]

            print(f"  Encoding @ {br} kbps ...", end=" ", flush=True)
            elapsed, _ = run_ffmpeg(cmd, f"{codec_name} {br}kbps")
            if elapsed is None:
                print("FAILED")
                continue

            if not os.path.exists(out_file):
                print("OUTPUT MISSING")
                continue

            file_size = os.path.getsize(out_file)
            actual_kbps = (file_size * 8 / 1000) / duration_s if duration_s > 0 else 0

            psnr, ssim = compute_psnr_ssim(input_path, out_file)
            print(f"✓ PSNR={psnr:.2f}dB SSIM={ssim:.4f} actual={actual_kbps:.0f}kbps time={elapsed:.1f}s"
                  if psnr else f"✓ actual={actual_kbps:.0f}kbps time={elapsed:.1f}s")

            row = {
                "codec":               codec_name,
                "bitrate_target_kbps": br,
                "bitrate_actual_kbps": round(actual_kbps, 1),
                "encode_time_s":       round(elapsed, 2),
                "file_size_bytes":     file_size,
                "psnr_db":             round(psnr, 3) if psnr else "",
                "ssim":                round(ssim, 5) if ssim else "",
                "bd_rate_vs_h264":     "",
                "timestamp":           datetime.utcnow().isoformat(),
            }
            rows.append(row)

            # Write CSV incrementally
            write_header = not os.path.exists(OUTPUT_CSV)
            with open(OUTPUT_CSV, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                if write_header:
                    w.writeheader()
                w.writerow(row)

    # ── Print summary table ──────────────────────────────────────────────────
    print(f"\n{'═'*75}")
    print(f"{'CODEC':<10} {'TARGET':>8} {'ACTUAL':>8} {'PSNR':>8} {'SSIM':>7} {'TIME':>6}")
    print(f"{'─'*75}")
    for r in rows:
        print(f"{r['codec']:<10} {r['bitrate_target_kbps']:>6}k {r['bitrate_actual_kbps']:>6.0f}k "
              f"{str(r['psnr_db']):>8} {str(r['ssim']):>7} {r['encode_time_s']:>5.1f}s")
    print(f"{'═'*75}")
    print(f"\nResults saved to: {OUTPUT_CSV}")

    # ── Generate plots ───────────────────────────────────────────────────────
    if _PLOT and rows:
        try:
            import pandas as pd
            df = pd.DataFrame(rows)
            df["psnr_db"] = pd.to_numeric(df["psnr_db"], errors="coerce")
            df["ssim"]    = pd.to_numeric(df["ssim"],    errors="coerce")

            fig = plt.figure(figsize=(14, 10))
            fig.suptitle("Codec Comparative Benchmark — Adaptive CCTV Compression",
                         fontsize=14, fontweight="bold")
            gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.4, wspace=0.35)

            palette = {"H.264": "#4e79a7", "H.265": "#f28e2b",
                       "AV1":   "#e15759", "VP9":   "#76b7b2"}

            # PSNR vs Bitrate
            ax1 = fig.add_subplot(gs[0, 0])
            for name, grp in df.groupby("codec"):
                ax1.plot(grp["bitrate_target_kbps"], grp["psnr_db"],
                         marker="o", label=name, color=palette.get(name))
            ax1.set_title("PSNR vs Target Bitrate"); ax1.set_xlabel("kbps"); ax1.set_ylabel("PSNR (dB)")
            ax1.legend(); ax1.grid(alpha=0.3)

            # SSIM vs Bitrate
            ax2 = fig.add_subplot(gs[0, 1])
            for name, grp in df.groupby("codec"):
                ax2.plot(grp["bitrate_target_kbps"], grp["ssim"],
                         marker="s", label=name, color=palette.get(name))
            ax2.set_title("SSIM vs Target Bitrate"); ax2.set_xlabel("kbps"); ax2.set_ylabel("SSIM")
            ax2.legend(); ax2.grid(alpha=0.3)

            # Encode time vs Bitrate
            ax3 = fig.add_subplot(gs[1, 0])
            for name, grp in df.groupby("codec"):
                ax3.plot(grp["bitrate_target_kbps"], grp["encode_time_s"],
                         marker="^", label=name, color=palette.get(name))
            ax3.set_title("Encode Time vs Bitrate"); ax3.set_xlabel("kbps"); ax3.set_ylabel("Time (s)")
            ax3.legend(); ax3.grid(alpha=0.3)

            # PSNR vs Actual Bitrate (rate-distortion curve)
            ax4 = fig.add_subplot(gs[1, 1])
            for name, grp in df.groupby("codec"):
                ax4.plot(grp["bitrate_actual_kbps"], grp["psnr_db"],
                         marker="D", label=name, color=palette.get(name))
            ax4.set_title("Rate-Distortion Curve"); ax4.set_xlabel("Actual kbps"); ax4.set_ylabel("PSNR (dB)")
            ax4.legend(); ax4.grid(alpha=0.3)

            plt.savefig(OUTPUT_PLOT, dpi=150, bbox_inches="tight")
            plt.close()
            print(f"Plots saved to: {OUTPUT_PLOT}")
        except Exception as e:
            print(f"[bench] Plot error: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Codec benchmark: H.264 vs H.265 vs AV1 vs VP9"
    )
    parser.add_argument(
        "--input", "-i", required=True,
        help="Input video file path OR camera index (e.g. 0) for live capture"
    )
    parser.add_argument(
        "--capture-seconds", type=int, default=CAPTURE_SECONDS,
        help=f"Seconds to capture from camera (default: {CAPTURE_SECONDS})"
    )
    args = parser.parse_args()

    # Determine input
    input_path = args.input
    try:
        cam_idx = int(args.input)
        # Capture from camera
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        raw_path = os.path.join(OUTPUT_DIR, "reference.avi")
        input_path = capture_from_camera(cam_idx, args.capture_seconds, raw_path)
    except ValueError:
        if not os.path.exists(input_path):
            print(f"[bench] Input file not found: {input_path}")
            sys.exit(1)

    run_benchmark(input_path)


if __name__ == "__main__":
    main()
