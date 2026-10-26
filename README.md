# cctv

## Reproducible RQ3 evaluation

Run the focused I420/ROI metric tests before encoding:

```bash
python3 -m unittest discover -s evaluation -p 'test_phase4_sota.py' -v
```

Use a new output directory for each run; the CLI writes run summaries and CSVs
to that directory. This example uses the same target rates for every primary
method:

```bash
python3 evaluation/phase4_sota.py \
  --output experiments/phase4_sota_run/primary \
  --targets 300,600,1200,1800,2400,3600 \
  --clips primary \
  --methods uniform,roi_qp,fg_bg,proposed \
  --skip-det

python3 evaluation/phase4_sota.py \
  --output experiments/phase4_sota_run/control \
  --clips control \
  --methods uniform,proposed \
  --skip-det
```

`--skip-det` skips only encoded-output detector-consistency scoring; source
frames are still detected once to build the cached ROI masks. The output
validates video-only HEVC Main, `yuv420p`, BT.709 limited-range metadata, frame
rate, resolution, and decoded frame count. `actual_kbps` is MP4 file size
(including container overhead) divided by source duration. See `runs.csv`,
`failures.csv`, `bd_diagnostics.json`, `provenance.json`, and the RD plots in
the selected output directory.
The x265 values are ABR bitrate requests with shared VBV constraints, not
strict CBR rates; use `actual_kbps` for rate-quality comparisons.

For this RQ3 comparison, Proposed uses the documented fixed risk-state
background scales (0.35/0.60/1.00), not `evaluation/rate_allocator.py`.
`ROI-QP` is the existing method label for background downscaling plus source
ROI restoration before uniform x265 encoding; it does not use encoder
per-region QP controls.

The evaluator dilates cached source boxes by 8 pixels for its protected-region
ROI-PSNR/SSIM. ROI methods restore that same clipped expanded region in the
Y/U/V input planes before encoding. `runs.csv` also reports strict
`roi_core_*` metrics over the detector boxes and `roi_guard_*` metrics over
the added 8-pixel ring, so boundary quality is visible separately. This
pre-encode restoration does not guarantee lossless ROI output: x265 still uses
one global ABR rate controller.
