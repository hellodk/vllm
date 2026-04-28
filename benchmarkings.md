# Hydra LLM Benchmark Log

**Cluster**: 40-node Apple Silicon Mac Mini (M1/M3/M4)  
**Scale target**: ≤1,000 internal users  
**Revision**: 1.1 — 2026-04-28  
**Status**: Live — append rows to Section 11 (Run Log) after each run

> **Phase 1 (current)**: 3-machine pilot — Apple M2 8 GB, Apple M3 16 GB, Intel i7-12th Gen + RTX 3050 4 GB (Linux).  
> Results from this phase will determine the 40-node pool hardware direction.

---

## Table of Contents

1. [Purpose & Scope](#1-purpose--scope)
2. [Phase 1 — 3-Machine Pilot](#2-phase-1--3-machine-pilot)
3. [Benchmark Dimensions](#3-benchmark-dimensions)
4. [Metrics Definitions](#4-metrics-definitions)
5. [Tooling & Execution Methodology](#5-tooling--execution-methodology)
6. [Hardware Reference Sheet](#6-hardware-reference-sheet)
7. [Model Catalogue](#7-model-catalogue)
8. [Workload Scenarios & Results](#8-workload-scenarios--results)
   - [8.1 Autocomplete (FastPool)](#81-autocomplete-fastpool)
   - [8.2 Chat (ReasonPool)](#82-chat-reasonpool)
   - [8.3 Reasoning / Long-context](#83-reasoning--long-context)
   - [8.4 Vision (VisionPool)](#84-vision-visionpool)
   - [8.5 Embedding (EmbedPool)](#85-embedding-embedpool)
9. [Concurrent User Capacity Matrix](#9-concurrent-user-capacity-matrix)
10. [Cloud API Reference Baselines](#10-cloud-api-reference-baselines)
11. [Quantization Impact Analysis](#11-quantization-impact-analysis)
12. [Run Log](#12-run-log)

---

## 1. Purpose & Scope

This document is the authoritative benchmark record for the Hydra LLM farm. Its goals are:

1. **Capacity planning** — determine how many concurrent users each node pool can serve at acceptable latency.
2. **Runtime selection** — compare inference runtimes (llama.cpp, Ollama, vLLM-MLX, MLX-LM) head-to-head on identical hardware.
3. **Hardware comparison** — characterise throughput differences across GPU providers and chip generations (Apple M1/M3/M4, NVIDIA GB10, AMD ROCm).
4. **Model sizing** — identify the largest model that fits within the 13.4 GB usable RAM budget per node at each quantization level.
5. **Cloud reference** — place on-prem results in context against published cloud-API latency/throughput numbers.

### Out of scope

- Fine-tuning or training performance
- Multi-node tensor-parallel inference (no NCCL/NVLink on Apple Silicon)
- Model quality / accuracy evaluation (see `opik-observability/experiments/` for that)

### How to use this document

- **Operators**: Read Section 8 to answer "can the cluster handle X concurrent users?"
- **SREs**: Read Section 7 to set Grafana alert thresholds per pool.
- **Engineers adding a new model**: Run the standard suite (Section 4.3), append a row to Section 11, and add results to the relevant Section 7 table.

---

---

## 2. Phase 1 — 3-Machine Pilot

Before committing the 40-node pool to a single hardware architecture, we benchmark three representative machines that cover every relevant GPU provider family. Results here directly drive the pool hardware decision.

### 2.1 Test machines

| ID | Machine | Chip / CPU | GPU Provider | GPU | RAM | OS | Runtime(s) |
|----|---------|-----------|-------------|-----|-----|----|-----------|
| `p1-m2-8g` | Apple Mac (M2) | Apple M2 | Apple Metal / MLX | 8-core GPU | 8 GB unified | macOS | llama.cpp · Ollama |
| `p1-m3-16g` | Apple Mac Mini (M3) | Apple M3 | Apple Metal / MLX | 10-core GPU | 16 GB unified | macOS | llama.cpp · Ollama · vLLM-MLX |
| `p1-i7-rtx3050` | Linux workstation | Intel i7 12th Gen | NVIDIA CUDA | RTX 3050 4 GB VRAM | 64 GB DDR5 (system) | Linux | llama.cpp · Ollama |

### 2.2 Memory constraints per machine

| Machine | GPU VRAM | System RAM | Max model (GPU-only) | Max model (CPU offload) | Notes |
|---------|----------|-----------|---------------------|------------------------|-------|
| `p1-m2-8g` | unified 8 GB | — | ~3.6 GB (60% rule) | N/A (unified) | KV cache competes with weights |
| `p1-m3-16g` | unified 16 GB | — | ~9.6 GB (60% rule) | N/A (unified) | 14B Q4 fits; 20B tight |
| `p1-i7-rtx3050` | **4 GB VRAM** | **64 GB DDR5** | ~3.5 GB on GPU | **~55 GB available for CPU layers** | Can run 70B Q4 fully via CPU offload |

> **RTX 3050 + 64 GB DDR5 note**: 4 GB VRAM limits full-GPU inference to ~3B models. However, with `--n-gpu-layers N` in llama.cpp, the GPU handles the first N transformer layers (fast) and the remaining layers run on CPU system RAM (slower but large capacity). With 64 GB DDR5, models up to 70B Q4_K_M (~40 GB) can run via split CPU+GPU. Benchmark the GPU-layer sweep to find the optimal ngl value for 7B and 14B models. Also benchmark pure CPU-only path on 64 GB DDR5 as a baseline — i7 12th Gen has strong AVX-512 throughput.

### 2.3 Model fit matrix — which models are viable per machine

| Model | Quant | Size | `p1-m2-8g` | `p1-m3-16g` | `p1-i7-rtx3050` |
|-------|-------|------|-----------|------------|-----------------|
| Qwen 2.5 1.5B | Q4_K_M | ~1.0 GB | ✓ fits | ✓ fits | ✓ full GPU (VRAM) |
| Llama 3.2 3B | Q4_K_M | ~2.0 GB | ✓ fits | ✓ fits | ✓ full GPU (VRAM) |
| Mistral 7B v0.3 | Q4_K_M | ~4.1 GB | ✓ fits | ✓ fits | ⚠ partial GPU + CPU offload |
| Qwen 2.5 7B | Q4_K_M | ~4.7 GB | ⚠ tight (small KV) | ✓ fits | ⚠ partial GPU + CPU offload |
| Llama 3 8B | Q4_K_M | ~4.9 GB | ⚠ tight (small KV) | ✓ fits | ⚠ partial GPU + CPU offload |
| Qwen 2.5 14B | Q4_K_M | ~8.9 GB | ✗ OOM | ✓ fits (tight) | ✓ CPU offload via 64 GB DDR5 |
| Phi-4 14B | Q4_K_M | ~8.4 GB | ✗ OOM | ✓ fits (tight) | ✓ CPU offload via 64 GB DDR5 |
| Qwen 2.5 32B | Q4_K_M | ~20 GB | ✗ OOM | ✗ OOM | ✓ CPU offload via 64 GB DDR5 |
| Llama 3 70B | Q4_K_M | ~40 GB | ✗ OOM | ✗ OOM | ✓ CPU offload via 64 GB DDR5 |
| BGE-M3 (embed) | F16 | ~1.1 GB | ✓ fits | ✓ fits | ✓ full GPU (VRAM) |
| Whisper large-v3 | F16 | ~3.0 GB | ✓ fits | ✓ fits | ✓ full GPU (VRAM) |

### 2.4 Phase 1 benchmark matrix (what to run)

For each machine, run the following combinations in order. All runs use `llama-bench` + `llama-batched-bench` first, then an Ollama concurrent-user load test.

| Priority | Machine | Model | Runtime | Purpose |
|---------|---------|-------|---------|---------|
| 1 | All 3 | Llama 3.2 3B Q4_K_M | llama.cpp | Baseline — fits all machines cleanly |
| 2 | All 3 | Llama 3 8B Q4_K_M | llama.cpp | Primary fleet model candidate |
| 3 | All 3 | Qwen 2.5 7B Q4_K_M | llama.cpp | Alternative fleet model candidate |
| 4 | `p1-m3-16g` | Qwen 2.5 14B Q4_K_M | llama.cpp | Upper bound for M3 16 GB |
| 5 | `p1-i7-rtx3050` | Llama 3 8B Q4_K_M | llama.cpp | ngl sweep: 0, 10, 20, 33 layers on GPU |
| 6 | `p1-i7-rtx3050` | Qwen 2.5 14B Q4_K_M | llama.cpp | CPU offload via 64 GB DDR5 |
| 7 | `p1-i7-rtx3050` | Llama 3 70B Q4_K_M | llama.cpp | Large model CPU offload ceiling test |
| 8 | All 3 | Llama 3 8B Q4_K_M | Ollama | Runtime comparison vs llama.cpp |
| 9 | `p1-m3-16g` | Llama 3 8B Q4_K_M | vLLM-MLX | vLLM-MLX vs llama.cpp on same hardware |
| 10 | All 3 | BGE-M3 | llama.cpp | Embedding throughput |

### 2.5 Decision framework — how results map to the 40-node pool

After Phase 1 results are in, apply this decision tree:

```
Is M3 16 GB TG t/s on 8B Q4_K_M ≥ 25 tok/s?
├── YES → Apple Silicon M3/M4 Mac Mini is viable for FastPool
│         Is vLLM-MLX within 20% of llama.cpp throughput?
│         ├── YES → Use vLLM-MLX (continuous batching, PagedAttention)
│         └── NO  → Use Ollama or llama.cpp-server for FastPool
│
└── NO  → Apple Silicon insufficient; evaluate NVIDIA path
          Is RTX 3050 partial-GPU Llama 3 8B TG t/s ≥ 30 tok/s?
          ├── YES → NVIDIA RTX series viable; evaluate RTX 4090 / A4000
          └── NO  → CPU-offload path too slow; require full-GPU VRAM
                    (e.g., RTX 4090 24 GB, A10G 24 GB, or H100)

M2 8 GB result:
  If TG t/s ≥ 15 tok/s (3B model): M2 8 GB usable for EmbedPool/SpeechPool only
  If TG t/s < 15 tok/s:            M2 8 GB too slow; minimum spec = M3 16 GB
```

### 2.6 Phase 1 results summary (fill in after runs)

| Machine | Model | Runtime | TG t/s | TTFT p50 (B=1) | Max concurrent @ SLA | Decision signal |
|---------|-------|---------|--------|----------------|---------------------|-----------------|
| `p1-m2-8g` | 3B Q4_K_M | llama.cpp | — | — | — | Pending |
| `p1-m2-8g` | 8B Q4_K_M | llama.cpp | — | — | — | Pending |
| `p1-m2-8g` | 8B Q4_K_M | Ollama | — | — | — | Pending |
| `p1-m3-16g` | 8B Q4_K_M | llama.cpp | — | — | — | Pending |
| `p1-m3-16g` | 8B Q4_K_M | Ollama | — | — | — | Pending |
| `p1-m3-16g` | 8B Q4_K_M | vLLM-MLX | — | — | — | Pending |
| `p1-m3-16g` | 14B Q4_K_M | llama.cpp | — | — | — | Pending |
| `p1-i7-rtx3050` | 8B Q4_K_M | llama.cpp (CPU only, ngl=0) | — | — | — | Pending |
| `p1-i7-rtx3050` | 8B Q4_K_M | llama.cpp (ngl=20) | — | — | — | Pending |
| `p1-i7-rtx3050` | 8B Q4_K_M | llama.cpp (ngl=33) | — | — | — | Pending |
| `p1-i7-rtx3050` | 8B Q4_K_M | Ollama | — | — | — | Pending |
| `p1-i7-rtx3050` | 14B Q4_K_M | llama.cpp (CPU offload, 64 GB) | — | — | — | Pending |
| `p1-i7-rtx3050` | 70B Q4_K_M | llama.cpp (CPU offload, 64 GB) | — | — | — | Pending |

---

## 3. Benchmark Dimensions

Every benchmark run is characterised by the following independent variables:

| Dimension | Values tested |
|-----------|---------------|
| **Hardware** | Apple M1 Mac Mini · Apple M3 Mac Mini · Apple M4 Mac Mini · Mac M2 Ultra · NVIDIA DGX Spark (GB10) · AMD ROCm (planned) · CPU-only (planned) |
| **GPU provider** | Apple (Metal/MLX) · NVIDIA (CUDA) · AMD (ROCm) · None (CPU) |
| **Inference runtime** | llama.cpp · Ollama · vLLM-MLX · MLX-LM |
| **LLM provider / API layer** | Self-hosted (above runtimes) · Cloud ref: OpenAI · Cloud ref: Anthropic · Cloud ref: Groq · Cloud ref: Together AI |
| **Model family** | Llama 3 · Qwen 2.5 · Mistral / Mixtral · Phi-4 · gpt-oss (Meta OSS) · Whisper (speech) · BGE-M3 (embed) |
| **Model size** | 1.5B · 3B · 7B / 8B · 14B · 20B · 32B · 70B · 120B |
| **Quantization** | F16 · Q8_0 · Q4_K_M · Q4_0 · Q3_K_M · MXFP4 |
| **Workload type** | Autocomplete (short prompt, short output) · Chat · Reasoning / long-context · Vision · Embedding |
| **Prompt tokens (PP)** | 64 · 512 · 2048 · 4096 · 8192 |
| **Generation tokens (TG)** | 32 · 64 · 256 · 512 |
| **Batch size** | 1 · 2 · 4 · 8 · 16 · 32 |
| **Concurrent users** | 1 · 5 · 10 · 20 · 50 · 100 · 200 · 500 · 1000 |
| **Node count** | 1 · 2 · 4 · 8 · 20 · 40 |

---

## 4. Metrics Definitions

### 3.1 Latency metrics

| Metric | Definition | Target (FastPool) | Target (ReasonPool) |
|--------|-----------|-------------------|---------------------|
| **TTFT** | Time to first token (ms) — wall-clock from request received to first streamed token | p50 < 500 ms | p50 < 2,000 ms |
| **TPOT** | Time per output token (ms) — inverse of generation throughput per stream | < 50 ms | < 100 ms |
| **E2E latency** | Total wall-clock from request to final token | p95 < 3 s (64-tok output) | p95 < 15 s (256-tok output) |
| **p50 / p95 / p99** | Percentile latency across N requests; p99 used for SLA alerting | — | — |

### 3.2 Throughput metrics

| Metric | Definition | Unit |
|--------|-----------|------|
| **PP t/s** | Prompt processing (prefill) throughput | tokens / second |
| **TG t/s** | Token generation (decode) throughput | tokens / second |
| **S t/s** | Combined throughput (PP+TG weighted) | tokens / second |
| **RPS** | Requests per second at target concurrency | req / second |
| **tok/s/node** | Generation throughput normalised per node | tokens / second / node |

### 3.3 Resource metrics

| Metric | Definition |
|--------|-----------|
| **Mem used (GB)** | Peak unified / VRAM consumed by model + KV cache |
| **KV cache util %** | KV cache pages in use / total allocated |
| **GPU util %** | GPU compute utilisation during sustained load |
| **Thermal headroom** | Sustained vs. peak clock — flag if throttling observed |
| **Power draw (W)** | Wall power per node (Apple: powermetrics; NVIDIA: nvidia-smi) |

### 3.4 Scale metrics

| Metric | Definition |
|--------|-----------|
| **Max concurrent @ p95 TTFT SLA** | Highest concurrency where p95 TTFT stays within target |
| **Queue depth at saturation** | Mean pending requests when throughput plateaus |
| **Error rate %** | HTTP 5xx / timeout / OOM errors as % of total requests |

---

## 5. Tooling & Execution Methodology

### 4.1 Benchmark tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `llama-bench` | Single-stream PP/TG throughput | Built into `llama.cpp/` |
| `llama-batched-bench` | Batched PP+TG across varying batch sizes | Built into `llama.cpp/` |
| `opik-observability/experiments/baseline.py` | p50/p95/p99 latency + tok/s via Opik traces | Pulls from LiteLLM gateway |
| `locust` / `k6` | Concurrent user load generation against OpenAI-compatible endpoint | Use for Sections 7 & 8 |
| `prometheus` + `grafana` | Real-time resource metrics during load test | See `apple-silicon-monitoring/` |
| `powermetrics` (macOS) | Per-node power and thermal data | Run as root on each node |
| `nvidia-smi` | NVIDIA GPU util, VRAM, power | DGX Spark nodes only |

### 4.2 Standard single-node benchmark procedure

Run on each hardware × runtime × model combination:

```bash
# 1. Ensure only the target model is loaded (no colocation)
# 2. Warm up: 3 requests, discard results
# 3. llama-bench (single-stream baseline)
./llama-bench \
  -m /path/to/model.gguf \
  -p 512,2048,4096,8192 \
  -n 32,64,256 \
  -b 1,8,32 \
  --flash-attn 1 \
  -r 5

# 4. llama-batched-bench (batched throughput)
./llama-batched-bench \
  -m /path/to/model.gguf \
  --n-pp 512,2048,4096,8192 \
  --n-tg 32 \
  --n-pl 1,2,4,8,16,32 \
  --flash-attn 1

# 5. Record system info (uname, GPU driver, model checksum)
```

### 4.3 Standard concurrent-user load test procedure

```bash
# Target: LiteLLM gateway (port 4000) — tests full stack
# Tool: k6 or locust

k6 run \
  --vus 10,20,50,100 \
  --duration 5m \
  -e MODEL=llama3-8b-q4 \
  -e SCENARIO=chat \
  scripts/bench/k6-chat.js

# Collect from Prometheus during run:
#   llm_ttft_seconds (histogram)
#   llm_tgs_tokens_per_second (gauge)
#   llm_kv_cache_utilization (gauge)
#   apple_silicon_gpu_utilization (gauge) or dcgm_gpu_utilization
```

### 4.4 Result recording

After each run:

1. Append a row to **Section 11 (Run Log)** with: date, hardware, chip, runtime, model, quant, key results.
2. Update the relevant **Section 7** scenario table with the new row.
3. Commit: `git commit -m "bench: <hardware> <model> <runtime> <date>"`

### 4.5 Environment freeze

Before any benchmark session, record and commit:

```bash
uname -a
# llama.cpp: git rev-parse HEAD
# Ollama: ollama --version
# vLLM-MLX: pip show vllm-mlx | grep Version
# macOS: sw_vers
# NVIDIA: nvidia-smi --query-gpu=driver_version --format=csv,noheader
```

---

## 6. Hardware Reference Sheet

#### Phase 1 pilot machines (active testing)

| ID | Platform | Chip / CPU | GPU Provider | GPU / VRAM | RAM | Memory BW | OS |
|----|----------|-----------|-------------|------------|-----|-----------|-----|
| `p1-m2-8g` | Apple Mac (M2) | Apple M2 | Apple Metal / MLX | 8-core GPU | **8 GB unified** | ~100 GB/s | macOS |
| `p1-m3-16g` | Apple Mac Mini (M3) | Apple M3 | Apple Metal / MLX | 10-core GPU | **16 GB unified** | 102.4 GB/s | macOS |
| `p1-i7-rtx3050` | Linux workstation | Intel i7 12th Gen | **NVIDIA CUDA** | RTX 3050 **4 GB VRAM** | 64 GB DDR5 | ~4 TB/s (DDR5) / 112 GB/s (VRAM) | Linux |

#### Reference / production machines (prior runs + future cluster)

| ID | Platform | Chip | GPU Provider | GPU Cores | Memory | Bandwidth | Thermal |
|----|----------|------|-------------|-----------|--------|-----------|---------|
| `m1-mini` | Mac Mini | Apple M1 | Apple (Metal) | 7–8 | 16 GB unified | 68.25 GB/s | Lower |
| `m3-mini` | Mac Mini | Apple M3 | Apple (Metal) | 10 | 16 GB unified | 102.4 GB/s | Moderate |
| `m4-mini` | Mac Mini | Apple M4 | Apple (Metal) | 10 | 16 GB unified | 120.0 GB/s | Moderate |
| `m2-ultra` | Mac Studio / Mac Pro | Apple M2 Ultra | Apple (Metal) | 60–76 | 192 GB unified | 800 GB/s | High |
| `dgx-spark` | NVIDIA DGX Spark | NVIDIA GB10 | NVIDIA (CUDA 13.0) | — | Shared NVLink | ~900 GB/s | High |
| `amd-rocm` | Linux server | AMD GPU (ROCm) | AMD (ROCm) | — | — | — | Planned |
| `cpu-only` | Any Linux | CPU | None | — | — | — | Planned |

### 5.1 Hydra cluster node pool assignment

| Pool | Node count | Chip | Primary workload |
|------|-----------|------|-----------------|
| FastPool | 20 | M4 | Autocomplete, short chat (8B Q4_K_M) |
| ReasonPool | 8 | M4 | Long chat, reasoning (14B Q4_K_M) |
| VisionPool | 6 | M3 | Vision/multimodal (8B VL Q4_K_M) |
| EmbedPool | 4 | M1/M3 | Embeddings (BGE-M3) |
| SpeechPool | 2 | M1 | ASR (Whisper) |
| Gateway × 2 | 2 | M1 | LiteLLM + HAProxy (no inference) |
| Model Store | 2 | M1 | MinIO / NFS |

---

## 7. Model Catalogue

| Model ID | Family | Params | Default Quant | GGUF size | Target pool | Status |
|----------|--------|--------|--------------|-----------|-------------|--------|
| `llama3-8b-q4` | Llama 3 | 8B | Q4_K_M | ~4.9 GB | FastPool | Tested |
| `llama3-70b-q4` | Llama 3 | 70B | Q4_K_M | ~40 GB | ReasonPool (dist.) | Planned |
| `qwen2.5-7b-q4` | Qwen 2.5 | 7B | Q4_K_M | ~4.7 GB | FastPool | Planned |
| `qwen2.5-14b-q4` | Qwen 2.5 | 14B | Q4_K_M | ~8.9 GB | ReasonPool | Planned |
| `mistral-7b-q4` | Mistral | 7B | Q4_K_M | ~4.1 GB | FastPool | Planned |
| `phi4-14b-q4` | Phi-4 | 14B | Q4_K_M | ~8.4 GB | ReasonPool | Planned |
| `gpt-oss-20b-mxfp4` | gpt-oss (Meta) | 20.9B MoE | MXFP4 | 11.27 GB | ReasonPool | **Tested** |
| `gpt-oss-120b-mxfp4` | gpt-oss (Meta) | 120B MoE | MXFP4 | ~68 GB | DGX only | **Tested** |
| `llava-8b-q4` | LLaVA / MiniCPM-V | 8B VL | Q4_K_M | ~5.5 GB | VisionPool | Planned |
| `bge-m3` | BGE-M3 | 568M | F16 | ~1.1 GB | EmbedPool | Planned |
| `whisper-large-v3` | Whisper | 1.5B | F16 | ~3.0 GB | SpeechPool | Planned |

---

## 8. Workload Scenarios & Results

Column key:

- **PP t/s** = prompt processing tokens/sec
- **TG t/s** = generation tokens/sec
- **TTFT p50** = median time to first token (ms) at B=1
- **Max conc.** = max concurrent requests within TTFT SLA
- `[public ref]` = value from vendor/published benchmarks, not directly measured here

---

### 8.1 Autocomplete (FastPool)

**Profile**: Short prompt (64–512 tokens), short output (32–64 tokens), latency-critical (TTFT SLA: p50 < 500 ms).

#### 7.1.1 llama-bench results — gpt-oss-20B MXFP4 MoE (pp512, tg32, B=1)

| Hardware | Runtime | Quant | PP t/s | TG t/s | TTFT p50 (est.) | Tested |
|----------|---------|-------|--------|--------|-----------------|--------|
| `m2-ultra` | llama.cpp | MXFP4 | 2,381 | 130 | ~12 ms | 2026-02 |
| `dgx-spark` | llama.cpp | MXFP4 | 1,896 | 80 | ~16 ms | 2026-02-05 |
| `m4-mini` | llama.cpp | — | — | — | — | Pending |
| `m3-mini` | llama.cpp | — | — | — | — | Pending |
| `m1-mini` | llama.cpp | — | — | — | — | Pending |
| `m4-mini` | Ollama | — | — | — | — | Pending |
| `m4-mini` | vLLM-MLX | — | — | — | — | Pending |
| OpenAI GPT-4o (cloud) | OpenAI API | — | — | ~80–120 [public ref] | ~200–400 ms [public ref] | ref only |
| Groq (cloud) | Groq API | — | — | ~500–900 [public ref] | ~50–150 ms [public ref] | ref only |

#### 7.1.2 Batched throughput — gpt-oss-20B MXFP4 (pp512, tg32, varying batch)

| Hardware | Runtime | Batch | PP t/s | TG t/s | Combined t/s |
|----------|---------|-------|--------|--------|--------------|
| `m2-ultra` | llama.cpp | 1 | 2,381 | 130 | 1,182 |
| `m2-ultra` | llama.cpp | 4 | 2,839 | 212 | 1,641 |
| `m2-ultra` | llama.cpp | 16 | 2,871 | 326 | 1,968 |
| `m2-ultra` | llama.cpp | 32 | 2,875 | 536 | 2,288 |
| `dgx-spark` | llama.cpp | 1 | 1,896 | 80 | 813 |
| `dgx-spark` | llama.cpp | 4 | 4,689 | 156 | 1,731 |
| `dgx-spark` | llama.cpp | 16 | 4,748 | 436 | 3,003 |
| `dgx-spark` | llama.cpp | 32 | 4,767 | 681 | 3,524 |

> Note: DGX Spark CUDA shows superior PP throughput at high batch sizes; M2 Ultra leads at TG single-stream (130 vs 80 t/s).

---

### 8.2 Chat (ReasonPool)

**Profile**: Medium prompt (512–4096 tokens), medium output (128–512 tokens), TTFT SLA: p50 < 2,000 ms.

#### 7.2.1 llama-bench — gpt-oss-20B MXFP4 (pp4096, tg32, B=1)

| Hardware | Runtime | Quant | PP t/s | TG t/s | TTFT p50 (est.) | Tested |
|----------|---------|-------|--------|--------|-----------------|--------|
| `m2-ultra` | llama.cpp | MXFP4 | 2,639 | 96 | ~155 ms | 2026-02 |
| `dgx-spark` | llama.cpp | MXFP4 | 4,514 | 79 | ~91 ms | 2026-02-05 |
| `m4-mini` | llama.cpp | — | — | — | — | Pending |
| `m4-mini` | Ollama | — | — | — | — | Pending |
| `m4-mini` | vLLM-MLX | — | — | — | — | Pending |
| Anthropic Claude Sonnet (cloud) | Anthropic API | — | — | ~70–100 [public ref] | ~300–800 ms [public ref] | ref only |
| OpenAI GPT-4o (cloud) | OpenAI API | — | — | ~80–120 [public ref] | ~300–700 ms [public ref] | ref only |

#### 7.2.2 Batched throughput — gpt-oss-20B MXFP4 (pp4096, tg32)

| Hardware | Runtime | Batch | PP t/s | TG t/s | Combined t/s |
|----------|---------|-------|--------|--------|--------------|
| `m2-ultra` | llama.cpp | 1 | 2,639 | 96 | 2,188 |
| `m2-ultra` | llama.cpp | 8 | 2,667 | 225 | 2,460 |
| `m2-ultra` | llama.cpp | 32 | 2,669 | 427 | 2,564 |
| `dgx-spark` | llama.cpp | 1 | 4,514 | 79 | 3,140 |
| `dgx-spark` | llama.cpp | 8 | 4,561 | 233 | 3,988 |
| `dgx-spark` | llama.cpp | 32 | 4,558 | 474 | 4,272 |

---

### 8.3 Reasoning / Long-context

**Profile**: Long prompt (8192–32768 tokens), medium output (256–512 tokens), TTFT SLA: p50 < 5,000 ms.

#### 7.3.1 llama-bench — gpt-oss-20B MXFP4 (pp8192, tg32, B=1)

| Hardware | Runtime | Quant | PP t/s | TG t/s | TTFT p50 (est.) | Tested |
|----------|---------|-------|--------|--------|-----------------|--------|
| `m2-ultra` | llama.cpp | MXFP4 | 2,449 | 116 | ~335 ms | 2026-02 |
| `dgx-spark` | llama.cpp | MXFP4 | 4,406 | 74 | ~186 ms | 2026-02-05 |
| `m4-mini` | llama.cpp | — | — | — | — | Pending |
| `m4-mini` | vLLM-MLX | — | — | — | — | Pending |

#### 7.3.2 llama-bench — gpt-oss-120B MXFP4 (pp4096, tg32, B=1)

| Hardware | Runtime | Quant | PP t/s | TG t/s | TTFT p50 (est.) | Tested |
|----------|---------|-------|--------|--------|-----------------|--------|
| `m2-ultra` | llama.cpp | MXFP4 | 1,587 | 82 | ~258 ms | 2026-02 |
| `dgx-spark` | llama.cpp | MXFP4 | 2,413 | 55 | ~170 ms | 2026-02-05 |
| `m4-mini` | llama.cpp | — | — | — | — | Pending (dist. only) |

#### 7.3.3 Context degradation — gpt-oss-20B MXFP4 (tg32, varying depth)

| Hardware | Runtime | Prompt depth | PP t/s | TG t/s |
|----------|---------|-------------|--------|--------|
| `m2-ultra` | llama.cpp | 4096 | 2,325 | 123 |
| `m2-ultra` | llama.cpp | 8192 | 1,990 | 117 |
| `m2-ultra` | llama.cpp | 16384 | 1,557 | 110 |
| `m2-ultra` | llama.cpp | 32768 | 1,123 | 98 |
| `dgx-spark` | llama.cpp | 4096 | 4,158 | 79 |
| `dgx-spark` | llama.cpp | 8192 | 3,994 | 75 |
| `dgx-spark` | llama.cpp | 16384 | 3,450 | 70 |
| `dgx-spark` | llama.cpp | 32768 | 2,689 | 62 |

> Both platforms degrade gracefully. M2 Ultra TG degrades ~25% from 4K→32K depth; DGX Spark PP degrades ~35% but retains higher absolute throughput.

---

### 8.4 Vision (VisionPool)

**Profile**: Image + text prompt, medium output (128–256 tokens), TTFT SLA: p50 < 3,000 ms.

| Hardware | Runtime | Model | PP t/s | TG t/s | TTFT p50 | Tested |
|----------|---------|-------|--------|--------|----------|--------|
| `m3-mini` | llama.cpp | llava-8b-q4 | — | — | — | Pending |
| `m3-mini` | Ollama | llava-8b-q4 | — | — | — | Pending |
| `m4-mini` | vLLM-MLX | llava-8b-q4 | — | — | — | Pending |
| GPT-4o Vision (cloud) | OpenAI API | — | — | ~80 [public ref] | ~500–1500 ms [public ref] | ref only |

---

### 8.5 Embedding (EmbedPool)

**Profile**: Text input (64–512 tokens), vector output, latency SLA: p95 < 200 ms.

| Hardware | Runtime | Model | Throughput (seq/s) | p95 latency | Tested |
|----------|---------|-------|-------------------|-------------|--------|
| `m1-mini` | llama.cpp | bge-m3 | — | — | Pending |
| `m3-mini` | Ollama | bge-m3 | — | — | Pending |
| OpenAI text-embedding-3-small (cloud) | OpenAI API | — | ~2000 [public ref] | ~50 ms [public ref] | ref only |

---

## 9. Concurrent User Capacity Matrix

This table answers: **at this concurrency level, does the pool stay within TTFT SLA?**

Target SLAs:

| Pool | Workload | TTFT p50 target | TTFT p95 target |
|------|---------|-----------------|-----------------|
| FastPool | Autocomplete | < 500 ms | < 1,500 ms |
| ReasonPool | Chat | < 2,000 ms | < 5,000 ms |
| VisionPool | Vision | < 3,000 ms | < 8,000 ms |
| EmbedPool | Embedding | < 200 ms | < 500 ms |

### 8.1 FastPool capacity — llama3-8b-q4 on M4 Mac Mini × N nodes

| Concurrent users | Nodes | Runtime | TTFT p50 | TTFT p95 | TG t/s total | SLA met? | Tested |
|-----------------|-------|---------|----------|----------|-------------|---------|--------|
| 10 | 1 | vLLM-MLX | — | — | — | Pending | — |
| 20 | 1 | vLLM-MLX | — | — | — | Pending | — |
| 50 | 2 | vLLM-MLX | — | — | — | Pending | — |
| 100 | 5 | vLLM-MLX | — | — | — | Pending | — |
| 200 | 10 | vLLM-MLX | — | — | — | Pending | — |
| 500 | 20 | vLLM-MLX | — | — | — | Pending | — |
| 1000 | 40 | vLLM-MLX | — | — | — | Pending | — |

### 8.2 ReasonPool capacity — qwen2.5-14b-q4 on M4 Mac Mini × N nodes

| Concurrent users | Nodes | Runtime | TTFT p50 | TTFT p95 | TG t/s total | SLA met? | Tested |
|-----------------|-------|---------|----------|----------|-------------|---------|--------|
| 5 | 1 | vLLM-MLX | — | — | — | Pending | — |
| 10 | 2 | vLLM-MLX | — | — | — | Pending | — |
| 25 | 4 | vLLM-MLX | — | — | — | Pending | — |
| 50 | 8 | vLLM-MLX | — | — | — | Pending | — |

### 8.3 Architecture capacity model (theoretical, pre-measurement)

Based on Section 1.2 chip bandwidth and the 60% RAM rule:

| Pool | Nodes | Model | Estimated tok/s/node | Cluster tok/s | Est. max concurrent @ SLA |
|------|-------|-------|---------------------|---------------|--------------------------|
| FastPool | 20 × M4 | 8B Q4_K_M | 25–35 | 500–700 | ~140–200 users |
| ReasonPool | 8 × M4 | 14B Q4_K_M | 15–22 | 120–176 | ~30–50 users |
| VisionPool | 6 × M3 | 8B VL Q4_K_M | 18–25 | 108–150 | ~25–40 users |
| EmbedPool | 4 × M1/M3 | BGE-M3 | ~500 seq/s | ~2000 seq/s | ~200+ users |

> These estimates will be validated and replaced by measured values as Section 8.1/8.2 fills in.

---

## 10. Cloud API Reference Baselines

These figures are sourced from public benchmarks and vendor documentation. They are **not directly measured** in this project — they serve as a reference ceiling for on-prem context.

| Provider | Model | TG t/s (reported) | TTFT p50 (reported) | Source |
|----------|-------|------------------|---------------------|--------|
| OpenAI | GPT-4o | ~80–120 tok/s | ~200–600 ms | OpenAI status / community benchmarks |
| OpenAI | GPT-4o-mini | ~150–200 tok/s | ~100–300 ms | OpenAI status / community benchmarks |
| Anthropic | Claude 3.5 Sonnet | ~80–100 tok/s | ~300–700 ms | Anthropic docs / community benchmarks |
| Anthropic | Claude 3 Haiku | ~150–200 tok/s | ~150–400 ms | Anthropic docs / community benchmarks |
| Groq | Llama 3 70B | ~400–900 tok/s | ~50–150 ms | Groq benchmark page |
| Groq | Mixtral 8×7B | ~500–1000 tok/s | ~50–100 ms | Groq benchmark page |
| Together AI | Llama 3 8B | ~150–300 tok/s | ~100–300 ms | Together AI docs |
| Together AI | Llama 3 70B | ~80–150 tok/s | ~200–500 ms | Together AI docs |

> When Hydra cluster results are available, a direct comparison row will be added above each provider entry.

---

## 11. Quantization Impact Analysis

Impact of quantization on TG throughput and memory for the same model (relative to F16 baseline). Fill in as runs complete.

### 10.1 8B model (Llama 3 / Qwen 2.5) on M4 Mac Mini

| Quant | VRAM used | TG t/s | PP t/s | Quality degradation | Tested |
|-------|-----------|--------|--------|---------------------|--------|
| F16 | ~16 GB | — | — | Baseline | Pending |
| Q8_0 | ~8.5 GB | — | — | ~0.1% PPL increase | Pending |
| Q4_K_M | ~4.9 GB | — | — | ~0.5% PPL increase | Pending |
| Q4_0 | ~4.5 GB | — | — | ~1.0% PPL increase | Pending |
| Q3_K_M | ~3.7 GB | — | — | ~2.0% PPL increase | Pending |

### 10.2 20B MoE model (gpt-oss-20B) — measured

| Quant | VRAM used | TG t/s (M2 Ultra) | TG t/s (DGX Spark) | Tested |
|-------|-----------|-------------------|--------------------|----|
| MXFP4 | 11.27 GB | 130 | 80 | 2026-02 |
| Q4_K_M | ~12.5 GB | — | — | Pending |
| Q8_0 | ~21 GB | N/A (OOM on mini) | — | N/A |

---

## 12. Run Log

Append one row per benchmark session. Reference the commit hash of the run script / config for reproducibility.

| Date | Hardware | Chip | Runtime | Model | Quant | PP t/s | TG t/s | Notes | Commit |
|------|----------|------|---------|-------|-------|--------|--------|-------|--------|
| 2026-02-05 | dgx-spark | NVIDIA GB10 | llama.cpp b11fb327 | gpt-oss-20B MoE | MXFP4 | 4,506 (pp2048) | 83 (tg32) | CUDA 13.0, ngl=99, fa=1, n_threads=20 | — |
| 2026-02-05 | dgx-spark | NVIDIA GB10 | llama.cpp b11fb327 | gpt-oss-120B MoE | MXFP4 | 2,413 (pp4096) | 55 (tg32) | CUDA 13.0, ngl=99, fa=1 | — |
| 2026-02 | m2-ultra | Apple M2 Ultra | llama.cpp b828e18c | gpt-oss-20B MoE | MXFP4 | 2,713 (pp2048) | 130 (tg32) | MTL+BLAS, fa=1, n_threads=16 | — |
| 2026-02 | m2-ultra | Apple M2 Ultra | llama.cpp b828e18c | gpt-oss-120B MoE | MXFP4 | 1,587 (pp4096) | 82 (tg32) | MTL+BLAS, fa=1 | — |

---

## Appendix A — Grafana Alert Thresholds (derived from benchmarks)

Update these after Section 8 tables are populated:

| Alert | Expression | Severity | Notes |
|-------|-----------|---------|-------|
| FastPool TTFT p95 > 1500ms | `histogram_quantile(0.95, llm_ttft_seconds_bucket{pool="fast"}) > 1.5` | warning | |
| FastPool TTFT p95 > 3000ms | same, threshold 3.0 | critical | |
| ReasonPool TTFT p95 > 5000ms | `histogram_quantile(0.95, llm_ttft_seconds_bucket{pool="reason"}) > 5.0` | warning | |
| KV cache util > 85% | `llm_kv_cache_utilization > 0.85` | warning | Per node |
| KV cache util > 95% | `llm_kv_cache_utilization > 0.95` | critical | Per node |
| Node tok/s drop > 30% | rate below 0.7× 1h baseline | warning | Thermal throttle signal |
| Error rate > 1% | `rate(llm_requests_total{status="error"}[5m]) > 0.01` | warning | |
| Error rate > 5% | same, threshold 0.05 | critical | |

---

## Appendix B — Benchmark Expansion Checklist

### Phase 1 — 3-machine pilot (do these first)

- [ ] `p1-m2-8g`: Llama 3.2 3B Q4_K_M — llama.cpp single-stream + batched
- [ ] `p1-m2-8g`: Llama 3 8B Q4_K_M — llama.cpp (check for OOM / KV pressure)
- [ ] `p1-m2-8g`: Llama 3 8B Q4_K_M — Ollama concurrent users (1, 2, 5)
- [ ] `p1-m2-8g`: BGE-M3 — llama.cpp embedding throughput
- [ ] `p1-m3-16g`: Llama 3.2 3B Q4_K_M — llama.cpp
- [ ] `p1-m3-16g`: Llama 3 8B Q4_K_M — llama.cpp single-stream + batched
- [ ] `p1-m3-16g`: Llama 3 8B Q4_K_M — Ollama concurrent users (1, 5, 10, 20)
- [ ] `p1-m3-16g`: Llama 3 8B Q4_K_M — vLLM-MLX concurrent users (1, 5, 10, 20)
- [ ] `p1-m3-16g`: Qwen 2.5 14B Q4_K_M — llama.cpp
- [ ] `p1-m3-16g`: BGE-M3 — llama.cpp embedding throughput
- [ ] `p1-i7-rtx3050`: Llama 3.2 3B Q4_K_M — llama.cpp CPU-only baseline
- [ ] `p1-i7-rtx3050`: Llama 3 8B Q4_K_M — llama.cpp CPU-only (ngl=0)
- [ ] `p1-i7-rtx3050`: Llama 3 8B Q4_K_M — llama.cpp ngl=10 (partial GPU)
- [ ] `p1-i7-rtx3050`: Llama 3 8B Q4_K_M — llama.cpp ngl=20 (partial GPU)
- [ ] `p1-i7-rtx3050`: Llama 3 8B Q4_K_M — llama.cpp ngl=33 (max GPU layers that fit)
- [ ] `p1-i7-rtx3050`: Llama 3 8B Q4_K_M — Ollama concurrent users (1, 5, 10)
- [ ] `p1-i7-rtx3050`: Mistral 7B Q4_K_M — llama.cpp ngl sweep (fits better in 4 GB VRAM)
- [ ] Populate Section 2.6 summary table with all Phase 1 results
- [ ] Apply Section 2.5 decision tree → document 40-node pool hardware decision

### Phase 2 — 40-node pool validation (after Phase 1 decision)

- [ ] Fleet model on selected hardware — llama.cpp / vLLM-MLX / Ollama
- [ ] Concurrent user load test: FastPool 20 nodes (k6, 10→1000 VUs)
- [ ] Concurrent user load test: ReasonPool 8 nodes (k6, 5→50 VUs)
- [ ] Quantization sweep: 8B model on selected chip (F16 → Q3_K_M)
- [ ] Vision: llava-8b-q4 via Ollama
- [ ] Speech: whisper-large-v3 via llama.cpp
- [ ] AMD ROCm node (when available)
- [ ] AIME-25 eval on gpt-oss-120B DGX Spark (partial data in `benches/dgx-spark/`)
