"""
ptz_controller.py — Pan-Tilt-Zoom control for PTZ cameras.
Supports ONVIF and simple HTTP-based PTZ APIs.

Auto-tracking: when a high-priority object approaches the frame edge,
sends pan/tilt commands to keep it centered.

Usage:
  from ptz_controller import PtzController
  ptz = PtzController(camera_config)
  ptz.relative_move(pan=0.1, tilt=0.0)  # pan right
"""
import threading
import time
import requests
import logging

logger = logging.getLogger("ptz")


class PtzController:
    def __init__(self, config=None):
        """
        config: {
            "protocol": "onvif" | "http" | "none",
            "host": "192.168.1.100",
            "port": 80,
            "username": "admin",
            "password": "password",
            "http_pan_url": "http://{host}/cgi-bin/ptz.cgi?action=start&channel=0&code=PanLeft&speed={speed}",
            "http_stop_url": "http://{host}/cgi-bin/ptz.cgi?action=stop&channel=0",
            "onvif_profile": "Profile_1",
        }
        """
        self.config = config or {}
        self.protocol = self.config.get("protocol", "none")
        self.lock = threading.Lock()
        self._onvif_device = None
        self._onvif_ptz = None

        if self.protocol == "onvif":
            self._init_onvif()

    def _init_onvif(self):
        try:
            from onvif import ONVIFCamera
            host = self.config.get("host", "192.168.1.100")
            port = self.config.get("port", 80)
            user = self.config.get("username", "admin")
            pwd = self.config.get("password", "password")
            self._onvif_device = ONVIFCamera(host, port, user, pwd)
            self._onvif_ptz = self._onvif_device.create_ptz_service()
            logger.info(f"[PTZ] ONVIF initialized: {host}:{port}")
        except ImportError:
            logger.warning("[PTZ] onvif-zeep not installed. Install with: pip install onvif-zeep")
            self.protocol = "none"
        except Exception as e:
            logger.warning(f"[PTZ] ONVIF init failed: {e}")
            self.protocol = "none"

    def relative_move(self, pan=0.0, tilt=0.0, zoom=0.0, speed=0.5):
        """Move PTZ relative to current position.
        pan:  positive=right, negative=left  (-1.0 to 1.0)
        tilt: positive=up, negative=down      (-1.0 to 1.0)
        zoom: positive=in, negative=out       (-1.0 to 1.0)
        """
        with self.lock:
            if self.protocol == "onvif":
                return self._onvif_move(pan, tilt, zoom, speed)
            elif self.protocol == "http":
                return self._http_move(pan, tilt, speed)
            return False

    def _onvif_move(self, pan, tilt, zoom, speed):
        try:
            request = self._onvif_ptz.create_type("RelativeMove")
            request.ProfileToken = self.config.get("onvif_profile", "Profile_1")
            request.Translation = {
                "PanTilt": {"x": pan, "y": tilt, "space": None},
                "Zoom": {"x": zoom, "space": None},
            }
            request.Speed = {
                "PanTilt": {"x": speed, "y": speed},
                "Zoom": {"x": speed},
            }
            self._onvif_ptz.RelativeMove(request)
            return True
        except Exception as e:
            logger.error(f"[PTZ] ONVIF move error: {e}")
            return False

    def _http_move(self, pan, tilt, speed):
        host = self.config.get("host", "192.168.1.100")
        try:
            if pan > 0.1:
                url = self.config.get("http_pan_right_url",
                    f"http://{host}/cgi-bin/ptz.cgi?action=start&channel=0&code=PanRight&speed={int(speed*100)}")
            elif pan < -0.1:
                url = self.config.get("http_pan_left_url",
                    f"http://{host}/cgi-bin/ptz.cgi?action=start&channel=0&code=PanLeft&speed={int(speed*100)}")
            elif tilt > 0.1:
                url = self.config.get("http_tilt_up_url",
                    f"http://{host}/cgi-bin/ptz.cgi?action=start&channel=0&code=TiltUp&speed={int(speed*100)}")
            elif tilt < -0.1:
                url = self.config.get("http_tilt_down_url",
                    f"http://{host}/cgi-bin/ptz.cgi?action=start&channel=0&code=TiltDown&speed={int(speed*100)}")
            else:
                url = self.config.get("http_stop_url",
                    f"http://{host}/cgi-bin/ptz.cgi?action=stop&channel=0")
            requests.get(url, timeout=1)
            return True
        except Exception as e:
            logger.error(f"[PTZ] HTTP move error: {e}")
            return False

    def stop(self):
        with self.lock:
            if self.protocol == "onvif":
                try:
                    request = self._onvif_ptz.create_type("Stop")
                    request.ProfileToken = self.config.get("onvif_profile", "Profile_1")
                    self._onvif_ptz.Stop(request)
                except Exception:
                    pass
            elif self.protocol == "http":
                self._http_move(0, 0, 0)

    def center_on_bbox(self, bbox, frame_w, frame_h, deadzone=0.15):
        """Auto-track: returns (pan, tilt) needed to center bbox.
        deadzone: fraction of frame width/height to ignore (prevents jitter).

        Call from the main loop, then pass result to relative_move().
        """
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2 / frame_w
        cy = (y1 + y2) / 2 / frame_h

        pan = 0.0
        tilt = 0.0

        if cx < 0.5 - deadzone / 2:
            pan = -(0.5 - cx) * 2  # object is left, pan left
        elif cx > 0.5 + deadzone / 2:
            pan = (cx - 0.5) * 2   # object is right, pan right

        if cy < 0.5 - deadzone / 2:
            tilt = (0.5 - cy) * 2  # object is high, tilt up
        elif cy > 0.5 + deadzone / 2:
            tilt = -(cy - 0.5) * 2 # object is low, tilt down

        return pan, tilt
