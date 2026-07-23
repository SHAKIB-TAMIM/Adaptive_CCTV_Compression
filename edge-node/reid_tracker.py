"""
reid_tracker.py — Cross-camera person re-identification (v2 — Deep Embedding).

Strategy:
  Primary  : OSNet-x0.25 ONNX model (deep appearance embedding, 512-dim).
             Downloaded automatically from a public ONNX hub on first use.
  Fallback : Colour-histogram + HOG-like descriptor (original behaviour),
             used when ONNX is unavailable or the crop is too small.

Usage:
  from reid_tracker import ReidTracker
  tracker = ReidTracker(similarity_threshold=0.55)
  track_id, conf, is_new = tracker.match(camera_id, bbox, person_crop, timestamp)
"""

import os
import time as time_module
import threading
import numpy as np
import cv2
from collections import defaultdict

# ── Try to load ONNX runtime ──────────────────────────────────────────────────
try:
    import onnxruntime as ort
    _ORT_AVAILABLE = True
except ImportError:
    _ORT_AVAILABLE = False
    print("[reid] onnxruntime not installed — using colour-histogram fallback")
    print("[reid]   Install: pip install onnxruntime")

# ── OSNet-x0.25 ONNX model path / download ────────────────────────────────────
_MODEL_DIR   = os.path.join(os.path.dirname(__file__), "..", "models")
_MODEL_PATH  = os.path.join(_MODEL_DIR, "osnet_x025_reid.onnx")

# Public ONNX export of OSNet-x0.25 pretrained on Market-1501
_MODEL_URL = (
    "https://github.com/JDAI-CV/fast-reid/releases/download/"
    "v0.1.1/osnet_x0_25_market.onnx"
)

def _download_model():
    """Download OSNet-x0.25 ONNX model if not present."""
    if os.path.exists(_MODEL_PATH):
        return True
    os.makedirs(_MODEL_DIR, exist_ok=True)
    try:
        import urllib.request
        print(f"[reid] Downloading OSNet-x0.25 ONNX model from:\n       {_MODEL_URL}")
        urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
        print(f"[reid] Model saved to {_MODEL_PATH}")
        return True
    except Exception as e:
        print(f"[reid] Download failed: {e}")
        print("[reid] Falling back to colour-histogram Re-ID")
        return False


class DeepEmbedder:
    """OSNet-x0.25 ONNX inference — 512-dim appearance embedding."""

    INPUT_SIZE = (128, 256)   # (W, H) — OSNet standard
    MEAN  = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    STD   = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def __init__(self):
        self.session  = None
        self._lock    = threading.Lock()
        self.available = False

        if not _ORT_AVAILABLE:
            return
        if not os.path.exists(_MODEL_PATH):
            if not _download_model():
                return

        try:
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 2
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider'] \
                        if 'CUDAExecutionProvider' in ort.get_available_providers() \
                        else ['CPUExecutionProvider']
            self.session  = ort.InferenceSession(_MODEL_PATH, opts, providers=providers)
            self.input_name  = self.session.get_inputs()[0].name
            self.output_name = self.session.get_outputs()[0].name
            self.available   = True
            print(f"[reid] OSNet-x0.25 ONNX loaded ({providers[0]})")
        except Exception as e:
            print(f"[reid] ONNX load error: {e} — using histogram fallback")

    def embed(self, bgr_crop):
        """
        Extract 512-dim L2-normalised feature vector.
        Returns float32 ndarray or None on failure.
        """
        if not self.available or bgr_crop is None or bgr_crop.size == 0:
            return None
        h, w = bgr_crop.shape[:2]
        if h < 20 or w < 10:
            return None
        try:
            img = cv2.resize(bgr_crop, self.INPUT_SIZE, interpolation=cv2.INTER_LINEAR)
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            img = (img - self.MEAN) / self.STD
            blob = img.transpose(2, 0, 1)[np.newaxis]          # (1, C, H, W)
            with self._lock:
                feat = self.session.run([self.output_name], {self.input_name: blob})[0][0]
            norm = np.linalg.norm(feat)
            return feat / max(norm, 1e-6)
        except Exception as e:
            print(f"[reid] embed error: {e}")
            return None


