#!/usr/bin/env python3
"""
Comprehensive CCTV Compression Evaluation Pipeline

Runs all evaluation steps and produces consolidated results.
"""

import os
import sys
import json
import time
import subprocess
import tempfile
import shutil
import csv
import math
from pathlib import Path
from datetime import datetime

import cv2
import numpy as np

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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
RESULTS_DIR = os.path.join(BASE_DIR, "results")
PLOTS_DIR = os.path.join(RESULTS_DIR, "plots")
MODEL_PATH = os.path.join(PROJECT_DIR, "models", "yolov8n.pt")

os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(PLOTS_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# Utility Functions
# ═══════════════════════════════════════════════════════════════════════════

def get_video_info(path):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps
    cap.release()
    return {"fps": fps, "width": w, "height": h, "frames": total_frames, "duration_s": duration}


def encode_ffmpeg(input_path, output_path, codec, bitrate_kbps, preset="medium",
                  resolution=None, extra_flags=None):
    """Encode video with ffmpeg. Returns (output_path, elapsed_s) or (None, elapsed_s)."""
    cmd = ["ffmpeg", "-y", "-i", input_path]
    if resolution:
        w, h = resolution
        cmd.extend(["-vf", f"scale={w}:{h}"])
    cmd.extend([
        "-c:v", codec, "-preset", preset,
        "-b:v", f"{bitrate_kbps}k",
        "-maxrate", f"{int(bitrate_kbps * 1.5)}k",
        "-bufsize", f"{bitrate_kbps * 2}k",
    ])
    if extra_flags:
        cmd.extend(extra_flags)
    cmd.append(output_path)

    start = time.time()
    try:
        result = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                text=True, timeout=600)
        elapsed = time.time() - start
        if result.returncode != 0:
            print(f"    [ffmpeg error] {codec} @{bitrate_kbps}kbps: {result.stderr[-200:]}")
            return None, elapsed
        return output_path, elapsed
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"    [encode error] {codec}: {e}")
        return None, time.time() - start


def compute_psnr_ssim(orig_path, recon_path, max_frames=200):
    """Compute PSNR and SSIM frame-by-frame."""
    cap_a = cv2.VideoCapture(orig_path)
    cap_b = cv2.VideoCapture(recon_path)
    psnrs, ssims = [], []
    idx = 0
    while idx < max_frames:
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
            mse = np.mean((fa.astype(float) - fb.astype(float)) ** 2)
            psnrs.append(100.0 if mse == 0 else 10 * np.log10(255**2 / mse))
        idx += 1
    cap_a.release()
    cap_b.release()
    return {
        "psnr": float(np.mean(psnrs)) if psnrs else None,
        "ssim": float(np.mean(ssims)) if ssims else None,
        "frames_evaluated": idx,
    }


def compute_file_metrics(path, duration_s):
    if not os.path.exists(path):
        return {"file_size_bytes": 0, "actual_kbps": 0}
    sz = os.path.getsize(path)
    return {"file_size_bytes": sz, "actual_kbps": round(sz * 8 / 1000 / max(duration_s, 0.01), 1)}


def compute_detection_recall(orig_path, recon_path, model, max_frames=50):
    if model is None:
        return None
    cap_a, cap_b = cv2.VideoCapture(orig_path), cv2.VideoCapture(recon_path)
    orig_c, recon_c = [], []
    idx = 0
    while idx < max_frames:
        ok_a, fa = cap_a.read()
        ok_b, fb = cap_b.read()
        if not ok_a or not ok_b:
            break
        r_a = model(fa, verbose=False, conf=0.3)
        r_b = model(fb, verbose=False, conf=0.3)
        orig_c.append(len(r_a[0].boxes))
        recon_c.append(len(r_b[0].boxes))
        idx += 1
    cap_a.release()
    cap_b.release()
    if not orig_c or sum(orig_c) == 0:
        return 1.0
    return min(np.mean(recon_c) / max(np.mean(orig_c), 1e-6), 1.0)


