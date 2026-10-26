---
type: bug
topic: RQ3 ROI metric and compositor alignment
date: 2026-10-25
tags: [rq3, roi, metrics, yuv420p, x265]
---

## Summary
V4 measured an 8-pixel guard ring that its ROI compositor did not restore. V5 aligns restoration with the clipped, dilated metric mask and reports core/guard quality separately. Across the full six-rate primary ladder, Proposed gains 0.393–0.645 dB ROI-PSNR over Uniform and has a valid -8.62% ROI BD-rate, while losing 0.768–5.441 dB full-frame PSNR and 1.268–6.615 dB background PSNR.

## Context
The v4 RQ3 results appeared to show Proposed losing ROI-PSNR despite correct planar I420 restoration. The analysis traced input frames, cached detector boxes, compositor masks, and metric masks. This led to a targeted fix, two matched-rate checks, and then a full v4-matched v5 ladder.

## Decision / Finding
The frame path is: one YOLOv8n detection pass on resized source frames -> boxes scaled to source coordinates and cached -> source decoded as planar yuv420p -> each Y/U/V plane background-resized (area downsample, linear upsample) -> source samples restored -> one global libx265 Main/yuv420p encode -> decoded frames compared to the source using the same cached boxes. Detector coordinates map back exactly at 2x for the primary clip. All methods share frame indices and ROI cache; no coordinate or box-source mismatch was found.

In v4, the compositor restored exact detector boxes, while `eval_pair()` measured their union dilated by 8 pixels. At 600 kbps, v4 Proposed's strict core was 35.039 vs 34.447 dB and guard ring 34.427 vs 38.616 dB, leaving combined dilated ROI nearly tied at 34.738 vs 34.730 dB. This confirmed that the metric included blurred pixels outside the restored core.

V5 restores source Y samples across the same clipped, 8-pixel-dilated union used by the ROI metric; U/V restoration covers the necessary half-resolution 2x2 chroma cells. Regression tests ensure no Y writes escape the intended mask and no U/V writes escape its covering cells. V5 separately reports strict box-core and guard-ring quality. The original source caches were copied byte-for-byte from v4.

The complete v5 common ladder covered four methods at six primary nominal targets (300, 600, 1200, 1800, 2400, 3600 kbps) and four methods at two no-ROI control targets (300, 1200 kbps): 32 outputs, zero encode/protocol/metric failures. All passed independent ffprobe checks for HEVC Main, yuv420p, BT.709 limited range, expected dimensions, frame rate and decoded frame count, and video-only streams. The exact evaluation script SHA-256 is `94e4d184234e0787094b5e28a131d4642880d907eebc66d23f19e0e381492c2d`.

At each primary target, Proposed ROI-PSNR gains over Uniform were +0.393, +0.616, +0.641, +0.645, +0.590, and +0.481 dB. Proposed's actual rates were 1.25–3.64% higher at the same nominal targets. The ROI BD-rate gate passed: 5 points from each curve in an 11.759 dB shared interval, fit RMSE 0.0009/0.0025, interpolation only. `evaluation/validate_bd.py` independently reproduced -8.62% for Proposed and -8.14% for ROI-QP. Under the project's sign convention, negative means fewer bits at equivalent ROI-PSNR. Global-PSNR BD-rate is withheld because only two Uniform points lie inside the shared quality range; the minimum is four.

The paired v4/v5 comparison isolates the correction: Proposed strict-core changes were only -0.046 to +0.053 dB, guard-ring gains were +2.597 to +10.646 dB, and combined ROI gains were +0.204 to +2.909 dB. The scale policy and encoder settings did not change. This supports the mask mismatch as the source of v4's suppressed dilated ROI score. Some re-encoded streams are not bit-identical, so small between-run changes outside the corrected area cannot be attributed solely to the mask.

At the same nominal targets, Proposed global PSNR decreased by 0.768–5.441 dB and background PSNR by 1.268–6.615 dB versus Uniform; losses increased at higher rates. The no-ROI control remained nearly identical between the two methods. Proposed is a fixed risk-state background-scaling heuristic, not `RateAllocator`, encoder ROI-QP, or a Lagrangian allocator. A single global x265 rate controller cannot guarantee regional QP allocation.

## Rationale
The minimal valid correction was to align the protected source region with the declared measured region, without changing the research algorithm. Integrating `RateAllocator` would not by itself ensure that x265 honors regional quality allocation and is not justified by these results. Keep the ROI benefit and the substantial background/full-frame loss visible together.

## Consequences
Do not interpret v4's dilated ROI PSNR as strict detector-box core quality, or source-sample restoration as lossless encoded ROI. V5 shows a repeatable ROI-quality advantage across the tested ladder on one primary sequence, but does not demonstrate whole-frame improvement or generalization to other scenes, detectors, codecs, or settings. The full results are in `experiments/phase4_sota_v5_common_ladder_20261024/`; the RQ3 addendum is `docs/rq3_thesis_addendum.tex`. The original thesis PDF remains untouched.

## References
- `evaluation/phase4_sota.py`
- `evaluation/test_phase4_sota.py`
- `evaluation/validate_bd.py`
- `experiments/phase4_sota_v4_final_20261024/runs.csv`
- `experiments/phase4_sota_v5_common_ladder_20261024/runs.csv`
- `experiments/phase4_sota_v5_common_ladder_20261024/provenance.json`
- `experiments/phase4_sota_v5_common_ladder_20261024/independent_ffprobe_validation.json`
- `experiments/phase4_sota_v5_common_ladder_20261024/bd_diagnostics.json`
- `experiments/phase4_sota_v5_common_ladder_20261024/v4_v5_paired_comparison.csv`
- `experiments/phase4_sota_v5_common_ladder_20261024/phase4_sota_v5_used.py`
- `.agent/CURRENT_TASK.md`
- `.agent/reviews/architecture-review-rq3-roi-mask-alignment.md`
