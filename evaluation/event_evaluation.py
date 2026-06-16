#!/usr/bin/env python3
"""
event_evaluation.py
====================
Event-Preservation Evaluation for the Risk-Aware Surveillance Compression System.

Replaces (or supplements) pure signal-quality metrics (PSNR/SSIM/VMAF) with
surveillance-utility metrics that answer: "Is the incident still understandable
after compression?"

Metrics computed
----------------
1. event_detection_recall     — fraction of critical events still detectable
                                 in compressed frames (YOLOv8 re-detection)
2. event_frame_retention      — fraction of critical-event frames retained
                                 at or above minimum quality threshold (PSNR ≥ 30 dB)
3. pre_event_context_retention — average PSNR of the 15-second pre-event buffer
                                 frames (were they preserved well enough for context?)
4. investigation_usability_score — composite 0–100 score combining the above
                                 plus face/person detectability

Usage
-----
    python3 event_evaluation.py --server http://127.0.0.1:5000 \
                                 --events_dir ../server/events \
                                 --output event_results.csv

The script:
  • Reads event archives from <events_dir>/<event_id>/ produced by the edge node
  • Loads pre-event frames (pre_XXXXX.jpg) and post-event frames (post_XXXXX.jpg)
  • Loads metadata JSON sidecars to obtain original ROI bboxes and risk scores
  • Computes per-frame PSNR against a synthetic "full-quality" baseline
    (the edge node saves the original frame in the pre-event buffer,
     so we can compare pre_XXXXX.jpg [original] vs post_XXXXX.jpg [compressed])
  • Re-runs YOLOv8 on post-event compressed frames to measure detection recall
  • Writes results to CSV and prints a summary table
"""

import os
import sys
import csv
import json
import argparse
import time
from datetime import datetime

import cv2
import numpy as np
import requests
from skimage.metrics import peak_signal_noise_ratio as sk_psnr
from skimage.metrics import structural_similarity as sk_ssim

# Optional: YOLOv8 for detection recall
try:
    from ultralytics import YOLO
    _yolo_available = True
except ImportError:
    _yolo_available = False
    print("[WARNING] ultralytics not installed — detection recall will be skipped")

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

SERVER            = "http://127.0.0.1:5000"
DEFAULT_EVENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "server", "events")
OUTPUT_CSV        = "event_results.csv"
MODEL_PATH        = os.path.join(os.path.dirname(__file__), "..", "models", "yolov8n.pt")
MIN_PSNR_THRESHOLD = 30.0   # dB — below this a frame is considered "unacceptable quality"
CONTEXT_SECONDS    = 15     # pre-event buffer length (must match edge node setting)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_image_gray(path):
    img = cv2.imread(path)
    if img is None:
        return None, None
    return img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def compute_psnr(a, b):
    try:
        a = cv2.resize(a, (b.shape[1], b.shape[0])) if a.shape != b.shape else a
        return float(sk_psnr(a, b, data_range=255))
    except Exception:
        mse = np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2)
        return float("inf") if mse == 0 else 10.0 * np.log10(255.0 ** 2 / mse)


def compute_ssim(a_gray, b_gray):
    try:
        if a_gray.shape != b_gray.shape:
            b_gray = cv2.resize(b_gray, (a_gray.shape[1], a_gray.shape[0]))
        return float(sk_ssim(a_gray, b_gray, data_range=255))
    except Exception as e:
        print("SSIM error:", e)
        return None


def yolo_detect_count(image_path, model):
    """Return number of detected objects in image_path."""
    try:
        results = model(image_path, verbose=False)
        return len(results[0].boxes)
    except Exception as e:
        print("YOLO detect error:", e)
        return 0


