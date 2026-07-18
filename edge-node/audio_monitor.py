"""
audio_monitor.py — Real-time audio event detection.

Phase 1 (always active): Simple energy-threshold detector (impulse/sustained_loud).
  - Calibrates noise floor over 3 seconds.
  - Uses dB-scale thresholds (20*log10 RMS).
  - Two high-precision events:
      * impulse:  RMS dB > floor + 15dB, decays within 300ms
      * sustained_loud: RMS dB > floor + 10dB for > 600ms
  - Precision goal: >95%.

Phase 2 (optional): YAMNet 11-class classifier.
  - Requires a YAMNet TFLite model path.
  - Downsampled 22050Hz → 16000Hz audio fed to YAMNet.
  - Produces fine-grained classes: gunshot, glass_break, car_horn, siren,
    dog_bark, clapping, footsteps, talking, knocking, alarm, explosion.
  - YAMNet results take priority; falls back to Phase 1 when unavailable.

Usage:
  python audio_monitor.py --server http://localhost:5000 --camera-id camera_0
  python audio_monitor.py --server http://localhost:5000 --camera-id camera_0 --yamnet-model /tmp/yamnet_model/1.tflite --debug
"""
import numpy as np
import time
import threading
import argparse
import socketio

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
CHUNK = 1024
FRAME_S = CHUNK / RATE  # ~46ms

# YAMNet resampling: 22050 -> 16000
try:
    from yamnet_classifier import YamNetClassifier, LABEL_META as YAMNET_LABELS, CLASS_INDICES
    _HAVE_YAMNET = True
except ImportError:
    YamNetClassifier = None
    YAMNET_LABELS = {}
    CLASS_INDICES = {}
    _HAVE_YAMNET = False


class SimpleAudioDetector:
    """
    Two-class detector: impulse / sustained_loud.

    Uses absolute dB thresholds relative to a calibrated noise floor.
    No energy-ratio gates — eliminates the root cause of false positives.
    """

    # dB offset from calibrated noise floor
    IMPULSE_DB_OFFSET = 15.0
    SUSTAINED_DB_OFFSET = 10.0

    # Duration limits (in frames ~46ms each)
    IMPULSE_MAX_FRAMES = int(0.30 / FRAME_S)   # < 300ms → impulse
    SUSTAINED_MIN_FRAMES = int(0.60 / FRAME_S)  # > 600ms → sustained

    # Silence between events before declaring end
    END_HOLD_FRAMES = int(0.15 / FRAME_S)  # 150ms

    # Max event duration safety valve (prevents infinite segments)
    MAX_EVENT_FRAMES = int(5.0 / FRAME_S)

    # Cooldown between emissions of the same type
    COOLDOWN = 5.0

    def __init__(self, debug=False):
        self.debug = debug

        # Calibration
        self.cal_frames = 0
        self.CAL_TARGET = int(3.0 / FRAME_S)  # ~3 seconds
        self.cal_rms_dbs = []
        self.noise_floor_db = -60.0  # safe initial value

        # Event state
        self.active_frames = 0      # consecutive frames above threshold
        self.silent_frames = 0      # consecutive frames below threshold
        self.in_event = False
        self.seen_impulse = False   # true within current event if already emitted

        # Running calibration update
        self.recent_rms_dbs = []

        # Cooldown tracking
        self.last_emit = {}

        # Labels
        self.LABELS = {
            "impulse":        "Loud Impulse (gunshot/slam/knock)",
            "sustained_loud": "Sustained Loud (alarm/horn/shout)",
        }
        self.COLORS = {
            "impulse":        "red",
            "sustained_loud": "orange",
        }

    # ---- Feature extraction ----

    def extract_features(self, chunk):
        """Compute RMS and dB from one audio chunk.
        Normalizes int16 to [-1, 1] so dB values are physically meaningful.
        """
        normalized = chunk.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(normalized ** 2)))
        rms_db = 20.0 * np.log10(max(rms, 1e-10))
        return {"rms": rms, "rms_db": rms_db, "raw_rms": float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))}

    # ---- Calibration ----

    def _calibrate(self, features):
        """Accumulate calibration data for first 3 seconds."""
        self.cal_frames += 1
        self.cal_rms_dbs.append(features["rms_db"])
        if self.cal_frames >= self.CAL_TARGET:
            self.noise_floor_db = float(np.percentile(self.cal_rms_dbs, 90))
            print(f"[audio] Calibration done: noise floor = {self.noise_floor_db:.1f} dB")

    def is_calibrated(self):
        return self.cal_frames >= self.CAL_TARGET

    # ---- Frame classification ----

    def classify_frame(self, features):
        """
        Process one frame. Returns (event_type, confidence) when an event
        completes, otherwise (None, 0.0).
        """
        if not self.is_calibrated():
            self._calibrate(features)
            return None, 0.0

        # Update running baseline (slowly adapt noise floor down)
        self.recent_rms_dbs.append(features["rms_db"])
        if len(self.recent_rms_dbs) > 200:
            self.recent_rms_dbs.pop(0)
        if len(self.recent_rms_dbs) >= 30:
            p30 = float(np.percentile(self.recent_rms_dbs, 30))
            # Only lower the floor (environment gets quieter), never raise
            if p30 < self.noise_floor_db:
                self.noise_floor_db = 0.99 * self.noise_floor_db + 0.01 * p30

        db = features["rms_db"]
        above_impulse = db > self.noise_floor_db + self.IMPULSE_DB_OFFSET
        above_sustained = db > self.noise_floor_db + self.SUSTAINED_DB_OFFSET

        if above_sustained:
            # Sound active
            self.active_frames += 1
            self.silent_frames = 0
            if not self.in_event:
                if self.debug:
                    print(f"  [event_start] db={db:.1f} floor={self.noise_floor_db:.1f} "
                          f"> thr={self.noise_floor_db + self.SUSTAINED_DB_OFFSET:.1f}")
                self.in_event = True
                self.seen_impulse = False

            # Check for sustained classification
            if (above_sustained and self.active_frames >= self.SUSTAINED_MIN_FRAMES
                    and not self.seen_impulse):
                self.seen_impulse = True  # prevent double-emit
                if self.debug:
                    print(f"  >>> SUSTAINED ({self.active_frames} frames)")
                result = ("sustained_loud",
                          min(1.0, self.active_frames / self.SUSTAINED_MIN_FRAMES))
                return result

            # Safety valve: force-end if event exceeds max duration
            if self.active_frames >= self.MAX_EVENT_FRAMES:
                self.in_event = False
                self.active_frames = 0
                if self.debug:
                    print(f"  [event_force_end] max duration ({self.MAX_EVENT_FRAMES} frames)")
                return None, 0.0

            return None, 0.0

        # Below threshold — silence or quiet
        if self.in_event:
            self.silent_frames += 1
            if self.silent_frames >= self.END_HOLD_FRAMES:
                # Event ended — classify
                result = self._finalize_event(features)
                return result
            return None, 0.0

        # Not in event, still silent
        self.silent_frames = 0
        self.active_frames = 0
        return None, 0.0

    def _finalize_event(self, features):
        """Called when event ends (silence detected for hold-off period)."""
        n = self.active_frames
        self.in_event = False
        self.seen_impulse = False
        self.active_frames = 0
        self.silent_frames = 0

        if n < 2:
            return None, 0.0

        # Short + energetic → impulse
        if n <= self.IMPULSE_MAX_FRAMES:
            conf = min(1.0, n / self.IMPULSE_MAX_FRAMES)
            if self.debug:
                print(f"  >>> IMPULSE ({n} frames, conf={conf:.2f})")
            return "impulse", round(conf, 3)

        # Sustained was already emitted during the event, don't re-emit
        if n > self.SUSTAINED_MIN_FRAMES:
            return None, 0.0

        # Medium-length that didn't qualify for sustained → nothing
        if self.debug:
            print(f"  [event_end] n={n} — too long for impulse, too short for sustained")
        return None, 0.0


