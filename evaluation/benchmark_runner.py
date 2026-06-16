import os
import sys
import subprocess
import json
import pandas as pd
import time

def run_benchmark(config_path, test_video):
    print(f"Running benchmark with config: {config_path} on {test_video}")
    # Mocking actual benchmark since this requires full edge-node runtime
    # In a real thesis scenario, this would spin up capture_stream with the test video and measure output size
    
    # Mock results
    time.sleep(1)
    results = {
        "config": os.path.basename(config_path),
        "video": os.path.basename(test_video),
        "vmaf": 85.0 + (5 if 'high_quality' in config_path else -10),
        "bitrate_kbps": 2000 if 'high_quality' in config_path else 500,
        "latency_ms": 120,
        "fps_achieved": 30 if 'high_quality' in config_path else 15
    }
    return results

if __name__ == "__main__":
    configs = ["../configs/ultra_low_bandwidth.yaml", "../configs/balanced.yaml", "../configs/high_quality.yaml"]
    results = []
    for cfg in configs:
        res = run_benchmark(cfg, "sample_test_video.mp4")
        results.append(res)
    
    df = pd.DataFrame(results)
    out_csv = "benchmark_results.csv"
    df.to_csv(out_csv, index=False)
    print(f"Saved benchmark results to {out_csv}")
