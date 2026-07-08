"""
reid_tracker.py — Cross-camera person re-identification.
Extracts appearance features (color histograms + texture) from detected persons
and matches them across cameras using cosine similarity.

Usage:
  from reid_tracker import ReidTracker
  tracker = ReidTracker(similarity_threshold=0.6)
  track_id = tracker.match(camera_id, bbox, person_crop)
"""
import numpy as np
import cv2
from collections import defaultdict


class ReidTracker:
    def __init__(self, similarity_threshold=0.6, feature_ttl_seconds=30):
        self.similarity_threshold = similarity_threshold
        self.feature_ttl_seconds = feature_ttl_seconds
        # Global track database: track_id -> { features, camera_id, last_seen, bbox }
        self.tracks = {}
        self.next_track_id = 1
        # Index by camera for faster matching
        self.camera_features = defaultdict(list)  # camera_id -> [(track_id, feature_vector)]

    def extract_features(self, person_crop):
        """Extract appearance features from a person bounding box crop.
        Returns a compact feature vector (color histogram + HOG-like descriptor).
        """
        if person_crop is None or person_crop.size == 0:
            return None

        h, w = person_crop.shape[:2]
        if h < 10 or w < 10:
            return None

        # Resize to consistent size
        patch = cv2.resize(person_crop, (64, 128), interpolation=cv2.INTER_AREA)

        # 1. Color histogram (HSV) — 48 bins
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        hist_h = cv2.calcHist([hsv], [0], None, [16], [0, 180]).flatten()
        hist_s = cv2.calcHist([hsv], [1], None, [8], [0, 256]).flatten()
        hist_v = cv2.calcHist([hsv], [2], None, [8], [0, 256]).flatten()
        color_feat = np.concatenate([hist_h, hist_s, hist_v])
        color_feat = color_feat / max(1e-6, np.linalg.norm(color_feat))

        # 2. Simple texture descriptor: horizontal + vertical gradient histograms
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag = np.sqrt(gx ** 2 + gy ** 2)
        mag_hist = np.histogram(mag.flatten(), bins=16, range=(0, 255))[0].astype(np.float32)
        mag_hist = mag_hist / max(1e-6, np.linalg.norm(mag_hist))

        # 3. Spatial color: split into 4 horizontal stripes, compute mean RGB per stripe
        stripe_h = 128 // 4
        spatial_feat = []
        for i in range(4):
            stripe = patch[i * stripe_h:(i + 1) * stripe_h, :]
            mean_bgr = cv2.mean(stripe)[:3]
            spatial_feat.extend(mean_bgr)
        spatial_feat = np.array(spatial_feat, dtype=np.float32)
        spatial_feat = spatial_feat / max(1e-6, np.linalg.norm(spatial_feat))

        # Concatenate all features
        feature = np.concatenate([color_feat, mag_hist, spatial_feat])
        return feature

    def cosine_similarity(self, a, b):
        if a is None or b is None:
            return 0.0
        dot = np.dot(a, b)
        norm = max(1e-6, np.linalg.norm(a) * np.linalg.norm(b))
        return float(dot / norm)

    def match(self, camera_id, bbox, person_crop, timestamp=None):
        """Match a detected person against existing tracks.
        Returns (track_id, confidence, is_new).
        """
        if timestamp is None:
            timestamp = np.float64(time.time())

        features = self.extract_features(person_crop)
        if features is None:
            return None, 0.0, True

        # Get candidates from the same camera (faster)
        candidates = self.camera_features.get(camera_id, [])
        # Also check other cameras for cross-camera matches
        all_candidates = []
        for cam_features in self.camera_features.values():
            all_candidates.extend(cam_features)

        best_match = None
        best_score = 0.0

        for track_id, feat in all_candidates:
            score = self.cosine_similarity(features, feat)
            if score > best_score:
                best_score = score
                best_match = track_id

        if best_match is not None and best_score >= self.similarity_threshold:
            # Update existing track
            track_id = best_match
            self.tracks[track_id]["features"] = features
            self.tracks[track_id]["camera_id"] = camera_id
            self.tracks[track_id]["bbox"] = bbox
            self.tracks[track_id]["last_seen"] = timestamp
            return track_id, best_score, False
        else:
            # Create new track
            track_id = self.next_track_id
            self.next_track_id += 1
            self.tracks[track_id] = {
                "features": features,
                "camera_id": camera_id,
                "bbox": bbox,
                "last_seen": timestamp,
                "first_seen": timestamp,
            }
            self.camera_features[camera_id].append((track_id, features))
            return track_id, best_score, True

    def prune_expired(self, current_time=None):
        """Remove tracks that haven't been seen for feature_ttl_seconds."""
        if current_time is None:
            current_time = np.float64(time.time())
        cutoff = current_time - self.feature_ttl_seconds
        expired = [tid for tid, t in self.tracks.items() if t["last_seen"] < cutoff]
        for tid in expired:
            cam_id = self.tracks[tid]["camera_id"]
            self.camera_features[cam_id] = [
                (ctid, cf) for ctid, cf in self.camera_features[cam_id] if ctid != tid
            ]
            del self.tracks[tid]

    def get_active_count(self):
        return len(self.tracks)

    def get_tracks_by_camera(self, camera_id):
        """Get active tracks currently visible on a specific camera."""
        return {tid: t for tid, t in self.tracks.items() if t["camera_id"] == camera_id}


import time as time_module
time = time_module
