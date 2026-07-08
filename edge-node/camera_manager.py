#!/usr/bin/env python3
"""
camera_manager.py — Multi-camera capture manager.
Manages N concurrent camera capture threads, each with its own:
  - Video capture (USB or RTSP)
  - YOLOv8 object detector (shared model)
  - Risk state machine
  - FFmpeg H.265 encoder (unique UDP port)
  - Rolling event buffer

Single Socket.IO connection to server, frames tagged with camera_id.
Usage:
  python camera_manager.py --config ../configs/cameras.yaml
"""
import cv2
import time
import base64
import socketio
from io import BytesIO
from PIL import Image
import numpy as np
import requests
import argparse
import sys
import os
import subprocess
import yaml
import datetime
import collections
import json
import threading
from detector import Detector
from reid_tracker import ReidTracker
from ptz_controller import PtzController

DEFAULT_SERVER = "http://127.0.0.1:5000"
NAMESPACE = "/stream"

sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=1)

control = {
    "BG_SCALE": 0.5,
    "BG_QUALITY": 20,
    "ROI_QUALITY": 90,
    "DETECT_EVERY_N": 3,
    "PRIVACY_BLUR": False,
    "ETHICAL_MODE": False,
    "MASK_FACES": False,
    "CODEC": "libx265",
    "BITRATE": 2000,
}

# ── Risk / Event constants ──
STATE_NORMAL = "normal"
STATE_ALERT = "alert"
STATE_CRITICAL = "critical"

RISK_NORMAL_THRESHOLD = 0.30
RISK_ALERT_THRESHOLD = 0.65
RISK_CRITICAL_EXIT_THRESHOLD = 0.50

STATE_COMPRESSION = {
    STATE_NORMAL:   {"BG_SCALE": None,  "BG_QUALITY": None, "ROI_QUALITY": None},
    STATE_ALERT:    {"BG_SCALE": 0.75,  "BG_QUALITY": 45,   "ROI_QUALITY": 95},
    STATE_CRITICAL: {"BG_SCALE": 1.0,   "BG_QUALITY": 88,   "ROI_QUALITY": 100},
}

PRE_EVENT_SECONDS = 15
POST_EVENT_SECONDS = 10

GOP_CONFIG = {
    STATE_NORMAL:   {"gop_size": 120, "keyint_min": 60},
    STATE_ALERT:    {"gop_size": 30,  "keyint_min": 15},
    STATE_CRITICAL: {"gop_size": 10,  "keyint_min": 5},
}

RES_CONFIG = {
    STATE_NORMAL:   (640, 480),
    STATE_ALERT:    (854, 480),
    STATE_CRITICAL: (1920, 1080),
}

EVENT_DIR = os.path.join(os.path.dirname(__file__), "..", "server", "events")


