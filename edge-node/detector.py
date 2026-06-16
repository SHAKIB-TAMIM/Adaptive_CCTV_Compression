import cv2
import os

# COCO vehicle class IDs
VEHICLE_CLASSES = {2, 3, 5, 6, 7}  # car, motorcycle, bus, train, truck

class Detector:
    def __init__(self, model_type='yolo', model_path='yolov8n.pt'):
        self.model_type = model_type
        if self.model_type == 'yolo':
            try:
                from ultralytics import YOLO
                self.model = YOLO(model_path)
            except ImportError:
                raise ImportError("Please install ultralytics: pip install ultralytics")
        else:
            raise ValueError("Unsupported model_type. Use 'yolo'.")

    def detect(self, frame, conf=0.25):
        if self.model_type == 'yolo':
            results = self.model(frame, verbose=False, conf=conf)
            rects = []
            for r in results:
                for box in r.boxes:
                    cls_id = int(box.cls[0])
                    x1, y1, x2, y2 = box.xyxy[0]

                    # Priority:
                    #   high   -> person (cls 0)
                    #   medium -> vehicles (car/moto/bus/train/truck)
                    #   low    -> all other detected objects
                    if cls_id == 0:
                        priority = "high"
                    elif cls_id in VEHICLE_CLASSES:
                        priority = "medium"
                    else:
                        priority = "low"

                    rects.append({
                        "bbox": [int(x1), int(y1), int(x2), int(y2)],
                        "priority": priority,
                        "class": cls_id
                    })
            return rects
        return []
