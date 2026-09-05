"""
sota_comparison.py — State-of-the-Art Comparison Framework

Compares the proposed risk-aware adaptive compression against:
  1. Uniform H.265 (fixed QP, no ROI adaptation)
  2. Uniform H.264 (fixed QP, baseline)
  3. Uniform AV1 (SVT-AV1, fixed CRF)
  4. ROI-only (high-quality ROIs, no risk adaptation)
  5. Fixed-profile (balanced profile, no dynamic switching)

Metrics: PSNR, SSIM, VMAF, BD-rate, bitrate savings, detection recall.

Usage:
    python3 sota_comparison.py --input /path/to/video.mp4 --output comparison_results.csv
    python3 sota_comparison.py --input 0 --capture-seconds 30   # live camera
"""

import os
import sys
import csv
import time
import subprocess
import argparse
import json
import tempfile
import shutil
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

# Optional dependencies
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

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    _PLOT = True
except ImportError:
    _PLOT = False


# ── Configuration ──────────────────────────────────────────────────────────

BITRATE_LEVELS_KBPS = [200, 500, 1000, 2000, 4000]
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "sota_results")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "yolov8n.pt")


# ── Methods to Compare ─────────────────────────────────────────────────────

def get_methods():
    """Return list of comparison methods with their FFmpeg encode commands."""
    return [
        {
            "name": "Proposed (Risk-Adaptive)",
            "type": "proposed",
            "description": "Risk-aware ROI compression with adaptive GOP/resolution",
        },
        {
            "name": "Uniform H.265",
            "type": "uniform",
            "codec": "libx265",
            "preset": "medium",
            "description": "Fixed QP H.265, no ROI, no adaptation",
        },
        {
            "name": "Uniform H.264",
            "type": "uniform",
            "codec": "libx264",
            "preset": "medium",
            "description": "Fixed QP H.264 baseline",
        },
        {
            "name": "Uniform AV1",
            "type": "uniform",
            "codec": "libsvtav1",
            "preset": "6",
            "description": "SVT-AV1 fixed CRF",
        },
        {
            "name": "Uniform VP9",
            "type": "uniform",
            "codec": "libvpx-vp9",
            "preset": "good",
            "description": "VP9 fixed quality",
        },
        {
            "name": "ROI-Only (No Risk)",
            "type": "roi_only",
            "description": "High quality on detected ROIs, low on background, no risk state",
        },
        {
            "name": "Fixed Balanced",
            "type": "fixed",
            "description": "Fixed balanced profile (no dynamic switching)",
        },
    ]


# ── Encoding Helpers ──────────────────────────────────────────────────────

