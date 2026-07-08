"""
audio_monitor.py — Real-time multi-class audio event detection.
Captures microphone input, extracts spectral/temporal features,
and classifies sounds using per-frame + temporal-context analysis.
Emits via Socket.IO in real-time.

Classes: gunshot, glass_break, car_horn, siren, dog_bark,
         clapping, footsteps, talking, knocking, alarm, explosion.

Design:
  - First 3 seconds calibrate noise floor (no false positives during this time).
  - Each 46ms frame is classified individually, but a sliding buffer of ~1.5s
    provides temporal context (duration, rhythm, onset density).
  - Silence: rejected aggressively at 4x noise-floor margin.
  - Impulse sounds (gunshot, glass, clap, knock) are decided within ~100ms.
  - Sustained sounds (horn, siren, alarm, talking) require evidence over ~400ms.
  - Rhythmic sounds (footsteps, dog bark) require a repeating pattern over ~1s.

Usage:
  python audio_monitor.py --server http://localhost:5000 --camera-id camera_0
"""
import numpy as np
import time
import json
import threading
import argparse
import socketio
from collections import deque

try:
    import pyaudio
except ImportError:
    pyaudio = None

if pyaudio is not None:
    FORMAT = pyaudio.paInt16
else:
    FORMAT = None
CHANNELS = 1
RATE = 22050
CHUNK = 1024  # ~46ms per frame
FRAME_DURATION_S = CHUNK / RATE
BUFFER_SECONDS = 2
BUFFER_SIZE = int(BUFFER_SECONDS / FRAME_DURATION_S)  # ~44 frames

# Frequency band edges (Hz)
BANDS = {
    "sub_bass":   (20, 60),
    "bass":       (60, 250),
    "low_mid":    (250, 500),
    "mid":        (500, 2000),
    "upper_mid":  (2000, 4000),
    "high":       (4000, 11025),  # nyquist = 11025
}

NYQUIST = RATE / 2


def _band_energy(magnitude, freqs, lo, hi):
    """Mean magnitude² in frequency band [lo, hi] Hz."""
    mask = (freqs >= lo) & (freqs < hi)
    if not np.any(mask):
        return 0.0
    return float(np.sqrt(np.mean(magnitude[mask] ** 2)))


