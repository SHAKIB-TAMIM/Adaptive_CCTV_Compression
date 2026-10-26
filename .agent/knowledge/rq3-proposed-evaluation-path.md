---
type: research
topic: Proposed method evaluated in RQ3
date: 2026-10-24
tags: [rq3, rate-allocation, evaluation, thesis]
---

## Summary
The Proposed method in `evaluation/phase4_sota.py` is a fixed risk-state background-scaling heuristic; it does not invoke `RateAllocator`. The live camera path separately uses the allocator, so RQ3 results must not be presented as validation of Lagrangian rate allocation.

## Context
The thesis describes the deployed system as using Lagrangian-inspired or adaptive rate allocation, while the RQ3 experiment compares a Proposed method against uniform and other ROI baselines. The evaluation code path needed to be checked directly before interpreting those results.

## Decision / Finding
RQ3 Proposed uses risk thresholds 0.30 and 0.65 without hysteresis to choose background scales 0.35, 0.60, or 1.00; with no ROI, scale is 1.00. `evaluation/phase4_sota.py` does not import or call `evaluation/rate_allocator.py`. `edge-node/camera_manager.py` imports `RateAllocator` through `AdaptiveRateAllocator`, but that live path is not the one measured by the controlled RQ3 experiment.

`ROI-QP` is also a historical label: its evaluation implementation downscales the background and restores ROI samples before a uniform x265 encode; it does not set encoder per-region QP.

## Rationale
The experiment must be described according to the code that generated its outputs. Inferring allocator use from the live camera path would conflate two implementations and overstate what the RQ3 comparison establishes.

## Consequences
Report the v4 RQ3 result as evidence about the risk-state heuristic and tested ROI construction only. To evaluate Lagrangian allocation, create a separate locked experiment that actually routes its frames/bit budgets through `RateAllocator`, and state its settings and scope explicitly.

## References
- `evaluation/phase4_sota.py`
- `evaluation/rate_allocator.py`
- `edge-node/camera_manager.py`
- `docs/rq3_thesis_addendum.tex`
