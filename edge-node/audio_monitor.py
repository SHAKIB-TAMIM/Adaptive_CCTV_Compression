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
import os
import sys
import numpy as np
import time
import threading
import argparse
import socketio

# ── Suppress ALSA/JACK/PyAudio probing noise ──────────────────────────────
# These are harmless device-probing messages that clutter output.
# PyAudio's C extension prints them directly to stderr during init,
# so we redirect stderr to /dev/null during the probe phase.
def _suppress_audio_errors():
    old_fd = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    os.close(devnull)
    return old_fd

def _restore_stderr(old_fd):
    os.dup2(old_fd, 2)
    os.close(old_fd)

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


# ── PTZ trigger configuration ──────────────────────────────────────────────────
# Audio event types that should pre-position the PTZ camera (scan wide-angle)
PTZ_TRIGGER_EVENTS      = {'gunshot', 'explosion', 'alarm', 'siren', 'glass_break'}
PTZ_TRIGGER_CONFIDENCE  = 0.55   # minimum confidence to trigger PTZ
PTZ_COOLDOWN_SECONDS    = 15.0   # don't re-trigger PTZ more than once per 15s

class AudioMonitor:
    """Manages PyAudio stream and emits YAMNet audio detection results via Socket.IO."""

    STARTUP_DELAY = 3.0
    COOLDOWN = 5.0

    def __init__(self, server_url, camera_id="camera_0", device_index=None, debug=False,
                 yamnet_model_path=None):
        self.server_url = server_url.rstrip('/')
        self.camera_id = camera_id
        self.device_index = device_index
        self.running = False
        self.stream = None
        self.audio = None
        self.debug = debug

        self._start_ts = time.time()
        self._last_emit = {}

        # YAMNet classifier
        self.yamnet = None
        if yamnet_model_path and _HAVE_YAMNET and YamNetClassifier is not None:
            self.yamnet = YamNetClassifier(yamnet_model_path)
            if not self.yamnet.available:
                self.yamnet = None
                print("[audio] YAMNet unavailable")
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
        self._start_ts = time.time()
        print(f"[audio] Starting — YAMNet delay: {int(self.STARTUP_DELAY)}s")
        if self.yamnet:
            print(f"[audio] YAMNet active: {len(CLASS_INDICES)} target classes")
        try:
            old_err = _suppress_audio_errors()
            try:
                self.audio = pyaudio.PyAudio()
                self.stream = self.audio.open(
                    format=FORMAT, channels=CHANNELS, rate=RATE,
                    input=True, input_device_index=self.device_index,
                    frames_per_buffer=CHUNK,
                    stream_callback=self._callback,
                )
            finally:
                _restore_stderr(old_err)
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

            # Compute energy metadata for payload
            normalized = chunk_raw.astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(normalized ** 2)))
            rms_db = 20.0 * np.log10(max(rms, 1e-10))
            features = {"rms": rms, "rms_db": rms_db}

            # YAMNet only (Phase 1 removed)
            if self.yamnet and (time.time() - self._start_ts) >= self.STARTUP_DELAY:
                chunk_norm = chunk_raw.astype(np.float32) / 32768.0
                chunk_16k = self._resample_to_16k(chunk_norm)
                if chunk_16k is not None:
                    try:
                        p2_type, p2_conf = self.yamnet.feed_chunk(chunk_16k)
                        if p2_type and p2_conf >= self.yamnet.conf_threshold:
                            t = threading.Thread(target=self._emit_event,
                                                 args=(p2_type, p2_conf, features), daemon=True)
                            t.start()
                    except Exception:
                        pass
        except Exception as e:
            print(f"[audio] Process error: {e}")
        return (in_data, pyaudio.paContinue)

    MIN_EMIT_CONFIDENCE = 0.45

    def _emit_event(self, event_type, confidence, features):
        """Send YAMNet audio event via Socket.IO with HTTP fallback."""
        if confidence < self.MIN_EMIT_CONFIDENCE:
            return
        now = time.time()
        last_ts = self._last_emit.get(event_type, 0)
        if now - last_ts < self.COOLDOWN:
            return
        self._last_emit[event_type] = now

        label_info = YAMNET_LABELS.get(event_type, {})
        label = label_info.get("desc", event_type)
        color = label_info.get("color", "white")

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
                print(f"[audio] [YAMNet] >>> {label} ({confidence:.2f})")

                # ── PTZ pre-positioning trigger ──────────────────────────
                if (event_type in PTZ_TRIGGER_EVENTS
                        and confidence >= PTZ_TRIGGER_CONFIDENCE):
                    self._trigger_ptz(event_type, confidence, now)
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
            print(f"[audio] [YAMNet] >>> {event_type} ({confidence:.2f}) via HTTP")

            # PTZ trigger via HTTP fallback
            if (event_type in PTZ_TRIGGER_EVENTS
                    and confidence >= PTZ_TRIGGER_CONFIDENCE):
                self._trigger_ptz(event_type, confidence, time.time(), use_http=True)
        except Exception as e:
            print(f"[audio] Post error: {e}")

    def _trigger_ptz(self, event_type, confidence, now, use_http=False):
        """
        Emit a PTZ pre-positioning command when a dangerous audio event fires.

        The PTZ command tells the camera_manager to:
          1. Zoom out to wide-angle (to maximise scene coverage)
          2. Re-scan to last known high-risk zone (or centre)

        The spatial_hint uses the last quadrant estimate from audio localisation
        if available, otherwise defaults to (0.5, 0.5) = frame centre.
        """
        last_ptz = getattr(self, '_last_ptz_ts', 0)
        if now - last_ptz < PTZ_COOLDOWN_SECONDS:
            return
        self._last_ptz_ts = now

        spatial_x = getattr(self, '_last_audio_x', 0.5)
        spatial_y = getattr(self, '_last_audio_y', 0.5)

        ptz_payload = {
            "camera_id":    self.camera_id,
            "action":       "audio_scan",      # camera_manager recognises this
            "event_type":   event_type,
            "confidence":   round(confidence, 3),
            "spatial_x":    spatial_x,         # 0.0=left  1.0=right
            "spatial_y":    spatial_y,         # 0.0=top   1.0=bottom
            "zoom_out":     True,              # pre-position = wide first
            "timestamp":    now,
        }

        if not use_http and self.sio_connected and self.sio.connected:
            try:
                self.sio.emit("ptz_audio_trigger", ptz_payload)
                print(f"[audio] PTZ pre-position triggered: {event_type} "
                      f"x={spatial_x:.2f} y={spatial_y:.2f}")
                return
            except Exception:
                pass

        # HTTP fallback
        try:
            import requests
            requests.post(f"{self.server_url}/ptz_trigger", json=ptz_payload, timeout=1)
            print(f"[audio] PTZ pre-position via HTTP: {event_type}")
        except Exception as e:
            print(f"[audio] PTZ trigger error: {e}")



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", type=str, default="http://127.0.0.1:5000")
    parser.add_argument("--camera-id", type=str, default="camera_0")
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--yamnet-model", type=str, default="",
                        help="Path to YAMNet TFLite model (default: auto-detect)")

    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    # Auto-detect YAMNet model at default path
    yamnet_path = args.yamnet_model
    if not yamnet_path:
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
        print("[audio] No YAMNet model found. Use --yamnet-model <path> to enable audio detection.")

    monitor = AudioMonitor(args.server, args.camera_id, args.device,
                           debug=args.debug, yamnet_model_path=yamnet_path)
    try:
        monitor.run()
    except KeyboardInterrupt:
        monitor.stop()
        print("[audio] Exiting")
