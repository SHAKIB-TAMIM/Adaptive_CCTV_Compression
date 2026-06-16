#!/usr/bin/env python3
import sys
import os

print("Running Automated Tests...")

# Example test: check if configs exist
config_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "configs"))
required_configs = ["balanced.yaml", "high_quality.yaml", "privacy_mode.yaml", "ultra_low_bandwidth.yaml"]

all_passed = True
for c in required_configs:
    path = os.path.join(config_dir, c)
    if not os.path.exists(path):
        print(f"[FAIL] Missing config: {c}")
        all_passed = False
    else:
        print(f"[PASS] Found config: {c}")

if not all_passed:
    sys.exit(1)

print("\nAll basic tests passed!")
sys.exit(0)