class AudioClassifier:
    """
    Multi-frame audio classifier using spectral + temporal features.

    State machine: SILENT -> <onset> -> IMPULSE_CANDIDATE / SUSTAINED_CANDIDATE
        -> classify after enough evidence -> EMIT -> cooldown
    """

    # Silence threshold relative to running noise floor
    SILENCE_MARGIN = 4.0
    # Minimum frames above threshold to confirm a sustained event
    SUSTAIN_MIN_FRAMES = int(0.4 / FRAME_DURATION_S)  # ~9 frames (400ms)
    # Impulse sounds must finish (energy drop) within this many frames
    IMPULSE_MAX_FRAMES = int(0.15 / FRAME_DURATION_S)  # ~4 frames (150ms)
    # Minimum consecutive rhythmic bursts to classify rhythmic sounds
    RHYTHM_MIN_BURSTS = 3

    def __init__(self):
        # --- Calibration ---
        self.calibration_frames = 0
        self.CALIBRATION_TARGET = int(3.0 / FRAME_DURATION_S)  # 3 seconds
        self.cal_energies = []
        self.silence_floor = 0.01
        self.peak_energy = 1.0
        self.peak_floor_ratio = 1.0

        # --- Temporal buffers ---
        self.feature_buffer = deque(maxlen=BUFFER_SIZE)  # list of feature dicts
        self.energy_above_threshold = 0  # consecutive frames above margin
        self.energy_below_threshold = 0  # consecutive frames below margin
        self.in_event = False
        self.event_start_frame = 0
        self.total_frames = 0

        # --- Onset / flux ---
        self.prev_magnitude = None

        # --- Rhythmic tracking ---
        self.low_energy_envelope = deque(maxlen=BUFFER_SIZE)
        self.burst_frames = []  # frame indices of recent low-freq bursts
        self.last_burst_frame = -100

        # --- Cooldown ---
        self.last_emit_time = {}
        self.cooldown_secs = 3.0

        # Per-class display info
        self.CLASS_PROFILES = {
            "gunshot":     {"desc": "Gunshot",      "icon": "🔫", "color": "red"},
            "glass_break": {"desc": "Glass Break",  "icon": "💥", "color": "orange"},
            "car_horn":    {"desc": "Car Horn",     "icon": "📯", "color": "yellow"},
            "siren":       {"desc": "Siren",        "icon": "🚨", "color": "purple"},
            "dog_bark":    {"desc": "Dog Bark",     "icon": "🐕", "color": "amber"},
            "clapping":    {"desc": "Clapping",     "icon": "👏", "color": "cyan"},
            "footsteps":   {"desc": "Footsteps",    "icon": "👣", "color": "lime"},
            "talking":     {"desc": "Talking",      "icon": "🗣️", "color": "green"},
            "knocking":    {"desc": "Knocking",     "icon": "🚪", "color": "teal"},
            "alarm":       {"desc": "Alarm",        "icon": "🔔", "color": "magenta"},
            "explosion":   {"desc": "Explosion",    "icon": "💣", "color": "red"},
        }

    # ---- Feature extraction ----

    def extract_features(self, chunk):
        """Extract feature vector from one audio chunk (~46ms)."""
        fft = np.fft.rfft(chunk)
        magnitude = np.abs(fft)
        freqs = np.fft.rfftfreq(len(chunk), 1.0 / RATE)

        # RMS energy
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        peak = float(np.max(np.abs(chunk)))

        # Per-band energies (mean magnitude per band)
        band_energies = {}
        for name, (lo, hi) in BANDS.items():
            band_energies[name] = _band_energy(magnitude, freqs, lo, hi)

        # Spectral centroid
        if np.sum(magnitude) > 1e-10:
            centroid = float(np.sum(freqs * magnitude) / np.sum(magnitude))
        else:
            centroid = 0.0
        centroid_norm = centroid / NYQUIST

        # Spectral rolloff (85% and 95%)
        cumsum = np.cumsum(magnitude ** 2)
        total_m2 = cumsum[-1] if len(cumsum) > 0 else 1
        rolloff85_idx = np.searchsorted(cumsum, 0.85 * total_m2)
        rolloff85 = float(freqs[min(rolloff85_idx, len(freqs) - 1)]) if rolloff85_idx < len(freqs) else NYQUIST
        rolloff95_idx = np.searchsorted(cumsum, 0.95 * total_m2)
        rolloff95 = float(freqs[min(rolloff95_idx, len(freqs) - 1)]) if rolloff95_idx < len(freqs) else NYQUIST

        # Zero-crossing rate
        signs = np.signbit(chunk)
        zcr = float(np.sum(np.abs(np.diff(signs)))) / (2.0 * len(chunk))

        # Spectral flatness (tonality): geometric_mean / arithmetic_mean of magnitude spectrum
        mag_nonzero = magnitude[magnitude > 1e-10]
        if len(mag_nonzero) > 0:
            log_mean = float(np.exp(np.mean(np.log(mag_nonzero))))
            arith_mean = float(np.mean(mag_nonzero))
            flatness = log_mean / max(1e-10, arith_mean)
        else:
            flatness = 1.0

        # Spectral flux (onset strength)
        if self.prev_magnitude is not None and len(self.prev_magnitude) == len(magnitude):
            diff = magnitude - self.prev_magnitude
            flux = float(np.sum(np.maximum(0, diff)) / max(1e-10, np.sum(magnitude)))
        else:
            flux = 0.0
        self.prev_magnitude = magnitude

        return {
            "rms": rms,
            "peak": peak,
            **band_energies,
            "centroid_norm": centroid_norm,
            "rolloff85_norm": rolloff85 / NYQUIST,
            "rolloff95_norm": rolloff95 / NYQUIST,
            "zcr": zcr,
            "flatness": flatness,
            "flux": flux,
        }

    # ---- Calibration ---

    def _calibrate(self, features):
        """Accumulate calibration stats for first 3 seconds."""
        self.calibration_frames += 1
        self.cal_energies.append(features["rms"])
        if self.calibration_frames >= self.CALIBRATION_TARGET:
            # Set noise floor as 90th percentile of calibration noise
            self.silence_floor = float(np.percentile(self.cal_energies, 90))
            self.silence_floor = max(self.silence_floor, 0.001)
            self.peak_energy = float(np.max(self.cal_energies))
            self.peak_floor_ratio = self.peak_energy / max(1e-10, self.silence_floor)
            print(f"[audio] Calibration done: floor={self.silence_floor:.4f}, "
                  f"peak={self.peak_energy:.4f}, ratio={self.peak_floor_ratio:.1f}x")

    def _update_baseline(self, features):
        """Update running noise floor (decaying max, not median)."""
        self.feature_buffer.append(features)
        self.total_frames += 1

        if self.calibration_frames < self.CALIBRATION_TARGET:
            return

        # Update noise floor: decaying percentile over recent history
        recent = [f["rms"] for f in list(self.feature_buffer)[-30:]]
        if len(recent) >= 10:
            # Use 30th percentile as noise floor (more robust than median)
            new_floor = float(np.percentile(recent, 30))
            # Slow attack, fast decay
            self.silence_floor = 0.95 * self.silence_floor + 0.05 * max(new_floor, self.silence_floor * 0.5)
            self.silence_floor = max(self.silence_floor, 0.001)

        # Track recent max for gain normalization
        recent_max = max([f["peak"] for f in list(self.feature_buffer)[-20:]])
        self.peak_energy = max(self.peak_energy * 0.999, recent_max)

    # ---- Silence / onset detection ---

    def _is_silence(self, features):
        """True if frame is below silence margin."""
        if self.calibration_frames < self.CALIBRATION_TARGET:
            return True  # no output during calibration
        return features["rms"] < self.silence_floor * self.SILENCE_MARGIN

    def _energy_ratio(self, features):
        """Current RMS as multiple of noise floor."""
        return features["rms"] / max(1e-10, self.silence_floor)

    # ---- Analysis helpers ---

    def _band_ratios(self, features):
        """Energy in each band as multiple of noise floor."""
        floor = max(1e-10, self.silence_floor)
        return {name: features[name] / floor for name in BANDS}

    def _buffer_duration(self):
        """Seconds of audio in the feature buffer."""
        return len(self.feature_buffer) * FRAME_DURATION_S

    def _recent_burst_count(self):
        """Number of low-frequency onsets in the last ~1.5 seconds."""
        cutoff = self.total_frames - int(1.5 / FRAME_DURATION_S)
        return sum(1 for bf in self.burst_frames if bf > cutoff)

    # ---- Classification ---

    def classify_frame(self, features):
        """Classify current frame. Returns (class_name, confidence) or (None, 0)."""
        if self.calibration_frames < self.CALIBRATION_TARGET:
            self._calibrate(features)
            self._update_baseline(features)
            return None, 0.0

        self._update_baseline(features)

        if self._is_silence(features):
            self.energy_above_threshold = 0
            self.energy_below_threshold += 1
            # If we were in an event and now silence, trigger classification
            if self.in_event:
                return self._finalize_event(features)
            return None, 0.0

        self.energy_above_threshold += 1
        self.energy_below_threshold = 0

        er = self._energy_ratio(features)
        br = self._band_ratios(features)
        cn = features["centroid_norm"]
        zcr = features["zcr"]
        flux = features["flux"]
        flatness = features["flatness"]
        roll85 = features["rolloff85_norm"]
        roll95 = features["rolloff95_norm"]

        # ---- Detect onset (new sound starting) ----
        is_onset = flux > 0.15 and self.energy_above_threshold <= 2

        # ---- Detect low-frequency burst (for footsteps, knocking, etc.) ----
        is_low_burst = br["sub_bass"] > 3.0 or br["bass"] > 4.0
        if is_low_burst and flux > 0.08:
            self.burst_frames.append(self.total_frames)
            self.last_burst_frame = self.total_frames

        # ---- Determine sound category ----
        # Category 1: Impulse — strong onset, short duration (<150ms)
        is_impulse = is_onset and (
            (flux > 0.30 and er > 10) or  # very strong onset
            (flux > 0.20 and er > 15 and cn > 0.10) or  # strong + bright
            (flux > 0.15 and br["low_mid"] > 8 and br["mid"] > 5)  # broad
        )

        # Category 2: Sustained — continues for many frames
        is_sustained = self.energy_above_threshold >= self.SUSTAIN_MIN_FRAMES

        # Category 3: Rhythmic low-frequency — repeated low bursts
        burst_count = self._recent_burst_count()
        frames_since_last = self.total_frames - self.last_burst_frame
        is_rhythmic_low = (burst_count >= self.RHYTHM_MIN_BURSTS and
                           frames_since_last < int(1.5 / FRAME_DURATION_S))

        self.in_event = True
        if self.event_start_frame == 0:
            self.event_start_frame = self.total_frames

        # ---- Impulse classification (decide immediately) ----
        if is_impulse and self.energy_above_threshold <= self.IMPULSE_MAX_FRAMES:
            result = self._classify_impulse(features, br, cn, zcr, flux, er, flatness)
            if result:
                return result

        # ---- Rhythmic classification (needs pattern + evidence) ----
        if is_rhythmic_low:
            result = self._classify_rhythmic(features, br, cn, zcr, flux, er)
            if result:
                return result

        # ---- Sustained classification (needs several continuous frames) ----
        if is_sustained:
            result = self._classify_sustained(features, br, cn, zcr, flux, er, flatness, is_onset)
            if result:
                return result

        return None, 0.0

    # ---- Sub-classifiers ----

    def _classify_impulse(self, features, br, cn, zcr, flux, er, flatness):
        """Classify impulse sounds (decided within ~150ms of onset)."""
        scores = {}

        # Gunshot: extreme onset, very broad spectrum, high energy, moderate centroid
        gunshot_ok = (
            flux > 0.25 and er > 15 and
            br["bass"] > 5 and br["low_mid"] > 5 and br["mid"] > 5 and
            cn > 0.10 and cn < 0.35 and
            zcr > 0.03 and zcr < 0.20
        )
        if gunshot_ok:
            score = min(1.0, (er / 30.0) * (flux / 0.3))
            if score > 0.5:
                scores["gunshot"] = score

        # Glass break: strong onset, very high centroid/high-freq dominant
        glass_ok = (
            flux > 0.18 and er > 8 and
            br["upper_mid"] > 5 and br["high"] > 4 and
            cn > 0.20 and
            zcr > 0.15
        )
        if glass_ok:
            score = min(1.0, (br["high"] / 6.0) * (cn / 0.25) * (zcr / 0.25))
            if score > 0.45:
                scores["glass_break"] = score

        # Clapping: moderate onset, mid-high centroid, broad-mid energy
        clap_ok = (
            flux > 0.12 and er > 5 and er < 30 and
            br["low_mid"] > 3 and br["mid"] > 4 and br["upper_mid"] > 2 and
            cn > 0.10 and cn < 0.30 and
            zcr > 0.10 and zcr < 0.35 and
            flatness > 0.3  # noise-like (not tonal)
        )
        if clap_ok:
            spread = (br["low_mid"] + br["mid"] + br["upper_mid"]) / max(1e-10, er)
            score = min(1.0, (spread / 2.0) * (cn / 0.20) * (zcr / 0.25))
            if score > 0.4:
                scores["clapping"] = score

        # Knocking: low-mid frequency thump, moderate onset, low centroid
        knock_ok = (
            flux > 0.08 and er > 4 and er < 20 and
            (br["sub_bass"] > 3 or br["bass"] > 4) and
            br["low_mid"] > 3 and
            cn > 0.02 and cn < 0.12 and
            zcr < 0.12 and
            br["upper_mid"] < br["bass"] * 1.5  # low-freq dominant
        )
        if knock_ok:
            low_dominance = max(br["sub_bass"], br["bass"]) / max(1e-10, br["mid"] + br["upper_mid"])
            score = min(1.0, (low_dominance / 2.0) * (1.0 - cn / 0.10))
            if score > 0.4:
                scores["knocking"] = score

        if not scores:
            return None

        best = max(scores, key=scores.get)
        return best, round(scores[best], 3)

    def _classify_sustained(self, features, br, cn, zcr, flux, er, flatness, is_onset):
        """Classify sustained sounds (continuous for >400ms)."""
        avg_er = np.mean([f["rms"] for f in list(self.feature_buffer)[-self.SUSTAIN_MIN_FRAMES:]])
        avg_er = avg_er / max(1e-10, self.silence_floor)

        # Energy variance over sustained period
        recent_energies = [f["rms"] for f in list(self.feature_buffer)[-self.SUSTAIN_MIN_FRAMES:]]
        energy_cv = float(np.std(recent_energies) / max(1e-10, np.mean(recent_energies))) if len(recent_energies) > 1 else 1.0

        scores = {}

        # Car horn: sustained narrowband tonal, mid freq, low ZCR
        horn_ok = (
            self.energy_above_threshold >= self.SUSTAIN_MIN_FRAMES and
            avg_er > 3 and avg_er < 30 and
            cn > 0.02 and cn < 0.10 and
            zcr < 0.08 and
            flatness < 0.35 and  # tonal
            br["low_mid"] > 3 and br["mid"] > 3 and
            energy_cv < 0.5  # steady energy
        )
        if horn_ok:
            score = min(1.0, (avg_er / 6.0) * (1.0 - flatness) * (1.0 - zcr / 0.08) * (1.0 - abs(cn - 0.05) / 0.05))
            if score > 0.45:
                scores["car_horn"] = score

        # Siren: sustained with frequency modulation (varying centroid)
        if self.energy_above_threshold >= self.SUSTAIN_MIN_FRAMES:
            recent_cn = [f["centroid_norm"] for f in list(self.feature_buffer)[-self.SUSTAIN_MIN_FRAMES:]]
            cn_range = float(np.max(recent_cn) - np.min(recent_cn)) if len(recent_cn) > 1 else 0.0
            siren_ok = (
                avg_er > 3 and avg_er < 25 and
                cn > 0.05 and cn < 0.25 and
                cn_range > 0.04 and  # centroid varies (modulation)
                zcr < 0.15 and
                br["mid"] > 3 and
                flatness < 0.45
            )
            if siren_ok:
                score = min(1.0, (cn_range / 0.08) * (avg_er / 5.0) * (1.0 - zcr / 0.15))
                if score > 0.4:
                    scores["siren"] = score

        # Alarm: high-frequency sustained, high centroid, potentially pulsed
        alarm_ok = (
            self.energy_above_threshold >= self.SUSTAIN_MIN_FRAMES and
            avg_er > 4 and avg_er < 25 and
            cn > 0.15 and
            zcr > 0.10 and
            br["upper_mid"] > 4 and br["high"] > 3 and
            flatness < 0.5
        )
        if alarm_ok:
            score = min(1.0, (br["high"] / 5.0) * (cn / 0.20) * (zcr / 0.20))
            if score > 0.4:
                scores["alarm"] = score

        # Talking: mid-band dominant, moderate centroid, varying energy, noise-like
        talking_ok = (
            self.energy_above_threshold >= self.SUSTAIN_MIN_FRAMES and
            avg_er > 2.5 and avg_er < 20 and
            cn > 0.04 and cn < 0.20 and
            zcr > 0.05 and zcr < 0.35 and
            br["mid"] > 3 and
            br["mid"] > br["bass"] * 1.2 and  # mid dominant over bass
            flatness > 0.35 and  # noise-like
            energy_cv > 0.2  # varying (not steady tone)
        )
        if talking_ok:
            mid_dom = br["mid"] / max(1e-10, br["bass"] + br["low_mid"])
            score = min(1.0, (mid_dom / 1.5) * (1.0 - abs(cn - 0.08) / 0.12) * (zcr / 0.25))
            if score > 0.35:
                scores["talking"] = score

        # Explosion: very low rumble, sustained, very low centroid
        explosion_ok = (
            self.energy_above_threshold >= int(0.3 / FRAME_DURATION_S) and
            avg_er > 8 and
            cn < 0.06 and
            zcr < 0.06 and
            (br["sub_bass"] > 5 or br["bass"] > 8) and
            br["upper_mid"] < br["bass"] * 0.5 and  # low freq heavily dominant
            flatness < 0.4
        )
        if explosion_ok:
            score = min(1.0, (br["bass"] / 10.0) * (1.0 - cn / 0.06) * (1.0 - zcr / 0.06))
            if score > 0.45:
                scores["explosion"] = score

        if not scores:
            return None

        best = max(scores, key=scores.get)
        return best, round(scores[best], 3)

    def _classify_rhythmic(self, features, br, cn, zcr, flux, er):
        """Classify rhythmic sounds (repeating low-frequency pattern)."""
        burst_count = self._recent_burst_count()
        scores = {}

        # Footsteps: Very low freq bursts, regular rhythm, low centroid
        frames_since_last = self.total_frames - self.last_burst_frame
        # Rhythm period: estimate gap between recent bursts
        burst_gaps = []
        sorted_bursts = sorted(self.burst_frames)
        for i in range(1, len(sorted_bursts)):
            gap = sorted_bursts[i] - sorted_bursts[i - 1]
            if gap < int(3.0 / FRAME_DURATION_S):  # gaps up to 3s
                burst_gaps.append(gap)

        avg_gap = np.mean(burst_gaps) if burst_gaps else 999
        gap_cv = float(np.std(burst_gaps) / max(1e-6, avg_gap)) if burst_gaps else 999

        footsteps_ok = (
            burst_count >= 2 and
            cn < 0.06 and
            zcr < 0.08 and
            br["sub_bass"] > 2.5 and
            br["bass"] > 3 and
            frames_since_last < int(2.0 / FRAME_DURATION_S) and
            # Relatively regular rhythm (gap CV < 0.5)
            (len(burst_gaps) < 2 or gap_cv < 0.5) and
            # Footsteps are short bursts, so energy drops between them
            br["upper_mid"] < br["bass"] * 0.5
        )
        if footsteps_ok:
            regularity = 1.0 - min(1.0, gap_cv) if len(burst_gaps) >= 2 else 0.5
            score = min(1.0, (burst_count / 4.0) * (1.0 - cn / 0.06) * regularity)
            if score > 0.4:
                scores["footsteps"] = score

        # Dog bark: mid-low frequency bursts, higher centroid and ZCR than footsteps
        dog_ok = (
            burst_count >= 2 and
            cn > 0.04 and cn < 0.15 and
            zcr > 0.05 and zcr < 0.20 and
            br["low_mid"] > 3 and br["mid"] > 3 and
            frames_since_last < int(2.0 / FRAME_DURATION_S) and
            (len(burst_gaps) < 2 or gap_cv < 0.5)  # fairly regular
        )
        if dog_ok:
            regularity = 1.0 - min(1.0, gap_cv) if len(burst_gaps) >= 2 else 0.5
            score = min(1.0, (burst_count / 4.0) * (1.0 - abs(cn - 0.08) / 0.08) * regularity)
            if score > 0.35:
                scores["dog_bark"] = score

        if not scores:
            return None

        best = max(scores, key=scores.get)
        return best, round(scores[best], 3)

    # ---- Event finalization ----

    def _finalize_event(self, features):
        """Called when an event ends (energy drops below threshold)."""
        self.in_event = False
        self.event_start_frame = 0
        self.energy_above_threshold = 0
        # If we were tracking an impulse but it didn't match any class, just drop it
        return None, 0.0

    # ---- Convenience ----

    def is_calibrated(self):
        return self.calibration_frames >= self.CALIBRATION_TARGET


