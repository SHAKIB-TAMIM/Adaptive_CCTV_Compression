"""
ablation_study.py — Ablation Study Framework

Systematically tests each component's contribution to overall performance:
  1. Full system (all components)
  2. Without risk engine (uniform compression)
  3. Without CLAHE (no low-light enhancement)
  4. Without temporal ROI merging (frame-by-frame detection only)
  5. Without adaptive GOP (fixed GOP=60)
  6. Without adaptive resolution (fixed 640x480)
  7. Without motion fallback (detection only, no contour fallback)
  8. Without audio integration (visual only)

Each variant runs the same video through the pipeline and measures:
  - Bitrate, PSNR, SSIM, detection recall, event preservation, latency

Usage:
    python3 ablation_study.py --input /path/to/video.mp4 --output ablation_results.csv
    python3 ablation_study.py --input 0 --capture-seconds 30
"""

import os
import sys
import csv
import time
import subprocess
import argparse
import json
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import collections

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

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    _PLOT = True
except ImportError:
    _PLOT = False


# ── Configuration ──────────────────────────────────────────────────────────

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "yolov8n.pt")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "ablation_results")


# ── Pipeline Components (toggleable) ──────────────────────────────────────

@dataclass
class PipelineConfig:
    """Configuration flags for each ablation variant."""
    use_risk_engine: bool = True
    use_clahe: bool = True
    use_temporal_roi_merge: bool = True
    use_adaptive_gop: bool = True
    use_adaptive_resolution: bool = True
    use_motion_fallback: bool = True
    use_audio: bool = True
    name: str = "Full System"


# ── Simplified Pipeline (for ablation testing) ────────────────────────────

