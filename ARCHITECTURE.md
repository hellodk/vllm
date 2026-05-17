# Hydra — System Architecture

> Full specification: `docs/hydra_architecture.md`  
> **Revision 1.2 — 2026-05-16** — 28 × M4 16 GB · 10 GbE + RDMA · zero internet

---

## Cluster Overview

```
Platform:     28 × Apple Silicon Mac Mini M4  (uniform, homogeneous)
RAM per node: 16 GB unified memory  ·  120 GB/s memory bandwidth
Storage:      512 GB NVMe SSD per node
Network:      10 GbE  (MTU 9000 jumbo frames)  +  RDMA RoCE-v2
Scale target: ≤1,000 internal users
Deployment:   On-premises  ·  air-gapped  ·  zero internet
```

---

## High-Level Topology

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Clients  (≤1,000 users — OpenAI-compatible REST API)                        │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │ HTTPS/TLS
┌──────────────────────────────▼───────────────────────────────────────────────┐
│  HAProxy + keepalived                                                         │
│  hydra-gw-01 (active)  ·  hydra-gw-02 (passive)                             │
│  Layer-4 TCP  ·  VIP failover <10s  ·  Health-check every 5s                │
└──────────────┬──────────────────────────────────────┬────────────────────────┘
               │                                      │
┌──────────────▼──────────────┐         ┌─────────────▼─────────────┐
│  LiteLLM Gateway 1  :4000  │         │  LiteLLM Gateway 2  :4000  │
│  Auth · Rate limits         │         │  Stateless · Redis-backed  │
│  Tag-based routing          │         │  Token budgets · Audit log │
└──────────────┬──────────────┘         └─────────────┬─────────────┘
               └──────────────────┬───────────────────┘
                                  │  tag-based routing  ·  10 GbE
        ┌─────────────────────────┼──────────────────────────────────────────┐
        │              │              │              │           │            │
┌───────▼──┐  ┌────────▼──┐  ┌───────▼──┐  ┌───────▼──┐  ┌────▼──┐  ┌─────▼──┐
│FastPool  │  │ReasonPool │  │LargePool │  │VisionPool│  │Embed  │  │Speech  │
│10 × M4   │  │4 × M4     │  │4 × M4    │  │3 × M4    │  │2 × M4 │  │1 × M4  │
│8B Q4_K_M │  │14B Q4_K_M │  │32B–70B   │  │8B VL Q4  │  │BGE-M3 │  │Whisper │
│          │  │           │  │TP pairs  │  │          │  │       │  │        │
└──────────┘  └───────────┘  └───┬───┬──┘  └──────────┘  └───────┘  └────────┘
                                  │   │
                      RDMA RoCE-v2 (sub-2 μs AllReduce)
                      Group A: large-01 ↔ large-02
                      Group B: large-03 ↔ large-04

                         10 GbE switch  (MTU 9000, PFC enabled)
                                  │
        ┌─────────────────────────┼──────────────────────────┐
        │                         │                          │
┌───────▼────────────┐  ┌─────────▼──────────────┐  ┌───────▼────────┐
│  Model Store/Reg.  │  │  Services node          │  │  Monitoring    │
│  hydra-store-01    │  │  hydra-svc-01           │  │  (per node)    │
│  MinIO    :9000    │  │  Fleet API   :8000      │  │  OTEL Coll.    │
│  Registry :8100    │  │  PostgreSQL  :5432      │  │  → Victoria    │
│  sha256 catalog    │  │  Redis       :6379      │  │  → Grafana     │
│  approval workflow │  │  VictoriaMetrics :8428  │  │  → Alertmgr    │
│  AIR-GAPPED        │  │  Grafana     :3000      │  │  → Fleet API   │
└────────────────────┘  │  Alertmanager:9093      │  └────────────────┘
                        │  NTP stratum · Salt Master │
                        └─────────────────────────────┘
