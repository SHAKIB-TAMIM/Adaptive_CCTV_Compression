import cv2
import os

# COCO vehicle class IDs
VEHICLE_CLASSES = {2, 3, 5, 6, 7}  # car, motorcycle, bus, train, truck

# ── Pluggable detector interface (U1: neural-ROI upgrade hook) ─────────────
# All detectors must implement detect(frame, conf) -> [{bbox, priority, class}].
# This keeps YOLOv8n as the thesis baseline while allowing Swin-Transformer,
# neural latent-scaling ROI (Perugachi-Diaz 2022), foveated (Galan 2011),
# or codec-independent pre/post ROI modules to be swapped without touching
# capture_stream.py. Register new models in DETECTOR_REGISTRY.

DETECTOR_REGISTRY = {}


def register_detector(name):
    """Decorator to register a new detector backend."""
    def deco(cls):
        DETECTOR_REGISTRY[name] = cls
        return cls
    return deco


class BaseDetector:
    """Common interface for all ROI detectors."""
    name = "base"

    def detect(self, frame, conf=0.25):
        raise NotImplementedError

    @staticmethod
    def priority_for_class(cls_id):
        if cls_id == 0:
            return "high"
        elif cls_id in VEHICLE_CLASSES:
            return "medium"
        return "low"


def create_detector(model_type='yolo', model_path='yolov8n.pt', **kwargs):
    """Factory: returns a detector instance by registry name."""
    cls = DETECTOR_REGISTRY.get(model_type)
    if cls is None:
        raise ValueError(f"Unknown detector '{model_type}'. Available: {sorted(DETECTOR_REGISTRY)}")
    if model_type == 'yolo':
        return cls(model_type=model_type, model_path=model_path, **kwargs)
    return cls(**kwargs)


@register_detector('yolo')
class YoloDetector(BaseDetector):
    name = "yolo-yolov8n-baseline"

    def __init__(self, model_type='yolo', model_path='yolov8n.pt'):
        try:
            from ultralytics import YOLO
            self.model = YOLO(model_path)
        except ImportError:
            raise ImportError("Please install ultralytics: pip install ultralytics")
        self.model_path = model_path

    def detect(self, frame, conf=0.25):
        results = self.model(frame, verbose=False, conf=conf)
        rects = []
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                x1, y1, x2, y2 = box.xyxy[0]
                rects.append({
                    "bbox": [int(x1), int(y1), int(x2), int(y2)],
                    "priority": self.priority_for_class(cls_id),
                    "class": cls_id
                })
        return rects


@register_detector('mock')
class MockDetector(BaseDetector):
    """Lightweight stub for unit tests / edge devices without YOLO."""
    name = "mock"

    def detect(self, frame, conf=0.25):
        return []


# ── Backward compatibility: Detector == YoloDetector ────────────────────────
class Detector(YoloDetector):
    def __init__(self, model_type='yolo', model_path='yolov8n.pt'):
        if model_type != 'yolo':
            # delegate to registry for future backends (swin, neural, foveated)
            inst = create_detector(model_type, model_path)
            self.__class__ = inst.__class__
            self.__dict__.update(inst.__dict__)
            return
        super().__init__(model_type=model_type, model_path=model_path)