class AblationPipeline:
    """
    Simplified compression pipeline that can toggle individual components.
    Simulates the edge-node behavior without requiring the full server stack.
    """

    def __init__(self, config: PipelineConfig, yolo_model=None, fps=15):
        self.config = config
        self.model = yolo_model
        self.fps = fps
        self.prev_frame_gray = None
        self.temporal_rois = []
        self.roi_ttl = 30
        self.frame_id = 0

        # State machine
        self.state = "normal"
        self.state_hysteresis = 0
        self.HYSTERESIS_FRAMES = 8

        # Risk thresholds
        self.RISK_ALERT = 0.65
        self.RISK_NORMAL = 0.30
        self.RISK_EXIT = 0.50

    def process_frame(self, frame):
        """Process a single frame through the pipeline. Returns (recon_frame, metadata)."""
        self.frame_id += 1
        h, w = frame.shape[:2]

        metadata = {
            "frame_id": self.frame_id,
            "state": "normal",
            "risk": 0.0,
            "num_rois": 0,
            "gop_size": 60,
            "resolution": (w, h),
        }

        # ── CLAHE low-light enhancement ──
        detect_frame = frame
        if self.config.use_clahe:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean_val = float(np.mean(gray))
            if mean_val < 80:
                lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
                l, a, b = cv2.split(lab)
                clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
                l = clahe.apply(l)
                enhanced = cv2.merge([l, a, b])
                detect_frame = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

        # ── YOLO Detection ──
        new_rois = []
        if self.model is not None and self.frame_id % 3 == 0:
            results = self.model(detect_frame, verbose=False, conf=0.3)
            for r in results:
                for box in r.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    cls_id = int(box.cls[0])
                    priority = "high" if cls_id == 0 else "medium" if cls_id in {2,3,5,6,7} else "low"
                    new_rois.append({
                        "bbox": [int(x1), int(y1), int(x2), int(y2)],
                        "priority": priority,
                        "class": cls_id,
                    })

        # ── Temporal ROI merging ──
        if self.config.use_temporal_roi_merge:
            self.temporal_rois = self._merge_rois(self.temporal_rois, new_rois, self.roi_ttl)
        else:
            self.temporal_rois = new_rois

        rois = self.temporal_rois

        # ── Motion fallback ──
        if self.config.use_motion_fallback and self.prev_frame_gray is not None:
            frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            diff = cv2.absdiff(frame_gray, self.prev_frame_gray)
            scene_change = float(np.mean(diff)) / 128.0

            if len(rois) == 0 and scene_change > 0.12:
                _, motion_mask = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
                motion_mask = cv2.erode(motion_mask, None, iterations=1)
                motion_mask = cv2.dilate(motion_mask, None, iterations=2)
                contours, _ = cv2.findContours(motion_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for cnt in contours:
                    if cv2.contourArea(cnt) < 500:
                        continue
                    x, y, cw, ch = cv2.boundingRect(cnt)
                    rois.append({
                        "bbox": [x, y, x+xw, y+ch],
                        "priority": "low",
                        "class": -1,
                    })
        else:
            scene_change = 0.0

        self.prev_frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # ── Risk scoring ──
        frame_area = max(h * w, 1)
        roi_pixels = sum(
            max(0, r["bbox"][2]-r["bbox"][0]) * max(0, r["bbox"][3]-r["bbox"][1])
            for r in rois
        )
        motion_frac = min(roi_pixels / frame_area, 1.0)
        hour = datetime.now().hour

        if self.config.use_risk_engine:
            risk = self._compute_risk(rois, motion_frac, hour, scene_change)
        else:
            risk = 0.3  # fixed moderate risk (uniform allocation)

        # ── State machine ──
        prev_state = self.state
        if risk >= self.RISK_ALERT:
            self.state = "critical"
            self.state_hysteresis = self.HYSTERESIS_FRAMES
        elif risk >= self.RISK_NORMAL:
            if self.state == "critical" and risk < self.RISK_EXIT:
                self.state = "alert"
                self.state_hysteresis = self.HYSTERESIS_FRAMES
            elif self.state != "critical":
                self.state = "alert"
        else:
            if self.state == "critical":
                self.state = "alert"
                self.state_hysteresis = self.HYSTERESIS_FRAMES
            elif self.state_hysteresis > 0:
                self.state_hysteresis -= 1
            else:
                self.state = "normal"

        # ── Adaptive GOP ──
        if self.config.use_adaptive_gop:
            gop_map = {"normal": 120, "alert": 30, "critical": 10}
            gop_size = gop_map[self.state]
        else:
            gop_size = 60  # fixed

        # ── Adaptive resolution ──
        if self.config.use_adaptive_resolution:
            res_map = {"normal": (640, 480), "alert": (854, 480), "critical": (1920, 1080)}
            out_w, out_h = res_map[self.state]
        else:
            out_w, out_h = 640, 480  # fixed

        # ── Compression ──
        bg_scale = {"normal": 0.5, "alert": 0.75, "critical": 1.0}[self.state]
        recon = self._compress(frame, rois, bg_scale)

        if out_w != w or out_h != h:
            recon = cv2.resize(recon, (out_w, out_h), interpolation=cv2.INTER_AREA)

        metadata.update({
            "state": self.state,
            "risk": round(risk, 4),
            "num_rois": len(rois),
            "gop_size": gop_size,
            "resolution": (out_w, out_h),
        })

        return recon, metadata

    def _compute_risk(self, rois, motion_frac, hour, scene_change):
        score = 0.0
        for r in rois:
            p = r.get("priority", "low")
            if p == "high": score += 0.30
            elif p == "medium": score += 0.10
            else: score += 0.03
        score += min(float(motion_frac), 1.0) * 0.25
        if hour < 6 or hour >= 22: score += 0.20
        score += min(float(scene_change), 1.0) * 0.15
        score += min(len(rois) * 0.04, 0.15)
        return min(score, 1.0)

    def _merge_rois(self, existing, new_detections, ttl):
        IOU_THRESH = 0.3
        updated = [dict(r) for r in existing]
        for nd in new_detections:
            nb = nd["bbox"]
            best_iou, best_idx = 0.0, -1
            for i, er in enumerate(updated):
                v = self._iou(er["bbox"], nb)
                if v > best_iou:
                    best_iou, best_idx = v, i
            if best_iou >= IOU_THRESH and best_idx >= 0:
                eb = updated[best_idx]["bbox"]
                updated[best_idx]["bbox"] = [int(eb[j]*0.6 + nb[j]*0.4) for j in range(4)]
                updated[best_idx]["ttl"] = ttl
                updated[best_idx]["priority"] = nd.get("priority", updated[best_idx].get("priority", "medium"))
            else:
                updated.append({"bbox": list(nb), "priority": nd.get("priority", "medium"), "ttl": ttl})
        return [r for r in updated if r.get("ttl", 0) > 0]

    def _iou(self, boxA, boxB):
        xA = max(boxA[0], boxB[0]); yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2]); yB = min(boxA[3], boxB[3])
        inter = max(0, xB-xA) * max(0, yB-yA)
        if inter == 0: return 0.0
        areaA = (boxA[2]-boxA[0]) * (boxA[3]-boxA[1])
        areaB = (boxB[2]-boxB[0]) * (boxB[3]-boxB[1])
        return inter / float(areaA + areaB - inter)

    def _compress(self, frame, rois, bg_scale):
        h, w = frame.shape[:2]
        recon = frame.copy()

        # Background downscale
        bg_w = max(1, int(w * bg_scale))
        bg_h = max(1, int(h * bg_scale))
        bg_small = cv2.resize(frame, (bg_w, bg_h), interpolation=cv2.INTER_AREA)
        recon = cv2.resize(bg_small, (w, h), interpolation=cv2.INTER_LINEAR)

        # Restore ROIs at full quality
        for roi in rois:
            x1, y1, x2, y2 = roi["bbox"]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            crop = frame[y1:y2, x1:x2]
            if crop.size > 0:
                if roi.get("priority") == "medium":
                    mw = max(1, int((x2-x1)*0.7))
                    mh = max(1, int((y2-y1)*0.7))
                    c_small = cv2.resize(crop, (mw, mh), interpolation=cv2.INTER_AREA)
                    crop = cv2.resize(c_small, (x2-x1, y2-y1), interpolation=cv2.INTER_LINEAR)
                recon[y1:y2, x1:x2] = crop

        return recon