```

---

## Hardware Tiers — 28 × M4 (homogeneous)

| Pool | Nodes | Role | Model | Network |
|------|-------|------|-------|---------|
| FastPool | 10 | Chat · Autocomplete | Llama 3 8B Q4_K_M | 10 GbE |
| ReasonPool | 4 | Long-context · Reasoning | Qwen 2.5 14B Q4_K_M | 10 GbE |
| **LargePool** | **4** | **32B–70B distributed** | **Qwen 2.5 32B Q4_K_M** | **10 GbE + RDMA** |
| VisionPool | 3 | Image + text · Multimodal | 8B VL Q4_K_M | 10 GbE |
| EmbedPool | 2 | Embeddings · RAG | BGE-M3 F16 | 10 GbE |
| SpeechPool | 1 | ASR · Transcription | Whisper large-v3 | 10 GbE |
| Gateway | 2 | LiteLLM + HAProxy | — | 10 GbE |
| Model Store / Registry | 1 | MinIO + Registry API | — | 10 GbE |
| Services | 1 | Fleet API + PG + Redis + Grafana | — | 10 GbE |
| **Total** | **28** | | | |

### Per-Node Specs (M4 base, uniform)

| Attribute | Value |
|-----------|-------|
| Chip | Apple M4 |
| CPU cores | 10 (4P + 6E) |
| GPU cores | 10-core Metal |
| Neural Engine | 16-core NPU |
| Unified RAM | 16 GB |
| Memory bandwidth | 120 GB/s |
| Storage | 512 GB NVMe SSD |
| Network | 10 GbE (built-in) |
| OS | macOS Sequoia / Tahoe |

### Per-Node Memory Budget

```
Total unified RAM:               16,384 MB
macOS + system daemons:          ~2,500 MB
Runtime overhead (vLLM-MLX):      ~500 MB
Available for model + KV cache:  ~13,400 MB
Model weight ceiling (60% rule):  ~9,600 MB   ← hard operational limit
KV cache budget:                 ~3,800–8,000 MB (context-dependent)
```

---

## LargePool — Distributed Inference via RDMA  *(new in v1.2)*

10 GbE + RDMA RoCE-v2 enables tensor-parallel inference for models exceeding single-node memory:

```
Group A:  hydra-large-01 (192.168.10.40) ↔ hydra-large-02 (192.168.10.41)
Group B:  hydra-large-03 (192.168.10.42) ↔ hydra-large-04 (192.168.10.43)

Each 2-node pair runs one Qwen 2.5 32B Q4_K_M instance:
  Model size:    ~20 GB  →  ~10 GB per node  ✓ within 9.6 GB weight ceiling
  AllReduce:     ~2 μs (RDMA RoCE-v2) vs ~320 ms (1 Gbps TCP)
  Tensor split:  attention heads sharded 50/50 across nodes
  Runtime:       vLLM multi-node  OR  exo framework
  TTFT target:   p50 < 8 s

For 70B (Llama 3 70B Q3_K_M ~30 GB):
  Extend to full 4-node group when both TP groups are available.
```

---

## Networking

```
Switch:    10 GbE managed (28-port + 2× uplinks)
Protocol:  RoCE v2 — RDMA over Converged Ethernet
MTU:       9000 (jumbo frames, set on switch and all nodes)
PFC:       Priority Flow Control enabled (lossless Ethernet for RDMA)
RTT:       ~128 μs (TCP)  ·  ~2 μs (RDMA)
Bandwidth: 1.25 GB/s per link

Model pull speed from MinIO (hydra-store-01):
  8B  model  (~5 GB):   ~4 s   (was ~40 s at 1 Gbps)
  14B model  (~9 GB):   ~7 s   (was ~72 s at 1 Gbps)
  32B model (~20 GB):  ~16 s   (was ~160 s at 1 Gbps)
  70B model (~40 GB):  ~32 s   (was ~320 s at 1 Gbps)

