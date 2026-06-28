#!/usr/bin/env python3
"""Apple-silicon distributed AllReduce benchmark (counterpart to the NVIDIA nccl-probe).

Run UNDER `mlx.launch --hosts <peer-ips>` so every peer in a tensor-parallel group
participates. Performs N `all_sum` iterations over a fixed payload, then — on rank 0
only — writes Prometheus textfile metrics (latency + busbw) that the always-on
mlx_fabric_exporter serves to the OTEL agent.

busbw uses the standard ring all-reduce formula:
    algbw = bytes / time
    busbw = algbw * 2 * (world_size - 1) / world_size
"""
import argparse
import os
import statistics
import tempfile
import time


def _emit(path, lines):
    """Atomic write of the Prometheus textfile."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size-mb", type=int, default=256)
    ap.add_argument("--iters", type=int, default=20)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--tp-group", default="")
    ap.add_argument("--out", default="/var/lib/hydra/textfile/mlx_allreduce.prom")
    args = ap.parse_args()

    import mlx.core as mx

    group = mx.distributed.init()
    rank, size = group.rank(), group.size()

    dtype_bytes = 2  # float16
    n = max(1, (args.size_mb * 1024 * 1024) // dtype_bytes)
    x = mx.ones((n,), dtype=mx.float16)

    for _ in range(args.warmup):
        mx.eval(mx.distributed.all_sum(x))

    samples = []
    for _ in range(args.iters):
        t0 = time.perf_counter()
        mx.eval(mx.distributed.all_sum(x))
        samples.append(time.perf_counter() - t0)

    if rank != 0:
        return

    samples.sort()
    payload_bytes = n * dtype_bytes
    p50 = statistics.median(samples)
    p99 = samples[min(len(samples) - 1, int(0.99 * len(samples)))]
    avg = statistics.fmean(samples)

    def busbw_gbps(t):
        algbw = payload_bytes / t  # bytes/sec
        bus = algbw * 2 * (size - 1) / size
        return bus / 1e9  # GB/s

    g = args.tp_group
    lbl = f'tp_group="{g}",world_size="{size}"'
    lines = [
        "# HELP mlx_allreduce_latency_seconds AllReduce wall time per iteration.",
        "# TYPE mlx_allreduce_latency_seconds gauge",
        f'mlx_allreduce_latency_seconds{{{lbl},quantile="0.5"}} {p50:.6f}',
        f'mlx_allreduce_latency_seconds{{{lbl},quantile="0.99"}} {p99:.6f}',
        f'mlx_allreduce_latency_seconds{{{lbl},quantile="avg"}} {avg:.6f}',
        "# HELP mlx_allreduce_busbw_gbytes_per_sec Bus bandwidth (ring all-reduce).",
        "# TYPE mlx_allreduce_busbw_gbytes_per_sec gauge",
        f'mlx_allreduce_busbw_gbytes_per_sec{{{lbl},quantile="0.5"}} {busbw_gbps(p50):.4f}',
        f'mlx_allreduce_busbw_gbytes_per_sec{{{lbl},quantile="0.99"}} {busbw_gbps(p99):.4f}',
        "# HELP mlx_allreduce_payload_bytes Payload size per iteration.",
        "# TYPE mlx_allreduce_payload_bytes gauge",
        f"mlx_allreduce_payload_bytes{{{lbl}}} {payload_bytes}",
        "# HELP mlx_allreduce_last_success_timestamp_seconds Unix time of last run.",
        "# TYPE mlx_allreduce_last_success_timestamp_seconds gauge",
        f"mlx_allreduce_last_success_timestamp_seconds{{{lbl}}} {time.time():.0f}",
    ]
    _emit(args.out, lines)


if __name__ == "__main__":
    main()
