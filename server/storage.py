import os
import json
import base64
from datetime import datetime

BASE_DIR = "recordings"


def get_today_dir():
    date = datetime.now().strftime("%Y-%m-%d")
    base = os.path.join(BASE_DIR, date)
    bg_dir = os.path.join(base, "bg")
    roi_dir = os.path.join(base, "roi")

    os.makedirs(bg_dir, exist_ok=True)
    os.makedirs(roi_dir, exist_ok=True)

    return base, bg_dir, roi_dir


def save_base64_image(b64, path):
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))


def save_frame(frame_msg):
    print("[STORAGE] Saving frame", frame_msg["frame_id"])

    """
    frame_msg = {
      frame_id,
      bg_data,
      rois: [{bbox, data}],
      timestamp
    }
    """
    base, bg_dir, roi_dir = get_today_dir()

    fid = frame_msg["frame_id"]

    # ---- save background ----
    bg_path = os.path.join(bg_dir, f"frame_{fid:06d}.jpg")
    save_base64_image(frame_msg["bg_data"], bg_path)

    roi_entries = []

    # ---- save ROIs ----
    for i, roi in enumerate(frame_msg.get("rois", [])):
        roi_name = f"frame_{fid:06d}_roi{i}.jpg"
        roi_path = os.path.join(roi_dir, roi_name)
        save_base64_image(roi["data"], roi_path)

        roi_entries.append({
            "bbox": roi["bbox"],
            "file": roi_name
        })

    # ---- metadata ----
    meta_path = os.path.join(base, "meta.json")
    entry = {
        "frame_id": fid,
        "timestamp": frame_msg.get("timestamp"),
        "bg_file": os.path.basename(bg_path),
        "rois": roi_entries
    }

    if os.path.exists(meta_path):
        with open(meta_path, "r") as f:
            data = json.load(f)
    else:
        data = []

    data.append(entry)

    with open(meta_path, "w") as f:
        json.dump(data, f, indent=2)