def fetch_event_log(server_url):
    """Fetch recent events from server /events endpoint."""
    try:
        r = requests.get(server_url.rstrip("/") + "/events", timeout=5)
        if r.ok:
            return r.json()
    except Exception as e:
        print("fetch_event_log error:", e)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Per-event evaluation
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_event(event_dir, model=None):
    """
    Evaluate a single event archive directory.

    Directory layout (written by edge node):
        pre_00000.jpg / pre_00000_meta.json  — original frames BEFORE event
        post_XXXXX.jpg                        — compressed frames AFTER event

    Returns a dict with all metrics, or None on failure.
    """
    event_id = os.path.basename(event_dir)
    print(f"\n─── Evaluating event: {event_id} ───")

    pre_frames  = sorted(f for f in os.listdir(event_dir) if f.startswith("pre_") and f.endswith(".jpg"))
    post_frames = sorted(f for f in os.listdir(event_dir) if f.startswith("post_") and f.endswith(".jpg"))

    if not pre_frames and not post_frames:
        print("  [skip] No frames found.")
        return None

    # ── 1. Pre-event context retention ──────────────────────────────────────
    # We compare the pre-event frames against themselves to measure
    # how well the rolling buffer preserved quality (these are ORIGINAL frames
    # saved by the edge node, so ideally PSNR should be very high).
    # For a proper comparison we need a reference; use the first pre frame as ref.
    pre_psnrs = []
    if len(pre_frames) >= 2:
        ref_path = os.path.join(event_dir, pre_frames[0])
        ref, ref_gray = load_image_gray(ref_path)
        if ref is not None:
            for pf in pre_frames[1:]:
                img, img_gray = load_image_gray(os.path.join(event_dir, pf))
                if img is not None:
                    pre_psnrs.append(compute_psnr(ref, img))

    pre_context_psnr = float(np.mean(pre_psnrs)) if pre_psnrs else None
    print(f"  Pre-event context PSNR (frame-to-frame): {pre_context_psnr:.2f} dB" if pre_context_psnr else "  Pre-event: not enough frames")

    # ── 2. Event frame retention ─────────────────────────────────────────────
    # For post-event frames: compare compressed (post_XXXXX.jpg) against the
    # last pre-event frame (best available original reference).
    post_psnrs  = []
    post_ssims  = []
    retained_count = 0
    ref_for_post = None

    if pre_frames:
        ref_for_post, ref_gray_post = load_image_gray(
            os.path.join(event_dir, pre_frames[-1])
        )

    if ref_for_post is not None:
        for pf in post_frames:
            img, img_gray = load_image_gray(os.path.join(event_dir, pf))
            if img is None:
                continue
            p = compute_psnr(ref_for_post, img)
            post_psnrs.append(p)
            if p >= MIN_PSNR_THRESHOLD:
                retained_count += 1
            # SSIM
            if img_gray is not None:
                ref_g = cv2.cvtColor(ref_for_post, cv2.COLOR_BGR2GRAY)
                s = compute_ssim(ref_g, img_gray)
                if s is not None:
                    post_ssims.append(s)

    total_post = len(post_frames)
    event_frame_retention = retained_count / total_post if total_post > 0 else None
    mean_post_psnr = float(np.mean(post_psnrs)) if post_psnrs else None
    mean_post_ssim = float(np.mean(post_ssims)) if post_ssims else None

    print(f"  Post-event frames: {total_post} | retained ≥{MIN_PSNR_THRESHOLD} dB: {retained_count} ({100*event_frame_retention:.1f}%)" if event_frame_retention is not None else "  Post-event: no reference available")
    if mean_post_psnr:
        print(f"  Mean post-event PSNR: {mean_post_psnr:.2f} dB | SSIM: {mean_post_ssim:.3f}" if mean_post_ssim else f"  Mean post-event PSNR: {mean_post_psnr:.2f} dB")

    # ── 3. Event detection recall ─────────────────────────────────────────────
    # Re-run YOLOv8 on compressed post-event frames and compare detection count
    # against the original pre-event reference.
    detection_recall = None
    ref_det_count = 0

    if model is not None and ref_for_post is not None:
        ref_tmp = os.path.join(event_dir, "_ref_tmp.jpg")
        cv2.imwrite(ref_tmp, ref_for_post)
        ref_det_count = yolo_detect_count(ref_tmp, model)
        try:
            os.remove(ref_tmp)
        except Exception:
            pass

        post_det_counts = []
        for pf in post_frames[:10]:   # evaluate up to 10 post frames
            ppath = os.path.join(event_dir, pf)
            post_det_counts.append(yolo_detect_count(ppath, model))

        if post_det_counts and ref_det_count > 0:
            mean_post_det = float(np.mean(post_det_counts))
            detection_recall = min(mean_post_det / ref_det_count, 1.0)
            print(f"  Detection recall: {detection_recall:.3f} (ref={ref_det_count}, post_avg={mean_post_det:.1f})")
        elif ref_det_count == 0:
            detection_recall = 1.0  # nothing to detect = recall is perfect
            print("  Detection recall: 1.0 (no objects in reference)")

    # ── 4. Investigation usability score ─────────────────────────────────────
    # Composite 0–100 score:
    #   40% event_frame_retention   (are the critical frames preserved?)
    #   30% detection_recall        (can YOLO still find the intruder?)
    #   30% pre_event_context_psnr  (is the context legible?)
    score_parts = []
    weights = []

    if event_frame_retention is not None:
        score_parts.append(event_frame_retention * 100)
        weights.append(0.40)
    if detection_recall is not None:
        score_parts.append(detection_recall * 100)
        weights.append(0.30)
    if pre_context_psnr is not None:
        # Map PSNR [20–45 dB] to [0–100]
        ctx_score = max(0.0, min(100.0, (pre_context_psnr - 20.0) / 25.0 * 100.0))
        score_parts.append(ctx_score)
        weights.append(0.30)

    if score_parts:
        total_weight = sum(weights)
        usability_score = sum(s * w for s, w in zip(score_parts, weights)) / total_weight
    else:
        usability_score = None

    print(f"  Investigation usability score: {usability_score:.1f}/100" if usability_score is not None else "  Usability: insufficient data")

    # ── Load risk score from first meta file ──────────────────────────────────
    peak_risk = None
    meta_files = sorted(f for f in os.listdir(event_dir) if f.endswith("_meta.json"))
    if meta_files:
        try:
            with open(os.path.join(event_dir, meta_files[-1])) as mf:
                m = json.load(mf)
                peak_risk = m.get("risk")
        except Exception:
            pass

    return {
        "event_id":                event_id,
        "timestamp":               datetime.utcnow().isoformat(),
        "pre_frames":              len(pre_frames),
        "post_frames":             total_post,
        "pre_context_psnr":        round(pre_context_psnr, 3) if pre_context_psnr else "",
        "event_frame_retention":   round(event_frame_retention, 4) if event_frame_retention is not None else "",
        "retained_above_threshold": retained_count,
        "mean_post_psnr":          round(mean_post_psnr, 3) if mean_post_psnr else "",
        "mean_post_ssim":          round(mean_post_ssim, 4) if mean_post_ssim else "",
        "detection_recall":        round(detection_recall, 4) if detection_recall is not None else "",
        "ref_det_count":           ref_det_count,
        "peak_risk":               peak_risk if peak_risk else "",
        "usability_score":         round(usability_score, 2) if usability_score is not None else "",
        "psnr_threshold_db":       MIN_PSNR_THRESHOLD,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def save_row(csv_path, row, fieldnames):
    exists = os.path.exists(csv_path)
    with open(csv_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            w.writeheader()
        w.writerow(row)


def print_summary(results):
    if not results:
        print("\n[summary] No results to display.")
        return
    print("\n" + "═" * 75)
    print(f"{'EVENT ID':<35} {'RETENTION':>10} {'RECALL':>8} {'USABILITY':>10}")
    print("─" * 75)
    for r in results:
        ret  = f"{float(r['event_frame_retention'])*100:.1f}%" if r['event_frame_retention'] != "" else "N/A"
        rec  = f"{float(r['detection_recall'])*100:.1f}%" if r['detection_recall'] != "" else "N/A"
        usz  = f"{r['usability_score']}/100" if r['usability_score'] != "" else "N/A"
        print(f"{r['event_id']:<35} {ret:>10} {rec:>8} {usz:>10}")
    print("═" * 75)


def main():
    parser = argparse.ArgumentParser(description="Event-preservation evaluator")
    parser.add_argument("--server",     default=SERVER,            help="Server base URL")
    parser.add_argument("--events_dir", default=DEFAULT_EVENTS_DIR, help="Path to events archive dir")
    parser.add_argument("--output",     default=OUTPUT_CSV,         help="Output CSV path")
    parser.add_argument("--model",      default=MODEL_PATH,         help="YOLOv8 model path")
    args = parser.parse_args()

    events_dir = os.path.abspath(args.events_dir)
    if not os.path.isdir(events_dir):
        print(f"[ERROR] events_dir not found: {events_dir}")
        sys.exit(1)

    # Load YOLO model
    model = None
    if _yolo_available and os.path.exists(args.model):
        print(f"Loading YOLOv8 model: {args.model}")
        model = YOLO(args.model)
    else:
        print("[INFO] YOLOv8 model not available — detection recall will be skipped")

    # Also fetch server event log for cross-reference
    server_events = fetch_event_log(args.server)
    print(f"[server] {len(server_events)} events logged on server")

    # Discover event subdirectories
    event_dirs = sorted(
        os.path.join(events_dir, d)
        for d in os.listdir(events_dir)
        if os.path.isdir(os.path.join(events_dir, d)) and d.startswith("event_")
    )

    if not event_dirs:
        print(f"[INFO] No event directories found in {events_dir}. Run the system and trigger some critical events first.")
        sys.exit(0)

    print(f"\nFound {len(event_dirs)} event archive(s) to evaluate.")

    fieldnames = [
        "event_id", "timestamp", "pre_frames", "post_frames",
        "pre_context_psnr", "event_frame_retention", "retained_above_threshold",
        "mean_post_psnr", "mean_post_ssim", "detection_recall",
        "ref_det_count", "peak_risk", "usability_score", "psnr_threshold_db",
    ]

    results = []
    for ev_dir in event_dirs:
        result = evaluate_event(ev_dir, model=model)
        if result:
            save_row(args.output, result, fieldnames)
            results.append(result)

    print_summary(results)
    print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
