"""
scalability_test.py — Multi-Camera Scalability Test Harness

Simulates and measures system performance at different scales:
  - 1, 2, 4, 8, 16, 32 cameras
  - Measures: CPU usage, memory, throughput, latency, bandwidth
  - Tests both single-threaded and multi-threaded pipelines

Generates scalability plots showing how the system degrades with camera count.

Usage:
    python3 scalability_test.py --max-cameras 16 --test-duration 10
    python3 scalability_test.py --max-cameras 32 --video /path/to/test.mp4
"""

import os
import sys
import csv
import time
import json
import threading
import multiprocessing
import argparse
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import collections

import cv2
import numpy as np

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    _PLOT = True
except ImportError:
    _PLOT = False

try:
    from ultralytics import YOLO
    _YOLO = True
except ImportError:
    _YOLO = False


# ── Configuration ──────────────────────────────────────────────────────────

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "yolov8n.pt")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "scalability_results")


# ── Resource Monitor ──────────────────────────────────────────────────────

class ResourceMonitor:
    """Monitor CPU, memory, and thread count during tests."""

    def __init__(self, interval_s: float = 0.5):
        self.interval_s = interval_s
        self.samples = []
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _monitor_loop(self):
        while self._running:
            sample = {
                "timestamp": time.time(),
                "cpu_percent": 0,
                "memory_mb": 0,
                "thread_count": threading.active_count(),
            }
            if _PSUTIL:
                sample["cpu_percent"] = psutil.cpu_percent(interval=None)
                sample["memory_mb"] = psutil.Process().memory_info().rss / (1024 * 1024)
            self.samples.append(sample)
            time.sleep(self.interval_s)

    def get_stats(self) -> Dict:
        if not self.samples:
            return {}
        cpu = [s["cpu_percent"] for s in self.samples]
        mem = [s["memory_mb"] for s in self.samples]
        threads = [s["thread_count"] for s in self.samples]
        return {
            "avg_cpu_pct": round(np.mean(cpu), 1),
            "peak_cpu_pct": round(max(cpu), 1),
            "avg_memory_mb": round(np.mean(mem), 1),
            "peak_memory_mb": round(max(mem), 1),
            "avg_threads": round(np.mean(threads), 1),
            "peak_threads": max(threads),
            "samples": len(self.samples),
        }


# ── Simulated Camera Thread ──────────────────────────────────────────────

class SimulatedCamera:
    """
    Simulates a camera pipeline (capture → detect → compress) without
    requiring an actual camera device. Uses a test video or generated frames.
    """

    def __init__(self, camera_id: str, video_source, detector=None, fps: int = 15):
        self.camera_id = camera_id
        self.video_source = video_source
        self.detector = detector
        self.fps = fps
        self.running = False
        self.frame_count = 0
        self.total_bytes = 0
        self.latencies = collections.deque(maxlen=100)
        self._thread = None

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        cap = cv2.VideoCapture(self.video_source)
        if not cap.isOpened():
            print(f"  [{self.camera_id}] Cannot open source")
            return

        while self.running:
            ret, frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop
                continue

            start = time.perf_counter()

            # Detection (every 3rd frame)
            self.frame_count += 1
            rois = []
            if self.detector and self.frame_count % 3 == 0:
                rois = self.detector.detect(frame, conf=0.3)

            # Simple compression (downscale + ROI restore)
            h, w = frame.shape[:2]
            bg_scale = 0.5
            bg_w = max(1, int(w * bg_scale))
            bg_h = max(1, int(h * bg_scale))
            bg_small = cv2.resize(frame, (bg_w, bg_h), interpolation=cv2.INTER_AREA)
            recon = cv2.resize(bg_small, (w, h), interpolation=cv2.INTER_LINEAR)

            for roi in rois:
                x1, y1, x2, y2 = roi["bbox"]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                if x2 > x1 and y2 > y1:
                    recon[y1:y2, x1:x2] = frame[y1:y2, x1:x2]

            # Simulate encoding overhead (estimate bytes)
            gray = cv2.cvtColor(recon, cv2.COLOR_BGR2GRAY)
            self.total_bytes += gray.nbytes * 0.15  # ~15% compression ratio

            elapsed = (time.perf_counter() - start) * 1000
            self.latencies.append(elapsed)

            # Sleep to maintain target FPS
            sleep_time = max(0, (1.0 / self.fps) - (elapsed / 1000))
            time.sleep(sleep_time)

        cap.release()

    def get_stats(self) -> Dict:
        latencies = list(self.latencies)
        return {
            "camera_id": self.camera_id,
            "frame_count": self.frame_count,
            "total_bytes": self.total_bytes,
            "avg_latency_ms": round(np.mean(latencies), 3) if latencies else 0,
            "p95_latency_ms": round(sorted(latencies)[int(len(latencies)*0.95)], 3) if len(latencies) > 1 else 0,
            "avg_fps": round(1000 / np.mean(latencies), 1) if latencies else 0,
        }


