# Hydra — Local Enterprise LLM Farm

> **40-node Apple Silicon Mac Mini cluster · ≤1,000 internal users · On-premises, air-gapped-capable**

Hydra is a production-grade, on-premises LLM inference platform. It serves up to 1,000 internal users from a cluster of Apple Silicon Mac Mini nodes using open-weight models, with no cloud dependency. This repository contains the full stack: inference configuration, monitoring, benchmarking, and deployment automation.

---

## Repository Structure

```
hydra/
├── README.md                     ← this file
├── ARCHITECTURE.md               ← system architecture overview
├── commands.md                   ← operational reference (deploy, benchmark, monitor)
├── benchmarkings.md              ← live benchmark log with Phase 1 results
│
├── airllm/                       ← AirLLM: layer-by-layer GPU inference for large models
├── ansible/                      ← Monitoring deployment automation (multi-hardware)
├── apple-silicon-monitoring/     ← OTEL + Grafana + Alertmanager monitoring stack
├── bitnet/                       ← BitNet 1-bit LLM inference (planned)
├── docs/                         ← Architecture specifications and design docs
├── llama.cpp/                    ← llama.cpp inference engine + Phase 1 benchmarks
├── opik/                         ← Opik observability, LLM tracing, and experiment tracking
├── scripts/                      ← Benchmark automation scripts
└── vllm/                         ← vLLM-MLX inference configuration
```

---

## Hardware

### Production Cluster (40 nodes)

| Pool | Nodes | Chip | RAM | Models | Role |
|------|-------|------|-----|--------|------|
| FastPool | 20 | Apple M4 | 16 GB | 8B Q4_K_M | Chat, autocomplete |
| ReasonPool | 8 | Apple M4 | 16 GB | 14B Q4_K_M | Long-context reasoning |
| VisionPool | 6 | Apple M3 | 16 GB | 8B VL Q4_K_M | Multimodal |
| EmbedPool | 4 | Apple M1/M3 | 16 GB | BGE-M3 F16 | Embeddings |
| SpeechPool | 2 | Apple M1 | 16 GB | Whisper F16 | ASR |

### Phase 1 Pilot (3 machines — active benchmarking)

| ID | Machine | RAM | GPU |
|----|---------|-----|-----|
| `p1-m2-8g` | Apple Mac (M2) | 8 GB unified | Apple Metal |
| `p1-m3-16g` | Apple Mac Mini (M3) | 16 GB unified | Apple Metal |
| `p1-i7-rtx3050` | Linux i7-12th Gen | 64 GB DDR5 | NVIDIA RTX 3050 4 GB |

Phase 1 results determine the 40-node pool hardware direction. See `benchmarkings.md` §2.

---

## Components

### `airllm/`
AirLLM enables inference of 70B+ parameter models on consumer hardware by loading model layers on-demand from SSD rather than keeping the full model in RAM. Useful for running large models on the 16 GB Mac Mini nodes that would otherwise be constrained to 7–8B models.

### `ansible/`
Ansible playbook that deploys the full monitoring stack to any node type (Apple Silicon, NVIDIA, CPU-only) from a single config file.

```bash
# Edit nodes in:
ansible/cluster.yml

# Deploy:
cd ansible && ansible-playbook site.yml

# Re-discover LLM providers after model changes:
ansible-playbook site.yml --tags llm-discovery,monitoring-cfg
```

Four roles: `base` (OTEL agent) → `hw-exporter` (hardware metrics) → `llm-discovery` (probe LLM endpoints) → `monitoring-cfg` (render per-node OTEL config).

### `apple-silicon-monitoring/`
Full observability stack for the LLM cluster:
- **Hardware exporters**: `apple-silicon-exporter` (Go), dcgm-exporter (NVIDIA), node_exporter (CPU)
- **OTEL pipeline**: per-node agent → gateway tier → Prometheus/Mimir
- **Grafana dashboards**: Fleet Overview, Node Deep Dive, Quality Monitor
- **Alert rules**: 50+ rules covering GPU, thermal, power, inference latency, hallucination detection

All dashboards support multi-hardware filtering via template variables (`$gpu_provider`, `$gpu_util_metric`, etc.).

### `bitnet/`
Placeholder for Microsoft BitNet 1-bit LLM inference. On 64 GB DDR5 (i7 node), 1-bit quantization could make 70B+ models viable on CPU without GPU. See `bitnet/README.md`.

### `docs/`
Architecture specifications:

| Document | What it covers |
|----------|---------------|
| `docs/hydra_architecture.md` | Full 40-node spec: hardware, KV cache math, model sizing, governance |
| `docs/mac-mini-cluster/architecture-decision-document.md` | ADR: Ollama vs exo vs Tabby vs hybrid |
| `docs/mac-mini-cluster/hydra-pilot-architecture.md` | 2-node pilot spec with success criteria |
| `docs/hybrid-cluster-architecture.md` | Hybrid on-prem + cloud overflow architecture |
| `docs/superpowers/specs/` | Design specs for implemented features |
| `docs/superpowers/plans/` | Implementation plans |

### `llama.cpp/`
llama.cpp inference engine used for:
- Phase 1 benchmarks (`llama-bench`, `llama-batched-bench`)
- Production inference on Apple Silicon (Metal backend) and NVIDIA (CUDA backend)
- CPU-offload benchmarks on the i7/RTX 3050 node

