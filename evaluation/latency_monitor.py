"""
latency_monitor.py — End-to-End Latency Measurement

Measures pipeline latency at each stage:
  1. Capture latency (camera → frame buffer)
  2. Detection latency (YOLO inference)
  3. Compression latency (reconstruct + encode)
  4. Network latency (Socket.IO / UDP transfer)
  5. Decode latency (server receives → display)
  6. Total end-to-end latency

Usage:
    from latency_monitor import LatencyMonitor
    monitor = LatencyMonitor()
    # At each pipeline stage:
    monitor.mark("capture")
    frame = cap.read()
    monitor.mark("detection_start")
    rois = detector.detect(frame)
    monitor.mark("detection_end")
    # ...
    report = monitor.get_report()
"""

import time
import threading
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import json
import os
from datetime import datetime


@dataclass
class LatencySample:
    """Single latency measurement."""
    stage: str
    start_time: float
    end_time: float
    duration_ms: float
    metadata: Dict = field(default_factory=dict)


class LatencyMonitor:
    """
    High-precision pipeline latency monitor.
    Uses monotonic clock for accurate timing.
    """

    STAGES = [
        "capture",
        "preprocess",
        "clahe",
        "detection",
        "roi_merge",
        "risk_score",
        "reconstruct",
        "encode",
        "network_emit",
        "decode",
        "display",
    ]

    def __init__(self, window_size: int = 100, emit_to_server: bool = False,
                 server_url: str = None):
        self.window_size = window_size
        self.samples: Dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))
        self._marks: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.emit_to_server = emit_to_server
        self.server_url = server_url
        self._frame_latencies: deque = deque(maxlen=window_size)
        self._active = True

    def mark(self, stage: str, metadata: Dict = None):
        """Mark a timestamp for a pipeline stage."""
        if not self._active:
            return

        now = time.perf_counter()

        with self._lock:
            self._marks[stage] = now

            # If we have a start mark for this stage's pair, compute latency
            prev_stage = self._get_prev_stage(stage)
            if prev_stage and prev_stage in self._marks:
                duration_ms = (now - self._marks[prev_stage]) * 1000
                sample = LatencySample(
                    stage=f"{prev_stage}→{stage}",
                    start_time=self._marks[prev_stage],
                    end_time=now,
                    duration_ms=duration_ms,
                    metadata=metadata or {},
                )
                self.samples[f"{prev_stage}→{stage}"].append(sample)

    def mark_frame_start(self):
        """Mark the start of a new frame processing cycle."""
        now = time.perf_counter()
        with self._lock:
            self._marks["__frame_start__"] = now

    def mark_frame_end(self):
        """Mark the end of frame processing and record total latency."""
        now = time.perf_counter()
        with self._lock:
            if "__frame_start__" in self._marks:
                total_ms = (now - self._marks["__frame_start__"]) * 1000
                self._frame_latencies.append(total_ms)

    def _get_prev_stage(self, current: str) -> Optional[str]:
        """Get the previous stage in the pipeline."""
        try:
            idx = self.STAGES.index(current)
            return self.STAGES[idx - 1] if idx > 0 else None
        except ValueError:
            return None

    def get_stage_stats(self, stage: str) -> Dict:
        """Get statistics for a specific stage."""
        samples = list(self.samples.get(stage, []))
        if not samples:
            return {"stage": stage, "count": 0}

        durations = [s.duration_ms for s in samples]
        return {
            "stage": stage,
            "count": len(durations),
            "mean_ms": round(statistics.mean(durations), 3),
            "median_ms": round(statistics.median(durations), 3),
            "std_ms": round(statistics.stdev(durations), 3) if len(durations) > 1 else 0,
            "min_ms": round(min(durations), 3),
            "max_ms": round(max(durations), 3),
            "p95_ms": round(sorted(durations)[int(len(durations) * 0.95)], 3) if len(durations) > 1 else round(durations[0], 3),
            "p99_ms": round(sorted(durations)[int(len(durations) * 0.99)], 3) if len(durations) > 1 else round(durations[0], 3),
        }

    def get_report(self) -> Dict:
        """Get full latency report."""
        # Per-stage stats
        stage_stats = {}
        for stage_pair in self.samples:
            stage_stats[stage_pair] = self.get_stage_stats(stage_pair)

        # Total frame latency
        frame_latencies = list(self._frame_latencies)
        total_stats = {}
        if frame_latencies:
            total_stats = {
                "count": len(frame_latencies),
                "mean_ms": round(statistics.mean(frame_latencies), 3),
                "median_ms": round(statistics.median(frame_latencies), 3),
                "std_ms": round(statistics.stdev(frame_latencies), 3) if len(frame_latencies) > 1 else 0,
                "min_ms": round(min(frame_latencies), 3),
                "max_ms": round(max(frame_latencies), 3),
                "p95_ms": round(sorted(frame_latencies)[int(len(frame_latencies) * 0.95)], 3),
                "estimated_fps": round(1000.0 / statistics.mean(frame_latencies), 1) if frame_latencies else 0,
            }

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "total_frames": len(frame_latencies),
            "total_latency": total_stats,
            "stage_breakdown": stage_stats,
        }

    def get_realtime_display(self) -> str:
        """Get a formatted string for real-time console display."""
        report = self.get_report()
        lines = ["\n╔══════════════════════════════════════════════════╗",
                 "║         PIPELINE LATENCY MONITOR                ║",
                 "╠══════════════════════════════════════════════════╣"]

        if report["total_latency"]:
            t = report["total_latency"]
            lines.append(f"║  Total: {t['mean_ms']:.1f}ms (±{t['std_ms']:.1f}ms) | "
                        f"P95: {t['p95_ms']:.1f}ms | FPS: {t['estimated_fps']:.0f}")

        lines.append("╠══════════════════════════════════════════════════╣")
        for stage, stats in report["stage_breakdown"].items():
            if stats["count"] > 0:
                lines.append(f"║  {stage:<35} {stats['mean_ms']:>6.1f}ms  "
                            f"(p95={stats['p95_ms']:>6.1f}ms)")

        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    def save_report(self, filepath: str):
        """Save latency report to JSON file."""
        report = self.get_report()
        with open(filepath, "w") as f:
            json.dump(report, f, indent=2)

    def reset(self):
        """Clear all samples."""
        with self._lock:
            self.samples.clear()
            self._marks.clear()
            self._frame_latencies.clear()

    def stop(self):
        """Stop monitoring."""
        self._active = False