class HistogramEmbedder:
    """Colour-histogram + gradient-histogram fallback (original v1 logic)."""

    def embed(self, bgr_crop):
        if bgr_crop is None or bgr_crop.size == 0:
            return None
        h, w = bgr_crop.shape[:2]
        if h < 10 or w < 10:
            return None

        patch = cv2.resize(bgr_crop, (64, 128), interpolation=cv2.INTER_AREA)

        # HSV colour histogram (48 bins)
        hsv   = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        hist_h = cv2.calcHist([hsv], [0], None, [16], [0, 180]).flatten()
        hist_s = cv2.calcHist([hsv], [1], None, [8],  [0, 256]).flatten()
        hist_v = cv2.calcHist([hsv], [2], None, [8],  [0, 256]).flatten()
        colour = np.concatenate([hist_h, hist_s, hist_v])
        colour /= max(1e-6, np.linalg.norm(colour))

        # Gradient-magnitude histogram (16 bins)
        gray  = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        gx    = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy    = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag   = np.sqrt(gx ** 2 + gy ** 2)
        mag_hist = np.histogram(mag.flatten(), bins=16, range=(0, 255))[0].astype(np.float32)
        mag_hist /= max(1e-6, np.linalg.norm(mag_hist))

        # Spatial-stripe mean RGB (4 stripes × 3 channels = 12)
        stripe_h = 32
        stripes  = []
        for i in range(4):
            s = patch[i * stripe_h:(i + 1) * stripe_h, :]
            stripes.extend(cv2.mean(s)[:3])
        spatial = np.array(stripes, dtype=np.float32)
        spatial /= max(1e-6, np.linalg.norm(spatial))

        feat = np.concatenate([colour, mag_hist, spatial])
        return feat / max(1e-6, np.linalg.norm(feat))


# ── Singleton embedder (initialised once) ─────────────────────────────────────
_deep_embedder = None
_hist_embedder = HistogramEmbedder()

def _get_embedder():
    global _deep_embedder
    if _deep_embedder is None:
        _deep_embedder = DeepEmbedder()
    return _deep_embedder


# ── ReidTracker ───────────────────────────────────────────────────────────────