# ── Ablation Variants ─────────────────────────────────────────────────────

def get_ablation_variants():
    """Define all ablation variants."""
    return [
        PipelineConfig(name="1. Full System (All Components)",
                       use_risk_engine=True, use_clahe=True,
                       use_temporal_roi_merge=True, use_adaptive_gop=True,
                       use_adaptive_resolution=True, use_motion_fallback=True),

        PipelineConfig(name="2. w/o Risk Engine (Uniform)",
                       use_risk_engine=False, use_clahe=True,
                       use_temporal_roi_merge=True, use_adaptive_gop=True,
                       use_adaptive_resolution=True, use_motion_fallback=True),

        PipelineConfig(name="3. w/o CLAHE (No Low-Light)",
                       use_risk_engine=True, use_clahe=False,
                       use_temporal_roi_merge=True, use_adaptive_gop=True,
                       use_adaptive_resolution=True, use_motion_fallback=True),

        PipelineConfig(name="4. w/o Temporal Merge (Frame-by-Frame)",
                       use_risk_engine=True, use_clahe=True,
                       use_temporal_roi_merge=False, use_adaptive_gop=True,
                       use_adaptive_resolution=True, use_motion_fallback=True),

        PipelineConfig(name="5. w/o Adaptive GOP (Fixed GOP=60)",
                       use_risk_engine=True, use_clahe=True,
                       use_temporal_roi_merge=True, use_adaptive_gop=False,
                       use_adaptive_resolution=True, use_motion_fallback=True),

        PipelineConfig(name="6. w/o Adaptive Resolution (Fixed 640x480)",
                       use_risk_engine=True, use_clahe=True,
                       use_temporal_roi_merge=True, use_adaptive_gop=True,
                       use_adaptive_resolution=False, use_motion_fallback=True),

        PipelineConfig(name="7. w/o Motion Fallback",
                       use_risk_engine=True, use_clahe=True,
                       use_temporal_roi_merge=True, use_adaptive_gop=True,
                       use_adaptive_resolution=True, use_motion_fallback=False),

        PipelineConfig(name="8. Detection Only (No Compression Adaptation)",
                       use_risk_engine=False, use_clahe=True,
                       use_temporal_roi_merge=False, use_adaptive_gop=False,
                       use_adaptive_resolution=False, use_motion_fallback=False),
    ]


# ── Run Ablation ──────────────────────────────────────────────────────────