# ── Decorator for automatic timing ────────────────────────────────────────

def timed(monitor: LatencyMonitor, stage_name: str):
    """
    Decorator to time a function call.

    @timed(monitor, "detection")
    def detect(frame):
        ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            monitor.mark(f"{stage_name}_start")
            result = func(*args, **kwargs)
            monitor.mark(stage_name)
            return result
        return wrapper
    return decorator


# ── Network Latency Probe ─────────────────────────────────────────────────

class NetworkLatencyProbe:
    """
    Measures network round-trip latency between edge node and server.
    Sends timestamped ping messages via Socket.IO or HTTP.
    """

    def __init__(self, server_url: str, interval_s: float = 5.0):
        self.server_url = server_url
        self.interval_s = interval_s
        self.rtts: deque = deque(maxlen=100)
        self._running = False

    def start(self):
        """Start background latency probing."""
        self._running = True
        thread = threading.Thread(target=self._probe_loop, daemon=True)
        thread.start()

    def stop(self):
        self._running = False

    def _probe_loop(self):
        """Periodically send ping and measure RTT."""
        import requests
        while self._running:
            try:
                start = time.perf_counter()
                resp = requests.get(
                    f"{self.server_url}/metrics/live",
                    timeout=2
                )
                end = time.perf_counter()
                if resp.ok:
                    rtt_ms = (end - start) * 1000
                    self.rtts.append(rtt_ms)
            except Exception:
                pass
            time.sleep(self.interval_s)

    def get_stats(self) -> Dict:
        """Get network latency statistics."""
        rtts = list(self.rtts)
        if not rtts:
            return {"count": 0, "mean_ms": 0, "p95_ms": 0}
        return {
            "count": len(rtts),
            "mean_ms": round(statistics.mean(rtts), 2),
            "median_ms": round(statistics.median(rtts), 2),
            "p95_ms": round(sorted(rtts)[int(len(rtts)*0.95)], 2) if len(rtts) > 1 else round(rtts[0], 2),
            "min_ms": round(min(rtts), 2),
            "max_ms": round(max(rtts), 2),
        }


# ── Integration Helper ────────────────────────────────────────────────────

class PipelineProfiler:
    """
    Integrates latency monitoring into the existing pipeline.
    Drop-in replacement for timing the edge-node pipeline.
    """

    def __init__(self, output_dir: str = None):
        self.monitor = LatencyMonitor(window_size=200)
        self.output_dir = output_dir
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

    def profile_capture(self, cap):
        """Wrap cv2.VideoCapture.read() with latency measurement."""
        self.monitor.mark("capture")
        ret, frame = cap.read()
        self.monitor.mark("preprocess")
        return ret, frame

    def profile_detection(self, detector, frame, conf=0.3):
        """Wrap detector.detect() with latency measurement."""
        self.monitor.mark("preprocess")
        rois = detector.detect(frame, conf=conf)
        self.monitor.mark("detection")
        return rois

    def profile_reconstruction(self, frame, rois, bg_scale, **kwargs):
        """Wrap reconstruct function with latency measurement."""
        from camera_manager import reconstruct_background_with_rois
        self.monitor.mark("reconstruct")
        recon = reconstruct_background_with_rois(
            frame, rois, bg_scale,
            kwargs.get("privacy_blur", False),
            kwargs.get("ethical_mode", False),
            kwargs.get("mask_faces", False)
        )
        self.monitor.mark("encode")
        return recon

    def profile_emit(self):
        """Mark network emit stage."""
        self.monitor.mark("network_emit")
        self.monitor.mark("display")

    def get_report(self) -> Dict:
        return self.monitor.get_report()

    def print_report(self):
        print(self.monitor.get_realtime_display())

    def save(self):
        if self.output_dir:
            path = os.path.join(self.output_dir, f"latency_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
            self.monitor.save_report(path)
            print(f"Latency report saved to: {path}")


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Latency Monitor")
    parser.add_argument("--server", default="http://127.0.0.1:5000",
                       help="Server URL for network probe")
    parser.add_argument("--probe-interval", type=float, default=2.0,
                       help="Network probe interval (seconds)")
    parser.add_argument("--duration", type=int, default=30,
                       help="Probe duration (seconds)")
    parser.add_argument("--output", default=None,
                       help="Output JSON path")
    args = parser.parse_args()

    probe = NetworkLatencyProbe(args.server, args.probe_interval)
    print(f"Probing network latency to {args.server} for {args.duration}s...")
    probe.start()
    time.sleep(args.duration)
    probe.stop()

    stats = probe.get_stats()
    print(f"\n=== Network Latency Results ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(stats, f, indent=2)
        print(f"Saved to: {args.output}")
