"""
dataset_loader.py — Standardized Dataset Support for Evaluation

Supports loading and preparing surveillance datasets for evaluation:
  - VIRAT Video Dataset (ground truth events)
  - UCF-Crime Dataset (anomaly detection)
  - Custom CCTV recordings (with ROI annotations)

Provides uniform interface:
  - list_scenes() → list of video clips with metadata
  - load_scene(scene_id) → video frames + ground truth annotations
  - get_events(scene_id) → list of annotated events (timestamps, types, bboxes)

Usage:
    from dataset_loader import DatasetLoader
    loader = DatasetLoader(dataset_path="/data/VIRAT", dataset_type="virat")
    for scene in loader.list_scenes():
        frames, annotations = loader.load_scene(scene["id"])
        # Run evaluation...
"""

import os
import json
import csv
import glob
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Generator
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np


@dataclass
class EventAnnotation:
    """Ground truth event annotation."""
    event_id: str
    event_type: str  # "person", "vehicle", "loitering", " intrusion", "abandoned_object"
    start_frame: int
    end_frame: int
    bbox: Optional[List[int]] = None  # [x1, y1, x2, y2]
    confidence: float = 1.0
    metadata: Dict = field(default_factory=dict)


@dataclass
class SceneInfo:
    """Metadata for a video scene/clip."""
    scene_id: str
    video_path: str
    fps: float
    width: int
    height: int
    total_frames: int
    duration_s: float
    camera_id: str
    location: str
    time_of_day: str  # "day", "night", "dawn", "dusk"
    weather: str  # "clear", "rain", "fog", "night"
    events: List[EventAnnotation] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


# ── VIRAT Dataset Loader ──────────────────────────────────────────────────