class CodecEncoder:
    """Per-camera FFmpeg H.265 encoder."""
    def __init__(self, camera_id, udp_port):
        self.camera_id = camera_id
        self.udp_port = udp_port
        self.process = None
        self.current_w = 0
        self.current_h = 0
        self.current_gop = 120
        self.current_fps = 15
        self.current_codec = "libx265"
        self.current_bitrate = 2000
        self.lock = threading.Lock()

    def start(self, w, h, fps, codec, bitrate, gop_size=None):
        self.stop()
        with self.lock:
            self.current_w = w
            self.current_h = h
            self.current_fps = fps
            self.current_codec = codec
            self.current_bitrate = bitrate
            if gop_size is None:
                gop_size = fps * 2
            self.current_gop = gop_size

            if codec in ('vvc_test', 'neural_test'):
                return

            preset = 'fast' if codec in ['libx265', 'libx264', 'libsvtav1'] else 'p1'
            keyint_min = max(1, gop_size // 2)
            cmd = [
                'ffmpeg', '-y',
                '-f', 'rawvideo', '-vcodec', 'rawvideo',
                '-s', f"{w}x{h}", '-pix_fmt', 'bgr24',
                '-r', str(fps), '-i', '-',
                '-c:v', codec,
                '-preset', preset,
                '-b:v', f"{bitrate}k",
                '-maxrate', f"{int(bitrate*1.5)}k",
                '-bufsize', f"{bitrate*2}k",
                '-g', str(gop_size),
                '-keyint_min', str(keyint_min),
                '-f', 'mpegts',
                f'udp://127.0.0.1:{udp_port}'
            ]
            print(f"[{camera_id}] FFmpeg start: port={udp_port}")
            self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def write(self, frame_bytes):
        if self.process and self.process.poll() is None:
            try:
                with self.lock:
                    self.process.stdin.write(frame_bytes)
            except Exception:
                pass

    def set_gop(self, state):
        config = GOP_CONFIG.get(state)
        if not config or config["gop_size"] == self.current_gop:
            return
        print(f"[{self.camera_id}] GOP {self.current_gop} -> {config['gop_size']} ({state})")
        self.start(self.current_w, self.current_h, self.current_fps,
                   self.current_codec, self.current_bitrate, gop_size=config["gop_size"])

    def set_resolution(self, state):
        config = RES_CONFIG.get(state)
        if not config:
            return
        w, h = config
        if w == self.current_w and h == self.current_h:
            return
        print(f"[{self.camera_id}] Res {self.current_w}x{self.current_h} -> {w}x{h} ({state})")
        self.start(w, h, self.current_fps, self.current_codec, self.current_bitrate,
                   gop_size=self.current_gop)

    def stop(self):
        with self.lock:
            if self.process is not None:
                try:
                    self.process.stdin.close()
                    self.process.wait(timeout=2)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
                self.process = None


class CameraThread(threading.Thread):
    """Manages a single camera: capture, detect, compress, stream."""
    def __init__(self, cam_cfg, detector, server_url, reid_tracker=None):
        super().__init__(daemon=True)
        self.cam_cfg = cam_cfg
        self.camera_id = cam_cfg["id"]
        self.server_url = server_url
        self.detector = detector
        self.reid_tracker = reid_tracker
        self.encoder = CodecEncoder(self.camera_id, cam_cfg.get("udp_port", 1234))
        self.target_fps = cam_cfg.get("fps", 15)
        self.running = True

        # PTZ controller (optional)
        ptz_config = cam_cfg.get("ptz")
        self.ptz = PtzController(ptz_config) if ptz_config and ptz_config.get("protocol") != "none" else None

        self.surveillance_state = STATE_NORMAL
        self.state_hysteresis_count = 0
        self.HYSTERESIS_FRAMES = 8
        self.POST_EVENT_FRAMES = POST_EVENT_SECONDS * self.target_fps
        self.pre_event_buffer = collections.deque(maxlen=PRE_EVENT_SECONDS * self.target_fps)
        self.post_event_frames_remaining = 0
        self.event_id = None
        self.prev_frame_gray = None
        self.temporal_rois = []
        self.roi_ttl = 30

        self.frame_id = 0
        self.last_send = time.time()

    def run(self):
        source = self.cam_cfg["source"]
        try:
            src_int = int(source)
        except ValueError:
            src_int = None
        src_actual = src_int if src_int is not None else source

        cap = cv2.VideoCapture(src_actual)
        if not cap.isOpened():
            print(f"[{self.camera_id}] Cannot open source: {source}")
            return

        ret, frame = cap.read()
        if not ret:
            print(f"[{self.camera_id}] Failed to read first frame")
            cap.release()
            return

        h, w = frame.shape[:2]
        self.encoder.start(w, h, self.target_fps, control['CODEC'], control['BITRATE'])
        print(f"[{self.camera_id}] Started: {source} -> UDP port {udp_port}")

        try:
            while self.running:
                ret, frame = cap.read()
                if not ret or frame is None:
                    time.sleep(0.05)
                    continue

                self.frame_id += 1
                frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

                # Scene change
                scene_change_score = 0.0
                if self.prev_frame_gray is not None and self.prev_frame_gray.shape == frame_gray.shape:
                    diff = cv2.absdiff(frame_gray, self.prev_frame_gray)
                    scene_change_score = float(np.mean(diff)) / 128.0
                self.prev_frame_gray = frame_gray

                # ── Low-light enhancement (CLAHE) ──
                frame_mean = float(cv2.mean(frame_gray)[0])
                if frame_mean < 80:
                    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
                    l, a, b = cv2.split(lab)
                    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
                    l = clahe.apply(l)
                    enhanced = cv2.merge([l, a, b])
                    detect_frame = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
                else:
                    detect_frame = frame

                # Detection
                detect_n = control.get('DETECT_EVERY_N', 3)
                if self.frame_id % max(1, int(detect_n)) == 0:
                    new_rois = self.detector.detect(detect_frame, conf=0.3)
                    self.temporal_rois = merge_rois(self.temporal_rois, new_rois, self.roi_ttl)
                else:
                    self.temporal_rois = [tr for tr in self.temporal_rois if tr["ttl"] > 0]
                    for tr in self.temporal_rois:
                        tr["ttl"] -= 1

                # ── Motion fallback (night/low-light compensation) ──
                if len(self.temporal_rois) == 0 and scene_change_score > 0.12:
                    _, motion_mask = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
                    motion_mask = cv2.erode(motion_mask, None, iterations=1)
                    motion_mask = cv2.dilate(motion_mask, None, iterations=2)
                    contours, _ = cv2.findContours(motion_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    motion_rois = []
                    for cnt in contours:
                        area = cv2.contourArea(cnt)
                        if area < 500:
                            continue
                        x, y, cw, ch = cv2.boundingRect(cnt)
                        motion_rois.append({
                            "bbox": [x, y, x + cw, y + ch],
                            "priority": "low",
                            "class": -1,
                        })
                    if motion_rois:
                        self.temporal_rois = merge_rois(self.temporal_rois, motion_rois, self.roi_ttl)

                rois = self.temporal_rois

                # ── Re-ID matching for high-priority ROIs ──
                if self.reid_tracker and len(rois) > 0:
                    now = time.time()
                    for roi in rois:
                        if roi.get("priority") == "high" or roi.get("class") == 0:
                            x1, y1, x2, y2 = roi["bbox"]
                            person_crop = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
                            if person_crop.size > 0:
                                track_id, conf, is_new = self.reid_tracker.match(
                                    self.camera_id, roi["bbox"], person_crop, now
                                )
                                roi["track_id"] = track_id
                                roi["reid_conf"] = round(conf, 3)
                    self.reid_tracker.prune_expired(now)

                # ── PTZ auto-tracking (follow highest-priority person) ──
                if self.ptz:
                    high_rois = [r for r in rois if r.get("priority") == "high"]
                    if high_rois:
                        target = high_rois[0]
                        pan, tilt = self.ptz.center_on_bbox(target["bbox"], w, h)
                        if abs(pan) > 0.05 or abs(tilt) > 0.05:
                            self.ptz.relative_move(pan, tilt, speed=0.3)

                frame_area = max(h * w, 1)
                roi_pixels = sum(
                    max(0, r["bbox"][2] - r["bbox"][0]) * max(0, r["bbox"][3] - r["bbox"][1])
                    for r in rois
                )
                motion_area_frac = min(roi_pixels / frame_area, 1.0)
                hour_now = datetime.datetime.now().hour
                risk = risk_score(rois, motion_area_frac, hour_now, scene_change_score)

                # State machine
                prev_state = self.surveillance_state
                if risk >= RISK_ALERT_THRESHOLD:
                    self.surveillance_state = STATE_CRITICAL
                    self.state_hysteresis_count = self.HYSTERESIS_FRAMES
                elif risk >= RISK_NORMAL_THRESHOLD:
                    if self.surveillance_state == STATE_CRITICAL:
                        if risk < RISK_CRITICAL_EXIT_THRESHOLD:
                            self.surveillance_state = STATE_ALERT
                            self.state_hysteresis_count = self.HYSTERESIS_FRAMES
                    else:
                        self.surveillance_state = STATE_ALERT
                else:
                    if self.surveillance_state == STATE_CRITICAL:
                        self.surveillance_state = STATE_ALERT
                        self.state_hysteresis_count = self.HYSTERESIS_FRAMES
                    elif self.state_hysteresis_count > 0:
                        self.state_hysteresis_count -= 1
                    else:
                        self.surveillance_state = STATE_NORMAL

                if self.surveillance_state != prev_state:
                    self.encoder.set_gop(self.surveillance_state)
                    self.encoder.set_resolution(self.surveillance_state)

                # Critical event trigger
                if prev_state != STATE_CRITICAL and self.surveillance_state == STATE_CRITICAL:
                    self.event_id = f"event_{int(time.time() * 1000)}"
                    print(f"[{self.camera_id}] CRITICAL: {self.event_id} risk={risk:.3f}")
                    ev_dir = os.path.join(EVENT_DIR, self.event_id)
                    os.makedirs(ev_dir, exist_ok=True)
                    for idx, buf_entry in enumerate(self.pre_event_buffer):
                        ep = os.path.join(ev_dir, f"pre_{idx:05d}.jpg")
                        cv2.imwrite(ep, buf_entry["frame"])
                        with open(ep.replace(".jpg", "_meta.json"), "w") as mf:
                            json.dump({"camera_id": self.camera_id,
                                       "frame_id": buf_entry["frame_id"],
                                       "rois": buf_entry["rois"],
                                       "risk": buf_entry["risk"]}, mf)
                    requests.post(f"{self.server_url.rstrip('/')}/event", json={
                        "camera_id": self.camera_id,
                        "event_id": self.event_id,
                        "risk": round(risk, 4),
                        "state": STATE_CRITICAL,
                        "frame_id": self.frame_id,
                        "hour": hour_now,
                        "num_rois": len(rois),
                        "rois": [{"bbox": r["bbox"], "priority": r.get("priority")} for r in rois]
                    }, timeout=2)
                    self.post_event_frames_remaining = self.POST_EVENT_FRAMES

                if self.post_event_frames_remaining > 0 and self.event_id:
                    ev_dir = os.path.join(EVENT_DIR, self.event_id)
                    ep = os.path.join(ev_dir, f"post_{self.post_event_frames_remaining:05d}.jpg")
                    cv2.imwrite(ep, frame)
                    self.post_event_frames_remaining -= 1

                # Compress and encode
                profile = STATE_COMPRESSION[self.surveillance_state]
                eff_bg_scale = profile["BG_SCALE"] if profile["BG_SCALE"] is not None else control['BG_SCALE']
                recon_frame = reconstruct_background_with_rois(
                    frame, rois, eff_bg_scale,
                    control['PRIVACY_BLUR'], control['ETHICAL_MODE'], control['MASK_FACES']
                )

                out_w = self.encoder.current_w or w
                out_h = self.encoder.current_h or h
                if out_w != w or out_h != h:
                    out_frame = cv2.resize(recon_frame, (out_w, out_h), interpolation=cv2.INTER_AREA)
                else:
                    out_frame = recon_frame
                self.encoder.write(out_frame.tobytes())

                self.pre_event_buffer.append({
                    "frame": frame.copy(),
                    "frame_id": self.frame_id,
                    "rois": [{"bbox": r["bbox"], "priority": r.get("priority")} for r in rois],
                    "risk": round(risk, 4),
                })

                # Dashboard preview
                vis_frame = recon_frame.copy()
                for r in rois:
                    x1, y1, x2, y2 = r["bbox"]
                    color = (0, 0, 255) if r.get("priority") == "high" else (0, 255, 0)
                    cv2.rectangle(vis_frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(vis_frame, r.get("priority", "ROI"), (x1, y1 - 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

                badge_color = {STATE_NORMAL: (0, 180, 0), STATE_ALERT: (0, 165, 255), STATE_CRITICAL: (0, 0, 220)}
                cv2.rectangle(vis_frame, (8, 8), (180, 34), badge_color[self.surveillance_state], -1)
                cv2.putText(vis_frame, f"{self.surveillance_state.upper()} risk={risk:.2f}",
                            (12, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

                prev_w = max(w * 3 // 4, 640)
                prev_h = max(h * 3 // 4, 480)
                vis_b64 = jpeg_encode_b64(cv2.resize(vis_frame, (prev_w, prev_h)), quality=82)

                msg = {
                    "camera_id": self.camera_id,
                    "frame_id": self.frame_id,
                    "timestamp": time.time(),
                    "frame_mean": round(float(frame_mean), 1),
                    "orig_w": w,
                    "orig_h": h,
                    "vis_frame": vis_b64,
                    "rois": [{"bbox": r["bbox"], "priority": r.get("priority"),
                              "track_id": r.get("track_id"), "reid_conf": r.get("reid_conf")} for r in rois],
                    "risk": round(risk, 4),
                    "state": self.surveillance_state,
                    "gop_size": self.encoder.current_gop,
                    "res_w": self.encoder.current_w,
                    "res_h": self.encoder.current_h,
                }
                try:
                    sio.emit('frame', msg, namespace=NAMESPACE)
                except Exception:
                    pass

                # Post heatmap data periodically (every 5 frames)
                if self.frame_id % 5 == 0:
                    try:
                        hm_rois = [{"bbox": r["bbox"], "priority": r.get("priority")} for r in rois[:20]] if rois else []
                        if hm_rois or self.frame_id % 15 == 0:
                            requests.post(f"{self.server_url.rstrip('/')}/heatmap", json={
                                "camera_id": self.camera_id,
                                "rois": hm_rois,
                                "orig_w": w, "orig_h": h,
                            }, timeout=1)
                    except Exception:
                        pass

                # Adaptive FPS
                if self.surveillance_state == STATE_NORMAL and len(rois) == 0:
                    current_fps = max(5, self.target_fps // 2)
                else:
                    current_fps = self.target_fps

                elapsed = time.time() - self.last_send
                sleep_time = max(0, (1.0 / current_fps) - elapsed)
                time.sleep(sleep_time)
                self.last_send = time.time()

        except Exception as e:
            print(f"[{self.camera_id}] Error: {e}")
        finally:
            cap.release()
            self.encoder.stop()

    def stop(self):
        self.running = False


# ── Shared utility functions ──

def jpeg_encode_b64(bgr_img, quality=80):
    if bgr_img is None or bgr_img.size == 0:
        return ""
    try:
        img_rgb = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(img_rgb)
        buff = BytesIO()
        pil.save(buff, format='JPEG', quality=int(quality))
        return base64.b64encode(buff.getvalue()).decode('ascii')
    except Exception:
        return ""


def reconstruct_background_with_rois(frame, rois, bg_scale, apply_blur, ethical_mode, mask_faces):
    h, w = frame.shape[:2]
    if ethical_mode and len(rois) == 0:
        return np.zeros_like(frame)
    recon = frame.copy()
    if apply_blur and not mask_faces:
        recon = cv2.GaussianBlur(recon, (99, 99), 30)
    elif not mask_faces:
        bg_w = max(1, int(w * float(bg_scale)))
        bg_h = max(1, int(h * float(bg_scale)))
        try:
            bg_small = cv2.resize(frame, (bg_w, bg_h), interpolation=cv2.INTER_AREA)
            recon = cv2.resize(bg_small, (w, h), interpolation=cv2.INTER_LINEAR)
        except Exception:
            pass
    for roi_dict in rois:
        r = roi_dict["bbox"]
        priority = roi_dict.get("priority", "medium")
        x1, y1, x2, y2 = [int(x) for x in r]
        try:
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            if priority == "medium":
                mw = max(1, int((x2 - x1) * 0.7))
                mh = max(1, int((y2 - y1) * 0.7))
                c_small = cv2.resize(crop, (mw, mh), interpolation=cv2.INTER_AREA)
                crop = cv2.resize(c_small, (x2 - x1, y2 - y1), interpolation=cv2.INTER_LINEAR)
            elif priority == "high" and mask_faces:
                crop = cv2.GaussianBlur(crop, (99, 99), 30)
            recon[y1:y2, x1:x2] = crop
        except Exception:
            continue
    return recon


def iou(boxA, boxB):
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    inter = max(0, xB - xA) * max(0, yB - yA)
    if inter == 0:
        return 0.0
    areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
    return inter / float(areaA + areaB - inter)


def merge_rois(existing, new_detections, ttl):
    IOU_THRESH = 0.3
    SMOOTH = 0.4
    updated = [dict(r) for r in existing]
    matched_existing = set()
    for nd in new_detections:
        nb = nd["bbox"]
        best_iou = 0.0
        best_idx = -1
        for i, er in enumerate(updated):
            v = iou(er["bbox"], nb)
            if v > best_iou:
                best_iou = v
                best_idx = i
        if best_iou >= IOU_THRESH and best_idx >= 0:
            eb = updated[best_idx]["bbox"]
            blended = [int(eb[j] * (1 - SMOOTH) + nb[j] * SMOOTH) for j in range(4)]
            updated[best_idx]["bbox"] = blended
            updated[best_idx]["ttl"] = ttl
            updated[best_idx]["priority"] = nd.get("priority", updated[best_idx].get("priority", "medium"))
            matched_existing.add(best_idx)
        else:
            updated.append({"bbox": list(nb), "priority": nd.get("priority", "medium"), "ttl": ttl})
    return updated


def risk_score(rois, motion_area_frac, hour_of_day, scene_change_score=0.0):
    score = 0.0
    for r in rois:
        p = r.get("priority", "low")
        if p == "high":
            score += 0.30
        elif p == "medium":
            score += 0.10
        else:
            score += 0.03
    score += min(float(motion_area_frac), 1.0) * 0.25
    if hour_of_day < 6 or hour_of_day >= 22:
        score += 0.20
    score += min(float(scene_change_score), 1.0) * 0.15
    score += min(len(rois) * 0.04, 0.15)
    return min(score, 1.0)


# ── Socket.IO handlers ──

@sio.event(namespace=NAMESPACE)
def connect():
    print("[camera_manager] Connected to server")

@sio.event(namespace=NAMESPACE)
def disconnect():
    print("[camera_manager] Disconnected from server")

@sio.on('control', namespace=NAMESPACE)
def on_control(msg):
    global control
    if not isinstance(msg, dict):
        return
    # If control targets a specific camera, apply to all (threads check camera_id themselves)
    mapping = {
        'bg_scale': 'BG_SCALE', 'bg_quality': 'BG_QUALITY', 'roi_quality': 'ROI_QUALITY',
        'detect_every_n': 'DETECT_EVERY_N', 'privacy_blur': 'PRIVACY_BLUR',
        'ethical_mode': 'ETHICAL_MODE', 'mask_faces': 'MASK_FACES',
        'codec': 'CODEC', 'bitrate': 'BITRATE',
    }
    for k, dest in mapping.items():
        if k in msg:
            if dest in ['PRIVACY_BLUR', 'ETHICAL_MODE', 'MASK_FACES']:
                control[dest] = bool(msg[k])
            elif dest in ['CODEC']:
                control[dest] = str(msg[k])
            else:
                try:
                    control[dest] = type(control[dest])(msg[k])
                except Exception:
                    control[dest] = int(msg[k])
    print(f"[camera_manager] control updated: bg_scale={control['BG_SCALE']}, codec={control['CODEC']}")


def main():
    parser = argparse.ArgumentParser(description="Multi-camera edge-node manager")
    parser.add_argument("--server", type=str, default=DEFAULT_SERVER)
    parser.add_argument("--config", type=str, default="../configs/cameras.yaml",
                        help="Path to cameras YAML config")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    cameras_cfg = cfg.get("cameras", [])
    enabled = [c for c in cameras_cfg if c.get("enabled", True)]
    if not enabled:
        print("No enabled cameras found in config")
        return

    print(f"Connecting to server: {args.server}")
    while True:
        try:
            sio.connect(args.server, namespaces=[NAMESPACE])
            break
        except Exception as e:
            print(f"Connect error, retrying: {e}")
            time.sleep(2)

    print(f"Loading YOLO detector...")
    detector = Detector(model_type='yolo', model_path='../models/yolov8n.pt')

    print(f"Initializing cross-camera re-ID tracker...")
    reid_tracker = ReidTracker(similarity_threshold=0.55)

    threads = []
    for cam_cfg in enabled:
        print(f"Starting camera: {cam_cfg['id']} ({cam_cfg.get('name', cam_cfg['id'])})")
        t = CameraThread(cam_cfg, detector, args.server, reid_tracker)
        t.start()
        threads.append(t)

    print(f"\nAll {len(threads)} camera(s) running. Press Ctrl+C to stop.")
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\nShutting down...")
        for t in threads:
            t.stop()
        for t in threads:
            t.join(timeout=3)
        sio.disconnect()
        print("Camera manager stopped.")


if __name__ == "__main__":
    main()
