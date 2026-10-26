---
type: bug
topic: RQ3 HEVC encoding protocol correction
date: 2026-10-24
tags: [rq3, ffmpeg, yuv420p, hevc, roi]
---

## Summary
RQ3 ROI encodes are valid only when raw planar I420 is addressed as separate Y, U, and V planes and every method uses the same explicit HEVC Main, yuv420p, BT.709, video-only output protocol. The corrected v4 pipeline fixes chroma restoration and excludes audio uniformly.

## Context
The earlier raw-BGR-to-libx265 path could select gbrp/HEVC Range Extensions for ROI methods while Uniform used yuv420p/HEVC Main. A later direct-I420 path also indexed chroma as if U and V were row-packed, corrupting ROI reconstruction. Uniform retained source audio while reconstructed paths omitted it.

## Decision / Finding
Use explicit planar I420 views: full-resolution Y followed by half-resolution U and V planes; map ROI bounds to the covering 2x2 chroma cells. Encode all methods as video-only HEVC Main/yuv420p with BT.709 limited-range metadata, consistent resolution/frame rate/GOP/preset, and verify metadata plus decoded frame count with ffprobe. Measure actual bitrate from video-only MP4 file size (including container overhead) divided by source duration. Background resizing and HEVC remain lossy; do not describe the pipeline as lossless.

The v4 experiment directory is `experiments/phase4_sota_v4_final_20261024/`. Its `provenance.json` and `phase4_sota_used.py` preserve the command, exact code snapshot, input hashes, and protocol.

## Rationale
The explicit format/profile lock removes method-dependent encoder input interpretation; correct plane addressing restores ROI samples in their actual I420 coordinates. Uniform audio removal prevents container-size differences unrelated to video. Strict probing prevents malformed or incomplete encodes from entering metrics.

## Consequences
Do not reuse prior results that used BGR/gbrp, incorrect chroma indexing, or mixed audio treatment. RQ3 comparisons must use `actual_kbps`, not nominal ABR requests. The current run skips encoded-output detector consistency, so it provides no recall or consistency result.

## References
- `evaluation/phase4_sota.py`
- `evaluation/test_phase4_sota.py`
- `experiments/phase4_sota_v4_final_20261024/provenance.json`
- `experiments/phase4_sota_v4_final_20261024/bd_diagnostics.json`
