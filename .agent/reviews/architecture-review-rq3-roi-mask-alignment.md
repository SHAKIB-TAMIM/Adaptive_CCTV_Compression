# Architecture Review — RQ3 ROI Mask Alignment
**Date:** 2026-10-24
**Reviewer:** Agent

## Verdict
Approved with conditions.

## 🔴 Blockers
None.

## 🟡 Warnings
### 1. Expanded input restoration is not coded ROI protection
**Checklist item:** Failure Modes / Data & State
**Issue:** The proposed minimal change restores the same 8-pixel source-space margin that the metric calls ROI, but x265 still performs a single global ABR encode. The encoder can quantize those samples, so restoration does not guarantee a minimum decoded ROI quality.
**Recommendation:** Report box-core, guard-ring, and dilated-region quality separately. Treat the patch as metric/protection-mask alignment, not as proof of ROI-QP control. If core quality still needs dedicated protection, test actual codec ROI QP maps or separate streams as a distinct algorithm.

### 2. Fixed risk scale does not depend on target bitrate
**Checklist item:** Data & State
**Issue:** The measured risk heuristic selected the 0.35 scale for 223/235 frames, 0.60 for 11, and 1.00 for 1; it uses the same rule at every bitrate.
**Recommendation:** First isolate the confirmed mask mismatch. Do not tune scales to target rates or infer allocator benefits from this correction.

## 🔵 Notes
### 1. Preserve the v4 evidence
The targeted experiment must use a new output directory and the existing v4 files remain read-only. The addendum and original thesis PDF are outside the implementation scope.

### 2. Reuse the mask geometry
Use one clipped source-coordinate box expansion rule for both YUV restoration and metric masks, and cover its odd/even bounds and image-edge behavior with unit tests.

## Summary
The surgical change is local to the compositor and evaluation diagnostics and has no runtime service or dependency impact. It should restore the pixels currently included by the dilated metric and expose core-versus-margin outcomes; the single global x265 rate controller remains a separate algorithmic limitation. Proceed with focused tests and a two-target primary-clip experiment only.