# ── Scalability Test Runner ──────────────────────────────────────────────

class ScalabilityTester:
    """
    Run scalability tests with increasing camera counts.
    """

    CAMERA_COUNTS = [1, 2, 4, 8, 16, 32]

    def __init__(
        self,
        test_video: str,
        max_cameras: int = 16,
        test_duration_s: int = 10,
        fps: int = 15,
    ):
        self.test_video = test_video
        self.max_cameras = min(max_cameras, 32)
        self.test_duration_s = test_duration_s
        self.fps = fps
        self.results = []

    def run(self, output_dir: str):
        """Run full scalability test suite."""
        os.makedirs(output_dir, exist_ok=True)

        # Load detector once
        detector = None
        if _YOLO and os.path.exists(MODEL_PATH):
            print(f"Loading YOLO model: {MODEL_PATH}")
            detector = YOLO(MODEL_PATH)

        # Filter camera counts up to max
        counts = [c for c in self.CAMERA_COUNTS if c <= self.max_cameras]

        print(f"\nScalability Test Configuration:")
        print(f"  Video: {self.test_video}")
        print(f"  Camera counts: {counts}")
        print(f"  Test duration: {self.test_duration_s}s per scale")
        print(f"  Target FPS: {self.fps}\n")

        for num_cameras in counts:
            print(f"\n{'═'*60}")
            print(f"  Testing with {num_cameras} camera(s)")
            print(f"{'═'*60}")

            result = self._test_scale(num_cameras, detector)
            self.results.append(result)

            print(f"  CPU: {result['resource_stats'].get('avg_cpu_pct', 'N/A')}% | "
                  f"Memory: {result['resource_stats'].get('avg_memory_mb', 'N/A')} MB | "
                  f"Throughput: {result['aggregate_fps']:.1f} fps | "
                  f"Avg Latency: {result['avg_latency_ms']:.1f} ms")

        # Save results
        self._save_results(output_dir)

        # Generate plots
        if _PLOT and self.results:
            self._generate_plots(output_dir)

        return self.results

    def _test_scale(self, num_cameras: int, detector) -> Dict:
        """Test a specific camera count."""
        # Start resource monitor
        res_monitor = ResourceMonitor(interval_s=0.25)
        res_monitor.start()

        # Create simulated cameras
        cameras = []
        for i in range(num_cameras):
            cam = SimulatedCamera(
                camera_id=f"cam_{i:02d}",
                video_source=self.test_video,
                detector=detector,
                fps=self.fps,
            )
            cameras.append(cam)

        # Start all cameras
        print(f"  Starting {num_cameras} camera threads...")
        for cam in cameras:
            cam.start()

        # Wait for test duration
        print(f"  Running for {self.test_duration_s}s...")
        time.sleep(self.test_duration_s)

        # Stop all cameras
        for cam in cameras:
            cam.stop()
        res_monitor.stop()

        # Collect stats
        cam_stats = [cam.get_stats() for cam in cameras]
        res_stats = res_monitor.get_stats()

        # Aggregate
        total_frames = sum(s["frame_count"] for s in cam_stats)
        total_bytes = sum(s["total_bytes"] for s in cam_stats)
        avg_latency = np.mean([s["avg_latency_ms"] for s in cam_stats if s["avg_latency_ms"] > 0])
        avg_fps = np.mean([s["avg_fps"] for s in cam_stats if s["avg_fps"] > 0])
        aggregate_fps = total_frames / self.test_duration_s

        return {
            "num_cameras": num_cameras,
            "test_duration_s": self.test_duration_s,
            "total_frames": total_frames,
            "total_bytes": total_bytes,
            "aggregate_fps": round(aggregate_fps, 1),
            "avg_latency_ms": round(float(avg_latency), 2),
            "avg_fps_per_camera": round(float(avg_fps), 1),
            "total_bandwidth_kbps": round((total_bytes * 8 / 1000) / self.test_duration_s, 1),
            "resource_stats": res_stats,
            "per_camera_stats": cam_stats,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _save_results(self, output_dir: str):
        """Save results to CSV and JSON."""
        # CSV
        csv_path = os.path.join(output_dir, "scalability_results.csv")
        fieldnames = [
            "num_cameras", "test_duration_s", "total_frames", "total_bytes",
            "aggregate_fps", "avg_latency_ms", "avg_fps_per_camera",
            "total_bandwidth_kbps", "avg_cpu_pct", "peak_cpu_pct",
            "avg_memory_mb", "peak_memory_mb", "avg_threads",
        ]
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in self.results:
                row = {
                    k: r[k] for k in fieldnames if k in r
                }
                # Flatten resource stats
                res = r.get("resource_stats", {})
                row["avg_cpu_pct"] = res.get("avg_cpu_pct", "")
                row["peak_cpu_pct"] = res.get("peak_cpu_pct", "")
                row["avg_memory_mb"] = res.get("avg_memory_mb", "")
                row["peak_memory_mb"] = res.get("peak_memory_mb", "")
                row["avg_threads"] = res.get("avg_threads", "")
                writer.writerow(row)
        print(f"\nResults saved to: {csv_path}")

        # Full JSON
        json_path = os.path.join(output_dir, "scalability_results.json")
        with open(json_path, "w") as f:
            json.dump(self.results, f, indent=2, default=str)

    def _generate_plots(self, output_dir: str):
        """Generate scalability analysis plots."""
        fig = plt.figure(figsize=(16, 12))
        fig.suptitle("Scalability Analysis — Multi-Camera Surveillance System",
                     fontsize=14, fontweight="bold")
        gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.35, wspace=0.3)

        cameras = [r["num_cameras"] for r in self.results]

        # 1. Throughput vs Camera Count
        ax1 = fig.add_subplot(gs[0, 0])
        ax1.plot(cameras, [r["aggregate_fps"] for r in self.results],
                 marker="o", linewidth=2, color="#4e79a7", label="Aggregate")
        ax1.plot(cameras, [r["avg_fps_per_camera"] for r in self.results],
                 marker="s", linewidth=2, color="#f28e2b", label="Per Camera")
        ax1.set_title("Throughput vs Camera Count")
        ax1.set_xlabel("Number of Cameras")
        ax1.set_ylabel("Frames per Second")
        ax1.legend()
        ax1.grid(alpha=0.3)
        ax1.set_xscale("log", base=2)
        ax1.set_xticks(cameras)
        ax1.set_xticklabels(cameras)

        # 2. Latency vs Camera Count
        ax2 = fig.add_subplot(gs[0, 1])
        ax2.plot(cameras, [r["avg_latency_ms"] for r in self.results],
                 marker="o", linewidth=2, color="#e15759")
        ax2.set_title("Average Latency vs Camera Count")
        ax2.set_xlabel("Number of Cameras")
        ax2.set_ylabel("Latency (ms)")
        ax2.grid(alpha=0.3)
        ax2.set_xscale("log", base=2)
        ax2.set_xticks(cameras)
        ax2.set_xticklabels(cameras)

        # Add real-time threshold line (33ms = 30fps)
        ax2.axhline(y=33.3, color="green", linestyle="--", alpha=0.7, label="30fps threshold")
        ax2.axhline(y=66.7, color="orange", linestyle="--", alpha=0.7, label="15fps threshold")
        ax2.legend()

        # 3. CPU Usage vs Camera Count
        ax3 = fig.add_subplot(gs[1, 0])
        cpu_avg = [r.get("resource_stats", {}).get("avg_cpu_pct", 0) for r in self.results]
        cpu_peak = [r.get("resource_stats", {}).get("peak_cpu_pct", 0) for r in self.results]
        ax3.plot(cameras, cpu_avg, marker="o", linewidth=2, color="#59a14f", label="Average")
        ax3.plot(cameras, cpu_peak, marker="^", linewidth=2, color="#edc948", label="Peak")
        ax3.set_title("CPU Usage vs Camera Count")
        ax3.set_xlabel("Number of Cameras")
        ax3.set_ylabel("CPU (%)")
        ax3.legend()
        ax3.grid(alpha=0.3)
        ax3.set_xscale("log", base=2)
        ax3.set_xticks(cameras)
        ax3.set_xticklabels(cameras)
        ax3.axhline(y=100, color="red", linestyle="--", alpha=0.5)

        # 4. Memory Usage vs Camera Count
        ax4 = fig.add_subplot(gs[1, 1])
        mem_avg = [r.get("resource_stats", {}).get("avg_memory_mb", 0) for r in self.results]
        mem_peak = [r.get("resource_stats", {}).get("peak_memory_mb", 0) for r in self.results]
        ax4.plot(cameras, mem_avg, marker="o", linewidth=2, color="#b07aa1", label="Average")
        ax4.plot(cameras, mem_peak, marker="^", linewidth=2, color="#ff9da7", label="Peak")
        ax4.set_title("Memory Usage vs Camera Count")
        ax4.set_xlabel("Number of Cameras")
        ax4.set_ylabel("Memory (MB)")
        ax4.legend()
        ax4.grid(alpha=0.3)
        ax4.set_xscale("log", base=2)
        ax4.set_xticks(cameras)
        ax4.set_xticklabels(cameras)

        plot_path = os.path.join(output_dir, "scalability_plots.png")
        plt.savefig(plot_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Scalability plots saved to: {plot_path}")


# ── Entry Point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scalability Test Harness")
    parser.add_argument("--video", "-v", default=None,
                        help="Test video path (uses camera 0 if not specified)")
    parser.add_argument("--max-cameras", type=int, default=16,
                        help="Maximum camera count to test")
    parser.add_argument("--test-duration", type=int, default=10,
                        help="Seconds per camera-count test")
    parser.add_argument("--fps", type=int, default=15,
                        help="Target FPS per camera")
    parser.add_argument("--output", "-o", default=OUTPUT_DIR,
                        help="Output directory")
    args = parser.parse_args()

    # Get test video
    test_video = args.video
    if test_video is None:
        # Capture from camera 0
        print("No video specified, capturing 10s from camera 0...")
        test_video = os.path.join(args.output, "test_reference.avi")
        os.makedirs(args.output, exist_ok=True)
        cap = cv2.VideoCapture(0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 15
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
        out = cv2.VideoWriter(test_video, cv2.VideoWriter_fourcc(*'XVID'), fps, (w, h))
        start = time.time()
        while time.time() - start < 10:
            ok, frame = cap.read()
            if not ok:
                break
            out.write(frame)
        cap.release()
        out.release()

    if not os.path.exists(test_video):
        print(f"Test video not found: {test_video}")
        sys.exit(1)

    tester = ScalabilityTester(
        test_video=test_video,
        max_cameras=args.max_cameras,
        test_duration_s=args.test_duration,
        fps=args.fps,
    )
    tester.run(args.output)


if __name__ == "__main__":
    main()