def compute_bdry_rate(psnrs_a, rates_a, psnrs_t, rates_t):
    """BD-rate: negative = test is better (same quality at lower bitrate)."""
    if len(psnrs_a) < 2 or len(psnrs_t) < 2:
        return None
    try:
        log_a = np.log2(np.array(rates_a, dtype=float))
        log_t = np.log2(np.array(rates_t, dtype=float))
        poly_a = np.polyfit(psnrs_a, log_a, 3)
        poly_t = np.polyfit(psnrs_t, log_t, 3)
        lo = max(min(psnrs_a), min(psnrs_t))
        hi = min(max(psnrs_a), max(psnrs_t))
        if lo >= hi:
            return None
        rng = np.linspace(lo, hi, 100)
        int_a = np.mean(np.polyval(poly_a, rng))
        int_t = np.mean(np.polyval(poly_t, rng))
        return round(float((2**int_t / 2**int_a - 1) * 100), 2)
    except Exception as e:
        print(f"  BD-rate error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# STEP 1: Benchmark Runner (per-config evaluation)
# ═══════════════════════════════════════════════════════════════════════════

def run_benchmark(input_path, video_info, yolo_model):
    """Run benchmark_runner across all YAML configs."""
    print("\n" + "="*70)
    print("  STEP 1: Benchmark Runner — Config Profile Evaluation")
    print("="*70)

    import yaml
    configs_dir = os.path.join(PROJECT_DIR, "configs")
    results = []

    for cfg_file in sorted(Path(configs_dir).glob("*.yaml")):
        if cfg_file.name == "cameras.yaml":
            continue
        with open(cfg_file) as f:
            config = yaml.safe_load(f)

        edge = config.get("edge_node", {})
        encoder = edge.get("encoder", {})
        codec = encoder.get("codec", "libx265")
        preset = encoder.get("preset", "medium")
        bitrate_str = str(encoder.get("bitrate", "800k")).replace("k", "")
        bitrate = int(bitrate_str)
        resolution_str = edge.get("resolution", "1280x720")
        try:
            res_w, res_h = [int(x) for x in resolution_str.split("x")]
        except ValueError:
            res_w, res_h = 1280, 720

        # Skip hevc_nvenc if not available, use libx265 instead
        if codec == "hevc_nvenc":
            codec = "libx265"
            print(f"\n  [{cfg_file.stem}] hevc_nvenc -> libx265 (software fallback)")

        print(f"\n  [{cfg_file.stem}] codec={codec} bitrate={bitrate}kbps res={resolution_str}")

        out_path = os.path.join(RESULTS_DIR, f"bench_{cfg_file.stem}.mp4")
        encoded, elapsed = encode_ffmpeg(
            input_path, out_path, codec, bitrate, preset,
            resolution=(res_w, res_h)
        )

        if encoded is None:
            print(f"    FAILED")
            continue

        duration_s = video_info["duration_s"]
        file_m = compute_file_metrics(out_path, duration_s)
        quality = compute_psnr_ssim(input_path, out_path)
        det = compute_detection_recall(input_path, out_path, yolo_model, max_frames=50)

        row = {
            "config": cfg_file.stem,
            "codec": codec,
            "target_bitrate_kbps": bitrate,
            "resolution": resolution_str,
            "actual_bitrate_kbps": file_m["actual_kbps"],
            "psnr_db": round(quality["psnr"], 3) if quality["psnr"] else None,
            "ssim": round(quality["ssim"], 5) if quality["ssim"] else None,
            "encode_time_s": round(elapsed, 2),
            "file_size_bytes": file_m["file_size_bytes"],
            "detection_recall": round(det, 4) if det is not None else None,
            "frames_evaluated": quality["frames_evaluated"],
        }
        results.append(row)
        print(f"    PSNR={quality['psnr']:.2f}dB  SSIM={quality['ssim']:.4f}  "
              f"bitrate={file_m['actual_kbps']:.0f}kbps  time={elapsed:.1f}s"
              + (f"  det_recall={det:.2%}" if det is not None else ""))

        # Cleanup
        try: os.remove(out_path)
        except: pass

    return results


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2: SOTA Comparison (multi-codec, multi-bitrate)
# ═══════════════════════════════════════════════════════════════════════════

BITRATE_LEVELS = [200, 500, 1000, 2000, 4000]

CODECS = [
    {"name": "H.264", "codec": "libx264", "preset": "medium"},
    {"name": "H.265", "codec": "libx265", "preset": "medium"},
    {"name": "AV1 (SVT-AV1)", "codec": "libsvtav1", "preset": "6"},
    {"name": "VP9", "codec": "libvpx-vp9", "preset": "good"},
]


def run_sota_comparison(input_path, video_info, yolo_model):
    """Encode with each codec at multiple bitrates, measure PSNR/SSIM."""
    print("\n" + "="*70)
    print("  STEP 2: SOTA Comparison — Multi-Codec Evaluation")
    print("="*70)

    duration_s = video_info["duration_s"]
    results = []

    for codec_info in CODECS:
        codec_name = codec_info["name"]
        print(f"\n  --- {codec_name} ({codec_info['codec']}) ---")

        for br in BITRATE_LEVELS:
            out_path = os.path.join(RESULTS_DIR, f"sota_{codec_name.replace(' ', '_')}_{br}kbps.mp4")
            encoded, elapsed = encode_ffmpeg(
                input_path, out_path, codec_info["codec"], br, codec_info["preset"]
            )
            if encoded is None:
                print(f"    @{br}kbps FAILED")
                continue

            file_m = compute_file_metrics(out_path, duration_s)
            quality = compute_psnr_ssim(input_path, out_path)
            det = compute_detection_recall(input_path, out_path, yolo_model, max_frames=50)

            row = {
                "method": codec_name,
                "target_bitrate_kbps": br,
                "actual_bitrate_kbps": file_m["actual_kbps"],
                "psnr_db": round(quality["psnr"], 3) if quality["psnr"] else None,
                "ssim": round(quality["ssim"], 5) if quality["ssim"] else None,
                "encode_time_s": round(elapsed, 2),
                "file_size_bytes": file_m["file_size_bytes"],
                "detection_recall": round(det, 4) if det is not None else None,
            }
            results.append(row)
            print(f"    @{br:>5}kbps: PSNR={quality['psnr']:.2f}dB  SSIM={quality['ssim']:.4f}  "
                  f"actual={file_m['actual_kbps']:.0f}kbps  time={elapsed:.1f}s"
                  + (f"  det={det:.2%}" if det is not None else ""))

            try: os.remove(out_path)
            except: pass

    return results


# ═══════════════════════════════════════════════════════════════════════════
# STEP 3: Rate Allocator — Lagrangian RD Optimization
# ═══════════════════════════════════════════════════════════════════════════

def run_rate_allocator(input_path, video_info, yolo_model):
    """Test the Lagrangian RD optimizer with various ROI configurations."""
    print("\n" + "="*70)
    print("  STEP 3: Rate Allocator — Lagrangian RD Optimization")
    print("="*70)

    # Import rate allocator
    sys.path.insert(0, BASE_DIR)
    from rate_allocator import RateAllocator, RegionOfInterest

    cap = cv2.VideoCapture(input_path)
    ret, sample_frame = cap.read()
    cap.release()
    if not ret:
        print("  Could not read sample frame")
        return {}

    # Detect ROIs in sample frame
    rois_detected = []
    if yolo_model is not None:
        results = yolo_model(sample_frame, verbose=False, conf=0.3)
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                cls_id = int(box.cls[0])
                priority = "high" if cls_id == 0 else "medium" if cls_id in {2, 3, 5, 6, 7} else "low"
                rois_detected.append(RegionOfInterest(
                    bbox=[int(x1), int(y1), int(x2), int(y2)],
                    priority=priority,
                    class_id=cls_id,
                ))
        print(f"  Detected {len(rois_detected)} ROIs in sample frame")
    else:
        # Synthetic ROIs for testing
        h, w = sample_frame.shape[:2]
        rois_detected = [
            RegionOfInterest(bbox=[w//4, h//4, 3*w//4, 3*h//4], priority="high", class_id=0),
            RegionOfInterest(bbox=[0, 0, w//3, h//3], priority="medium", class_id=2),
        ]
        print(f"  Using {len(rois_detected)} synthetic ROIs (YOLO not available)")

    # Test at multiple budget levels
    budget_levels = [200, 500, 1000, 2000]
    risk_states = ["normal", "alert", "critical"]
    allocation_results = []

    for budget in budget_levels:
        for state in risk_states:
            ra = RateAllocator(
                total_budget_kbps=budget,
                fps=video_info["fps"],
                resolution=(video_info["width"], video_info["height"]),
                min_roi_kbps=30,
                min_bg_kbps=15,
            )
            alloc = ra.allocate(rois_detected, sample_frame, risk_state=state)

            entry = {
                "budget_kbps": budget,
                "risk_state": state,
                "lambda": alloc["lambda"],
                "total_kbps": alloc["total_kbps"],
                "efficiency": alloc["efficiency"],
                "bg_quality": alloc["bg_quality"],
                "bg_scale": alloc["bg_scale"],
                "bg_target_kbps": alloc["bg_target_kbps"],
                "num_rois": alloc["num_rois"],
                "roi_allocations": alloc["rois"],
            }
            allocation_results.append(entry)
            print(f"  Budget={budget:>5}kbps  State={state:<10} "
                  f"λ={alloc['lambda']:.6f}  total={alloc['total_kbps']:.0f}kbps  "
                  f"eff={alloc['efficiency']:.3f}  bgQ={alloc['bg_quality']}")

    # Lambda sensitivity analysis
    print("\n  Lambda Sensitivity Analysis:")
    lambda_sweep = []
    ra_test = RateAllocator(total_budget_kbps=1000, fps=video_info["fps"],
                            resolution=(video_info["width"], video_info["height"]))
    model = ra_test.build_rd_model(rois_detected, sample_frame, "alert")

    for lam_exp in range(-4, 2):
        lam = 10 ** lam_exp
        total_rate = 0
        for region in model["regions"]:
            r = ra_test.optimal_rate(region, lam)
            total_rate += r
        lambda_sweep.append({"lambda": lam, "total_kbps": round(total_rate, 1)})
        print(f"    λ={lam:.6f}  →  total_rate={total_rate:.0f}kbps")

    return {
        "allocations": allocation_results,
        "lambda_sweep": lambda_sweep,
        "detected_rois": len(rois_detected),
    }


# ═══════════════════════════════════════════════════════════════════════════
# STEP 4: Ablation Study
# ═══════════════════════════════════════════════════════════════════════════

def run_ablation(input_path, video_info, yolo_model):
    """Run ablation study with simplified pipeline variants."""
    print("\n" + "="*70)
    print("  STEP 4: Ablation Study — Component Contribution")
    print("="*70)

    from dataclasses import dataclass

    @dataclass
    class PipelineConfig:
        use_risk_engine: bool = True
        use_clahe: bool = True
        use_temporal_merge: bool = True
        use_adaptive_gop: bool = True
        use_adaptive_resolution: bool = True
        name: str = "Full System"

    variants = [
        PipelineConfig(name="Full System", use_risk_engine=True, use_clahe=True,
                       use_temporal_merge=True, use_adaptive_gop=True, use_adaptive_resolution=True),
        PipelineConfig(name="w/o Risk Engine", use_risk_engine=False, use_clahe=True,
                       use_temporal_merge=True, use_adaptive_gop=True, use_adaptive_resolution=True),
        PipelineConfig(name="w/o CLAHE", use_risk_engine=True, use_clahe=False,
                       use_temporal_merge=True, use_adaptive_gop=True, use_adaptive_resolution=True),
        PipelineConfig(name="w/o Temporal Merge", use_risk_engine=True, use_clahe=True,
                       use_temporal_merge=False, use_adaptive_gop=True, use_adaptive_resolution=True),
        PipelineConfig(name="w/o Adaptive GOP", use_risk_engine=True, use_clahe=True,
                       use_temporal_merge=True, use_adaptive_gop=False, use_adaptive_resolution=True),
        PipelineConfig(name="w/o Adaptive Res", use_risk_engine=True, use_clahe=True,
                       use_temporal_merge=True, use_adaptive_gop=True, use_adaptive_resolution=False),
    ]

    results = []
    cap = cv2.VideoCapture(input_path)
    fps = video_info["fps"]
    w, h = video_info["width"], video_info["height"]

    prev_gray = None
    temporal_rois = []

    for variant in variants:
        print(f"\n  --- {variant.name} ---")
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        frame_id = 0
        total_bytes = 0
        states = []
        risks = []
        start = time.time()

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_id += 1
            fh, fw = frame.shape[:2]

            # Detection
            new_rois = []
            if yolo_model is not None and frame_id % 3 == 0:
                results_det = yolo_model(frame, verbose=False, conf=0.3)
                for r in results_det:
                    for box in r.boxes:
                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                        cls_id = int(box.cls[0])
                        priority = "high" if cls_id == 0 else "medium"
                        new_rois.append({"bbox": [int(x1), int(y1), int(x2), int(y2)],
                                         "priority": priority})

            # Temporal merge
            if variant.use_temporal_merge:
                IOU_THRESH = 0.3
                updated = [dict(r) for r in temporal_rois]
                for nd in new_rois:
                    nb = nd["bbox"]
                    best_iou, best_idx = 0.0, -1
                    for i, er in enumerate(updated):
                        xA = max(er["bbox"][0], nb[0]); yA = max(er["bbox"][1], nb[1])
                        xB = min(er["bbox"][2], nb[2]); yB = min(er["bbox"][3], nb[3])
                        inter = max(0, xB-xA) * max(0, yB-yA)
                        if inter > 0:
                            aA = (er["bbox"][2]-er["bbox"][0])*(er["bbox"][3]-er["bbox"][1])
                            aB = (nb[2]-nb[0])*(nb[3]-nb[1])
                            iou = inter / float(aA + aB - inter)
                            if iou > best_iou:
                                best_iou, best_idx = iou, i
                    if best_iou >= IOU_THRESH and best_idx >= 0:
                        eb = updated[best_idx]["bbox"]
                        updated[best_idx]["bbox"] = [int(eb[j]*0.6+nb[j]*0.4) for j in range(4)]
                    else:
                        updated.append({"bbox": list(nb), "priority": nd["priority"]})
                temporal_rois = updated
            else:
                temporal_rois = new_rois

            rois = temporal_rois

            # Risk
            roi_pixels = sum(max(0, r["bbox"][2]-r["bbox"][0])*max(0, r["bbox"][3]-r["bbox"][1]) for r in rois)
            motion_frac = min(roi_pixels / max(fw*fh, 1), 1.0)
            hour = datetime.now().hour

            if variant.use_risk_engine:
                score = 0.0
                for r in rois:
                    p = r.get("priority", "low")
                    score += 0.30 if p == "high" else 0.10 if p == "medium" else 0.03
                score += min(motion_frac, 1.0) * 0.25
                if hour < 6 or hour >= 22: score += 0.20
                score += min(len(rois) * 0.04, 0.15)
                risk = min(score, 1.0)
            else:
                risk = 0.3

            # State
            if risk >= 0.65: state = "critical"
            elif risk >= 0.30: state = "alert"
            else: state = "normal"
            states.append(state)
            risks.append(risk)

            # Adaptive GOP
            gop = {"normal": 120, "alert": 30, "critical": 10}[state] if variant.use_adaptive_gop else 60

            # Adaptive resolution
            if variant.use_adaptive_resolution:
                out_res = {"normal": (640, 480), "alert": (854, 480), "critical": (1920, 1080)}[state]
            else:
                out_res = (640, 480)

            # Compression estimate
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            total_bytes += gray.nbytes * 0.15

        elapsed = time.time() - start
        duration_s = video_info["duration_s"]
        state_counts = {}
        for s in states:
            state_counts[s] = state_counts.get(s, 0) + 1

        row = {
            "variant": variant.name,
            "frames": frame_id,
            "avg_risk": round(float(np.mean(risks)), 4) if risks else 0,
            "critical_pct": round(state_counts.get("critical", 0) / max(frame_id, 1) * 100, 1),
            "alert_pct": round(state_counts.get("alert", 0) / max(frame_id, 1) * 100, 1),
            "estimated_kbps": round(total_bytes * 8 / 1000 / max(duration_s, 0.01), 1),
            "processing_time_s": round(elapsed, 2),
            "fps_throughput": round(frame_id / max(elapsed, 0.001), 1),
            "risk_engine": variant.use_risk_engine,
            "clahe": variant.use_clahe,
            "temporal_merge": variant.use_temporal_merge,
            "adaptive_gop": variant.use_adaptive_gop,
            "adaptive_resolution": variant.use_adaptive_resolution,
        }
        results.append(row)
        print(f"    risk={row['avg_risk']:.3f}  critical={row['critical_pct']:.1f}%  "
              f"est_bitrate={row['estimated_kbps']:.0f}kbps  throughput={row['fps_throughput']:.1f}fps")

    cap.release()
    return results


# ═══════════════════════════════════════════════════════════════════════════
# STEP 5: Generate R-D Plots and Results JSON
# ═══════════════════════════════════════════════════════════════════════════

def generate_plots(sota_results, benchmark_results, rate_alloc_results, ablation_results):
    """Generate comprehensive evaluation plots."""
    print("\n" + "="*70)
    print("  STEP 5: Generating Plots and Results")
    print("="*70)

    palette = {
        "H.264": "#f28e2b",
        "H.265": "#4e79a7",
        "AV1 (SVT-AV1)": "#76b7b2",
        "VP9": "#59a14f",
        "Proposed": "#e15759",
    }

    # ── Plot 1: Rate-Distortion Curves (PSNR) ──
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle("CCTV Compression — Comprehensive Evaluation Results",
                 fontsize=14, fontweight="bold", y=0.98)

    ax = axes[0, 0]
    for method in ["H.264", "H.265", "AV1 (SVT-AV1)", "VP9"]:
        pts = [r for r in sota_results if r["method"] == method and r["psnr_db"]]
        if pts:
            pts.sort(key=lambda x: x["actual_bitrate_kbps"])
            ax.plot([p["actual_bitrate_kbps"] for p in pts],
                    [p["psnr_db"] for p in pts],
                    marker="o", label=method, color=palette.get(method, "#999"),
                    linewidth=2, markersize=7)
    ax.set_title("Rate-Distortion Curve (PSNR vs Bitrate)", fontsize=11)
    ax.set_xlabel("Bitrate (kbps)")
    ax.set_ylabel("PSNR (dB)")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)

    # ── Plot 2: SSIM vs Bitrate ──
    ax = axes[0, 1]
    for method in ["H.264", "H.265", "AV1 (SVT-AV1)", "VP9"]:
        pts = [r for r in sota_results if r["method"] == method and r["ssim"]]
        if pts:
            pts.sort(key=lambda x: x["actual_bitrate_kbps"])
            ax.plot([p["actual_bitrate_kbps"] for p in pts],
                    [p["ssim"] for p in pts],
                    marker="s", label=method, color=palette.get(method, "#999"),
                    linewidth=2, markersize=7)
    ax.set_title("Rate-Distortion Curve (SSIM vs Bitrate)", fontsize=11)
    ax.set_xlabel("Bitrate (kbps)")
    ax.set_ylabel("SSIM")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)

    # ── Plot 3: Benchmark Config Comparison ──
    ax = axes[1, 0]
    if benchmark_results:
        configs = [r["config"] for r in benchmark_results]
        psnrs = [r["psnr_db"] for r in benchmark_results if r["psnr_db"]]
        bitrates = [r["actual_bitrate_kbps"] for r in benchmark_results]
        if psnrs:
            colors = plt.cm.Set2(np.linspace(0, 1, len(configs)))
            bars = ax.barh(configs[:len(psnrs)], psnrs, color=colors[:len(psnrs)])
            ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
            ax.set_xlabel("PSNR (dB)")
            ax.set_title("Benchmark: PSNR per Config Profile", fontsize=11)
            ax.grid(alpha=0.3, axis="x")

    # ── Plot 4: Ablation Throughput ──
    ax = axes[1, 1]
    if ablation_results:
        variants = [r["variant"] for r in ablation_results]
        throughputs = [r["fps_throughput"] for r in ablation_results]
        colors = plt.cm.Set2(np.linspace(0, 1, len(variants)))
        bars = ax.barh(variants, throughputs, color=colors)
        ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=8)
        ax.set_xlabel("Throughput (fps)")
        ax.set_title("Ablation: Processing Throughput", fontsize=11)
        ax.grid(alpha=0.3, axis="x")

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plot_path = os.path.join(PLOTS_DIR, "comprehensive_evaluation.png")
    plt.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Main plot saved: {plot_path}")

    # ── Plot 5: Rate Allocator Lambda Sweep ──
    if rate_alloc_results.get("lambda_sweep"):
        fig2, ax2 = plt.subplots(figsize=(8, 5))
        lams = [r["lambda"] for r in rate_alloc_results["lambda_sweep"]]
        rates = [r["total_kbps"] for r in rate_alloc_results["lambda_sweep"]]
        ax2.semilogx(lams, rates, marker="o", linewidth=2, color="#e15759", markersize=8)
        ax2.set_title("Rate Allocator: Lambda vs Total Bitrate", fontsize=12)
        ax2.set_xlabel("Lambda (λ)")
        ax2.set_ylabel("Total Bitrate (kbps)")
        ax2.axhline(y=1000, color="#4e79a7", linestyle="--", alpha=0.7, label="1000 kbps budget")
        ax2.legend()
        ax2.grid(alpha=0.3)
        plt.tight_layout()
        lambda_path = os.path.join(PLOTS_DIR, "lambda_sweep.png")
        plt.savefig(lambda_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"  Lambda sweep plot saved: {lambda_path}")

    # ── Plot 6: Ablation Component Impact ──
    if ablation_results:
        fig3, axes3 = plt.subplots(1, 2, figsize=(14, 6))
        fig3.suptitle("Ablation Study — Component Impact Analysis", fontsize=13, fontweight="bold")

        ax = axes3[0]
        variants = [r["variant"] for r in ablation_results]
        est_bitrates = [r["estimated_kbps"] for r in ablation_results]
        colors = plt.cm.Set2(np.linspace(0, 1, len(variants)))
        bars = ax.barh(variants, est_bitrates, color=colors)
        ax.bar_label(bars, fmt="%.0f", padding=3, fontsize=8)
        ax.set_xlabel("Estimated Bitrate (kbps)")
        ax.set_title("Bandwidth Consumption")
        ax.grid(alpha=0.3, axis="x")

        ax = axes3[1]
        crit_pcts = [r["critical_pct"] for r in ablation_results]
        bars = ax.barh(variants, crit_pcts, color=colors)
        ax.bar_label(bars, fmt="%.1f%%", padding=3, fontsize=8)
        ax.set_xlabel("Critical Frames (%)")
        ax.set_title("Event Sensitivity")
        ax.grid(alpha=0.3, axis="x")

        plt.tight_layout(rect=[0, 0, 1, 0.95])
        ablation_path = os.path.join(PLOTS_DIR, "ablation_study.png")
        plt.savefig(ablation_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"  Ablation plot saved: {ablation_path}")


def compute_bd_rates(sota_results):
    """Compute BD-rate for each codec vs H.265 anchor."""
    anchor = [r for r in sota_results if r["method"] == "H.265" and r["psnr_db"]]
    anchor_psnrs = [r["psnr_db"] for r in anchor]
    anchor_rates = [r["actual_bitrate_kbps"] for r in anchor]

    bd_rates = {}
    for method in set(r["method"] for r in sota_results):
        if method == "H.265":
            continue
        test = [r for r in sota_results if r["method"] == method and r["psnr_db"]]
        test_psnrs = [r["psnr_db"] for r in test]
        test_rates = [r["actual_bitrate_kbps"] for r in test]
        bd = compute_bdry_rate(anchor_psnrs, anchor_rates, test_psnrs, test_rates)
        bd_rates[method] = bd
    return bd_rates


def save_results(benchmark_results, sota_results, rate_alloc_results, ablation_results):
    """Save consolidated JSON results."""
    bd_rates = compute_bd_rates(sota_results)

    all_results = {
        "metadata": {
            "timestamp": datetime.utcnow().isoformat(),
            "video": "test_video1.mp4",
        },
        "benchmark": benchmark_results,
        "sota_comparison": sota_results,
        "rate_allocator": rate_alloc_results,
        "ablation": ablation_results,
        "bd_rates": bd_rates,
    }

    json_path = os.path.join(RESULTS_DIR, "evaluation_results.json")
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results JSON saved: {json_path}")

    # Also save CSV
    csv_path = os.path.join(RESULTS_DIR, "sota_comparison.csv")
    if sota_results:
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(sota_results[0].keys()))
            writer.writeheader()
            writer.writerows(sota_results)
        print(f"  SOTA CSV saved: {csv_path}")

    return all_results, bd_rates


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    input_video = "/home/brothers/Downloads/test_video1.mp4"

    if not os.path.exists(input_video):
        print(f"ERROR: Video not found: {input_video}")
        sys.exit(1)

    print("="*70)
    print("  CCTV COMPRESSION — COMPREHENSIVE EVALUATION PIPELINE")
    print(f"  Video: {input_video}")
    print("="*70)

    video_info = get_video_info(input_video)
    print(f"  Resolution: {video_info['width']}x{video_info['height']}")
    print(f"  FPS: {video_info['fps']}")
    print(f"  Duration: {video_info['duration_s']:.1f}s")
    print(f"  Frames: {video_info['frames']}")

    # Load YOLO
    yolo_model = None
    if _YOLO and os.path.exists(MODEL_PATH):
        print(f"\n  Loading YOLO model: {MODEL_PATH}")
        yolo_model = YOLO(MODEL_PATH)
    else:
        print("\n  YOLO not available — using synthetic ROIs where needed")

    # Run all steps
    t0 = time.time()

    benchmark_results = run_benchmark(input_video, video_info, yolo_model)
    sota_results = run_sota_comparison(input_video, video_info, yolo_model)
    rate_alloc_results = run_rate_allocator(input_video, video_info, yolo_model)
    ablation_results = run_ablation(input_video, video_info, yolo_model)

    # Save and plot
    all_results, bd_rates = save_results(benchmark_results, sota_results,
                                          rate_alloc_results, ablation_results)
    generate_plots(sota_results, benchmark_results, rate_alloc_results, ablation_results)

    total_time = time.time() - t0
    print("\n" + "="*70)
    print("  EVALUATION COMPLETE")
    print(f"  Total time: {total_time:.1f}s")
    print("="*70)

    # Print summary
    print("\n  BD-Rate Summary (vs H.265 anchor):")
    for method, bd in bd_rates.items():
        if bd is not None:
            sign = "better" if bd < 0 else "worse"
            print(f"    {method:<20} BD-rate: {bd:+.2f}% ({sign})")
        else:
            print(f"    {method:<20} BD-rate: N/A")

    print(f"\n  Results: {os.path.join(RESULTS_DIR, 'evaluation_results.json')}")
    print(f"  Plots:   {PLOTS_DIR}/")


if __name__ == "__main__":
    main()
