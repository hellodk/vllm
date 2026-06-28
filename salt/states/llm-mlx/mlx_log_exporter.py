#!/usr/bin/env python3
"""Tail the MLX error log and expose Metal/GPU failure counters for Prometheus.

Uses substring matching (no regex) against known fatal MLX/Metal signatures so it
stays dependency-light and works with the venv's prometheus_client.
"""
import argparse
import os
import time
from prometheus_client import Counter, start_http_server

METAL_SIGNATURES = (
    "check_error",
    "MetalAllocator",
    "CommandBuffer",
    "Insufficient Memory",
    "MTLCommandBuffer",
    "[METAL]",
)
GPU_SIGNATURES = ("AGXMetal", "GPU command", "eval_gpu")

metal_failures = Counter("mlx_metal_failures_total", "MLX Metal command-buffer failures")
gpu_errors = Counter("mlx_gpu_errors_total", "MLX GPU errors")


def follow(path):
    while not os.path.exists(path):
        time.sleep(2)
    with open(path, "r", errors="ignore") as fh:
        fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if not line:
                time.sleep(1)
                continue
            if any(sig in line for sig in METAL_SIGNATURES):
                metal_failures.inc()
            if any(sig in line for sig in GPU_SIGNATURES):
                gpu_errors.inc()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="/var/log/hydra/mlx-server-error.log")
    ap.add_argument("--port", type=int, default=11502)
    args = ap.parse_args()
    start_http_server(args.port)
    follow(args.log)


if __name__ == "__main__":
    main()