Subnet:  192.168.10.0/24
NTP:     hydra-svc-01 (internal stratum 1)
DNS:     hydra-gw-01  dnsmasq  domain=hydra.local
```

See `docs/network-design.md` for full topology, RDMA setup, and IP allocation.

---

## Model Registry  *(air-gapped)*

No internet means all models are pre-staged by an operator. The Model Registry enforces integrity and approval:

```
Operator workflow (internet-connected machine):
  1. Download model from HuggingFace / official source
  2. Compute sha256
  3. Transfer to cluster (USB / internal NAS)
  4. Upload to MinIO:  mc cp model.gguf local/hydra-models/gguf/
  5. Register:  POST /api/v1/models/register  →  Registry API (hydra-store-01:8100)
  6. Approve:   POST /api/v1/models/{name}/approve
  7. LiteLLM config auto-updates on next sync

Registry API endpoints (hydra-store-01 :8100):
  GET    /api/v1/models              list approved models
  GET    /api/v1/models/{name}       metadata + MinIO download URL
  POST   /api/v1/models/register     register new model (admin token)
  POST   /api/v1/models/{name}/approve
  DELETE /api/v1/models/{name}       retire

Model metadata:  name · version · sha256 · format (GGUF/MLX) · size_bytes ·
                 quantization · pool_assignment · approved_by · approved_at ·
                 license · loaded_nodes[]
```

---

## Observability

```
Per node:
  apple-silicon-exporter  →  GPU%, ANE%, thermal, power (W)
  otelcol-contrib  →  hostmetrics + apple_hw  →  fan-out:
    prometheusremotewrite  →  Victoria Metrics (hydra-svc-01:8428)
    otlp/grpc              →  kube-prometheus-stack
    otlp/http              →  Fleet Platform   (hydra-svc-01:8000/otlp)

  salt.minion_id label on EVERY metric
  → Grafana label-join with Fleet Platform data (drift score, SBOM, group)

Dashboards (Grafana :3000):
  Fleet Overview  ·  Node Deep Dive  ·  Quality Monitor

Alert rules (50+):
  GPU util  ·  thermal pressure  ·  power (>65W for M4)
  Latency SLAs  ·  KV cache  ·  error rates  ·  hallucination signals
```

---

## Air-Gapped Delivery

| Artifact | How to deliver |
|----------|---------------|
| Ansible playbooks | Operator laptop → SSH (no agent-side internet needed) |
| otelcol-contrib binary | Pre-download → `roles/otel-mac-agent/files/` |
| apple-silicon-exporter | Cross-compile on Mac → `roles/otel-mac-agent/files/` |
| MinIO server | Pre-download → `roles/model-registry/files/` |
| Homebrew packages | `brew bundle` from cached tap OR pre-built binaries |
| LLM model weights | Download → sha256 → transfer → model registry |
| OS updates | Apple MDM / USB — out of band |

---

## Known Hardware Constraints (M4 / macOS)

- **No NCCL** — NVIDIA library, not available on Apple Silicon
- **No NVLink / GPU-GPU RDMA at hardware level** — Apple Silicon has no NVLink equivalent; cross-node GPU communication goes through Ethernet
- **RDMA via RoCE-v2** — requires Thunderbolt-to-RDMA adapter (e.g. Mellanox ConnectX via Sonnet enclosure) OR software RDMA via libfabric ofi+tcp (lower performance)
- **Metal-only GPU** — all GPU compute via Metal or MLX; no CUDA
- **Unified memory ceiling** — 16 GB hard bound per node; no ECC, no cross-node memory pooling at hardware level
- **Thermal throttling** — M4 sustained peak ~65W; alert threshold set at 65W

---

## Documents & References

| Document | Location |
|----------|---------|
| Full architecture spec | `docs/hydra_architecture.md` |
| Network design (10 GbE + RDMA) | `docs/network-design.md` |
| Architecture Decision Record | `docs/mac-mini-cluster/architecture-decision-document.md` |
| Monitoring design spec | `docs/superpowers/specs/2026-04-28-monitoring-ansible-design.md` |
| Reaudit gap analysis | `docs/reaudit-2026-05-16.md` |
| Benchmark log | `benchmarkings.md` |
| Operations reference | `commands.md` |
