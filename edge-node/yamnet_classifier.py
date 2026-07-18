"""
yamnet_classifier.py — YAMNet-based 11-class audio classifier.

Runs YAMNet (TFLite) on 0.975s windows of 16kHz audio with 75% overlap.
Maps YAMNet's 521 AudioSet classes to 11 surveillance-relevant classes.
Applies temporal smoothing (median over last 3 windows).

Usage (from audio_monitor.py):
  from yamnet_classifier import YamNetClassifier
  classifier = YamNetClassifier("/tmp/yamnet_model/1.tflite")
  classifier.feed_chunk(chunk_16khz)  # returns (class, confidence) or (None, 0)
"""
import os
import csv
import threading
import numpy as np
from collections import deque

# ---- Class map: YAMNet index -> our label ----
CLASS_INDICES = {
    "gunshot":     [421, 422, 425, 428],
    "glass_break": [435, 437, 463, 464],
    "car_horn":    [302, 312, 325],
    "siren":       [316, 317, 318, 319, 390, 391],
    "dog_bark":    [69, 70, 72],
    "clapping":    [58, 62],
    "footsteps":   [48],
    "talking":     [0, 1, 2, 3, 6, 9, 11],
    "knocking":    [348, 351, 353, 354],
    "alarm":       [304, 382, 389, 392, 393, 394, 475],
    "explosion":   [420, 430, 460],
}

# Inverse map: YAMNet index -> our label
_INDEX_TO_LABEL = {}
for label, indices in CLASS_INDICES.items():
    for idx in indices:
        _INDEX_TO_LABEL[idx] = label

LABEL_META = {
    "gunshot":     {"desc": "Gunshot",     "color": "red"},
    "glass_break": {"desc": "Glass Break", "color": "orange"},
    "car_horn":    {"desc": "Car Horn",    "color": "yellow"},
    "siren":       {"desc": "Siren",       "color": "purple"},
    "dog_bark":    {"desc": "Dog Bark",    "color": "amber"},
    "clapping":    {"desc": "Clapping",    "color": "cyan"},
    "footsteps":   {"desc": "Footsteps",   "color": "lime"},
    "talking":     {"desc": "Talking",     "color": "green"},
    "knocking":    {"desc": "Knocking",    "color": "teal"},
    "alarm":       {"desc": "Alarm",       "color": "magenta"},
    "explosion":   {"desc": "Explosion",   "color": "red"},
}

YAMNET_INPUT_SIZE = 15600  # 0.975s @ 16kHz
YAMNET_SR = 16000
HOP_SIZE = 3900  # 75% overlap = infer every ~0.25s
SMOOTHING_WINDOW = 3  # median over last 3 predictions (~0.75s)


class YamNetClassifier:
    """YAMNet TFLite audio classifier with temporal smoothing."""

    def __init__(self, model_path, conf_threshold=0.3):
        self.conf_threshold = conf_threshold
        self.available = False

        try:
            from ai_edge_litert.interpreter import Interpreter
            self.interpreter = Interpreter(model_path=model_path)
            self.interpreter.allocate_tensors()
            self.input_details = self.interpreter.get_input_details()
            self.output_details = self.interpreter.get_output_details()
            self.available = True
            print(f"[yamnet] Loaded model from {model_path} ({os.path.getsize(model_path)} bytes)")
        except Exception as e:
            print(f"[yamnet] Failed to load model: {e}")
            self.interpreter = None

        # Audio ring buffer
        self.buffer = deque(maxlen=YAMNET_INPUT_SIZE)
        self.total_samples = 0

        # Smoothing buffer: holds (label, confidence) tuples from recent inferences
        self.history = deque(maxlen=SMOOTHING_WINDOW)

        # Cooldown
        self.last_emit = {}
        self.cooldown = 3.0

        # Lock for thread-safe inference
        self._lock = threading.Lock()

    def feed_chunk(self, chunk):
        """
        Feed a chunk of 16kHz audio samples.
        Returns (label, confidence) when enough audio accumulates and
        inference produces a confident result, otherwise (None, 0.0).
        """
        if not self.available:
            return None, 0.0

        with self._lock:
            self.buffer.extend(chunk)
            self.total_samples += len(chunk)

            if len(self.buffer) < YAMNET_INPUT_SIZE:
                return None, 0.0

            # Run inference on current buffer content
            waveform = np.array(self.buffer, dtype=np.float32)
            # Trim to exact input size and normalize to [-1, 1]
            waveform = waveform[:YAMNET_INPUT_SIZE]
            if np.abs(waveform).max() > 1.0:
                waveform = waveform / 32768.0

            scores = self._infer(waveform)
            if scores is None:
                return None, 0.0

            # Map to our classes
            result = self._map_scores(scores)
            self.history.append(result)

            # Advance buffer by hop_size
            for _ in range(HOP_SIZE):
                if self.buffer:
                    self.buffer.popleft()

        # Apply temporal smoothing
        smoothed = self._smooth()
        if smoothed:
            label, conf = smoothed
            return label, round(conf, 3)

        return None, 0.0

    def _infer(self, waveform):
        """Run TFLite inference. Returns scores array of shape (521,)."""
        try:
            self.interpreter.set_tensor(self.input_details[0]['index'],
                                        waveform.astype(np.float32))
            self.interpreter.invoke()
            return self.interpreter.get_tensor(self.output_details[0]['index'])[0]
        except Exception as e:
            print(f"[yamnet] Inference error: {e}")
            return None

    def _map_scores(self, scores):
        """
        Map YAMNet 521-class scores to our 11 classes.
        Returns dict of {label: aggregated_confidence}.
        """
        # Find top-5 YAMNet predictions
        top_indices = np.argsort(scores)[-5:][::-1]
        result = {}
        for idx in top_indices:
            label = _INDEX_TO_LABEL.get(int(idx))
            if label:
                score = float(scores[int(idx)])
                result[label] = max(result.get(label, 0.0), score)
        return result

    def _smooth(self):
        """Apply temporal smoothing over last N predictions."""
        if len(self.history) < 2:
            # Need at least 2 consistent predictions
            return None

        # Collect all labels seen across the window
        label_confidences = {}
        for h in self.history:
            for label, conf in h.items():
                label_confidences.setdefault(label, []).append(conf)

        # Pick the label with highest median confidence that's persistent
        best_label = None
        best_conf = 0.0
        for label, confs in label_confidences.items():
            if len(confs) < 2:
                continue
            median_conf = float(np.median(confs))
            if median_conf > self.conf_threshold and median_conf > best_conf:
                best_label = label
                best_conf = median_conf

        if best_label:
            return best_label, best_conf
        return None

    @property
    def labels_info(self):
        return LABEL_META
