# Current Task

## Objective
Validate whether the v5 correction aligning ROI restoration with the dilated ROI metric produces a reproducible benefit across the existing RQ3 evaluation setup, without redesigning the algorithm.

## Status
Complete. The full v5 common-ladder evaluation, independent output validation, v4/v5 comparison, BD-rate cross-check, regression tests, and RQ3 addendum update are complete. The original `Final_Thesis_Paper_1.pdf` was not modified.

## Verified v5 experiment
- Output directory: `experiments/phase4_sota_v5_common_ladder_20261024/`
- Command: `python3 evaluation/phase4_sota.py --output experiments/phase4_sota_v5_common_ladder_20261024 --targets 300,600,1200,1800,2400,3600 --clips primary,control --methods uniform,roi_qp,fg_bg,proposed --skip-det`
- 32 valid outputs; zero encode, validation, metric, or detection failures; runtime 11793.7 s.
- Independent ffprobe validation passed all 32 outputs: HEVC Main, yuv420p, BT.709 limited range, expected dimensions/frame rates/frame counts, and video-only streams.
- The full evaluation script SHA-256 is `94e4d184234e0787094b5e28a131d4642880d907eebc66d23f19e0e381492c2d`, and the saved script snapshot matches. Test snapshot SHA-256 is `f434b0c51a57c176c06c6c78f5a68223fc071360ef86910b033ec9a5c807b1cc`.
- The primary and control ROI caches are byte-identical to v4.
- An initial wrong-default launch was stopped and is marked invalid in `experiments/phase4_sota_v5_full_20261024/`; exclude that directory from results.

## Findings
- Across primary nominal targets 300, 600, 1200, 1800, 2400, and 3600 kbps, Proposed's ROI-PSNR gains versus Uniform were +0.393, +0.616, +0.641, +0.645, +0.590, and +0.481 dB. Actual bitrate was 1.25–3.64% higher at the same nominal target.
- The ROI BD-rate gate passed: 5 points per curve in an 11.759 dB common quality range; fit RMSE 0.0009/0.0025; interpolation only. Independent `evaluation/validate_bd.py` reproduced Proposed -8.62% and ROI-QP -8.14%. Negative means fewer bits for equivalent ROI-PSNR under the project's convention.
- Global-PSNR BD-rate is withheld: only 2 Uniform points are in the shared interval, below the four-point gate. FG/BG is also ineligible due to insufficient valid overlap.
- Proposed sacrifices global PSNR by 0.768–5.441 dB and background PSNR by 1.268–6.615 dB versus Uniform at the same nominal targets. Its actual rates are slightly higher. The control has no ROI detections and Uniform/Proposed results are almost identical.
- The paired v4/v5 result isolates the correction: Proposed core PSNR changes only -0.046 to +0.053 dB, guard-ring PSNR rises +2.597 to +10.646 dB, and combined ROI-PSNR rises +0.204 to +2.909 dB. The correction restores the measured guard ring; it does not redesign the heuristic.
- The evaluated Proposed path remains a fixed risk-state background-scaling heuristic, not `RateAllocator` or encoder ROI-QP. Results apply only to the tested primary clip, control, codec, and ladder.

## Current deliverables
- RQ3 addendum source: `docs/rq3_thesis_addendum.tex`
- New v5 addendum PDF: `experiments/phase4_sota_v5_common_ladder_20261024/rq3_thesis_addendum.pdf`
- Addendum PDF SHA-256: `53f349389417e7e2ce97b9d2c273552620208178b2d3bb62a1316edf486025d9`
- Main run measurements/provenance: `experiments/phase4_sota_v5_common_ladder_20261024/runs.csv`, `provenance.json`, `failures.csv`
- Validation and comparisons: `independent_ffprobe_validation.json`, `bd_diagnostics.json`, `v4_v5_paired_comparison.csv`, `v4_rescored_metrics.csv`
- Regression tests: `evaluation/test_phase4_sota.py`
- Project note: `.agent/knowledge/rq3-roi-mask-protection-alignment.md`; index updated at `.agent/knowledge/INDEX.md`

## Validation
- `python3 -m unittest discover -s evaluation -p 'test_phase4_sota.py' -v` — 11 tests passed.
- `python3 evaluation/validate_bd.py --output experiments/phase4_sota_v5_common_ladder_20261024` — passed; invalid global BD-rate remains withheld.
- Independent ffprobe report: 32 checked, 32 valid, zero failures.
- `python3 -m py_compile evaluation/phase4_sota.py evaluation/test_phase4_sota.py experiments/phase4_sota_v5_common_ladder_20261024/compare_v4_v5.py experiments/phase4_sota_v5_common_ladder_20261024/validate_v5_outputs.py` — passed.
- `git diff --check` — passed after all documentation updates.
- `pdflatex` compiled the five-page addendum with no warnings; `pdftotext` confirmed the v5 results and updated conclusion.
- Original thesis PDF was not written; its current SHA-256 is `d54e0e3add11ce562e6b135af7d40f25a4b6324718ec2b4edcd30df3ded8e938`.

## Next
No pending RQ3 evaluation or addendum work. Preserve v4 outputs, the original thesis PDF, and the invalid-run marker.