class VIRATLoader:
    """
    Loader for VIRAT Video Dataset.
    VIRAT provides real surveillance footage with annotated events.

    Expected directory structure:
        VIRAT/
        ├── scenes/
        │   ├── VIRAT_000001.mp4
        │   ├── VIRAT_000002.mp4
        │   └── ...
        └── annotations/
            ├── VIRAT_000001_events.json
            └── ...
    """

    EVENT_TYPES = {
        "person": ["person_walking", "person_running", "person_standing", "person_sitting"],
        "vehicle": ["vehicle_parking", "vehicle_moving", "vehicle_stopping"],
        "interaction": ["person_talking", "person_handing", "person_exchanging"],
        "anomaly": ["loitering", "intrusion", "abandoned_object", "fight"],
    }

    def __init__(self, dataset_path: str):
        self.dataset_path = Path(dataset_path)
        self.scenes_dir = self.dataset_path / "scenes"
        self.annotations_dir = self.dataset_path / "annotations"

    def list_scenes(self) -> List[Dict]:
        """List all available scenes with metadata."""
        scenes = []
        if not self.scenes_dir.exists():
            print(f"[VIRAT] Scenes directory not found: {self.scenes_dir}")
            return scenes

        for video_path in sorted(self.scenes_dir.glob("*.mp4")):
            scene_id = video_path.stem
            cap = cv2.VideoCapture(str(video_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()

            # Load annotations
            ann_path = self.annotations_dir / f"{scene_id}_events.json"
            events = self._load_annotations(ann_path) if ann_path.exists() else []

            scenes.append({
                "id": scene_id,
                "video_path": str(video_path),
                "fps": fps,
                "width": w,
                "height": h,
                "total_frames": total,
                "duration_s": round(total / fps, 2),
                "num_events": len(events),
                "event_types": list(set(e.event_type for e in events)),
            })

        return scenes

    def load_scene(self, scene_id: str) -> Tuple[Optional[Dict], List[EventAnnotation]]:
        """Load a scene and its annotations."""
        video_path = self.scenes_dir / f"{scene_id}.mp4"
        if not video_path.exists():
            return None, []

        cap = cv2.VideoCapture(str(video_path))
        info = {
            "scene_id": scene_id,
            "video_path": str(video_path),
            "fps": cap.get(cv2.CAP_PROP_FPS),
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "total_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        }
        cap.release()

        ann_path = self.annotations_dir / f"{scene_id}_events.json"
        events = self._load_annotations(ann_path) if ann_path.exists() else []

        return info, events

    def get_events(self, scene_id: str) -> List[EventAnnotation]:
        """Get event annotations for a scene."""
        ann_path = self.annotations_dir / f"{scene_id}_events.json"
        return self._load_annotations(ann_path) if ann_path.exists() else []

    def _load_annotations(self, path: Path) -> List[EventAnnotation]:
        """Parse VIRAT annotation JSON."""
        events = []
        try:
            with open(path) as f:
                data = json.load(f)
            for item in data.get("events", []):
                events.append(EventAnnotation(
                    event_id=item.get("id", ""),
                    event_type=item.get("type", "unknown"),
                    start_frame=item.get("start_frame", 0),
                    end_frame=item.get("end_frame", 0),
                    bbox=item.get("bbox"),
                    confidence=item.get("confidence", 1.0),
                ))
        except Exception as e:
            print(f"[VIRAT] Annotation parse error: {e}")
        return events

    def generate_frames(self, scene_id: str, start_frame=0, end_frame=None) -> Generator:
        """Yield frames from a scene as (frame_idx, frame_bgr)."""
        video_path = self.scenes_dir / f"{scene_id}.mp4"
        cap = cv2.VideoCapture(str(video_path))
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        idx = start_frame
        while True:
            if end_frame and idx >= end_frame:
                break
            ret, frame = cap.read()
            if not ret:
                break
            yield idx, frame
            idx += 1

        cap.release()


# ── UCF-Crime Dataset Loader ──────────────────────────────────────────────

class UCFCrimeLoader:
    """
    Loader for UCF-Crime Dataset (anomaly detection benchmark).

    Expected directory structure:
        UCF-Crime/
        ├── Normal/
        │   ├── Normal_Videos_001_x264.mp4
        │   └── ...
        ├── Abnormal/
        │   ├── Abuse001_x264.mp4
        │   ├── Arrest001_x264.mp4
        │   └── ...
        └── Anomaly_Detection.txt  (or .csv)
    """

    ANOMALY_CLASSES = [
        "Abuse", "Arrest", "Arson", "Assault", "Burglary",
        "Explosion", "Fighting", "Robbery", "Shooting",
        "Stealing", "Vandalism",
    ]

    def __init__(self, dataset_path: str):
        self.dataset_path = Path(dataset_path)

    def list_scenes(self) -> List[Dict]:
        scenes = []
        for split in ["Normal", "Abnormal"]:
            split_dir = self.dataset_path / split
            if not split_dir.exists():
                continue
            for video_path in sorted(split_dir.glob("*.mp4")):
                scene_id = video_path.stem
                cap = cv2.VideoCapture(str(video_path))
                fps = cap.get(cv2.CAP_PROP_FPS) or 30
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                cap.release()

                is_anomaly = split == "Abnormal"
                event_type = scene_id.split("_")[0] if is_anomaly else "normal"

                scenes.append({
                    "id": scene_id,
                    "video_path": str(video_path),
                    "fps": fps,
                    "width": w,
                    "height": h,
                    "total_frames": total,
                    "duration_s": round(total / fps, 2),
                    "is_anomaly": is_anomaly,
                    "event_type": event_type,
                })

        return scenes

    def load_scene(self, scene_id: str) -> Tuple[Optional[Dict], List[EventAnnotation]]:
        """Load scene info and event annotation."""
        for split in ["Normal", "Abnormal"]:
            video_path = self.dataset_path / split / f"{scene_id}.mp4"
            if video_path.exists():
                cap = cv2.VideoCapture(str(video_path))
                info = {
                    "scene_id": scene_id,
                    "video_path": str(video_path),
                    "fps": cap.get(cv2.CAP_PROP_FPS),
                    "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    "total_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
                }
                cap.release()

                is_anomaly = split == "Abnormal"
                event_type = scene_id.split("_")[0] if is_anomaly else "normal"
                events = [
                    EventAnnotation(
                        event_id=f"{scene_id}_event",
                        event_type=event_type if is_anomaly else "normal",
                        start_frame=0,
                        end_frame=info["total_frames"],
                        confidence=1.0,
                    )
                ] if is_anomaly else []

                return info, events

        return None, []


# ── Custom CCTV Loader ────────────────────────────────────────────────────

class CustomCCTVLoader:
    """
    Loader for custom CCTV recordings.

    Expected structure:
        custom_cctv/
        ├── camera_0/
        │   ├── 2026-01-15/
        │   │   ├── frames/
        │   │   │   ├── frame_00000001.jpg
        │   │   │   └── ...
        │   │   └── meta.json
        │   └── ...
        ├── events/
        │   ├── event_1234567890.json
        │   └── ...
        └── annotations.json  (optional manual annotations)
    """

    def __init__(self, dataset_path: str):
        self.dataset_path = Path(dataset_path)

    def list_scenes(self) -> List[Dict]:
        scenes = []
        for camera_dir in sorted(self.dataset_path.glob("camera_*")):
            if not camera_dir.is_dir():
                continue
            camera_id = camera_dir.name
            for date_dir in sorted(camera_dir.glob("20??-??-??")):
                if not date_dir.is_dir():
                    continue
                frames_dir = date_dir / "frames"
                if not frames_dir.exists():
                    continue

                frame_files = sorted(frames_dir.glob("frame_*.jpg"))
                if not frame_files:
                    continue

                meta_path = date_dir / "meta.json"
                meta = {}
                if meta_path.exists():
                    try:
                        with open(meta_path) as f:
                            meta = json.load(f)
                    except Exception:
                        pass

                scenes.append({
                    "id": f"{camera_id}_{date_dir.name}",
                    "camera_id": camera_id,
                    "date": date_dir.name,
                    "frames_dir": str(frames_dir),
                    "num_frames": len(frame_files),
                    "frame_files": [str(f) for f in frame_files],
                    "metadata": meta,
                })

        return scenes

    def load_scene(self, scene_id: str) -> Tuple[Optional[Dict], List[EventAnnotation]]:
        parts = scene_id.split("_")
        camera_id = "_".join(parts[:2]) if len(parts) > 2 else parts[0]
        date = parts[-1] if len(parts) > 2 else parts[1] if len(parts) > 1 else ""

        camera_dir = self.dataset_path / camera_id
        date_dir = camera_dir / date
        frames_dir = date_dir / "frames"

        if not frames_dir.exists():
            return None, []

        frame_files = sorted(frames_dir.glob("frame_*.jpg"))
        if not frame_files:
            return None, []

        # Read first frame for dimensions
        first_frame = cv2.imread(str(frame_files[0]))
        h, w = first_frame.shape[:2] if first_frame is not None else (480, 640)

        info = {
            "scene_id": scene_id,
            "camera_id": camera_id,
            "date": date,
            "frames_dir": str(frames_dir),
            "num_frames": len(frame_files),
            "width": w,
            "height": h,
        }

        # Load events
        events = []
        events_dir = self.dataset_path / "events"
        if events_dir.exists():
            for ev_file in events_dir.glob("event_*.json"):
                try:
                    with open(ev_file) as f:
                        ev_data = json.load(f)
                    if ev_data.get("camera_id") == camera_id:
                        events.append(EventAnnotation(
                            event_id=ev_data.get("event_id", ev_file.stem),
                            event_type=ev_data.get("state", "unknown"),
                            start_frame=0,
                            end_frame=ev_data.get("num_rois", 100),
                            metadata=ev_data,
                        ))
                except Exception:
                    pass

        return info, events


# ── Unified Dataset Loader ────────────────────────────────────────────────

class DatasetLoader:
    """
    Unified interface for loading any supported dataset.

    Usage:
        loader = DatasetLoader("/path/to/dataset", dataset_type="auto")
        for scene in loader.list_scenes():
            ...
    """

    LOADERS = {
        "virat": VIRATLoader,
        "ucf_crime": UCFCrimeLoader,
        "custom": CustomCCTVLoader,
    }

    def __init__(self, dataset_path: str, dataset_type: str = "auto"):
        self.dataset_path = dataset_path
        self.dataset_type = dataset_type

        if dataset_type == "auto":
            self.dataset_type = self._detect_type()

        loader_class = self.LOADERS.get(self.dataset_type)
        if loader_class is None:
            raise ValueError(f"Unknown dataset type: {dataset_type}. "
                           f"Supported: {list(self.LOADERS.keys())}")
        self.loader = loader_class(dataset_path)

    def _detect_type(self) -> str:
        """Auto-detect dataset type from directory structure."""
        path = Path(self.dataset_path)
        if (path / "scenes").exists() and (path / "annotations").exists():
            return "virat"
        elif (path / "Normal").exists() or (path / "Abnormal").exists():
            return "ucf_crime"
        elif any(path.glob("camera_*")):
            return "custom"
        else:
            return "custom"  # fallback

    def list_scenes(self) -> List[Dict]:
        return self.loader.list_scenes()

    def load_scene(self, scene_id: str) -> Tuple[Optional[Dict], List[EventAnnotation]]:
        return self.loader.load_scene(scene_id)

    def get_summary(self) -> Dict:
        """Get dataset summary statistics."""
        scenes = self.list_scenes()
        total_events = sum(s.get("num_events", 0) for s in scenes)
        total_frames = sum(s.get("total_frames", 0) for s in scenes)
        total_duration = sum(s.get("duration_s", 0) for s in scenes)

        return {
            "dataset_type": self.dataset_type,
            "total_scenes": len(scenes),
            "total_frames": total_frames,
            "total_duration_s": round(total_duration, 1),
            "total_events": total_events,
            "avg_scene_duration": round(total_duration / max(len(scenes), 1), 2),
        }


# ── Evaluation Dataset Builder ────────────────────────────────────────────

def create_evaluation_dataset(
    source_video: str,
    output_dir: str,
    scene_length_s: int = 30,
    overlap_s: int = 5,
):
    """
    Split a long video into fixed-length scenes for evaluation.
    Useful for creating a standardized test set from your own recordings.
    """
    os.makedirs(output_dir, exist_ok=True)
    cap = cv2.VideoCapture(source_video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    frames_per_scene = int(fps * scene_length_s)
    overlap_frames = int(fps * overlap_s)
    step = frames_per_scene - overlap_frames

    scenes = []
    scene_idx = 0
    start = 0

    while start + frames_per_scene <= total_frames:
        scene_id = f"scene_{scene_idx:04d}"
        scene_dir = os.path.join(output_dir, scene_id)
        frames_dir = os.path.join(scene_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        cap = cv2.VideoCapture(source_video)
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)

        frame_idx = 0
        while frame_idx < frames_per_scene:
            ret, frame = cap.read()
            if not ret:
                break
            frame_path = os.path.join(frames_dir, f"frame_{frame_idx:06d}.jpg")
            cv2.imwrite(frame_path, frame)
            frame_idx += 1
        cap.release()

        scene_meta = {
            "scene_id": scene_id,
            "start_frame": start,
            "end_frame": start + frames_per_scene,
            "fps": fps,
            "width": w,
            "height": h,
            "num_frames": frames_per_scene,
            "duration_s": scene_length_s,
        }
        with open(os.path.join(scene_dir, "meta.json"), "w") as f:
            json.dump(scene_meta, f, indent=2)

        scenes.append(scene_meta)
        scene_idx += 1
        start += step

    # Save overall manifest
    manifest = {
        "source_video": source_video,
        "total_scenes": len(scenes),
        "scene_length_s": scene_length_s,
        "overlap_s": overlap_s,
        "scenes": scenes,
    }
    with open(os.path.join(output_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Created {len(scenes)} scenes in {output_dir}")
    return manifest


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Dataset Loader & Builder")
    subparsers = parser.add_subparsers(dest="command")

    # Load command
    load_parser = subparsers.add_parser("load", help="Load and summarize a dataset")
    load_parser.add_argument("--path", required=True, help="Dataset root path")
    load_parser.add_argument("--type", default="auto",
                            choices=["auto", "virat", "ucf_crime", "custom"])

    # Build command
    build_parser = subparsers.add_parser("build", help="Build evaluation dataset from video")
    build_parser.add_argument("--input", required=True, help="Source video path")
    build_parser.add_argument("--output", required=True, help="Output directory")
    build_parser.add_argument("--scene-length", type=int, default=30, help="Seconds per scene")
    build_parser.add_argument("--overlap", type=int, default=5, help="Overlap seconds")

    args = parser.parse_args()

    if args.command == "load":
        loader = DatasetLoader(args.path, args.type)
        summary = loader.get_summary()
        print("\n=== Dataset Summary ===")
        for k, v in summary.items():
            print(f"  {k}: {v}")

        scenes = loader.list_scenes()
        print(f"\nFirst 5 scenes:")
        for s in scenes[:5]:
            print(f"  {s['id']}: {s.get('total_frames', '?')} frames, "
                  f"{s.get('duration_s', '?')}s, events={s.get('num_events', 0)}")

    elif args.command == "build":
        create_evaluation_dataset(args.input, args.output, args.scene_length, args.overlap)

    else:
        parser.print_help()