Benchmark data in `llama.cpp/benches/`:
- `dgx-spark/` — NVIDIA GB10 results (gpt-oss-20B, gpt-oss-120B)
- `mac-m2-ultra/` — Apple M2 Ultra results

### `opik/`
Opik observability platform for LLM experiment tracking:
- `sdk/` — Python wrappers for llama.cpp and Ollama with Opik trace injection
- `experiments/` — Baseline reporting (p50/p95/p99 latency, tokens/sec per model)
- `ansible/` — Opik SDK deployment to Mac nodes (separate from main `ansible/`)
- `k8s/` — Kubernetes deployment manifests

### `scripts/`
Benchmark automation:

| Script | Purpose |
|--------|---------|
| `scripts/bench/run-phase1.sh` | Master benchmark driver for Phase 1 (llama-bench + batched-bench) |
| `scripts/bench/k6-chat.js` | k6 concurrent user load test — chat workload |
| `scripts/bench/k6-autocomplete.js` | k6 concurrent user load test — autocomplete (FastPool SLA) |
| `scripts/bench/ollama-concurrent.sh` | Pure-bash concurrent user test via curl |
| `scripts/ingest_results.py` | Parse llama-bench output → update `benchmarkings.md` automatically |

### `vllm/`
vLLM inference configuration. vLLM-MLX is the production inference runtime for Apple Silicon nodes (provides continuous batching and PagedAttention via MLX backend). Standard vLLM is used on NVIDIA nodes. See `vllm/README.md`.

---

## Quick Start

### 1. Add a node to the cluster

Edit `ansible/cluster.yml`:
```yaml
nodes:
  - id: my-new-node
    hostname: mac-m4-001
    ip: 192.168.1.50
    user: dk
    os: macos
    gpu_provider: apple
    chip: m4
    ram_gb: 16
    pools: [fast]
    llm_endpoints:
      - provider: ollama
        url: "http://127.0.0.1:11434/v1"
        api_key: ~
```

Deploy monitoring:
```bash
cd ansible
ansible-galaxy collection install -r requirements.yml   # first time only
ansible-playbook site.yml --limit my-new-node
```

### 2. Run Phase 1 benchmarks

```bash
# Standard benchmark (results auto-saved to results/)
scripts/bench/run-phase1.sh --machine m3-16g --model 8b --runtime llamacpp

# Ingest results into benchmarkings.md
python scripts/ingest_results.py --machine p1-m3-16g --runtime llamacpp results/*.txt
```

### 3. Import Grafana dashboards

Upload these to Grafana → Dashboards → Import:
- `apple-silicon-monitoring/dashboards/fleet-overview.json`
- `apple-silicon-monitoring/dashboards/node-deep-dive.json`
- `apple-silicon-monitoring/dashboards/quality-monitor.json`

---

## Key Metrics & SLAs

| Pool | Workload | TTFT p50 target | TTFT p95 target |
|------|---------|-----------------|-----------------|
| FastPool | Autocomplete | < 500 ms | < 1,500 ms |
| ReasonPool | Chat | < 2,000 ms | < 5,000 ms |
| VisionPool | Vision | < 3,000 ms | < 8,000 ms |
| EmbedPool | Embedding | < 200 ms | < 500 ms |

Estimated cluster throughput (theoretical, pre-Phase 1):

| Pool | Nodes | Model | Cluster tok/s | Est. concurrent users |
|------|-------|-------|--------------|----------------------|
| FastPool | 20 × M4 | 8B Q4 | 500–700 | ~140–200 |
| ReasonPool | 8 × M4 | 14B Q4 | 120–176 | ~30–50 |

---

## Benchmark Reference

**DGX Spark (NVIDIA GB10) — gpt-oss-20B MXFP4:**
- PP: 4,506 t/s (pp2048) · TG: 83 t/s (tg32)

**Mac M2 Ultra — gpt-oss-20B MXFP4:**
- PP: 2,713 t/s (pp2048) · TG: 130 t/s (tg32)

Phase 1 results (M2 8GB, M3 16GB, i7+RTX 3050): see `benchmarkings.md` §7.

---

## Operations Reference

See `commands.md` for the full operational reference including:
- Ansible deployment commands
- Benchmark run commands
- Dashboard import and variable setup
- Alert deployment
- Common operations (add node, change pool, maintenance mode, upgrade OTEL)

---

## CI / Testing

```bash
# Ansible inventory + quantization logic (17 tests)
cd ansible && python -m pytest tests/ -v

# Dashboard patch validation (27 tests)
python -m pytest apple-silicon-monitoring/tests/ -v
```

---

## Design Principles

1. **No cloud dependency** — everything runs on-prem; no API calls to external services during inference
2. **60% RAM rule** — model weights never exceed 60% of available unified memory to leave headroom for KV cache growth
3. **Uniform labels** — every metric carries `node_id`, `gpu_provider`, `pool`, `provider`, `model`, `quantization` for consistent Grafana filtering
4. **Idempotent deployment** — all ansible roles and patch scripts are safe to re-run
5. **Single config file** — `ansible/cluster.yml` is the only file to edit for adding/changing nodes