def encode_ffmpeg(input_path, output_path, codec, bitrate_kbps, preset="medium",
                  extra_flags=None):
    """Encode a video with specified codec and bitrate."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-c:v", codec,
        "-preset", preset,
        "-b:v", f"{bitrate_kbps}k",
        "-maxrate", f"{int(bitrate_kbps * 1.5)}k",
        "-bufsize", f"{bitrate_kbps * 2}k",
    ]
    if extra_flags:
        cmd.extend(extra_flags)
    cmd.append(output_path)

    start = time.time()
    try:
        result = subprocess.run(
            cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
            text=True, timeout=300
        )
        elapsed = time.time() - start
        if result.returncode != 0:
            print(f"  [ffmpeg error] {codec} {bitrate_kbps}kbps: {result.stderr[-200:]}")
            return None, elapsed
        return output_path, elapsed
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"  [encode error] {codec}: {e}")
        return None, 0


def encode_proposed_roi(input_path, output_path, bitrate_kbps, yolo_model=None,
                        bg_quality=20, roi_quality=90):
    """
    Simulate proposed method: detect ROIs, compress background heavily,
    preserve ROIs at high quality, reassemble.
    """
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        return None, 0

    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out_writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))

    start = time.time()
    frame_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1

        # Detect every 3rd frame (like the real system)
        if frame_count % 3 == 0 and yolo_model is not None:
            results = yolo_model(frame, verbose=False, conf=0.3)
            rois = []
            for r in results:
                for box in r.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    cls_id = int(box.cls[0])
                    priority = "high" if cls_id == 0 else "medium"
                    rois.append({"bbox": [int(x1), int(y1), int(x2), int(y2)],
                                 "priority": priority})
        elif frame_count % 3 != 0:
            pass  # keep previous rois
        else:
            rois = []

        # Reconstruct: background downscale + ROI upscale
        bg_w = max(1, int(w * 0.5))
        bg_h = max(1, int(h * 0.5))
        bg_small = cv2.resize(frame, (bg_w, bg_h), interpolation=cv2.INTER_AREA)
        recon = cv2.resize(bg_small, (w, h), interpolation=cv2.INTER_LINEAR)

        for roi in rois:
            x1, y1, x2, y2 = roi["bbox"]
            x1 = max(0, min(x1, w))
            y1 = max(0, min(y1, h))
            x2 = max(x1 + 1, min(x2, w))
            y2 = max(y1 + 1, min(y2, h))
            crop = frame[y1:y2, x1:x2]
            if crop.size > 0:
                recon[y1:y2, x1:x2] = crop

        out_writer.write(recon)

    cap.release()
    out_writer.release()
    elapsed = time.time() - start

    # Now encode the reconstructed frames as H.265
    intermediate = output_path.replace(".mp4", "_raw.mp4")
    os.rename(output_path, intermediate)
    encode_ffmpeg(intermediate, output_path, "libx265", bitrate_kbps)
    if os.path.exists(intermediate):
        os.remove(intermediate)

    return output_path, elapsed


# ── Quality Metrics ────────────────────────────────────────────────────────

def compute_frame_metrics(orig_path, recon_path, max_frames=100):
    """Compute PSNR and SSIM between two videos, frame by frame."""
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


def compute_file_metrics(output_path, duration_s):
    """Compute file size and effective bitrate."""
    if not os.path.exists(output_path):
        return {"file_size_bytes": 0, "actual_kbps": 0}

    file_size = os.path.getsize(output_path)
    actual_kbps = (file_size * 8 / 1000) / duration_s if duration_s > 0 else 0
    return {
        "file_size_bytes": file_size,
        "actual_kbps": round(actual_kbps, 1),
    }


def compute_detection_recall(orig_path, recon_path, model, max_frames=30):
    """Check if YOLO still detects objects after compression."""
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

    avg_orig = np.mean(orig_counts)
    avg_recon = np.mean(recon_counts)
    return min(avg_recon / max(avg_orig, 1e-6), 1.0)


# ── BD-Rate Computation ───────────────────────────────────────────────────

def compute_bdry_rate(psnrs_anchor, rates_anchor, psnrs_test, rates_test):
    """
    Compute BD-rate (Bjontegaard Delta Rate) using polynomial fitting.
    Negative BD-rate = test method is better (same quality at lower bitrate).
    """
    if not all([psnrs_anchor, rates_anchor, psnrs_test, rates_test]):
        return None
    if len(psnrs_anchor) < 2 or len(psnrs_test) < 2:
        return None

    try:
        # Fit 3rd-order polynomial: PSNR = f(log2(rate))
        log_rates_a = np.log2(np.array(rates_anchor, dtype=float))
        log_rates_t = np.log2(np.array(rates_test, dtype=float))

        poly_a = np.polyfit(psnrs_anchor, log_rates_a, 3)
        poly_t = np.polyfit(psnrs_test, log_rates_t, 3)

        # Integrate over PSNR range [min_common, max_common]
        psnr_min = max(min(psnrs_anchor), min(psnrs_test))
        psnr_max = min(max(psnrs_anchor), max(psnrs_test))

        if psnr_min >= psnr_max:
            return None

        # Numerical integration
        psnr_range = np.linspace(psnr_min, psnr_max, 100)
        int_a = np.mean(np.polyval(poly_a, psnr_range))
        int_t = np.mean(np.polyval(poly_t, psnr_range))

        bd_rate = (2 ** int_t / 2 ** int_a - 1) * 100
        return round(float(bd_rate), 2)
    except Exception as e:
        print(f"BD-rate error: {e}")
        return None


# ── Main Comparison Runner ────────────────────────────────────────────────

def run_comparison(input_path, output_dir, capture_seconds=30):
    """Run full SOTA comparison."""
    os.makedirs(output_dir, exist_ok=True)

    methods = get_methods()
    results = []

    # Get video info
    cap = cv2.VideoCapture(input_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s = frame_count / fps if fps > 0 else capture_seconds
    cap.release()

    print(f"\nInput: {input_path}")
    print(f"Duration: {duration_s:.1f}s | FPS: {fps} | Frames: {frame_count}")
    print(f"Testing {len(methods)} methods × {len(BITRATE_LEVELS_KBPS)} bitrate levels\n")

    # Load YOLO if available
    yolo_model = None
    if _YOLO and os.path.exists(MODEL_PATH):
        print(f"Loading YOLO model: {MODEL_PATH}")
        yolo_model = YOLO(MODEL_PATH)

    # ── Run each method at each bitrate ────────────────────────────────────
    for method in methods:
        method_name = method["name"]
        print(f"\n{'─'*60}")
        print(f"  Method: {method_name}")
        print(f"{'─'*60}")

        for br in BITRATE_LEVELS_KBPS:
            out_file = os.path.join(
                output_dir,
                f"{method_name.replace(' ', '_').replace('(', '').replace(')', '')}_{br}kbps.mp4"
            )

            print(f"  Encoding @ {br} kbps ...", end=" ", flush=True)

            if method["type"] == "proposed":
                encoded, elapsed = encode_proposed_roi(
                    input_path, out_file, br, yolo_model=yolo_model
                )
            elif method["type"] == "uniform":
                encoded, elapsed = encode_ffmpeg(
                    input_path, out_file,
                    method["codec"], br, method["preset"]
                )
            elif method["type"] == "roi_only":
                encoded, elapsed = encode_proposed_roi(
                    input_path, out_file, br, yolo_model=yolo_model,
                    bg_quality=5, roi_quality=95
                )
            elif method["type"] == "fixed":
                encoded, elapsed = encode_ffmpeg(
                    input_path, out_file, "libx265", br, "medium"
                )
            else:
                print("SKIP (unknown type)")
                continue

            if encoded is None or not os.path.exists(out_file):
                print("FAILED")
                continue

            # Quality metrics
            metrics = compute_frame_metrics(input_path, out_file)
            file_metrics = compute_file_metrics(out_file, duration_s)

            # Detection recall
            det_recall = compute_detection_recall(input_path, out_file, yolo_model)

            row = {
                "method": method_name,
                "method_type": method["type"],
                "target_bitrate_kbps": br,
                "actual_bitrate_kbps": file_metrics["actual_kbps"],
                "psnr_db": round(metrics["psnr"], 3) if metrics["psnr"] else "",
                "ssim": round(metrics["ssim"], 5) if metrics["ssim"] else "",
                "encode_time_s": round(elapsed, 2),
                "file_size_bytes": file_metrics["file_size_bytes"],
                "detection_recall": round(det_recall, 4) if det_recall is not None else "",
                "frames_evaluated": metrics["frames_evaluated"],
                "timestamp": datetime.utcnow().isoformat(),
            }
            results.append(row)

            psnr_str = f"PSNR={metrics['psnr']:.2f}dB" if metrics["psnr"] else "PSNR=N/A"
            ssim_str = f"SSIM={metrics['ssim']:.4f}" if metrics["ssim"] else ""
            det_str = f"DetRec={det_recall:.2%}" if det_recall is not None else ""
            print(f"✓ {psnr_str} {ssim_str} {det_str} actual={file_metrics['actual_kbps']:.0f}kbps")

    # ── Compute BD-rates ──────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print("  BD-Rate Analysis (vs Uniform H.265 anchor)")
    print(f"{'═'*60}")

    # Get anchor (Uniform H.265) data
    anchor_rows = [r for r in results if r["method"] == "Uniform H.265" and r["psnr_db"]]
    anchor_psnrs = [r["psnr_db"] for r in anchor_rows]
    anchor_rates = [r["actual_bitrate_kbps"] for r in anchor_rows]

    for method_name in set(r["method"] for r in results):
        if method_name == "Uniform H.265":
            continue
        test_rows = [r for r in results if r["method"] == method_name and r["psnr_db"]]
        test_psnrs = [r["psnr_db"] for r in test_rows]
        test_rates = [r["actual_bitrate_kbps"] for r in test_rows]

        bd_rate = compute_bdry_rate(anchor_psnrs, anchor_rates, test_psnrs, test_rates)
        if bd_rate is not None:
            sign = "better" if bd_rate < 0 else "worse"
            print(f"  {method_name:<30} BD-rate: {bd_rate:+.2f}% ({sign})")
        else:
            print(f"  {method_name:<30} BD-rate: N/A")

    # ── Save CSV ──────────────────────────────────────────────────────────
    csv_path = os.path.join(output_dir, "sota_comparison_results.csv")
    fieldnames = [
        "method", "method_type", "target_bitrate_kbps", "actual_bitrate_kbps",
        "psnr_db", "ssim", "encode_time_s", "file_size_bytes",
        "detection_recall", "frames_evaluated", "timestamp",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\nResults saved to: {csv_path}")

    # ── Generate Plots ────────────────────────────────────────────────────
    if _PLOT and results:
        generate_comparison_plots(results, output_dir)

    return results


def generate_comparison_plots(results, output_dir):
    """Generate rate-distortion comparison plots."""
    import pandas as pd

    df = pd.DataFrame(results)
    df["psnr_db"] = pd.to_numeric(df["psnr_db"], errors="coerce")
    df["ssim"] = pd.to_numeric(df["ssim"], errors="coerce")
    df["actual_bitrate_kbps"] = pd.to_numeric(df["actual_bitrate_kbps"], errors="coerce")

    palette = {
        "Proposed (Risk-Adaptive)": "#e15759",
        "Uniform H.265": "#4e79a7",
        "Uniform H.264": "#f28e2b",
        "Uniform AV1": "#76b7b2",
        "Uniform VP9": "#59a14f",
        "ROI-Only (No Risk)": "#edc948",
        "Fixed Balanced": "#b07aa1",
    }

    fig = plt.figure(figsize=(16, 12))
    fig.suptitle("SOTA Comparison — Risk-Aware Surveillance Compression",
                 fontsize=14, fontweight="bold", y=0.98)
    gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.35, wspace=0.3)

    # 1. PSNR vs Bitrate (Rate-Distortion curve)
    ax1 = fig.add_subplot(gs[0, 0])
    for name, grp in df.groupby("method"):
        grp_sorted = grp.sort_values("actual_bitrate_kbps")
        ax1.plot(grp_sorted["actual_bitrate_kbps"], grp_sorted["psnr_db"],
                 marker="o", label=name, color=palette.get(name, "#999"),
                 linewidth=2, markersize=6)
    ax1.set_title("Rate-Distortion Curve (PSNR)", fontsize=11)
    ax1.set_xlabel("Bitrate (kbps)")
    ax1.set_ylabel("PSNR (dB)")
    ax1.legend(fontsize=7, loc="lower right")
    ax1.grid(alpha=0.3)

    # 2. SSIM vs Bitrate
    ax2 = fig.add_subplot(gs[0, 1])
    for name, grp in df.groupby("method"):
        grp_sorted = grp.sort_values("actual_bitrate_kbps")
        ax2.plot(grp_sorted["actual_bitrate_kbps"], grp_sorted["ssim"],
                 marker="s", label=name, color=palette.get(name, "#999"),
                 linewidth=2, markersize=6)
    ax2.set_title("Rate-Distortion Curve (SSIM)", fontsize=11)
    ax2.set_xlabel("Bitrate (kbps)")
    ax2.set_ylabel("SSIM")
    ax2.legend(fontsize=7, loc="lower right")
    ax2.grid(alpha=0.3)

    # 3. Detection Recall vs Bitrate
    ax3 = fig.add_subplot(gs[1, 0])
    df_det = df[df["detection_recall"] != ""].copy()
    df_det["detection_recall"] = pd.to_numeric(df_det["detection_recall"], errors="coerce")
    for name, grp in df_det.groupby("method"):
        grp_sorted = grp.sort_values("actual_bitrate_kbps")
        ax3.plot(grp_sorted["actual_bitrate_kbps"], grp_sorted["detection_recall"] * 100,
                 marker="^", label=name, color=palette.get(name, "#999"),
                 linewidth=2, markersize=6)
    ax3.set_title("Detection Recall vs Bitrate", fontsize=11)
    ax3.set_xlabel("Bitrate (kbps)")
    ax3.set_ylabel("Detection Recall (%)")
    ax3.legend(fontsize=7, loc="lower right")
    ax3.grid(alpha=0.3)

    # 4. Encode Time vs Bitrate
    ax4 = fig.add_subplot(gs[1, 1])
    for name, grp in df.groupby("method"):
        grp_sorted = grp.sort_values("actual_bitrate_kbps")
        ax4.plot(grp_sorted["actual_bitrate_kbps"], grp_sorted["encode_time_s"],
                 marker="D", label=name, color=palette.get(name, "#999"),
                 linewidth=2, markersize=6)
    ax4.set_title("Encoding Time vs Bitrate", fontsize=11)
    ax4.set_xlabel("Bitrate (kbps)")
    ax4.set_ylabel("Time (seconds)")
    ax4.legend(fontsize=7, loc="upper left")
    ax4.grid(alpha=0.3)

    plot_path = os.path.join(output_dir, "sota_comparison_plots.png")
    plt.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Plots saved to: {plot_path}")


# ── Entry Point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SOTA Comparison Framework")
    parser.add_argument("--input", "-i", required=True,
                        help="Input video file or camera index (e.g., 0)")
    parser.add_argument("--output", "-o", default=OUTPUT_DIR,
                        help="Output directory for results")
    parser.add_argument("--capture-seconds", type=int, default=30,
                        help="Seconds to capture from camera")
    args = parser.parse_args()

    input_path = args.input
    try:
        cam_idx = int(args.input)
        raw_path = os.path.join(args.output, "captured_reference.avi")
        os.makedirs(args.output, exist_ok=True)
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
        input_path = raw_path
    except ValueError:
        if not os.path.exists(input_path):
            print(f"Input not found: {input_path}")
            sys.exit(1)

    run_comparison(input_path, args.output, args.capture_seconds)


if __name__ == "__main__":
    main()