class ReidTracker:
    """
    Cross-camera person re-identification tracker.

    Uses deep OSNet-x0.25 embeddings when available, otherwise falls back to
    colour-histogram descriptors.  Cosine-similarity matching with temporal TTL.
    """

    def __init__(self, similarity_threshold=0.55, feature_ttl_seconds=30):
        self.similarity_threshold = similarity_threshold
        self.feature_ttl_seconds  = feature_ttl_seconds

        # Global track database: track_id -> { feature, camera_id, last_seen, bbox, hits }
        self.tracks       = {}
        self.next_track_id = 1

        # Index by camera for cross-camera lookup
        self.camera_features = defaultdict(list)   # camera_id -> [(track_id, feature)]

        embedder = _get_embedder()
        self._use_deep = embedder.available
        self._embedder = embedder if self._use_deep else _hist_embedder

        mode = "OSNet-x0.25 deep embeddings" if self._use_deep else "colour-histogram fallback"
        print(f"[reid] ReidTracker ready — {mode} | threshold={similarity_threshold}")

    # ── Feature extraction ────────────────────────────────────────────────────

    def extract_features(self, person_crop):
        """Return feature vector (L2-normalised) or None."""
        return self._embedder.embed(person_crop)

    # ── Cosine similarity ─────────────────────────────────────────────────────

    @staticmethod
    def cosine_similarity(a, b):
        if a is None or b is None:
            return 0.0
        dot  = float(np.dot(a, b))
        norm = float(np.linalg.norm(a) * np.linalg.norm(b))
        return dot / max(norm, 1e-6)

    # ── Match ─────────────────────────────────────────────────────────────────

    def match(self, camera_id, bbox, person_crop, timestamp=None):
        """
        Match a detected person crop against existing tracks.

        Returns (track_id, confidence, is_new).
          - track_id  : integer (None if feature extraction fails)
          - confidence: cosine similarity of best match (or 0 if new)
          - is_new    : True if this is a newly registered track
        """
        if timestamp is None:
            timestamp = time_module.time()

        features = self.extract_features(person_crop)
        if features is None:
            return None, 0.0, True

        # Build candidate list from ALL cameras for cross-camera matching
        all_candidates = []
        for cam_feats in self.camera_features.values():
            all_candidates.extend(cam_feats)

        best_id    = None
        best_score = 0.0

        for track_id, feat in all_candidates:
            score = self.cosine_similarity(features, feat)
            if score > best_score:
                best_score = score
                best_id    = track_id

        if best_id is not None and best_score >= self.similarity_threshold:
            # ── Update existing track ──────────────────────────────────────
            tid = best_id
            prev_cam = self.tracks[tid]["camera_id"]

            # Exponential moving average on feature (EMA α=0.3) for online adaptation
            old_feat = self.tracks[tid]["feature"]
            self.tracks[tid]["feature"]    = 0.7 * old_feat + 0.3 * features
            self.tracks[tid]["feature"]   /= max(1e-6, np.linalg.norm(self.tracks[tid]["feature"]))
            self.tracks[tid]["camera_id"]  = camera_id
            self.tracks[tid]["bbox"]       = bbox
            self.tracks[tid]["last_seen"]  = timestamp
            self.tracks[tid]["hits"]       = self.tracks[tid].get("hits", 0) + 1

            # Update index entry for this camera if camera changed
            if prev_cam != camera_id:
                # Remove from old camera index
                self.camera_features[prev_cam] = [
                    (ctid, cf) for ctid, cf in self.camera_features[prev_cam] if ctid != tid
                ]
                # Add to new camera index
                self.camera_features[camera_id].append((tid, self.tracks[tid]["feature"]))
            else:
                # Refresh the feature vector in the index
                self.camera_features[camera_id] = [
                    (ctid, self.tracks[tid]["feature"] if ctid == tid else cf)
                    for ctid, cf in self.camera_features[camera_id]
                ]
            return tid, round(best_score, 4), False

        else:
            # ── Create new track ───────────────────────────────────────────
            tid = self.next_track_id
            self.next_track_id += 1
            self.tracks[tid] = {
                "feature":    features,
                "camera_id":  camera_id,
                "bbox":       bbox,
                "last_seen":  timestamp,
                "first_seen": timestamp,
                "hits":       1,
            }
            self.camera_features[camera_id].append((tid, features))
            return tid, round(best_score, 4), True

    # ── Pruning ───────────────────────────────────────────────────────────────

    def prune_expired(self, current_time=None):
        """Remove tracks not seen for feature_ttl_seconds."""
        if current_time is None:
            current_time = time_module.time()
        cutoff  = current_time - self.feature_ttl_seconds
        expired = [tid for tid, t in self.tracks.items() if t["last_seen"] < cutoff]
        for tid in expired:
            cam_id = self.tracks[tid]["camera_id"]
            self.camera_features[cam_id] = [
                (ctid, cf) for ctid, cf in self.camera_features[cam_id] if ctid != tid
            ]
            del self.tracks[tid]

    # ── Accessors ─────────────────────────────────────────────────────────────

    def get_active_count(self):
        return len(self.tracks)

    def get_tracks_by_camera(self, camera_id):
        return {tid: t for tid, t in self.tracks.items() if t["camera_id"] == camera_id}

    def get_embedding_mode(self):
        return "deep_osnet" if self._use_deep else "histogram"
