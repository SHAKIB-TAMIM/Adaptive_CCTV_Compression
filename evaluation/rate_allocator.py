"""
rate_allocator.py — Formal Rate-Distortion Optimization Framework

Formulates the CCTV compression problem as a constrained optimization:

    min   Σ_i  D_i(R_i)          (total distortion across regions)
    s.t.  Σ_i  R_i  ≤  R_total   (total bitrate budget)
          R_i     ≥  R_min_i     (minimum forensic quality per ROI)
          R_bg    ≤  R_bg_max    (background rate cap)

Where:
  - D_i(R_i) = α_i · exp(-β_i · R_i) + γ_i   (exponential RD model per region)
  - α_i = semantic importance weight (from risk engine)
  - β_i = content complexity (variance-based)
  - γ_i = irreducible distortion floor

Solved via Lagrangian relaxation: min D + λ·R, then binary search on λ
to hit the bitrate constraint.

Usage:
    from rate_allocator import RateAllocator
    ra = RateAllocator(total_budget_kbps=1000)
    allocation = ra.allocate(rois, frame_shape, risk_state)
    # allocation = {
    #   "bg": {"quality": 15, "scale": 0.3, "target_kbps": 120},
    #   "rois": [{"id": 0, "quality": 95, "target_kbps": 280}, ...],
    #   "lambda": 0.042, "total_kbps": 980
    # }
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import math


@dataclass
class RegionOfInterest:
    bbox: List[int]  # [x1, y1, x2, y2]
    priority: str = "medium"  # "high", "medium", "low"
    class_id: int = -1
    track_id: Optional[int] = None
    variance: float = 0.0  # content complexity (computed from frame)


@dataclass
class AllocationResult:
    bg_quality: int
    bg_scale: float
    bg_target_kbps: float
    roi_allocations: List[Dict]
    lambda_val: float
    total_target_kbps: float
    budget_kbps: float
    efficiency: float  # useful_bits / total_bits


# ── Priority weights (semantic importance α_i) ────────────────────────────
PRIORITY_WEIGHTS = {
    "high": 1.0,     # person — maximum forensic importance
    "medium": 0.4,   # vehicle — moderate importance
    "low": 0.1,      # other — minimal importance
}

# ── State-based budget allocation ratios ──────────────────────────────────
# How much of total budget goes to ROI vs background
STATE_BUDGET_RATIO = {
    "normal":   {"roi_fraction": 0.30, "bg_fraction": 0.70},
    "alert":    {"roi_fraction": 0.55, "bg_fraction": 0.45},
    "critical": {"roi_fraction": 0.80, "bg_fraction": 0.20},
}


class RateAllocator:
    """
    Lagrangian rate allocator for risk-aware surveillance compression.

    Solves: min Σ D_i(R_i) s.t. Σ R_i ≤ R_total, R_i ≥ R_min_i
    """

    def __init__(
        self,
        total_budget_kbps: float = 1000,
        fps: float = 15,
        resolution: Tuple[int, int] = (640, 480),
        min_roi_kbps: float = 50,
        min_bg_kbps: float = 20,
    ):
        self.total_budget = total_budget_kbps
        self.fps = fps
        self.res_w, self.res_h = resolution
        self.min_roi_kbps = min_roi_kbps
        self.min_bg_kbps = min_bg_kbps

    def compute_region_complexity(
        self, frame: np.ndarray, bbox: List[int]
    ) -> float:
        """
        Compute content complexity (variance) for a region.
        Higher variance = more bits needed to maintain quality.
        Returns normalized value in [0.1, 1.0].
        """
        x1, y1, x2, y2 = [int(v) for v in bbox]
        x1 = max(0, min(x1, frame.shape[1] - 1))
        y1 = max(0, min(y1, frame.shape[0] - 1))
        x2 = max(x1 + 1, min(x2, frame.shape[1]))
        y2 = max(y1 + 1, min(y2, frame.shape[0]))

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return 0.3

        gray = np.mean(crop, axis=2) if len(crop.shape) == 3 else crop.astype(float)
        variance = float(np.var(gray))
        # Normalize: low variance → 0.1, high variance → 1.0
        normalized = 0.1 + 0.9 * min(variance / 2000.0, 1.0)
        return normalized

    def build_rd_model(
        self,
        rois: List[RegionOfInterest],
        frame: np.ndarray,
        risk_state: str,
    ) -> Dict:
        """
        Build the Rate-Distortion model for all regions.

        D_i(R_i) = α_i · β_i · exp(-γ_i · R_i) + δ_i

        Returns model parameters per region.
        """
        budget_ratio = STATE_BUDGET_RATIO.get(risk_state, STATE_BUDGET_RATIO["normal"])
        roi_budget = self.total_budget * budget_ratio["roi_fraction"]
        bg_budget = self.total_budget * budget_ratio["bg_fraction"]

        # Sort ROIs by priority (high first)
        priority_order = {"high": 0, "medium": 1, "low": 2}
        sorted_rois = sorted(
            rois, key=lambda r: priority_order.get(r.priority, 2)
        )

        regions = []

        # Background region
        bg_complexity = 0.3  # background is low-complexity by design
        bg_weight = 0.2  # low semantic importance
        bg_min = self.min_bg_kbps
        regions.append({
            "id": "bg",
            "alpha": bg_weight,
            "beta": bg_complexity,
            "min_kbps": bg_min,
            "max_kbps": bg_budget,
            "budget_kbps": bg_budget,
        })

        # ROI regions
        for i, roi in enumerate(sorted_rois):
            complexity = self.compute_region_complexity(frame, roi.bbox)
            alpha = PRIORITY_WEIGHTS.get(roi.priority, 0.1)

            # Boost weight for tracked individuals (temporal consistency)
            if roi.track_id is not None:
                alpha *= 1.3

            roi_area = max(1, (roi.bbox[2] - roi.bbox[0]) * (roi.bbox[3] - roi.bbox[1]))
            frame_area = self.res_w * self.res_h
            area_fraction = roi_area / frame_area

            # Allocate proportional to importance × complexity × area
            importance = alpha * complexity * (0.5 + 0.5 * area_fraction)
            per_roi_budget = roi_budget * importance / max(
                sum(
                    PRIORITY_WEIGHTS.get(r.priority, 0.1)
                    * self.compute_region_complexity(frame, r.bbox)
                    * (0.5 + 0.5 * ((r.bbox[2]-r.bbox[0])*(r.bbox[3]-r.bbox[1]))/frame_area)
                    for r in sorted_rois
                ),
                1e-6,
            )
            per_roi_budget = max(self.min_roi_kbps, min(per_roi_budget, roi_budget * 0.6))

            regions.append({
                "id": i,
                "alpha": alpha,
                "beta": complexity,
                "min_kbps": self.min_roi_kbps,
                "max_kbps": roi_budget,
                "budget_kbps": per_roi_budget,
                "bbox": roi.bbox,
                "priority": roi.priority,
                "track_id": roi.track_id,
            })

        return {
            "regions": regions,
            "roi_budget": roi_budget,
            "bg_budget": bg_budget,
        }

    def lagrangian_cost(
        self, region: Dict, rate_kbps: float, lam: float
    ) -> float:
        """
        Compute Lagrangian cost: D(rate) + λ * rate

        D_i(R) = α_i · β_i · exp(-R / (β_i · K)) + (1 - α_i) · D_floor
        """
        alpha = region["alpha"]
        beta = region["beta"]
        K = 50.0  # scale constant

        # Distortion model
        distortion = alpha * beta * math.exp(-rate_kbps / (beta * K + 1e-6))
        distortion += (1 - alpha) * 5.0  # irreducible floor

        return distortion + lam * rate_kbps

    def optimal_rate(
        self, region: Dict, lam: float
    ) -> float:
        """
        Find optimal rate for a region given λ (Lagrangian multiplier).
        dD/dR = -α·β·exp(-R/(β·K)) / (β·K) + λ = 0
        → R* = β·K · ln(α / (λ · β · K))
        """
        alpha = region["alpha"]
        beta = region["beta"]
        K = 50.0

        inner = alpha / (lam * beta * K + 1e-12)
        if inner <= 1.0:
            return region["min_kbps"]

        rate = beta * K * math.log(inner)
        return max(region["min_kbps"], min(rate, region.get("max_kbps", self.total_budget)))

    def find_lambda(
        self, model: Dict, target_kbps: float, tol: float = 1.0
    ) -> float:
        """
        Binary search for λ such that Σ R_i(λ) ≈ target_kbps.
        """
        lam_lo, lam_hi = 1e-6, 100.0

        for _ in range(50):  # max iterations
            lam_mid = (lam_lo + lam_hi) / 2.0
            total_rate = 0.0
            for region in model["regions"]:
                r = self.optimal_rate(region, lam_mid)
                total_rate += r

            if abs(total_rate - target_kbps) < tol:
                return lam_mid
            elif total_rate > target_kbps:
                lam_lo = lam_mid  # need higher λ to reduce rate
            else:
                lam_hi = lam_mid  # need lower λ to increase rate

        return (lam_lo + lam_hi) / 2.0

    def allocate(
        self,
        rois: List[RegionOfInterest],
        frame: np.ndarray,
        risk_state: str = "normal",
    ) -> Dict:
        """
        Compute optimal rate allocation for the current frame.

        Returns dict with bg quality/scale and per-ROI quality targets.
        """
        if frame is None or frame.size == 0:
            return self._default_allocation(risk_state)

        model = self.build_rd_model(rois, frame, risk_state)

        # Find optimal λ
        lam = self.find_lambda(model, self.total_budget)

        # Compute optimal rates
        allocations = []
        total_assigned = 0.0

        for region in model["regions"]:
            rate = self.optimal_rate(region, lam)
            total_assigned += rate

            # Convert rate to quality parameter (0-100 scale)
            # Higher rate → higher quality
            if region["id"] == "bg":
                bg_quality = self._rate_to_quality(rate, region["budget_kbps"])
                bg_scale = self._rate_to_scale(rate, region["budget_kbps"])
                bg_target = rate
            else:
                roi_quality = self._rate_to_quality(rate, region["budget_kbps"])
                allocations.append({
                    "id": region["id"],
                    "bbox": region.get("bbox"),
                    "priority": region.get("priority", "medium"),
                    "track_id": region.get("track_id"),
                    "target_kbps": round(rate, 1),
                    "quality": int(roi_quality),
                    "alpha": region["alpha"],
                    "complexity": region["beta"],
                })

        # Compute efficiency: how well budget is utilized
        efficiency = min(total_assigned / self.total_budget, 1.0) if self.total_budget > 0 else 0

        return {
            "bg_quality": int(bg_quality),
            "bg_scale": round(bg_scale, 2),
            "bg_target_kbps": round(bg_target, 1),
            "rois": allocations,
            "lambda": round(lam, 6),
            "total_kbps": round(total_assigned, 1),
            "budget_kbps": self.total_budget,
            "efficiency": round(efficiency, 3),
            "risk_state": risk_state,
            "num_rois": len(rois),
        }

    def _rate_to_quality(self, rate_kbps: float, budget_kbps: float) -> float:
        """Convert target rate to JPEG quality (0-100)."""
        ratio = min(rate_kbps / max(budget_kbps, 1), 1.0)
        # Non-linear mapping: low budget → aggressive compression
        return 5 + 95 * (ratio ** 0.6)

    def _rate_to_scale(self, rate_kbps: float, budget_kbps: float) -> float:
        """Convert target rate to background scale factor (0.1-1.0)."""
        ratio = min(rate_kbps / max(budget_kbps, 1), 1.0)
        return 0.1 + 0.9 * (ratio ** 0.4)

    def _default_allocation(self, risk_state: str) -> Dict:
        """Fallback allocation when frame is unavailable."""
        budget_ratio = STATE_BUDGET_RATIO.get(risk_state, STATE_BUDGET_RATIO["normal"])
        return {
            "bg_quality": 20 if risk_state == "normal" else 50,
            "bg_scale": 0.5 if risk_state == "normal" else 0.8,
            "bg_target_kbps": round(self.total_budget * budget_ratio["bg_fraction"], 1),
            "rois": [],
            "lambda": 0.0,
            "total_kbps": round(self.total_budget * 0.9, 1),
            "budget_kbps": self.total_budget,
            "efficiency": 0.9,
            "risk_state": risk_state,
            "num_rois": 0,
        }


# ── Risk Metric Comparison ────────────────────────────────────────────────

class RiskMetricComparator:
    """
    Compare different risk/scoring strategies for compression prioritization.
    """

    @staticmethod
    def heuristic_risk(rois, motion_frac, hour, scene_change):
        """Your current heuristic risk score."""
        score = 0.0
        for r in rois:
            p = r.get("priority", "low")
            if p == "high":
                score += 0.30
            elif p == "medium":
                score += 0.10
            else:
                score += 0.03
        score += min(float(motion_frac), 1.0) * 0.25
        if hour < 6 or hour >= 22:
            score += 0.20
        score += min(float(scene_change), 1.0) * 0.15
        score += min(len(rois) * 0.04, 0.15)
        return min(score, 1.0)

    @staticmethod
    def frame_difference_risk(prev_frame, curr_frame):
        """Baseline: pure frame-difference based risk."""
        if prev_frame is None or curr_frame is None:
            return 0.0
        if prev_frame.shape != curr_frame.shape:
            return 0.0
        diff = cv2.absdiff(prev_frame, curr_frame)
        return float(np.mean(diff)) / 128.0

    @staticmethod
    def entropy_risk(frame, rois):
        """Baseline: entropy-based risk (information content)."""
        if frame is None or frame.size == 0:
            return 0.0
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).flatten()
        hist = hist / hist.sum()
        entropy = -np.sum(hist[hist > 0] * np.log2(hist[hist > 0]))
        # Normalize to [0, 1] (max entropy = 8 bits)
        base_risk = entropy / 8.0

        # Boost for detected objects
        obj_boost = len(rois) * 0.05
        return min(base_risk + obj_boost, 1.0)

    @staticmethod
    def optical_flow_risk(prev_gray, curr_gray):
        """Baseline: optical flow magnitude based risk."""
        if prev_gray is None or curr_gray is None:
            return 0.0
        if prev_gray.shape != curr_gray.shape:
            return 0.0
        flow = cv2.calcOpticalFlowFarneback(
            prev_gray, curr_gray, None,
            pyr_scale=0.5, levels=3, winsize=15,
            iterations=3, poly_n=5, poly_sigma=1.2, flags=0
        )
        mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
        return min(float(np.mean(mag)) / 20.0, 1.0)

    @staticmethod
    def composite_risk(rois, motion_frac, hour, scene_change, prev_gray, curr_gray):
        """
        Weighted fusion of all risk signals.
        This is the proposed method.
        """
        heuristic = RiskMetricComparator.heuristic_risk(rois, motion_frac, hour, scene_change)
        flow_risk = RiskMetricComparator.optical_flow_risk(prev_gray, curr_gray)

        # Adaptive weighting: if flow detects motion, weight it higher
        if flow_risk > 0.3:
            return 0.5 * heuristic + 0.5 * flow_risk
        else:
            return 0.7 * heuristic + 0.3 * flow_risk

    @staticmethod
    def compare_all_methods(
        frame_curr, frame_prev, rois, motion_frac, hour
    ):
        """Run all risk methods and return comparison dict."""
        prev_gray = (
            cv2.cvtColor(frame_prev, cv2.COLOR_BGR2GRAY)
            if frame_prev is not None and len(frame_prev.shape) == 3
            else frame_prev
        )
        curr_gray = (
            cv2.cvtColor(frame_curr, cv2.COLOR_BGR2GRAY)
            if frame_curr is not None and len(frame_curr.shape) == 3
            else frame_curr
        )

        scene_change = RiskMetricComparator.frame_difference_risk(prev_gray, curr_gray)

        return {
            "proposed": RiskMetricComparator.composite_risk(
                rois, motion_frac, hour, scene_change, prev_gray, curr_gray
            ),
            "heuristic": RiskMetricComparator.heuristic_risk(
                rois, motion_frac, hour, scene_change
            ),
            "frame_diff": RiskMetricComparator.frame_difference_risk(prev_gray, curr_gray),
            "entropy": RiskMetricComparator.entropy_risk(frame_curr, rois),
            "optical_flow": RiskMetricComparator.optical_flow_risk(prev_gray, curr_gray),
        }
