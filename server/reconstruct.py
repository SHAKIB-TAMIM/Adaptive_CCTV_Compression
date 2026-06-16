import cv2
import json
import os

def reconstruct_frame(base_dir, frame_entry):
    bg_path = os.path.join(base_dir, "bg", frame_entry["bg_file"])
    frame = cv2.imread(bg_path)

    if frame is None:
        raise RuntimeError("Background frame missing")

    for roi in frame_entry["rois"]:
        roi_img = cv2.imread(os.path.join(base_dir, "roi", roi["file"]))
        x1, y1, x2, y2 = roi["bbox"]
        frame[y1:y2, x1:x2] = roi_img

    return frame
