# Hydra — System Architecture

> For the full specification see `docs/hydra_architecture.md`

---

## Cluster Overview

```
Platform:     40 × Apple Silicon Mac Mini (M1 / M3 / M4)
RAM per node: 16 GB unified memory
Network:      1 Gbps Ethernet (LAN)
Scale target: ≤ 1,000 internal users
Deployment:   On-premises, air-gapped-capable
```

---

## High-Level Topology

```
┌─────────────────────────────────────────────────────────────┐
│  Clients  (≤1,000 users — OpenAI-compatible REST API)       │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS
┌──────────────────────▼──────────────────────────────────────┐
│  HAProxy + keepalived  (active/passive, 2 nodes)            │
└──────────┬─────────────────────────┬───────────────────────┘
           │                         │
┌──────────▼──────────┐  ┌───────────▼──────────┐
│  LiteLLM Gateway 1  │  │  LiteLLM Gateway 2   │
│  :4000              │  │  :4000               │
└──────────┬──────────┘  └───────────┬──────────┘
           └──────────────┬───────────┘
                          │  tag-based routing
     ┌────────────────────┼────────────────────────┐
     │             │             │            │     │
┌────▼────┐  ┌─────▼─────┐  ┌───▼───┐  ┌────▼──┐ ┌▼────────┐
│FastPool │  │ReasonPool  │  │Vision │  │Embed  │ │Speech   │
│20 nodes │  │8 nodes     │  │6 nodes│  │4 nodes│ │2 nodes  │
│8B Q4    │  │14B Q4      │  │8B VL  │  │BGE-M3 │ │Whisper  │
│M4 chip  │  │M4 chip     │  │M3 chip│  │M1/M3  │ │M1 chip  │
└────┬────┘  └─────┬──────┘  └───┬───┘  └────┬──┘ └────┬────┘
     └─────────────┼─────────────┼────────────┘         │
                   │             │                       │
         ┌─────────▼─────────────▼───────────────────────▼───┐
         │  Model Store  (MinIO / NFS — dedicated node pair)  │
         └────────────────────────────────────────────────────┘
                   │
         ┌─────────▼──────────────────┐
         │  Observability Stack        │
         │  OTEL → Prometheus → Grafana│
         └────────────────────────────┘
```

---

## Hardware Tiers

| Node Pool | Count | Chip | Bandwidth | Primary Role |
|-----------|-------|------|-----------|-------------|
| FastPool | 20 | Apple M4 | 120 GB/s | Chat autocomplete, short inference |
| ReasonPool | 8 | Apple M4 | 120 GB/s | Long-context reasoning |
| VisionPool | 6 | Apple M3 | 102 GB/s | Multimodal (image + text) |
| EmbedPool | 4 | Apple M1/M3 | 68–102 GB/s | Embeddings (BGE-M3) |
| SpeechPool | 2 | Apple M1 | 68 GB/s | ASR (Whisper) |
| Gateway | 2 | Apple M1 | — | LiteLLM + HAProxy (no inference) |
| Model Store | 2 | Apple M1 | — | MinIO / NFS model artifacts |

### Per-Node Memory Budget

```
Total unified RAM:             16,384 MB
macOS + system daemons:        ~2,500 MB
Runtime overhead:               ~500 MB
Available for weights + KV:   ~13,400 MB
Model weight ceiling (60%):    ~9,600 MB   ← hard limit
KV cache budget:              ~3,800–8,000 MB (context-dependent)
```

The 60% rule prevents the OS page daemon from evicting model weights mid-request (10–100× latency spike).

---

## Inference Runtimes

| Runtime | Hardware | Use case |
|---------|----------|---------|
| **vLLM-MLX** | Apple Silicon | Production serving (continuous batching, PagedAttention) |
| **llama.cpp** | Apple (Metal) + NVIDIA (CUDA) + CPU | Benchmarking, development, CPU-offload for large models |
| **Ollama** | Apple + Linux | Developer-facing API, model management |
| **MLX-LM** | Apple Silicon | Research, fine-tuning evaluation |

### Hardware Constraints (Apple Silicon)

- **No CUDA / No NCCL** — all GPU compute via Metal or MLX
- **No GPU–GPU RDMA** — cross-node communication over 1 Gbps Ethernet
- **Unified memory** — CPU and GPU share the same 16 GB pool; no separate VRAM

---

## Phase 1 Pilot Hardware

Three machines benchmarked before committing to 40-node pool:

| ID | Machine | Memory | GPU |
|----|---------|--------|-----|
| `p1-m2-8g` | Apple Mac (M2) | 8 GB unified | Apple Metal |
| `p1-m3-16g` | Apple Mac Mini (M3) | 16 GB unified | Apple Metal |
| `p1-i7-rtx3050` | Linux workstation | 64 GB DDR5 + 4 GB VRAM | NVIDIA RTX 3050 |

See `benchmarkings.md` for Phase 1 results and the 40-node pool decision framework.

---

## Observability Stack

```
Per-node agent chain:
  Hardware exporter (apple-silicon-exporter / dcgm-exporter / node_exporter)
  → OTEL Collector agent  (resource labels: node_id, gpu_provider, pool, chip)
  → OTEL Gateway tier     (2+ instances, remote_write)
  → Prometheus / Mimir
  → Grafana (3 dashboards: Fleet Overview, Node Deep Dive, Quality Monitor)
  → Alertmanager          (50+ rules, Slack + PagerDuty)
```

Label taxonomy applied at ingest (all metrics carry these labels):

| Label | Example |
|-------|---------|
| `node_id` | `p1-m3-16g` |
| `hostname` | `mac-m3-16g` |
| `cluster` | `hydra-prod` |
| `gpu_provider` | `apple` / `nvidia` / `amd` |
| `pool` | `fast` / `reason` / `embed` |
| `provider` | `llamacpp` / `ollama` / `vllm-mlx` |
| `model` | `llama3-8b-q4_k_m` |
| `quantization` | `q4_k_m` / `f16` / `mxfp4` |

---

## Governance & Policy

All inference requests flow through **LiteLLM Gateway**:

- OIDC / API-key authentication → group membership
- Per-user and per-group token budgets + rate limits
- Task-category routing (autocomplete → FastPool, reasoning → ReasonPool)
- Context-window enforcement (reject requests exceeding model limits)
- Structured audit logging to persistent store

---

## Quantization Policy

| Tier | Default quant | RAM footprint | Notes |
|------|--------------|---------------|-------|
| FastPool 8B | Q4_K_M | ~4.9 GB | Balances speed and quality |
| ReasonPool 14B | Q4_K_M | ~8.9 GB | Fits within 9.6 GB ceiling |
| Embedding BGE-M3 | F16 | ~1.1 GB | Quality-sensitive |
| Speech Whisper | F16 | ~3.0 GB | Quality-sensitive |

F16 exceeds the 9.6 GB weight ceiling for 14B+ models on 16 GB nodes — Q4_K_M is the operational floor for those sizes.

---

## Documents & References

| Document | Location |
|----------|---------|
| Full architecture spec (40-node) | `docs/hydra_architecture.md` |
| Architecture Decision Record | `docs/mac-mini-cluster/architecture-decision-document.md` |
| Pilot cluster spec (2-node) | `docs/mac-mini-cluster/hydra-pilot-architecture.md` |
| Hybrid cluster architecture | `docs/hybrid-cluster-architecture.md` |
| Monitoring design spec | `docs/superpowers/specs/2026-04-28-monitoring-ansible-design.md` |
| Benchmark log | `benchmarkings.md` |
| Operations reference | `commands.md` |