class AudioMonitor:
    """Manages PyAudio stream and emits detection results via Socket.IO.

    Detects audio events using Phase 1 (SimpleAudioDetector) always,
    and Phase 2 (YAMNet) when a model path is provided.
    """

    def __init__(self, server_url, camera_id="camera_0", device_index=None, debug=False,
                 yamnet_model_path=None):
        self.server_url = server_url.rstrip('/')
        self.camera_id = camera_id
        self.device_index = device_index
        self.running = False
        self.stream = None
        self.audio = None
        self.debug = debug
        self.detector = SimpleAudioDetector(debug=debug)

        # YAMNet classifier (Phase 2)
        self.yamnet = None
        if yamnet_model_path and _HAVE_YAMNET and YamNetClassifier is not None:
            self.yamnet = YamNetClassifier(yamnet_model_path)
            if not self.yamnet.available:
                self.yamnet = None
                print("[audio] YAMNet unavailable, falling back to Phase 1 only")
            else:
                print("[audio] YAMNet classifier active (11 classes)")
        elif yamnet_model_path:
            print("[audio] YAMNet requested but yamnet_classifier module not found")

        # Resampling state: accumulate 22kHz chunks and downsample to 16kHz
        self._resample_buf = []

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

    def _resample_to_16k(self, chunk_22k):
        """Downsample 22050Hz chunk to 16000Hz using linear interpolation."""
        if not hasattr(self, '_resample_buf'):
            self._resample_buf = []
        self._resample_buf.extend(chunk_22k.tolist())
        # Only return when we have enough samples
        chunk_16k = None
        target_len = int(len(self._resample_buf) * 16000 / 22050)
        if target_len >= 256:  # minimum useful size
            # Simple linear interpolation
            old_len = len(self._resample_buf)
            indices = np.arange(target_len) * old_len / target_len
            lo = indices.astype(np.int32)
            hi = np.minimum(lo + 1, old_len - 1)
            frac = indices - lo
            arr = np.array(self._resample_buf, dtype=np.float32)
            chunk_16k = arr[lo] * (1 - frac) + arr[hi] * frac
            # Keep remaining samples (partial overlap)
            consumed = int(old_len * 16000 / 22050) * 22050 // 16000
            self._resample_buf = self._resample_buf[consumed:]
        return chunk_16k

    def run(self):
        if pyaudio is None:
            print("[audio] pyaudio not installed. Install with: pip install pyaudio")
            return

        self.running = True
        cal_secs = int(self.detector.CAL_TARGET * FRAME_S)
        print(f"[audio] Starting — calibration: {cal_secs}s")
        if self.yamnet:
            print(f"[audio] YAMNet active: {len(CLASS_INDICES)} target classes")
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
            chunk_raw = np.frombuffer(in_data, dtype=np.int16)
            features = self.detector.extract_features(chunk_raw)

            # Phase 1: SimpleAudioDetector (always)
            p1_type, p1_conf = self.detector.classify_frame(features)

            # Phase 2: YAMNet (if available)
            # Normalize int16 -> [-1, 1] before YAMNet, model expects float range
            p2_type, p2_conf = None, 0.0
            if self.yamnet and self.detector.is_calibrated():
                chunk_norm = chunk_raw.astype(np.float32) / 32768.0
                chunk_16k = self._resample_to_16k(chunk_norm)
                if chunk_16k is not None:
                    try:
                        p2_type, p2_conf = self.yamnet.feed_chunk(chunk_16k)
                    except Exception:
                        pass

            # Emit YAMNet result (takes priority when confident)
            if p2_type and p2_conf >= (self.yamnet.conf_threshold if self.yamnet else 0.3):
                t = threading.Thread(target=self._emit_event,
                                     args=(p2_type, p2_conf, features, True), daemon=True)
                t.start()
            elif p1_type:
                t = threading.Thread(target=self._emit_event,
                                     args=(p1_type, p1_conf, features, False), daemon=True)
                t.start()
        except Exception as e:
            print(f"[audio] Process error: {e}")
        return (in_data, pyaudio.paContinue)

    def _emit_event(self, event_type, confidence, features, is_yamnet=False):
        """Send audio event via Socket.IO with HTTP fallback."""
        now = time.time()
        last_ts = self.detector.last_emit.get(event_type, 0)
        if now - last_ts < self.detector.COOLDOWN:
            return
        self.detector.last_emit[event_type] = now

        if is_yamnet:
            label_info = YAMNET_LABELS.get(event_type, {})
            label = label_info.get("desc", event_type)
            color = label_info.get("color", "white")
        else:
            label = self.detector.LABELS.get(event_type, event_type)
            color = self.detector.COLORS.get(event_type, "white")

        payload = {
            "camera_id": self.camera_id,
            "event_type": event_type,
            "event_label": label,
            "color": color,
            "confidence": confidence,
            "energy": round(float(features["rms"]), 6),
            "rms_db": round(float(features["rms_db"]), 1),
            "timestamp": now,
        }

        if self.sio_connected and self.sio.connected:
            try:
                self.sio.emit("audio_event", payload)
                tag = "YAMNet" if is_yamnet else "Phase1"
                print(f"[audio] [{tag}] >>> {label} ({confidence:.2f})")
                return
            except Exception:
                pass

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
            tag = "YAMNet" if is_yamnet else "Phase1"
            print(f"[audio] [{tag}] >>> {event_type} ({confidence:.2f}) via HTTP")
        except Exception as e:
            print(f"[audio] Post error: {e}")

    def stop(self):
        self.running = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", type=str, default="http://127.0.0.1:5000")
    parser.add_argument("--camera-id", type=str, default="camera_0")
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--yamnet-model", type=str, default="",
                        help="Path to YAMNet TFLite model (default: auto-detect)")
    parser.add_argument("--phase1-only", action="store_true",
                        help="Skip YAMNet even if model is available")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    # Auto-detect YAMNet model at default path
    yamnet_path = args.yamnet_model
    if not args.phase1_only and not yamnet_path:
        import os as _os
        default_paths = [
            "/tmp/yamnet_model/1.tflite",
            _os.path.join(_os.path.dirname(__file__), "models", "yamnet.tflite"),
        ]
        for p in default_paths:
            if _os.path.exists(p):
                yamnet_path = p
                break

    if yamnet_path:
        print(f"[audio] YAMNet model: {yamnet_path}")
    else:
        print("[audio] No YAMNet model found — Phase 1 only (impulse + sustained_loud)")

    monitor = AudioMonitor(args.server, args.camera_id, args.device,
                           debug=args.debug, yamnet_model_path=yamnet_path)
    try:
        monitor.run()
    except KeyboardInterrupt:
        monitor.stop()
        print("[audio] Exiting")
