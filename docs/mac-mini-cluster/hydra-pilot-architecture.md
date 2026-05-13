# Hydra Pilot — 2-Node Test Cluster Architecture

**Scope**: Proof-of-concept deployment on 2 Mac Minis
**Target validation**: 5–10 developers (expandable to 100 on 20 nodes)
**Deployment class**: On-premises, LAN-isolated
**Parent specification**: Hydra Architecture v1.0 (2026-03)
**Revision**: Pilot 1.0 — 2026-03

---

## Table of Contents

1. [Purpose and Success Criteria](#1-purpose-and-success-criteria)
2. [Hardware Inventory and Constraints](#2-hardware-inventory-and-constraints)
3. [System Architecture](#3-system-architecture)
4. [KV Cache Math for This Cluster](#4-kv-cache-math-for-this-cluster)
5. [Model Selection](#5-model-selection)
6. [Inference Runtime](#6-inference-runtime)
7. [Gateway and Governance](#7-gateway-and-governance)
8. [Observability Stack](#8-observability-stack)
9. [Guardrails and Hallucination Control](#9-guardrails-and-hallucination-control)
10. [IDE Tooling and Developer Experience](#10-ide-tooling-and-developer-experience)
11. [Networking and Security](#11-networking-and-security)
12. [Failure Handling](#12-failure-handling)
13. [Capacity Analysis for 100 Developers](#13-capacity-analysis-for-100-developers)
14. [Scaling Path: Pilot → 20 Nodes → 40 Nodes](#14-scaling-path)
15. [Deployment Procedure](#15-deployment-procedure)
16. [Validation Checklist](#16-validation-checklist)
17. [Reference Links](#17-reference-links)

---

## 1. Purpose and Success Criteria

### 1.1 What This Pilot Proves

This 2-node deployment validates the Hydra architecture before committing to a 20–40 node cluster. It answers:

1. **Does vLLM-MLX deliver the throughput Hydra predicts?** Benchmark continuous batching and paged KV cache on real M3 hardware. Compare against Hydra's per-node estimates.
2. **Does the governance pipeline work?** Rate limiting, token budgets, context window enforcement, and audit logging through LiteLLM.
3. **Does the developer experience meet expectations?** Tab autocomplete latency, chat quality, codebase-aware context — tested by real developers in their daily workflow.
4. **Does the observability stack provide actionable data?** Can we see per-request latency, KV cache utilization, token throughput, and guardrail triggers in Grafana?

### 1.2 Success Criteria

| Criterion | Target | Measurement |
|-----------|--------|-------------|
| Autocomplete TTFT | < 1 second (p50) | Grafana histogram |
| Chat TTFT | < 3 seconds (p50) | Grafana histogram |
| Chat streaming rate | > 20 tok/s | vLLM-MLX metrics |
| Concurrent requests (M3 node) | ≥ 10 at 4K context | Load test script |
| KV cache utilization under load | < 85% at operational target | vLLM-MLX gauge |
| Guardrail: secret detection | Blocks API keys in responses | Manual test |
| Guardrail: context overflow | Rejects over-limit requests with token count | Manual test |
| End-to-end uptime (1-week soak) | > 99% | Prometheus `up` metric |
| Developer satisfaction (5-dev pilot) | Useful for daily work | Survey after 1 week |

### 1.3 What This Pilot Does NOT Prove

- HA failover (requires ≥2 gateways + keepalived)
- Multi-pool routing (requires ≥4 nodes for meaningful pool separation)
- Distributed inference via exo (requires ≥3 nodes with 16GB+ each)
- Scale behavior under 100+ concurrent users

These are validated in Phase 2 (20-node deployment).

---

## 2. Hardware Inventory and Constraints

### 2.1 Node Specifications

```
┌──────────────────────────────────────────────────────────────────┐
│                     PILOT CLUSTER INVENTORY                      │
├────────────┬────────────────────────┬────────────────────────────┤
│            │ Node 1 (Gateway)       │ Node 2 (Inference)         │
├────────────┼────────────────────────┼────────────────────────────┤
│ Chip       │ Apple M1               │ Apple M3                   │
│ CPU cores  │ 8 (4P + 4E)            │ 10 (5P + 5E)              │
│ GPU cores  │ 8*                     │ 10                         │
│ RAM        │ 8 GB unified           │ 16 GB unified              │
│ Mem BW     │ 68.25 GB/s             │ 102.4 GB/s                 │
│ NPU        │ 16-core                │ 16-core                    │
│ Storage    │ 256+ GB NVMe           │ 256+ GB NVMe               │
│ Network    │ 1 Gbps Ethernet        │ 1 Gbps Ethernet            │
├────────────┼────────────────────────┼────────────────────────────┤
│ Role       │ Gateway + Monitoring   │ Primary inference          │
│            │ (no inference)         │ (single model)             │
└────────────┴────────────────────────┴────────────────────────────┘

* User reported 10 GPU cores for M1 node. Standard M1 has 7-8.
  Architecture uses the conservative 8-core figure for calculations.
```

### 2.2 Per-Node Memory Budget

**Node 1 — M1, 8 GB (Gateway role, no inference)**

```
Total unified RAM                    : 8,192 MB
macOS + system daemons               : 2,500 MB
Docker/Colima (Prometheus, Grafana,
  Loki, Redis)                       : 2,000 MB
LiteLLM Proxy (Python)               :   500 MB
──────────────────────────────────────────────────
Available for other services          : 3,192 MB
Inference models                      : NONE
──────────────────────────────────────────────────
Verdict: Comfortable for gateway role. No inference.
```

**Node 2 — M3, 16 GB (Inference role)**

Per Hydra §1.3:

```
Total unified RAM                    : 16,384 MB
macOS + system daemons               :  2,500 MB
vLLM-MLX runtime overhead            :    500 MB
──────────────────────────────────────────────────
Available for model + KV cache        : 13,384 MB
Model weight ceiling (Hydra 60% rule) :  9,600 MB
──────────────────────────────────────────────────
```

### 2.3 Hardware Constraints (from Hydra §1.4)

These apply to the pilot cluster identically:

- **No NCCL, no NVLink**: Distributed tensor parallelism is not possible.
- **No CUDA**: GPU compute via Metal/MLX only.
- **Unified memory ceiling**: 16 GB max on Node 2, 8 GB on Node 1.
- **Thermal throttling**: Sustained GPU load on Mac Mini reduces bandwidth 5–15%.
- **M1 decode performance**: ~15–20 tok/s on 8B Q4_K_M — 75% slower than M4. Per Hydra §1.2, M1 nodes are unsuitable for latency-sensitive decode workloads.

---

## 3. System Architecture

### 3.1 High-Level Topology

```
┌─────────────────────────────────────────────────────────────────┐
│ Developer Machines (5–10 pilot users)                           │
│                                                                 │
│  VS Code / Cursor / JetBrains                                   │
│  ┌──────────────┐  ┌───────────┐  ┌──────────────────────────┐ │
│  │ Continue.dev  │  │ Tabby     │  │ Cline (agent tasks)      │ │
│  │ (chat + edit) │  │ (tab-     │  │ (multi-file, optional)   │ │
│  │              │  │ complete) │  │                          │ │
│  └──────┬───────┘  └─────┬─────┘  └────────────┬─────────────┘ │
└─────────┼────────────────┼──────────────────────┼───────────────┘
          │ :4000/v1       │ :4000/v1             │ :4000/v1
          │ (OpenAI API)   │ (OpenAI API)         │ (OpenAI API)
          │                │                      │
┌─────────▼────────────────▼──────────────────────▼───────────────┐
│                                                                  │
│  NODE 1 — M1, 8 GB — "HYDRA GATEWAY"                           │
│  IP: 192.168.x.10                                                │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                    LiteLLM Proxy (:4000)                   │  │
│  │                                                            │  │
│  │  ┌──────────────────────────────────────────────────────┐  │  │
│  │  │              REQUEST PIPELINE                         │  │  │
│  │  │                                                      │  │  │
│  │  │  1. Authenticate (API key)                           │  │  │
│  │  │  2. Rate limit check (Redis)                         │  │  │
│  │  │  3. Token budget check (Redis)                       │  │  │
│  │  │  4. Input token count (model tokenizer)              │  │  │
│  │  │  5. Context window validation                        │  │  │
│  │  │  6. Guardrails: secret scan, grounding prompt inject │  │  │
│  │  │  7. Route to inference node (least-busy)             │  │  │
│  │  │  8. Post-response: budget increment, audit log       │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  └─────────────────────────┬──────────────────────────────────┘  │
│                             │                                    │
│  ┌───────────┐ ┌───────────┤ ┌──────────┐ ┌──────────────────┐  │
│  │Prometheus │ │ Redis     │ │  Loki    │ │     Grafana      │  │
│  │  (:9090)  │ │ (:6379)   │ │ (:3100)  │ │    (:3000)       │  │
│  │           │ │ rate-limit│ │ log agg. │ │ 4 dashboards     │  │
│  │  scrapes  │ │ + budget  │ │          │ │                  │  │
│  │  both     │ │ counters  │ │          │ │ • Cluster Health │  │
│  │  nodes    │ │           │ │          │ │ • Request Perf.  │  │
│  └───────────┘ └───────────┘ └──────────┘ │ • Token Usage    │  │
│                                            │ • Guardrails     │  │
│  ┌──────────────────────────────────────┐  └──────────────────┘  │
│  │  Promtail — ships local logs → Loki  │                        │
│  └──────────────────────────────────────┘                        │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           │ HTTP :8000 (internal only)
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                                                                  │
│  NODE 2 — M3, 16 GB — "HYDRA INFERENCE"                        │
│  IP: 192.168.x.11                                                │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              vLLM-MLX Inference Server (:8000)             │  │
│  │                                                            │  │
│  │  Model: Qwen 2.5 Coder 7B Instruct (MLX 4-bit)           │  │
│  │  Weights: ~4.5 GB                                          │  │
│  │  KV cache budget: ~8.9 GB                                  │  │
│  │  Max concurrent sequences: 24 (at 4K context)             │  │
│  │  Max model length: 8,192 tokens                            │  │
│  │  GPU memory utilization: 0.90                              │  │
│  │                                                            │  │
│  │  Features:                                                 │  │
│  │    ✓ Continuous batching (no head-of-line blocking)        │  │
│  │    ✓ Paged KV cache (no memory fragmentation)             │  │
│  │    ✓ Prefix caching (repeated prompt acceleration)        │  │
│  │    ✓ OpenAI-compatible API                                │  │
│  │    ✓ Streaming (SSE)                                      │  │
│  │    ✓ Prometheus metrics (:8000/metrics)                   │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────┐                    │
│  │  node_exporter (:9100)                    │                    │
│  │  Promtail → ships logs to Node 1 Loki    │                    │
│  └──────────────────────────────────────────┘                    │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 Component Responsibilities

| Component | Node | Role | Reference |
|-----------|------|------|-----------|
| **vLLM-MLX** | Node 2 | Inference: continuous batching, paged KV, streaming | Hydra §5.3 |
| **LiteLLM Proxy** | Node 1 | Gateway: routing, auth, rate limits, budget, audit | Hydra §2.3 |
| **Redis** | Node 1 | Shared rate-limit counters and token budget state | Hydra §2.3 |
| **Prometheus** | Node 1 | Metrics: vLLM-MLX, node_exporter, LiteLLM, Redis | Hydra §15.1 |
| **Grafana** | Node 1 | Dashboards: cluster health, perf, usage, guardrails | Hydra §15.3 |
| **Loki + Promtail** | Both | Centralized log aggregation | Hydra §15.5 |
| **node_exporter** | Both | System metrics: memory, CPU, disk, network | Hydra §15.2 |
| **Guardrails** | Node 1 | Secret/PII scan, hallucination controls, temp cap | Original pilot |

### 3.3 Request Lifecycle

Per Hydra §2.4, adapted for pilot:

```
1. Developer sends POST /v1/chat/completions (OpenAI format)
   └─ Source: Continue.dev, Cursor, Cline, or any OpenAI-compatible client

2. LiteLLM Gateway (Node 1, :4000):
   a. Authenticate API key → resolve user
   b. Check rate limit (Redis): ≤ 30 req/min
   c. Check token budget (Redis): ≤ 500K tokens/day
   d. Count input tokens (Qwen tokenizer, cached in memory)
   e. Validate: input_tokens + max_tokens ≤ 8,192
      → Reject HTTP 400 if exceeded (no silent truncation, per Hydra §9.6)
   f. Guardrail pre-call: secret scan, grounding prompt injection, temp cap
   g. Route to Node 2 (:8000) — single inference node in pilot
   h. Forward request via HTTP

3. vLLM-MLX (Node 2, :8000):
   a. Add request to continuous batch scheduler
   b. Allocate paged KV cache blocks
   c. Run prefill → stream decode tokens (SSE)
   d. Return response

4. LiteLLM Gateway (post-response):
   a. Guardrail post-call: scan response for leaked secrets
   b. Increment token budget counter (Redis)
   c. Write structured audit log (JSON)
   d. Return response to developer
```

### 3.4 Why These Choices (Referenced to Hydra)

| Decision | Rationale | Hydra Reference |
|----------|-----------|-----------------|
| **vLLM-MLX over Ollama** | Continuous batching + paged KV cache = 21–87% higher throughput. Ollama has no paged KV cache, meaning memory fragmentation degrades under sustained multi-user load. | §5.2: "Ollama: No paged KV cache. Memory fragmentation under sustained load." |
| **No inference on M1 (8 GB)** | After macOS + gateway services, only ~3 GB remains. Insufficient for any meaningful model + KV cache for serving. M1's 68 GB/s bandwidth makes it unsuitable for latency-sensitive decode anyway. | §1.2: "M1 vs M4 decode gap: ~75%. Route latency-sensitive workloads to M4 nodes exclusively." §2.5: "M1 nodes absorb embedding, speech, and overflow batching workloads." |
| **Qwen 2.5 7B over Llama 3.1 8B** | Qwen uses 4 KV heads (vs Llama's 8), halving KV cache per token: 0.055 MB/tok vs 0.125 MB/tok. At 4K context: Qwen fits 24 concurrent vs Llama's 10. | §3.4, §4.2: "Qwen 2.5 7B's architecture is significantly more KV-cache-efficient than Llama 3.1 8B." |
| **Q4_K_M quantization** | < 1% quality delta vs FP16 on MMLU/HumanEval. Maximizes KV cache headroom. | §7.2: "Q4_K_M justification: benchmark degradation consistently below 1%." |
| **Single model per node** | Multi-model colocation causes eviction under memory pressure. One model, fully loaded, no swapping. | §8.5: "Each inference node runs a single model, loaded at startup." |
| **No silent truncation** | Truncation produces malformed prompts (cut system prompts, split code). Reject with exact token count; client must adjust. | §9.6: "The system does NOT perform automatic silent truncation." |
| **Redis for rate limits** | In-memory rate limits are lost on restart and can't be shared across gateways (relevant at scale). Redis sorted-set sliding window is the Hydra standard. | §9.5: "Redis sorted-set sliding window implementation." |

---

## 4. KV Cache Math for This Cluster

Per Hydra §3, adapted for pilot hardware.

### 4.1 KV Cache Formula

```
KV_bytes_per_token = 2 × num_layers × num_kv_heads × head_dim × bytes_per_element
```

### 4.2 Node 2 (M3, 16 GB) — Qwen 2.5 Coder 7B Q4_K_M

```
Model architecture:
  Layers:    28
  KV heads:  4  (GQA — this is why Qwen is chosen)
  Head dim:  128
  KV dtype:  FP16

KV bytes per token:
  2 × 28 × 4 × 128 × 2 = 57,344 bytes = 0.055 MB/token

Memory partitioning:
  Total RAM                     : 16,384 MB
  macOS + daemons               :  2,500 MB
  vLLM-MLX runtime              :    500 MB
  ─────────────────────────────────────────
  Available                     : 13,384 MB
  Model weights (Q4_K_M, MLX)   :  4,500 MB
  ─────────────────────────────────────────
  KV cache budget               :  8,884 MB
```

### 4.3 Concurrent Request Capacity (Node 2)

```
┌──────────────┬────────────┬─────────────────┬───────────────────┐
│ Context limit│ KV / req   │ Theoretical max │ Operational (×0.6)│
├──────────────┼────────────┼─────────────────┼───────────────────┤
│ 1K tokens    │ 55 MB      │ 161             │ 96                │
│ 2K tokens    │ 110 MB     │ 80              │ 48                │
│ 4K tokens    │ 220 MB     │ 40              │ 24                │
│ 8K tokens    │ 440 MB     │ 20              │ 12                │
│ 16K tokens   │ 880 MB     │ 10              │ 6                 │
└──────────────┴────────────┴─────────────────┴───────────────────┘

Operational target = theoretical × 0.60
  (Hydra §3.4: headroom for prefill spikes and OS jitter)

PILOT DEFAULT: 4K context → 24 concurrent requests per node
```

### 4.4 Throughput Estimates (Node 2, M3)

Per Hydra §16.1:

```
M3, Qwen 2.5 7B Q4_K_M:
  Single-request decode:   22–30 tok/s
  At 24 concurrent (4K):   200–350 aggregate tok/s
  TTFT (prefill, 4K):      ~500ms–1.5s
```

### 4.5 Comparison: What Hydra Predicted vs What Ollama Would Give

```
┌──────────────────┬────────────────────┬──────────────────────────┐
│ Metric           │ vLLM-MLX (Hydra)   │ Ollama (original pilot)  │
├──────────────────┼────────────────────┼──────────────────────────┤
│ Concurrent reqs  │ 24 (paged KV)      │ 4-6 (OLLAMA_NUM_PARALLEL)│
│ Aggregate tok/s  │ 200-350            │ 30-50                    │
│ KV fragmentation │ None (paged)       │ Grows under sustained    │
│ Batch efficiency │ Continuous batching│ Fixed batch at start     │
│ TTFT (4K ctx)    │ 500ms-1.5s         │ 1-3s                     │
│ Memory wasted    │ Minimal            │ 20-40% fragmentation     │
├──────────────────┴────────────────────┴──────────────────────────┤
│ vLLM-MLX delivers 4-7x more effective capacity per node.         │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. Model Selection

### 5.1 Primary Model: Qwen 2.5 Coder 7B Instruct

| Attribute | Value | Rationale |
|-----------|-------|-----------|
| Model | Qwen 2.5 Coder 7B Instruct | Code-specialized training; top of HumanEval at 7B class |
| Quantization | MLX 4-bit (≈ Q4_K_M) | < 1% quality loss; maximizes KV budget (Hydra §7.2) |
| Weight size | ~4.5 GB | Leaves 8.9 GB for KV cache |
| KV heads | 4 (GQA) | 2× more concurrent than Llama 3.1 8B (Hydra §4.2) |
| Native context | 128K | Runtime capped at 8K (Hydra §3.6) |
| License | Apache 2.0 | No commercial restrictions |
| Use cases | Code completion, chat, inline edit, code review, test generation |

### 5.2 Why Not Other Models

| Model | Why Not (for this pilot) |
|-------|--------------------------|
| Llama 3.1 8B | 8 KV heads → 0.125 MB/tok → only 10 concurrent at 4K. Half of Qwen's capacity. (Hydra §3.4) |
| Qwen 2.5 14B | Weights 9.0 GB, KV budget only 4.4 GB → max 3 concurrent at 4K. Too constrained for multi-user pilot. (Hydra §3.4) |
| DeepSeek Coder V2 16B | Exceeds weight ceiling at ~9+ GB. Marginal concurrent capacity. |
| Phi-3 Mini 3.8B | Fits easily but quality insufficient for code review/reasoning tasks. |
| Any 70B model | Requires distributed inference. Not viable on 2 nodes. (Hydra §6.5) |

### 5.3 Quantization Policy (Hydra §7.4, applied to pilot)

```
RULE 1: FP16 BLOCKED for the 7B model. Uses 14 GB; exceeds weight ceiling.
RULE 2: Q8_0 BLOCKED for serving. Uses 7.5 GB; leaves only 5.9 GB KV budget.
         (Only 5.9 GB / 220 MB = 26 theoretical, 16 operational — acceptable
          but Q4 gives 50% more concurrent for < 1% quality loss.)
RULE 3: Q4_K_M (MLX 4-bit) is the ONLY permitted serving quantization.
RULE 4: Q2_K BLOCKED. 15% quality degradation. (Hydra §7.4 RULE 4)
```

---

## 6. Inference Runtime

### 6.1 vLLM-MLX Configuration

Per Hydra §5.2 and §8.5:

```bash
# Node 2 startup command
vllm-mlx serve Qwen/Qwen2.5-Coder-7B-Instruct \
  --quantization mlx-4bit \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90 \
  --max-num-seqs 24 \
  --port 8000 \
  --host 0.0.0.0
```

| Flag | Value | Rationale |
|------|-------|-----------|
| `--quantization mlx-4bit` | MLX native 4-bit | Equivalent to Q4_K_M (Hydra §7.5) |
| `--max-model-len 8192` | 8K tokens | Runtime enforcement cap (Hydra §3.6, defense-in-depth) |
| `--gpu-memory-utilization 0.90` | 90% | Hydra standard; leaves 10% for OS jitter |
| `--max-num-seqs 24` | 24 concurrent | From KV cache math at 4K context (§4.3 above) |
| `--port 8000` | Internal only | Not exposed to developer network (Hydra §12.5) |

### 6.2 Why Not Ollama (Hydra §5.2)

Hydra's runtime analysis explicitly positions Ollama as a fallback:

> "Ollama: No paged KV cache. Memory fragmentation under sustained load.
> Single-instance-per-model constraint means horizontal scaling requires
> node-level routing. Model loading blocks in-flight requests."
>
> "Use case in this stack: Fallback for vision models not yet supported by
> vLLM-MLX. Acceptable for low-concurrency speech-adjacent workloads."

For the pilot, which validates multi-user serving under realistic load, vLLM-MLX is mandatory.

### 6.3 vLLM-MLX Key Features Used

```
┌─────────────────────────────────────────────────────────────────┐
│                    vLLM-MLX ON NODE 2                            │
│                                                                 │
│  CONTINUOUS BATCHING                                            │
│  ─────────────────                                              │
│  New requests join mid-decode. No head-of-line blocking.        │
│  Developer A's long response doesn't block Developer B's        │
│  short autocomplete. This is the single most important          │
│  feature for multi-tenant serving.                              │
│                                                                 │
│  PAGED KV CACHE                                                 │
│  ──────────────                                                 │
│  KV cache allocated in fixed-size pages (like OS virtual mem).  │
│  Eliminates fragmentation that causes Ollama to waste 20-40%    │
│  of memory under sustained load. Each of the 24 concurrent      │
│  requests gets exactly the KV pages it needs.                   │
│                                                                 │
│  PREFIX CACHING                                                 │
│  ─────────────                                                  │
│  System prompts shared across requests are cached and reused.   │
│  The grounding prompt (§9) is identical for every code request. │
│  After the first request, subsequent ones skip re-computing     │
│  the system prompt's KV cache. Hydra §5.2: "28× speedup on     │
│  repeated image queries" — similar principle for repeated       │
│  system prompts.                                                │
│                                                                 │
│  PREEMPTION + RECOMPUTE                                         │
│  ─────────────────────                                          │
│  If KV cache fills (shouldn't with 24 max-seqs guard), vLLM    │
│  can preempt lower-priority requests and recompute their KV     │
│  cache later. Graceful degradation instead of OOM crash.        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. Gateway and Governance

### 7.1 LiteLLM Proxy Configuration

Per Hydra §2.3 and §9.3, simplified for pilot:

```yaml
# hydra-pilot-litellm-config.yaml

model_list:
  - model_name: "hydra-coder"
    litellm_params:
      model: "openai/Qwen2.5-Coder-7B-Instruct"
      api_base: "http://<NODE_2_IP>:8000/v1"
      stream: true
      max_tokens: 4096

  # Alias for autocomplete (same model, lower max_tokens)
  - model_name: "hydra-autocomplete"
    litellm_params:
      model: "openai/Qwen2.5-Coder-7B-Instruct"
      api_base: "http://<NODE_2_IP>:8000/v1"
      stream: true
      max_tokens: 128
      temperature: 0.05

router_settings:
  routing_strategy: "least-busy"
  num_retries: 1
  timeout: 120
  enable_pre_call_checks: true

general_settings:
  master_key: "sk-hydra-pilot-CHANGE-ME"
  database_url: "sqlite:///opt/hydra/litellm/litellm.db"
  otel: true

litellm_settings:
  success_callback: ["prometheus"]
  failure_callback: ["prometheus"]
  count_tokens: true
  cache: true
  cache_params:
    type: "local"
    ttl: 300
```

### 7.2 Governance Policy (Pilot)

Per Hydra §9.3, scaled for pilot:

```yaml
# hydra-pilot-governance.yaml
governance:
  groups:
    pilot_user:
      tokens_per_day: 200_000
      tokens_per_month: 4_000_000
      requests_per_minute: 30
      requests_per_hour: 500
      max_concurrent_requests: 5
      max_context_tokens: 8_192
      max_generation_tokens: 4_096
      max_input_tokens: 4_096

    admin:
      tokens_per_day: unlimited
      tokens_per_month: unlimited
      requests_per_minute: 60
      max_concurrent_requests: 10
      max_context_tokens: 8_192
      max_generation_tokens: 4_096
      max_input_tokens: 8_192
```

### 7.3 Context Window Enforcement

Per Hydra §9.6 — two layers of defense:

```
Layer 1 — Gateway (pre-flight):
  LiteLLM counts input tokens using Qwen tokenizer.
  Rejects if input_tokens + max_gen_tokens > 8,192.
  Returns HTTP 400 with exact token count.

Layer 2 — Runtime (enforcement):
  vLLM-MLX --max-model-len 8192 is an absolute cap.
  Even if gateway miscounts, runtime rejects the request.
```

**No silent truncation.** Per Hydra §9.6: "Truncation is non-deterministic and leads to malformed prompts from the model's perspective (e.g., cut-off system prompt, split code blocks)."

---

## 8. Observability Stack

### 8.1 Architecture

```
┌──────────────────────────────────────────────────────────┐
│                 NODE 1 (Gateway)                          │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │              GRAFANA (:3000)                      │    │
│  │                                                   │    │
│  │  Dashboard 1: Cluster Health                      │    │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────────┐ │    │
│  │  │Node UP │ │Req/sec │ │Agg     │ │KV cache %  │ │    │
│  │  │status  │ │        │ │tok/s   │ │            │ │    │
│  │  └────────┘ └────────┘ └────────┘ └────────────┘ │    │
│  │                                                   │    │
│  │  Dashboard 2: Request Performance                 │    │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────────┐ │    │
│  │  │TTFT    │ │Latency │ │Batch   │ │Errors      │ │    │
│  │  │P50/P95 │ │P50/95  │ │size    │ │by type     │ │    │
│  │  └────────┘ └────────┘ └────────┘ └────────────┘ │    │
│  │                                                   │    │
│  │  Dashboard 3: Token & Budget Usage                │    │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────────┐ │    │
│  │  │In/Out  │ │Context │ │Budget  │ │Rate limit  │ │    │
│  │  │tok/min │ │window  │ │usage % │ │hits        │ │    │
│  │  └────────┘ └────────┘ └────────┘ └────────────┘ │    │
│  │                                                   │    │
│  │  Dashboard 4: Guardrails                          │    │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────────┐ │    │
│  │  │Secrets │ │Context │ │Temp    │ │Live logs   │ │    │
│  │  │blocked │ │rejects │ │caps    │ │(Loki)      │ │    │
│  │  └────────┘ └────────┘ └────────┘ └────────────┘ │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  Prometheus (:9090) ←── scrapes:                         │
│    • vLLM-MLX (:8000/metrics) on Node 2                  │
│    • LiteLLM  (:4000/metrics) on Node 1                  │
│    • node_exporter (:9100) on both nodes                 │
│    • Redis exporter (:9121) on Node 1                    │
│                                                          │
│  Loki (:3100) ←── receives from:                         │
│    • Promtail on Node 1 (LiteLLM + gateway logs)         │
│    • Promtail on Node 2 (vLLM-MLX + system logs)         │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 8.2 Key Metrics (from Hydra §15.2)

**vLLM-MLX metrics (Node 2):**

| Metric | Type | What it tells you |
|--------|------|------------------|
| `vllm_mlx_tokens_generated_total` | Counter | Total output tokens served |
| `vllm_mlx_time_to_first_token_seconds` | Histogram | Prefill latency — are users waiting too long? |
| `vllm_mlx_inter_token_latency_seconds` | Histogram | Streaming smoothness — are tokens stuttering? |
| `vllm_mlx_num_requests_running` | Gauge | Active concurrent requests — are we at capacity? |
| `vllm_mlx_kv_cache_usage_percent` | Gauge | **Critical**: if this nears 85%, node is overloaded |
| `vllm_mlx_batch_size` | Gauge | How well continuous batching is utilized |
| `vllm_mlx_gpu_cache_hit_rate` | Gauge | Prefix cache effectiveness (system prompt reuse) |

**Gateway metrics (LiteLLM):**

| Metric | Type | What it tells you |
|--------|------|------------------|
| `litellm_requests_total{status}` | Counter | Request volume and error rate |
| `litellm_rate_limit_hits_total` | Counter | Are users hitting limits? Time to adjust? |
| `litellm_budget_exhausted_total` | Counter | Token budget overruns |
| `litellm_context_reject_total` | Counter | Context-too-long rejections — are devs hitting limits? |

### 8.3 Alert Rules (Hydra §15.4, adapted for pilot)

| Alert | Severity | Condition | What to do |
|-------|----------|-----------|------------|
| `InferenceNodeDown` | Critical | Node 2 health check fails > 30s | SSH to Node 2; check vLLM-MLX process |
| `KVCacheNearFull` | Warning | KV usage > 85% for 5 min | Reduce `--max-num-seqs` or context limit |
| `HighTTFT` | Warning | P95 TTFT > 5s for 5 min | Check Node 2 load; thermal throttling? |
| `GatewayDown` | Critical | LiteLLM health fails | Check Node 1; restart LiteLLM |
| `MemoryPressure` | Warning | Node 2 MemAvailable < 2 GB | Potential OOM; check KV math |

---

## 9. Guardrails and Hallucination Control

### 9.1 Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│              GUARDRAIL PIPELINE (in LiteLLM)                 │
│                                                             │
│  ════════════ PRE-CALL ════════════                         │
│                                                             │
│  1. Rate limit (Redis)         → HTTP 429 if exceeded       │
│  2. Token budget (Redis)       → HTTP 429 if exhausted      │
│  3. Input token count (Qwen    → HTTP 400 if context        │
│     tokenizer, exact)             exceeds 8,192             │
│  4. Secret scan (input)        → Log warning, pass through  │
│  5. Grounding prompt inject    → Prepend anti-hallucination │
│                                   system prompt             │
│  6. Temperature cap            → Force ≤ 0.3 for code      │
│                                   Force ≤ 0.05 for complete │
│                                                             │
│  ════════════ POST-CALL ════════════                        │
│                                                             │
│  7. Secret scan (response)     → BLOCK if response leaks   │
│                                   API keys / passwords      │
│  8. PII scan (response)        → Log warning               │
│  9. Hallucination heuristics   → Flag fabricated URLs,      │
│                                   suspicious import names   │
│  10. Audit log write           → JSON record to Loki        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 9.2 Grounding Prompt (Hallucination Control)

Injected as system prompt prefix on every code request:

```
You are a precise coding assistant. Follow these rules strictly:
1. Only suggest code using libraries and APIs you are confident exist.
2. If unsure whether a function or library exists, say so explicitly.
3. Do not invent import paths, package names, or API endpoints.
4. When referencing documentation, only cite sources you are certain about.
5. If the request is ambiguous, ask for clarification rather than guessing.
6. Prefer well-known, stable libraries over obscure ones.
7. Do not hallucinate file paths or project structure — only reference
   what has been provided in context.
```

This prompt is identical across all requests → **prefix caching in vLLM-MLX** means its KV cache is computed once and reused, adding zero latency after the first request.

### 9.3 Secret Detection Patterns

```
Scanned in both input (warning) and output (block):

  • API keys:        sk-..., ghp_..., xoxb-..., AKIA...
  • Passwords:       password = "...", passwd: "..."
  • Private keys:    -----BEGIN PRIVATE KEY-----
  • Connection URIs: postgres://, mongodb://, redis://
  • AWS secrets:     aws_secret_access_key = "..."
```

---

## 10. IDE Tooling and Developer Experience

### 10.1 Recommended Setup Per Developer

```
┌─────────────────────────────────────────────────────────────┐
│                 DEVELOPER WORKSTATION                         │
│                                                             │
│  VS Code                                                    │
│  ├── Continue.dev extension                                 │
│  │   ├── Chat: Cmd+L → "hydra-coder" model                │
│  │   ├── Inline edit: Cmd+I → "hydra-coder" model          │
│  │   ├── @codebase → local embedding (nomic-embed-text)    │
│  │   ├── @file, @folder, @diff, @terminal → context        │
│  │   └── /review, /test, /fix, /explain → custom commands  │
│  │                                                         │
│  ├── Tab autocomplete (built into Continue.dev)            │
│  │   └── "hydra-autocomplete" model (same model,           │
│  │        temp=0.05, max_tokens=128)                       │
│  │                                                         │
│  └── Cline extension (optional — for power users)          │
│      └── Agent mode: creates files, runs commands          │
│          Uses "hydra-coder" model                          │
│                                                             │
│  All extensions point to:                                   │
│    API Base: http://<NODE_1_IP>:4000/v1                     │
│    API Key:  sk-hydra-pilot-CHANGE-ME                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 10.2 What Developers Get

| Feature | How it works | Expected quality (7B model) |
|---------|-------------|---------------------------|
| **Tab autocomplete** | Ghost text while typing. 300ms debounce, 128 token max. | Good for line completion, function signatures. Struggles with multi-line complex logic. |
| **Chat (Cmd+L)** | Ask questions, get code. Full codebase context via @codebase. | Good for explanations, simple refactors, boilerplate. Weaker than GPT-4/Claude on complex reasoning. |
| **Inline edit (Cmd+I)** | Select code, describe change, model rewrites in place. | Good for targeted edits ("make this async", "add error handling"). |
| **/review** | Paste code, get review. | Catches common bugs, style issues. Misses subtle logic errors. |
| **/test** | Generate unit tests from selected code. | Generates reasonable test structure. May hallucinate assertion values. |
| **/fix** | Select error + code, get fix suggestion. | Good for syntax errors, common patterns. Weaker on complex bugs. |
| **Cline agent** | Describe a task; Cline creates/edits files, runs terminal. | Keep tasks small and specific. "Add a REST endpoint for X" works. "Refactor the auth system" will struggle. |

### 10.3 Honest Expectations vs Cursor/Copilot

```
┌──────────────────────────────────────────────────────────────┐
│                 QUALITY COMPARISON                            │
│                                                              │
│  Tab autocomplete:  ████████░░  80% of Copilot quality      │
│  Chat:              ██████░░░░  60% of Cursor quality        │
│  Inline edit:       ██████░░░░  60% of Cursor Cmd+K          │
│  Agent/Composer:    ████░░░░░░  40% of Cursor Composer       │
│  Code review:       ███████░░░  70% — catches real issues    │
│  Test generation:   ██████░░░░  60% — needs human review     │
│                                                              │
│  The gap is model quality (7B vs GPT-4/Claude), not tooling. │
│  If you later add cloud API routing for chat/agent (Hydra    │
│  Option D), quality jumps to 95%+ while keeping local        │
│  autocomplete.                                               │
└──────────────────────────────────────────────────────────────┘
```

---

## 11. Networking and Security

### 11.1 Network Topology (Pilot, simplified from Hydra §12.1)

```
┌────────────────────────────────────────────────────────────┐
│ Corporate LAN                                               │
│                                                            │
│  Developer machines ──→ Node 1 (:4000, :3000)              │
│                              │                             │
│                              │ Internal (same LAN)         │
│                              │                             │
│                         Node 2 (:8000, :9100)              │
│                         (should not be directly accessible  │
│                          by developers — route via Node 1)  │
└────────────────────────────────────────────────────────────┘
```

### 11.2 Security Controls (Pilot Scope)

| Control | Hydra Full | Pilot | Notes |
|---------|-----------|-------|-------|
| API authentication | OIDC + API keys | API keys only | OIDC added at scale |
| Network isolation | VLAN 10/20 + firewall | Single LAN, firewall optional | Acceptable for pilot |
| Model integrity (SHA256) | Mandatory | SHA256 on first load | Full registry in Phase 2 |
| Audit logging | Immutable append-only | JSON to Loki | Good enough for pilot |
| No outbound internet on inference | Enforced | Manual/firewall | Enforce if possible |
| TLS (external) | HAProxy + TLS 1.3 | Plain HTTP | Acceptable on isolated LAN |
| Secrets in env vars | Mandatory | Mandatory | Same standard |

### 11.3 Port Reference

| Port | Node | Service | Access |
|------|------|---------|--------|
| 4000 | Node 1 | LiteLLM API (developer-facing) | Developers |
| 3000 | Node 1 | Grafana | Admins |
| 9090 | Node 1 | Prometheus | Internal |
| 3100 | Node 1 | Loki | Internal |
| 6379 | Node 1 | Redis | Internal (LiteLLM only) |
| 8000 | Node 2 | vLLM-MLX inference | Internal (Node 1 only) |
| 9100 | Both | node_exporter | Internal (Prometheus) |

---

## 12. Failure Handling

### 12.1 Failure Modes (Pilot)

Per Hydra §13.1, adapted for 2-node:

```
┌────────────────────────┬──────────────────────┬─────────────────────┐
│ Failure                │ Impact               │ Recovery            │
├────────────────────────┼──────────────────────┼─────────────────────┤
│ Node 2 (inference)     │ ALL inference stops.  │ Restart vLLM-MLX.  │
│ vLLM-MLX crash         │ No HA in pilot.       │ launchd auto-      │
│                        │                      │ restart. ~30-60s.   │
├────────────────────────┼──────────────────────┼─────────────────────┤
│ Node 2 memory pressure │ Latency spikes.       │ vLLM-MLX preempts  │
│ (KV cache near full)   │ Possible OOM.         │ low-priority reqs. │
│                        │                      │ If OOM: auto-restart│
├────────────────────────┼──────────────────────┼─────────────────────┤
│ Node 1 (gateway)       │ ALL access stops.     │ Restart LiteLLM +  │
│ LiteLLM crash          │ No HA in pilot.       │ Docker stack.       │
│                        │                      │ ~60s.               │
├────────────────────────┼──────────────────────┼─────────────────────┤
│ Node 1 Redis crash     │ Rate limits degrade   │ LiteLLM falls back │
│                        │ to in-memory.         │ to local counters.  │
│                        │ No data loss for      │ Redis auto-restart. │
│                        │ inference.            │                     │
├────────────────────────┼──────────────────────┼─────────────────────┤
│ Network between nodes  │ Inference unreachable.│ Fix network.        │
│                        │ Gateway returns 503.  │ No data loss.       │
├────────────────────────┼──────────────────────┼─────────────────────┤
│ Thermal throttling     │ M3 decode drops       │ Monitor via Grafana.│
│ (Node 2, sustained)   │ ~10-15%.              │ Ensure ventilation. │
└────────────────────────┴──────────────────────┴─────────────────────┘

KEY LIMITATION: 2-node pilot has NO high availability.
Single node failure = service down. This is acceptable for a test cluster.
HA requires ≥2 gateways + keepalived (Hydra §2.1, Phase 2).
```

---

## 13. Capacity Analysis for 100 Developers

### 13.1 This Pilot (2 Nodes, 5–10 Devs)

```
Node 2 capacity (Qwen 2.5 Coder 7B, 4K context):
  24 operational concurrent requests
  200–350 aggregate tok/s

Pilot demand (10 devs):
  Peak concurrent (30%): 3
  Autocomplete: ~1 req/s
  Chat: ~0.05 req/s

Headroom: 24 / 3 = 8× ← very comfortable
```

### 13.2 Scaling to 100 Developers (20 Nodes)

Per Hydra §3.5 and §8.1, mapped to 20 nodes:

```
┌─────────────────────────────────────────────────────────────────┐
│           20-NODE DEPLOYMENT FOR 100 DEVELOPERS                  │
│                                                                 │
│  Infrastructure (2 nodes — M1):                                  │
│    Node 1:  HAProxy + keepalived (primary)                       │
│    Node 2:  HAProxy + keepalived (standby) + Redis               │
│                                                                 │
│  Gateway (2 nodes — M1):                                         │
│    Node 3:  LiteLLM Gateway 1 + Prometheus + Grafana             │
│    Node 4:  LiteLLM Gateway 2 + Loki (cold spare)               │
│                                                                 │
│  Fast Pool — Code (12 nodes — M4/M3):                            │
│    Nodes 5–16: vLLM-MLX, Qwen 2.5 Coder 7B Q4                  │
│    Capacity: 12 × 24 = 288 concurrent (4K ctx)                  │
│    Demand: 100 devs × 10% = 10 concurrent                       │
│    Headroom: 28.8× ← massive                                    │
│                                                                 │
│  Reasoning Pool (3 nodes — M4):                                  │
│    Nodes 17–19: vLLM-MLX, Qwen 2.5 14B Q4                      │
│    Capacity: 3 × 7 = 21 concurrent (4K ctx)                     │
│    Demand: ~1.5 concurrent                                       │
│    Headroom: 14× ← comfortable                                  │
│                                                                 │
│  Embedding (1 node — M1):                                        │
│    Node 20: BGE-M3 FP16, batch serving                           │
│    Capacity: ~1,500 embeddings/s                                 │
│    Demand: minimal (Continue.dev codebase indexing)              │
│                                                                 │
│  TOTAL: 100 devs on 20 Mac Minis = very comfortable             │
│  Room to grow to 500+ devs on same hardware before stress       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 13.3 When You Actually Need More Than 20 Nodes

Based on Hydra's throughput analysis:

```
┌──────────────┬───────────────────────────────┐
│ Dev count    │ Minimum nodes needed          │
├──────────────┼───────────────────────────────┤
│ 5–10         │ 2 (this pilot)                │
│ 10–100       │ 6–8 (fast pool + infra)       │
│ 100–500      │ 12–20 (add reasoning pool)    │
│ 500–1,000    │ 25–40 (Hydra full spec)       │
│ 1,000–2,000  │ 40+ or hybrid cloud           │
└──────────────┴───────────────────────────────┘
```

---

## 14. Scaling Path: Pilot → 20 Nodes → 40 Nodes

```
PHASE 1 (NOW)                PHASE 2                    PHASE 3
2 nodes, 5-10 devs          20 nodes, 100 devs         40 nodes, 500-1000 devs
─────────────────           ──────────────────         ──────────────────────

┌──────────┐                ┌──────────┐               ┌──────────┐
│ Gateway  │                │ HAProxy  │               │ HAProxy  │
│ (M1 8GB) │                │ (2×M1)   │               │ (2×M1)   │
│          │                │ LiteLLM  │               │ LiteLLM  │
│ LiteLLM  │                │ (2×M1)   │               │ (3×M1)   │
│ +Monitor │                │ +Monitor │               │ +Monitor │
│ +Redis   │                │ +Redis   │               │ +Redis HA│
│ +Grafana │                │ +Grafana │               │ +PG+MinIO│
└────┬─────┘                └────┬─────┘               └────┬─────┘
     │                           │                          │
┌────▼─────┐                ┌────▼─────┐               ┌────▼─────┐
│ Inference│                │Fast Pool │               │Fast Pool │
│ (M3 16GB)│                │(12 nodes)│               │(20 nodes)│
│          │                │Qwen 7B   │               │Qwen 7B   │
│ vLLM-MLX │                │          │               │          │
│ Qwen 7B  │                │Reasoning │               │Reasoning │
│          │                │(3 nodes) │               │(8 nodes) │
│ 24 conc. │                │Qwen 14B  │               │Qwen 14B  │
│          │                │          │               │          │
│          │                │Embed     │               │Vision    │
│          │                │(1 node)  │               │(6 nodes) │
│          │                │BGE-M3    │               │MiniCPM-V │
│          │                │          │               │          │
│          │                │          │               │Embed     │
│          │                │          │               │(3 nodes) │
│          │                │          │               │          │
│          │                │          │               │Speech    │
│          │                │          │               │(1 node)  │
└──────────┘                └──────────┘               └──────────┘

What changes at each phase:

Pilot → Phase 2:
  ✓ Add HAProxy + keepalived for HA
  ✓ Add second LiteLLM gateway
  ✓ Introduce node pools (fast, reasoning, embed)
  ✓ Add PostgreSQL model registry
  ✓ Add MinIO model store
  ✓ VLAN isolation
  ✓ OIDC authentication
  ✓ Ansible scales from 2 to 20 nodes

Phase 2 → Phase 3 (Hydra full):
  ✓ Add Vision pool (MiniCPM-V 2.6)
  ✓ Add Speech pool (Whisper Large v3 Turbo)
  ✓ Add mDNS/Consul service discovery
  ✓ 4-dashboard Grafana with capacity planning
  ✓ Model approval workflow
  ✓ Optional: distributed pool (exo, 30B, off-peak)
  ✓ Optional: hybrid cloud routing for premium chat
```

### 14.1 What Carries Forward Unchanged

| Component | Pilot | 20-node | 40-node |
|-----------|-------|---------|---------|
| vLLM-MLX runtime | Same | Same | Same |
| LiteLLM governance model | Same | Extended (OIDC, groups) | Full Hydra spec |
| Qwen 2.5 Coder 7B Q4 | Same | Same (fast pool) | Same (fast pool) |
| Prometheus + Grafana | Same | Same (more targets) | Same (more dashboards) |
| Guardrail pipeline | Same | Same | Same |
| Continue.dev / Cline config | Same | Same (change API base if needed) | Same |
| Ansible roles | Same | Add roles for HAProxy, pools | Add roles for vision, speech |

---

## 15. Deployment Procedure

### 15.1 Prerequisites

```
On your control machine (laptop):
  1. Ansible installed: pip install ansible
  2. SSH key deployed to both Mac Minis: ssh-copy-id admin@<ip>
  3. Both Mac Minis on same LAN, static IPs assigned
  4. Both Mac Minis running macOS Ventura (13) or later
```

### 15.2 Node 2 (M3, 16 GB) — Inference Setup

```bash
# 1. Install Xcode command-line tools
xcode-select --install

# 2. Install Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 3. Install Python 3.11+
brew install python@3.11

# 4. Install vLLM-MLX
pip3 install vllm-mlx

# 5. Download and serve model
vllm-mlx serve Qwen/Qwen2.5-Coder-7B-Instruct \
  --quantization mlx-4bit \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90 \
  --max-num-seqs 24 \
  --port 8000 \
  --host 0.0.0.0

# 6. Verify
curl http://localhost:8000/v1/models
```

### 15.3 Node 1 (M1, 8 GB) — Gateway Setup

```bash
# 1. Install Homebrew + Docker (Colima)
brew install docker docker-compose colima python@3.11 redis

# 2. Start Colima (lightweight Docker)
colima start --cpu 4 --memory 2 --disk 20 --arch aarch64

# 3. Install LiteLLM
pip3 install 'litellm[proxy]'

# 4. Start Redis
brew services start redis

# 5. Start monitoring stack (Prometheus + Grafana + Loki)
cd /opt/hydra/docker && docker-compose up -d

# 6. Start LiteLLM
litellm --config /opt/hydra/litellm/config.yaml \
  --port 4000 --num_workers 4

# 7. Verify
curl http://localhost:4000/health
```

### 15.4 Developer Machine

```bash
# 1. Install Continue.dev extension in VS Code

# 2. Copy config
cp continue-dev/config.yaml ~/.continue/config.yaml

# 3. Edit: replace GATEWAY_IP with Node 1's IP

# 4. Open VS Code → Cmd+L for chat, Tab for autocomplete
```

---

## 16. Validation Checklist

Run these after deployment to confirm the pilot is working:

```
□ Node 2: vLLM-MLX responds to /v1/models
□ Node 2: vLLM-MLX metrics available at :8000/metrics
□ Node 1: LiteLLM health check passes at :4000/health
□ Node 1: Grafana accessible at :3000 (admin/admin)
□ Node 1: Prometheus targets all UP at :9090/targets

□ Inference: Chat completion returns valid code response
□ Inference: Streaming works (tokens arrive incrementally)
□ Inference: Autocomplete returns in < 1 second

□ Governance: Rate limit triggers at > 30 req/min (test with loop)
□ Governance: Context overflow rejected with HTTP 400 + token count
□ Governance: Token budget tracks and decrements

□ Guardrails: Secret in response is blocked
□ Guardrails: Temperature capped at 0.3 (check via /debug)
□ Guardrails: Grounding prompt injected (check logs)

□ Observability: TTFT histogram populating in Grafana
□ Observability: KV cache % gauge visible
□ Observability: Token throughput graph updating
□ Observability: Logs visible in Loki via Grafana Explore

□ IDE: Continue.dev chat works (Cmd+L)
□ IDE: Continue.dev autocomplete works (Tab)
□ IDE: /review command produces useful output
□ IDE: /test command generates runnable tests

□ Soak test: Run load-test.sh with 5 concurrent for 30 min
□ Soak test: No OOM, no process crashes, KV cache < 85%
□ Soak test: P95 TTFT remains < 5 seconds
```

---

## 17. Reference Links

### Infrastructure

| Tool | Purpose | Link |
|------|---------|------|
| vLLM-MLX | Inference runtime (continuous batching, paged KV) | https://github.com/vllm-project/vllm-mlx |
| LiteLLM Proxy | API gateway, routing, governance | https://github.com/BerriAI/litellm |
| Prometheus | Metrics collection | https://prometheus.io |
| Grafana | Dashboards | https://grafana.com |
| Loki | Log aggregation | https://grafana.com/oss/loki |
| Redis | Rate limit counters | https://redis.io |
| Colima | Lightweight Docker for macOS | https://github.com/abiosoft/colima |
| Ansible | Deployment automation | https://docs.ansible.com |

### Models

| Model | Purpose | Link |
|-------|---------|------|
| Qwen 2.5 Coder 7B Instruct | Primary code model | https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct |
| Qwen 2.5 14B Instruct | Reasoning (Phase 2) | https://huggingface.co/Qwen/Qwen2.5-14B-Instruct |
| MiniCPM-V 2.6 | Vision (Phase 3) | https://huggingface.co/openbmb/MiniCPM-V-2_6 |
| BGE-M3 | Embeddings | https://huggingface.co/BAAI/bge-m3 |
| Whisper Large v3 Turbo | Speech-to-text (Phase 3) | https://huggingface.co/openai/whisper-large-v3-turbo |

### IDE Tools

| Tool | Purpose | Link |
|------|---------|------|
| Continue.dev | VS Code AI coding (chat, edit, autocomplete) | https://continue.dev |
| Cline | Agentic multi-file editing | https://github.com/cline/cline |
| Tabby | Self-hosted code completion (VS Code + JetBrains) | https://tabby.tabbyml.com |

### Hydra Architecture (Parent Spec)

| Document | Location |
|----------|----------|
| Hydra Architecture Specification v1.0 | `docs/hydra_architecture.md` |
| Hydra Architecture PDF | `docs/hydra_architecture.pdf` |

---

*This document is the pilot implementation of the Hydra Architecture Specification.
All technical decisions reference the parent specification by section number.
When this pilot validates successfully, proceed to Phase 2 (20-node deployment)
using Hydra §8.1 pool allocation as the target architecture.*
