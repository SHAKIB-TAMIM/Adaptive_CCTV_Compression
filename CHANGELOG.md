# Changelog

## [Unreleased]

### Fixed
- RQ3 ROI reconstruction now reads standard planar I420 Y, U, and V planes
  correctly, and all comparison methods produce video-only HEVC Main/yuv420p
  outputs with verified BT.709 metadata.
- Removed the target-bitrate-dependent Proposed scale adjustment; the RQ3
  heuristic now uses the documented fixed risk-state scales.
- ROI PSNR/SSIM now use a union mask so overlapping detections are not counted
  multiple times; invalid or truncated encodes are excluded and recorded.
- ROI-path input restoration now uses the same clipped 8-pixel box expansion
  as the protected-region metric. Results also separate detector-box core and
  guard-ring PSNR/SSIM; the common x265 encode remains lossy and global.

### Documentation
- Updated the RQ3 addendum with the validated full v5 bitrate ladder,
  mask-correction comparison, ROI BD-rate, and background/full-frame trade-offs.
