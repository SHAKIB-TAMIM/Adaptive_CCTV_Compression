#!/usr/bin/env python3
"""
capture_stream.py - edge node sending ROI-aware compressed frames via FFmpeg
Features:
 - YOLOv8 based ROI detection
 - Real-time background blurring for privacy
 - FFmpeg subprocess for H.265/HEVC streaming via UDP
 - Dynamic adaptive bitrate and FPS
 - Socket.IO for control and sending metadata/preview frames
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
from detector import Detector

DEFAULT_SERVER = "http://127.0.0.1:5000"
NAMESPACE = "/stream"
CAM_INDEX = 0
TARGET_FPS = 15

# Socket.IO client with auto-reconnect
sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=1)

# Default control parameters
control = {
    "BG_SCALE": 0.5,
    "BG_QUALITY": 20,
    "ROI_QUALITY": 90,
    "DETECT_EVERY_N": 3,
    "SAVE_SAMPLE": False,
    "EXPERIMENT_ID": "",
    "PRIVACY_BLUR": False,
    "ETHICAL_MODE": False,
    "MASK_FACES": False,
    "CODEC": "libx265",  # hevc_nvenc or libx265 or libx264
    "BITRATE": 2000      # kbps
}

# -------------------- Risk / Event-State Constants --------------------
STATE_NORMAL   = "normal"
STATE_ALERT    = "alert"
STATE_CRITICAL = "critical"

RISK_NORMAL_THRESHOLD         = 0.30   # below => NORMAL
RISK_ALERT_THRESHOLD          = 0.65   # above => CRITICAL
RISK_CRITICAL_EXIT_THRESHOLD  = 0.50   # below exits CRITICAL (hysteresis)

# Compression profiles per surveillance state (None = use live user control value)
STATE_COMPRESSION = {
    STATE_NORMAL:   {"BG_SCALE": None,  "BG_QUALITY": None, "ROI_QUALITY": None},
    STATE_ALERT:    {"BG_SCALE": 0.75,  "BG_QUALITY": 45,   "ROI_QUALITY": 95},
    STATE_CRITICAL: {"BG_SCALE": 1.0,   "BG_QUALITY": 88,   "ROI_QUALITY": 100},
}

PRE_EVENT_SECONDS  = 15   # seconds of frames to keep in the rolling pre-event buffer
POST_EVENT_SECONDS = 10   # seconds of frames to save after a critical event

# Directory where event archives are stored
EVENT_DIR = os.path.join(os.path.dirname(__file__), "..", "server", "events")

class CodecManager:
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

    def __init__(self):
        self.process = None
        self.current_w = 0
        self.current_h = 0
        self.current_gop = 120
        self.current_fps = 15
        self.current_codec = "libx265"
        self.current_bitrate = 2000

    def start(self, w, h, fps, codec, bitrate, gop_size=None):
        self.stop()

        self.current_w = w
        self.current_h = h
        self.current_fps = fps
        self.current_codec = codec
        self.current_bitrate = bitrate

        if gop_size is None:
            gop_size = fps * 2
        self.current_gop = gop_size

        if codec == 'vvc_test' or codec == 'neural_test':
            print(f"[CodecManager] Stubbing experimental codec: {codec}")
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
            'udp://127.0.0.1:1234'
        ]

        print(f"[CodecManager] Starting: {' '.join(cmd)}")
        self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def set_gop(self, state):
        config = self.GOP_CONFIG.get(state)
        if not config:
            return
        gop = config["gop_size"]
        if gop == self.current_gop:
            return
        print(f"[CodecManager] GOP {self.current_gop} -> {gop} ({state})")
        self.start(self.current_w, self.current_h, self.current_fps,
                   self.current_codec, self.current_bitrate, gop_size=gop)

    def set_resolution(self, state):
        config = self.RES_CONFIG.get(state)
        if not config:
            return
        w, h = config
        if w == self.current_w and h == self.current_h:
            return
        print(f"[CodecManager] Resolution {self.current_w}x{self.current_h} -> {w}x{h} ({state})")
        self.start(w, h, self.current_fps, self.current_codec, self.current_bitrate,
                   gop_size=self.current_gop)

    def write(self, frame_bytes):
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.write(frame_bytes)
            except Exception as e:
                print(f"[CodecManager] write error: {e}")

    def stop(self):
        if self.process is not None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=2)
            except:
                self.process.kill()
            self.process = None

codec_manager = CodecManager()

def jpeg_encode_b64(bgr_img, quality=80):
    if bgr_img is None or bgr_img.size == 0:
        return ""
    try:
        img_rgb = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(img_rgb)
        buff = BytesIO()
        pil.save(buff, format='JPEG', quality=int(quality))
        return base64.b64encode(buff.getvalue()).decode('ascii')
    except Exception as e:
        print("JPEG encode error:", e)
        return ""

@sio.event(namespace=NAMESPACE)
def connect():
    print("[socketio] Connected to server")

@sio.event(namespace=NAMESPACE)
def disconnect():
    print("[socketio] Disconnected from server")

@sio.on('control', namespace=NAMESPACE)
def on_control(msg):
    global control
    try:
        mapping = {
            'bg_scale': 'BG_SCALE',
            'bg_quality': 'BG_QUALITY',
            'roi_quality': 'ROI_QUALITY',
            'detect_every_n': 'DETECT_EVERY_N',
            'save_sample': 'SAVE_SAMPLE',
            'experiment_id': 'EXPERIMENT_ID',
            'privacy_blur': 'PRIVACY_BLUR',
            'ethical_mode': 'ETHICAL_MODE',
            'mask_faces': 'MASK_FACES',
            'codec': 'CODEC',
            'bitrate': 'BITRATE'
        }
        changed = False
        restart_ffmpeg = False
        
        if not isinstance(msg, dict):
            return
            
        for k, dest in mapping.items():
            if k in msg:
                old_val = control[dest]
                new_val = msg[k]
                if dest in ['PRIVACY_BLUR', 'SAVE_SAMPLE', 'ETHICAL_MODE', 'MASK_FACES']:
                    control[dest] = bool(new_val)
                elif dest in ['CODEC', 'EXPERIMENT_ID']:
                    control[dest] = str(new_val)
                else:
                    try:
                        control[dest] = type(control[dest])(new_val)
                    except:
                        control[dest] = int(new_val)
                
                if old_val != control[dest]:
                    changed = True
                    if dest in ['CODEC', 'BITRATE']:
                        restart_ffmpeg = True

        if changed:
            print("[control] updated:", control)
        
        # If codec or bitrate changed from dashboard, we might want to restart ffmpeg
        # We will handle restart_ffmpeg in the main loop instead if width/height is known
    except Exception as e:
        print("[control] parse error:", e)

def post_motion(server_base, experiment_id, frame_id, motion_percent, rois):
    url = server_base.rstrip('/') + "/motion"
    payload = {
        "experiment_id": experiment_id,
        "frame_id": frame_id,
        "motion_percent": round(float(motion_percent), 4),
        "rois": [{"bbox": r} for r in rois]
    }
    try:
        requests.post(url, json=payload, timeout=2)
    except:
        pass

def post_event_to_server(server_base, event_data):
    """Notify server of a surveillance state-change event (alert/critical)."""
    url = server_base.rstrip('/') + "/event"
    try:
        requests.post(url, json=event_data, timeout=3)
        print(f"[event] posted to server: {event_data.get('event_id')} risk={event_data.get('risk'):.3f}")
    except Exception as e:
        print(f"[event] post error: {e}")

def post_sample_to_server(server_base, experiment_id, frame_id, orig_img, recon_img, rois, meta):
    url = server_base.rstrip('/') + "/sample"
    try:
        orig_b64 = jpeg_encode_b64(orig_img, quality=95)
        recon_b64 = jpeg_encode_b64(recon_img, quality=90)
        payload = {
            "experiment_id": experiment_id,
            "frame_id": frame_id,
            "timestamp": time.time(),
            "orig_b64": orig_b64,
            "recon_b64": recon_b64,
            "rois": [{"bbox": r} for r in rois],
            "meta": meta
        }
        requests.post(url, json=payload, timeout=5)
        print("[sample] posted to server:", experiment_id, frame_id)
    except Exception as e:
        print("[sample] post error:", e)

def reconstruct_background_with_rois(frame, rois, bg_scale, apply_blur, ethical_mode, mask_faces):
    h, w = frame.shape[:2]
    
    if ethical_mode and len(rois) == 0:
        # Ethical mode: return empty black frame if no ROIs
        return np.zeros_like(frame)

    recon = frame.copy()
    
    if apply_blur and not mask_faces:
        # Heavy blur for privacy over entire background
        recon = cv2.GaussianBlur(recon, (99, 99), 30)
    elif not mask_faces:
        # Original logic: downscale then upscale
        bg_w = max(1, int(w * float(bg_scale)))
        bg_h = max(1, int(h * float(bg_scale)))
        try:
            bg_small = cv2.resize(frame, (bg_w, bg_h), interpolation=cv2.INTER_AREA)
            recon = cv2.resize(bg_small, (w, h), interpolation=cv2.INTER_LINEAR)
        except Exception:
            pass

    # Restore ROIs based on priority
    for roi_dict in rois:
        r = roi_dict["bbox"]
        priority = roi_dict.get("priority", "medium")
        x1, y1, x2, y2 = [int(x) for x in r]
        try:
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            
            if priority == "medium":
                # slightly compress medium priority (e.g. vehicles) before placing back
                mw = max(1, int((x2 - x1) * 0.7))
                mh = max(1, int((y2 - y1) * 0.7))
                c_small = cv2.resize(crop, (mw, mh), interpolation=cv2.INTER_AREA)
                crop = cv2.resize(c_small, (x2 - x1, y2 - y1), interpolation=cv2.INTER_LINEAR)
            elif priority == "high" and mask_faces:
                # Black out high priority objects (faces/people) if face masking is enabled
                crop = cv2.GaussianBlur(crop, (99, 99), 30)

            recon[y1:y2, x1:x2] = crop
        except:
            continue
    return recon

def iou(boxA, boxB):
    """Compute Intersection-over-Union between two [x1,y1,x2,y2] boxes."""
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
    """
    Merge new detections into the existing temporal ROI list.
    - If a new detection overlaps an existing ROI (IoU > 0.3), refresh its TTL
      and update its position smoothly (blend bboxes) instead of hard-replacing.
    - New detections with no overlap are added fresh.
    - Existing ROIs with no matching new detection keep their current TTL (decay handled outside).
    """
    IOU_THRESH = 0.3
    SMOOTH = 0.4  # how much to move bbox toward new detection each frame

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
            # Smooth the bounding box toward the new detection
            eb = updated[best_idx]["bbox"]
            blended = [
                int(eb[j] * (1 - SMOOTH) + nb[j] * SMOOTH) for j in range(4)
            ]
            updated[best_idx]["bbox"] = blended
            updated[best_idx]["ttl"] = ttl          # refresh TTL
            updated[best_idx]["priority"] = nd.get("priority", updated[best_idx].get("priority", "medium"))
            matched_existing.add(best_idx)
        else:
            # Genuinely new detection — add it
            updated.append({"bbox": list(nb), "priority": nd.get("priority", "medium"), "ttl": ttl})

    return updated


def risk_score(rois, motion_area_frac, hour_of_day, scene_change_score=0.0):
    """
    Compute a normalized risk score [0.0, 1.0] for the current frame.

    Inputs:
      - rois             : list of ROI dicts with 'priority' field
      - motion_area_frac : fraction of frame pixels covered by ROI bounding boxes (0.0–1.0)
      - hour_of_day      : integer hour 0–23 (used for after-hours weighting)
      - scene_change_score: normalized frame-to-frame diff magnitude (0.0–1.0)

    Scoring logic:
      - Each HIGH-priority object (person) contributes 0.30
      - Each MEDIUM-priority object (vehicle) contributes 0.10
      - Each LOW-priority object contributes 0.03
      - Motion area fraction contributes up to 0.25
      - After-hours period (22:00–06:00) adds a flat 0.20 bonus
      - Scene change magnitude contributes up to 0.15
      - ROI density (count) adds up to 0.15
    """
    score = 0.0

    # Object presence
    for r in rois:
        p = r.get("priority", "low")
        if p == "high":
            score += 0.30
        elif p == "medium":
            score += 0.10
        else:
            score += 0.03

    # Motion intensity (area fraction)
    score += min(float(motion_area_frac), 1.0) * 0.25

    # After-hours bonus (10 PM – 6 AM)
    if hour_of_day < 6 or hour_of_day >= 22:
        score += 0.20

    # Scene change magnitude
    score += min(float(scene_change_score), 1.0) * 0.15

    # ROI density bonus (more objects = more suspicious)
    score += min(len(rois) * 0.04, 0.15)

    return min(score, 1.0)

def main(server_url, cam_index, target_fps, config_path=None):
    if config_path and os.path.exists(config_path):
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
            if 'edge_node' in cfg:
                ecfg = cfg['edge_node']
                if 'encoder' in ecfg:
                    control['CODEC'] = ecfg['encoder'].get('codec', control['CODEC'])
                    control['BITRATE'] = int(str(ecfg['encoder'].get('bitrate', control['BITRATE'])).replace('k',''))
                if 'privacy' in ecfg:
                    control['PRIVACY_BLUR'] = ecfg['privacy'].get('blur_background', control['PRIVACY_BLUR'])
                if 'base_fps' in ecfg:
                    target_fps = ecfg['base_fps']
        print(f"[config] Loaded configuration from {config_path}")

    while True:
        try:
            sio.connect(server_url, namespaces=[NAMESPACE])
            break
        except Exception as e:
            print("[socketio] connect error, retrying in 2s:", e)
            time.sleep(2)

    model_path = control.get('model_path', '../models/yolov8n.pt')
    print(f"Initializing YOLO Detector with {model_path}...")
    try:
        detector = Detector(model_type='yolo', model_path=model_path)
    except Exception as e:
        print(f"Error loading detector: {e}")
        return

    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        print(f"[camera] cannot open camera {cam_index}")
        return

    frame_id = 0
    last_send = time.time()
    last_codec = control['CODEC']
    last_bitrate = control['BITRATE']
    temporal_rois = []
    roi_ttl = 30  # Keep ROIs for 30 frames (~2s at 15fps) — prevents pulsing flicker

    # ── Risk / Event State Machine ──
    surveillance_state   = STATE_NORMAL
    state_hysteresis_count = 0
    HYSTERESIS_FRAMES    = 8            # frames to hold current state before downgrading
    POST_EVENT_FRAMES    = POST_EVENT_SECONDS * target_fps
    pre_event_buffer     = collections.deque(maxlen=PRE_EVENT_SECONDS * target_fps)
    post_event_frames_remaining = 0
    event_id             = None
    prev_frame_gray      = None
    os.makedirs(EVENT_DIR, exist_ok=True)

    # Read first frame to get dimensions
    ret, frame = cap.read()
    if not ret:
        print("Failed to read from camera")
        return

    h, w = frame.shape[:2]
    codec_manager.start(w, h, target_fps, control['CODEC'], control['BITRATE'])

    try:
        rois = []
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.05)
                continue

            frame_id += 1

            # ── Codec hot-swap (preserve adapted resolution & GOP) ──
            if last_codec != control['CODEC'] or last_bitrate != control['BITRATE']:
                codec_manager.start(
                    codec_manager.current_w or w,
                    codec_manager.current_h or h,
                    target_fps, control['CODEC'], control['BITRATE'],
                    gop_size=codec_manager.current_gop
                )
                last_codec  = control['CODEC']
                last_bitrate = control['BITRATE']

            # ── Scene-change score (frame-to-frame diff) ──
            frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if prev_frame_gray is not None and prev_frame_gray.shape == frame_gray.shape:
                diff = cv2.absdiff(frame_gray, prev_frame_gray)
                scene_change_score = float(np.mean(diff)) / 128.0
            else:
                scene_change_score = 0.0
            prev_frame_gray = frame_gray

            # ── YOLO Detection ──
            if frame_id % max(1, int(control['DETECT_EVERY_N'])) == 0:
                new_rois = detector.detect(frame, conf=0.3)
                temporal_rois = merge_rois(temporal_rois, new_rois, roi_ttl)
            else:
                temporal_rois = [tr for tr in temporal_rois if tr["ttl"] > 0]
                for tr in temporal_rois:
                    tr["ttl"] -= 1

            rois = temporal_rois

            # ── Motion area fraction ──
            frame_area = max(h * w, 1)
            roi_pixels = sum(
                max(0, r["bbox"][2] - r["bbox"][0]) * max(0, r["bbox"][3] - r["bbox"][1])
                for r in rois
            )
            motion_area_frac = min(roi_pixels / frame_area, 1.0)

            # ── Risk Engine ──
            hour_now = datetime.datetime.now().hour
            risk = risk_score(rois, motion_area_frac, hour_now, scene_change_score)

            # State machine transition with hysteresis
            prev_state = surveillance_state
            if risk >= RISK_ALERT_THRESHOLD:
                surveillance_state      = STATE_CRITICAL
                state_hysteresis_count  = HYSTERESIS_FRAMES
            elif risk >= RISK_NORMAL_THRESHOLD:
                if surveillance_state == STATE_CRITICAL:
                    if risk < RISK_CRITICAL_EXIT_THRESHOLD:
                        surveillance_state = STATE_ALERT
                        state_hysteresis_count = HYSTERESIS_FRAMES
                else:
                    surveillance_state = STATE_ALERT
            else:
                if surveillance_state == STATE_CRITICAL:
                    surveillance_state = STATE_ALERT
                    state_hysteresis_count = HYSTERESIS_FRAMES
                elif state_hysteresis_count > 0:
                    state_hysteresis_count -= 1
                else:
                    surveillance_state = STATE_NORMAL

            # ── Adaptive GOP & Resolution on state transition ──
            if surveillance_state != prev_state:
                codec_manager.set_gop(surveillance_state)
                codec_manager.set_resolution(surveillance_state)

            # Trigger actions on transition into CRITICAL
            if prev_state != STATE_CRITICAL and surveillance_state == STATE_CRITICAL:
                event_id = f"event_{int(time.time() * 1000)}"
                print(f"[RISK] \u26a0 CRITICAL EVENT: {event_id} | risk={risk:.3f} | hour={hour_now}")

                # Dump rolling pre-event buffer to disk
                ev_dir = os.path.join(EVENT_DIR, event_id)
                os.makedirs(ev_dir, exist_ok=True)
                for idx, buf_entry in enumerate(pre_event_buffer):
                    ep = os.path.join(ev_dir, f"pre_{idx:05d}.jpg")
                    cv2.imwrite(ep, buf_entry["frame"])
                    with open(ep.replace(".jpg", "_meta.json"), "w") as mf:
                        json.dump({"frame_id": buf_entry["frame_id"],
                                   "rois": buf_entry["rois"],
                                   "risk": buf_entry["risk"]}, mf)

                # Notify server
                post_event_to_server(server_url, {
                    "event_id": event_id,
                    "risk": round(risk, 4),
                    "state": STATE_CRITICAL,
                    "frame_id": frame_id,
                    "hour": hour_now,
                    "num_rois": len(rois),
                    "rois": [{"bbox": r["bbox"], "priority": r.get("priority")} for r in rois]
                })
                post_event_frames_remaining = POST_EVENT_FRAMES

            # Save post-event frames to event archive
            if post_event_frames_remaining > 0 and event_id:
                ev_dir = os.path.join(EVENT_DIR, event_id)
                ep = os.path.join(ev_dir, f"post_{post_event_frames_remaining:05d}.jpg")
                cv2.imwrite(ep, frame)
                post_event_frames_remaining -= 1
                if post_event_frames_remaining == 0:
                    print(f"[RISK] Post-event buffer complete: {event_id}")

            # ── Risk-aware compression profile ──
            profile = STATE_COMPRESSION[surveillance_state]
            eff_bg_scale    = profile["BG_SCALE"]    if profile["BG_SCALE"]    is not None else control['BG_SCALE']
            eff_bg_quality  = profile["BG_QUALITY"]  if profile["BG_QUALITY"]  is not None else control['BG_QUALITY']
            eff_roi_quality = profile["ROI_QUALITY"] if profile["ROI_QUALITY"] is not None else control['ROI_QUALITY']

            # ── Compose frame with effective parameters ──
            recon_frame = reconstruct_background_with_rois(
                frame, rois, eff_bg_scale,
                control['PRIVACY_BLUR'], control['ETHICAL_MODE'], control['MASK_FACES']
            )

            # Resize frame to match adapted resolution, then push to H.265 UDP stream
            out_w = codec_manager.current_w or w
            out_h = codec_manager.current_h or h
            if out_w != w or out_h != h:
                out_frame = cv2.resize(recon_frame, (out_w, out_h), interpolation=cv2.INTER_AREA)
            else:
                out_frame = recon_frame
            codec_manager.write(out_frame.tobytes())

            # Maintain rolling pre-event buffer (original full-res frames)
            pre_event_buffer.append({
                "frame":    frame.copy(),
                "frame_id": frame_id,
                "rois":     [{"bbox": r["bbox"], "priority": r.get("priority")} for r in rois],
                "risk":     round(risk, 4)
            })

            # ── Dashboard preview with ROI boxes + risk overlay ──
            vis_frame = recon_frame.copy()
            for r in rois:
                x1, y1, x2, y2 = r["bbox"]
                color = (0, 0, 255) if r.get("priority") == "high" else (0, 255, 0)
                cv2.rectangle(vis_frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(vis_frame, r.get("priority", "ROI"), (x1, y1 - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            # State badge burned into preview
            badge_color = {STATE_NORMAL: (0,180,0), STATE_ALERT: (0,165,255), STATE_CRITICAL: (0,0,220)}
            cv2.rectangle(vis_frame, (8, 8), (180, 34), badge_color[surveillance_state], -1)
            cv2.putText(vis_frame, f"{surveillance_state.upper()}  risk={risk:.2f}",
                        (12, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

            prev_w  = max(w * 3 // 4, 640)
            prev_h  = max(h * 3 // 4, 480)
            vis_b64 = jpeg_encode_b64(cv2.resize(vis_frame, (prev_w, prev_h)), quality=82)

            msg = {
                "frame_id":  frame_id,
                "timestamp": time.time(),
                "orig_w":    w,
                "orig_h":    h,
                "vis_frame": vis_b64,
                "rois":      [{"bbox": r["bbox"], "priority": r.get("priority")} for r in rois],
                "risk":      round(risk, 4),
                "state":     surveillance_state,
                "gop_size":  codec_manager.current_gop,
                "res_w":     codec_manager.current_w,
                "res_h":     codec_manager.current_h,
            }
            try:
                sio.emit('frame', msg, namespace=NAMESPACE)
            except:
                pass

            # Post motion log periodically
            if len(rois) > 0 and frame_id % max(1, int(control['DETECT_EVERY_N'])) == 0:
                try:
                    post_motion(server_url, control.get('EXPERIMENT_ID', ''), frame_id,
                                motion_area_frac * 100, [r["bbox"] for r in rois])
                except:
                    pass

            if control.get('SAVE_SAMPLE', False):
                try:
                    meta = {
                        "bg_scale":     eff_bg_scale,
                        "privacy_blur": control['PRIVACY_BLUR'],
                        "num_rois":     len(rois),
                        "risk":         round(risk, 4),
                        "state":        surveillance_state,
                    }
                    post_sample_to_server(server_url, control.get('EXPERIMENT_ID', ''), frame_id,
                                          frame, recon_frame, [r["bbox"] for r in rois], meta)
                finally:
                    control['SAVE_SAMPLE'] = False

            # ── Adaptive FPS (slow down only in NORMAL + no ROIs) ──
            if surveillance_state == STATE_NORMAL and len(rois) == 0:
                current_fps = max(5, target_fps // 2)
            else:
                current_fps = target_fps

            elapsed    = time.time() - last_send
            sleep_time = max(0, (1.0 / current_fps) - elapsed)
            time.sleep(sleep_time)
            last_send  = time.time()

    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        cap.release()
        codec_manager.stop()
        sio.disconnect()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", type=str, default=DEFAULT_SERVER)
    parser.add_argument("--cam", type=int, default=CAM_INDEX)
    parser.add_argument("--fps", type=int, default=TARGET_FPS)
    parser.add_argument("--config", type=str, default=None, help="Path to YAML config profile")
    args = parser.parse_args()
    main(args.server, args.cam, args.fps, args.config)