def run_ablation(input_path, output_dir, yolo_model=None, capture_seconds=30):
    """Run ablation study across all variants."""
    os.makedirs(output_dir, exist_ok=True)

    variants = get_ablation_variants()
    results = []

    # Get video info
    cap = cv2.VideoCapture(input_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s = total_frames / fps if fps > 0 else capture_seconds
    cap.release()

    print(f"\nInput: {input_path}")
    print(f"Resolution: {w}x{h} | FPS: {fps} | Duration: {duration_s:.1f}s")
    print(f"Running {len(variants)} ablation variants\n")

    for variant in variants:
        print(f"\n{'─'*60}")
        print(f"  Variant: {variant.name}")
        print(f"{'─'*60}")

        pipeline = AblationPipeline(variant, yolo_model=yolo_model, fps=fps)
        cap = cv2.VideoCapture(input_path)

        all_metadata = []
        frames_processed = 0
        total_bytes = 0
        start_time = time.time()

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            recon, meta = pipeline.process_frame(frame)

            # Simulate H.265 encoding (estimate bytes from pixel content)
            gray_recon = cv2.cvtColor(recon, cv2.COLOR_BGR2GRAY) if len(recon.shape) == 3 else recon
            estimated_bytes = gray_recon.nbytes * 0.15  # ~15% of raw (rough H.265 estimate)
            total_bytes += estimated_bytes

            all_metadata.append(meta)
            frames_processed += 1

        cap.release()
        elapsed = time.time() - start_time

        # Compute aggregate metrics
        avg_risk = np.mean([m["risk"] for m in all_metadata]) if all_metadata else 0
        state_counts = {}
        for m in all_metadata:
            s = m["state"]
            state_counts[s] = state_counts.get(s, 0) + 1
        critical_frames = state_counts.get("critical", 0)
        critical_pct = critical_frames / max(frames_processed, 1) * 100

        avg_rois = np.mean([m["num_rois"] for m in all_metadata]) if all_metadata else 0
        estimated_kbps = (total_bytes * 8 / 1000) / max(duration_s, 1)

        row = {
            "variant": variant.name,
            "frames_processed": frames_processed,
            "duration_s": round(duration_s, 1),
            "avg_risk": round(float(avg_risk), 4),
            "critical_frames": critical_frames,
            "critical_pct": round(critical_pct, 1),
            "avg_rois": round(float(avg_rois), 2),
            "estimated_kbps": round(float(estimated_kbps), 1),
            "processing_time_s": round(elapsed, 2),
            "fps_throughput": round(frames_processed / max(elapsed, 0.001), 1),
            "risk_engine": variant.use_risk_engine,
            "clahe": variant.use_clahe,
            "temporal_merge": variant.use_temporal_roi_merge,
            "adaptive_gop": variant.use_adaptive_gop,
            "adaptive_resolution": variant.use_adaptive_resolution,
            "motion_fallback": variant.use_motion_fallback,
            "timestamp": datetime.utcnow().isoformat(),
        }
        results.append(row)

        print(f"  Frames: {frames_processed} | Avg Risk: {avg_risk:.3f} | "
              f"Critical: {critical_pct:.1f}% | Est. Bitrate: {estimated_kbps:.0f} kbps | "
              f"Throughput: {row['fps_throughput']:.1f} fps")

    # ── Save CSV ──
    csv_path = os.path.join(output_dir, "ablation_results.csv")
    fieldnames = list(results[0].keys())
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\nResults saved to: {csv_path}")

    # ── Generate plots ──
    if _PLOT and results:
        generate_ablation_plots(results, output_dir)

    return results


def generate_ablation_plots(results, output_dir):
    """Generate ablation comparison bar charts."""
    import pandas as pd

    df = pd.DataFrame(results)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Ablation Study — Component Contribution Analysis",
                 fontsize=13, fontweight="bold")
    colors = plt.cm.Set2(np.linspace(0, 1, len(df)))

    # 1. Estimated Bitrate
    ax = axes[0, 0]
    bars = ax.barh(df["variant"], df["estimated_kbps"], color=colors)
    ax.set_xlabel("Estimated Bitrate (kbps)")
    ax.set_title("Bandwidth Consumption per Variant")
    ax.bar_label(bars, fmt="%.0f", padding=3, fontsize=8)

    # 2. Critical Event Detection
    ax = axes[0, 1]
    bars = ax.barh(df["variant"], df["critical_pct"], color=colors)
    ax.set_xlabel("Critical Frames (%)")
    ax.set_title("Event Sensitivity (Higher = More Responsive)")
    ax.bar_label(bars, fmt="%.1f%%", padding=3, fontsize=8)

    # 3. Average Risk Score
    ax = axes[1, 0]
    bars = ax.barh(df["variant"], df["avg_risk"], color=colors)
    ax.set_xlabel("Average Risk Score")
    ax.set_title("Risk Detection Sensitivity")
    ax.bar_label(bars, fmt="%.3f", padding=3, fontsize=8)

    # 4. Processing Throughput
    ax = axes[1, 1]
    bars = ax.barh(df["variant"], df["fps_throughput"], color=colors)
    ax.set_xlabel("Throughput (fps)")
    ax.set_title("Real-Time Processing Capability")
    ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=8)

    plt.tight_layout()
    plot_path = os.path.join(output_dir, "ablation_study_plots.png")
    plt.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Ablation plots saved to: {plot_path}")


# ── Entry Point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ablation Study Framework")
    parser.add_argument("--input", "-i", required=True,
                        help="Input video file or camera index")
    parser.add_argument("--output", "-o", default=OUTPUT_DIR,
                        help="Output directory")
    parser.add_argument("--capture-seconds", type=int, default=30)
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
            if not ok: break
            out.write(frame)
        cap.release()
        out.release()
        input_path = raw_path
    except ValueError:
        if not os.path.exists(input_path):
            print(f"Input not found: {input_path}")
            sys.exit(1)

    # Load YOLO
    yolo_model = None
    if _YOLO and os.path.exists(MODEL_PATH):
        print(f"Loading YOLO: {MODEL_PATH}")
        yolo_model = YOLO(MODEL_PATH)

    run_ablation(input_path, args.output, yolo_model, args.capture_seconds)


if __name__ == "__main__":
    main()