class AudioMonitor:
    """Manages PyAudio stream and emits classification results."""

    def __init__(self, server_url, camera_id="camera_0", device_index=None):
        self.server_url = server_url.rstrip('/')
        self.camera_id = camera_id
        self.device_index = device_index
        self.running = False
        self.stream = None
        self.audio = None
        self.classifier = AudioClassifier()

        # Socket.IO client
        self.sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=1)
        self.sio_connected = False
        self._setup_socketio()

    def _setup_socketio(self):
        @self.sio.event
        def connect():
            self.sio_connected = True
            print(f"[audio] Socket.IO connected to {self.server_url}")

        @self.sio.event
        def disconnect():
            self.sio_connected = False
            print("[audio] Socket.IO disconnected")

        @self.sio.event
        def connect_error(data):
            print(f"[audio] Socket.IO connection error: {data}")

        def _connect():
            try:
                self.sio.connect(self.server_url, transports=["websocket", "polling"], wait_timeout=3)
            except Exception as e:
                print(f"[audio] Socket.IO connect failed (HTTP fallback): {e}")
                self.sio_connected = False

        t = threading.Thread(target=_connect, daemon=True)
        t.start()

    def _emit_event(self, event_type, confidence, features):
        """Send audio event via Socket.IO with HTTP fallback."""
        now = time.time()
        last_ts = self.classifier.last_emit_time.get(event_type, 0)
        if now - last_ts < self.classifier.cooldown_secs:
            return
        self.classifier.last_emit_time[event_type] = now

        profile = self.classifier.CLASS_PROFILES.get(event_type, {})
        payload = {
            "camera_id": self.camera_id,
            "event_type": event_type,
            "event_label": f"{profile.get('icon', '')} {profile.get('desc', event_type)}",
            "color": profile.get("color", "white"),
            "confidence": confidence,
            "energy": round(float(features["rms"]), 4),
            "centroid": round(float(features["centroid_norm"]), 3),
            "timestamp": now,
        }

        # Try Socket.IO first
        if self.sio_connected and self.sio.connected:
            try:
                self.sio.emit("audio_event", payload)
                print(f"[audio] {payload['event_label']} ({confidence:.2f}) — Socket.IO")
                return
            except Exception:
                pass

        # HTTP fallback
        try:
            import requests
            http_payload = {
                "camera_id": self.camera_id,
                "event_id": f"audio_{event_type}_{int(time.time() * 1000)}",
                "risk": round(0.3 + confidence * 0.7, 4),
                "state": "alert",
                "frame_id": -1,
                "hour": time.localtime().tm_hour,
                "num_rois": 0,
                "rois": [],
                "anomaly_type": event_type,
                "anomaly_confidence": round(confidence, 3),
                "source": "audio",
            }
            requests.post(f"{self.server_url}/event", json=http_payload, timeout=1)
            print(f"[audio] {event_type} ({confidence:.2f}) — HTTP")
        except Exception as e:
            print(f"[audio] Post error: {e}")

    def run(self):
        if pyaudio is None:
            print("[audio] pyaudio not installed. Install with: pip install pyaudio")
            return

        self.running = True
        print(f"[audio] Starting — calibration period: {int(self.classifier.CALIBRATION_TARGET * FRAME_DURATION_S)}s")
        try:
            self.audio = pyaudio.PyAudio()
            self.stream = self.audio.open(
                format=FORMAT, channels=CHANNELS, rate=RATE,
                input=True, input_device_index=self.device_index,
                frames_per_buffer=CHUNK,
                stream_callback=self._callback,
            )
            self.stream.start_stream()
            while self.running:
                time.sleep(0.1)
        except Exception as e:
            print(f"[audio] Error: {e}")
        finally:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
            if self.audio:
                self.audio.terminate()
            if self.sio.connected:
                try:
                    self.sio.disconnect()
                except Exception:
                    pass
            print("[audio] Stopped")

    def _callback(self, in_data, frame_count, time_info, status):
        if not self.running:
            return (None, pyaudio.paComplete)

        try:
            chunk = np.frombuffer(in_data, dtype=np.int16).astype(np.float32)
            features = self.classifier.extract_features(chunk)
            event_type, confidence = self.classifier.classify_frame(features)

            if event_type:
                t = threading.Thread(
                    target=self._emit_event,
                    args=(event_type, confidence, features),
                    daemon=True,
                )
                t.start()
        except Exception as e:
            print(f"[audio] Process error: {e}")

        return (in_data, pyaudio.paContinue)

    def stop(self):
        self.running = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", type=str, default="http://127.0.0.1:5000")
    parser.add_argument("--camera-id", type=str, default="camera_0")
    parser.add_argument("--device", type=int, default=None, help="Audio input device index")
    args = parser.parse_args()

    monitor = AudioMonitor(args.server, args.camera_id, args.device)
    try:
        monitor.run()
    except KeyboardInterrupt:
        monitor.stop()
        print("[audio] Exiting")
