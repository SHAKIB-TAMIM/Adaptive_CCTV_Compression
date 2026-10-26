# Architecture Review — RQ3 evaluation continuation
**Date:** 2026-10-24
**Reviewer:** Copilot

## Verdict
Approved with conditions

## 🔴 Blockers
None for continuing the evaluation.

## 🟡 Warnings
### 1. The current run directory is not a run boundary
**Checklist item:** Data & State — source of truth.
**Issue:** The Phase 4 CLI rewrites shared CSV, summary, and provenance files even when a clip/method subset is selected. The latest control-only run left old primary encodes in the same directory, so directory contents alone can falsely look like one complete experiment.
**Recommendation:** Run the final evaluation in a new, uniquely named output directory and preserve the earlier outputs. Make any future resume conditional on recorded input/protocol provenance.

### 2. The evaluation Proposed path differs from its stated method
**Checklist item:** Data & State — source of truth.
**Issue:** `phase4_sota.py` chooses heuristic scales from risk and additionally relaxes them toward 1.0 based on target bitrate. It does not call `RateAllocator`; only the live camera path does. The bitrate-dependent relaxation is not in the declared fixed 0.35/0.60/1.00 RQ3 method and can predetermine favorable high-rate outcomes.
**Recommendation:** Keep the RQ3 implementation aligned with the documented risk-state heuristic and do not integrate the runtime allocator into this experiment without a separately approved research-design change.

### 3. Encoder validation is incomplete
**Checklist item:** Failure Modes — partial failures.
**Issue:** The current probe helper can return an empty mapping and the caller defaults a missing pixel format to `yuv420p`. It does not enforce codec/profile/color/frame-rate/dimensions/frame count, nor reject a truncated but present output.
**Recommendation:** Require successful ffprobe results and validate all protocol fields plus expected frame count before including a run in CSVs or BD-rate.

### 4. Main thesis PDF has no matching editable source
**Checklist item:** Operability — rollback plan.
**Issue:** `Final_Thesis_Paper_1.pdf` is the current 79-page thesis and is untracked. Its extracted text is available, but the workspace TeX files are separate, older 6–8 page manuscripts and do not match this PDF.
**Recommendation:** Preserve the original PDF. Update only a confirmed matching thesis source; otherwise prepare a clearly marked RQ3 replacement/addendum and do not represent the PDF as updated.

## 🔵 Notes
### 1. YUV420P approach is testable in isolation
**Checklist item:** Coupling & Cohesion — module testability.
**Issue:** YUV conversion and ROI plane-copying are functions in `phase4_sota.py`; expected source dimensions are even, but current helpers do not validate buffer length, array shape, or odd dimensions.
**Recommendation:** Add focused unit tests for supported dimensions, I420 layout, odd/even ROI boundaries, clipped/empty boxes, and frame reconstruction. Describe YUV↔BGR conversion as lossy processing, not lossless.

### 2. Cached box presence is not background-area evidence
**Checklist item:** Observability — metrics.
**Issue:** The current cache reports ROIs in 234/235 primary frames, but this does not imply the frame is mostly ROI.
**Recommendation:** Report measured union coverage: 14.56% mean bounding-box area (15.89% after 8-pixel dilation), leaving substantial non-ROI background on average.

## Summary
Proceed with isolated, metadata-validated experiments while preserving existing outputs. Keep the evaluation method a risk-state heuristic, correct the unsubstantiated bitrate-dependent relaxation, and report the mismatch between the RQ3 method and live allocator explicitly. The original thesis PDF must remain untouched until an editable source matching it is identified.
