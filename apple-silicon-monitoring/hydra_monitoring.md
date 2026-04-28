# Apple Silicon LLM Monitoring Platform

Production-grade monitoring architecture for LLM inference workloads running on Apple Silicon Mac Mini clusters, NVIDIA GPU servers, and other hardware.

## Overview

This repository contains a complete observability solution for monitoring fleets hosting LLM workloads across multiple hardware platforms:

- **Hardware metrics collection** — GPU, thermal, and power monitoring (Apple Silicon native; NVIDIA and AMD via standard exporters)
- **LLM inference telemetry** — latency, throughput, errors (hardware-agnostic)
- **Hallucination detection** — entropy, repetition, confidence scoring (hardware-agnostic)
- **Dual-mode metrics pipeline** — OTLP push or Prometheus scrape
- **Alerting and dashboards** — 50+ alert rules, 3 Grafana dashboards

## Supported Hardware

| Platform | Hardware Exporter | LLM Frameworks | Status |
|----------|------------------|----------------|--------|
| **Apple Silicon** (M1/M2/M3/M4 Mac Mini) | `apple-silicon-exporter` (included) | vLLM, MLX, llama.cpp, Ollama | Production-ready |
| **NVIDIA GPU** (Linux) | `dcgm-exporter` or `nvidia-gpu-exporter` | vLLM, TGI, llama.cpp, Ollama | Supported via standard exporters |
| **AMD GPU** (Linux, ROCm) | `amdgpu_exporter` | vLLM (ROCm), llama.cpp | Supported via standard exporters |
| **CPU-only** (Linux/macOS) | `node_exporter` | llama.cpp, Ollama | Supported via standard exporters |

The **LLM telemetry SDK** and **hallucination detection** components are fully hardware-agnostic and work identically on all platforms.

## Architecture

### Single-Platform (Apple Silicon)

```
┌─────────────────────────────────────────────────────────────────┐
│                      Mac Mini Node                              │
│  ┌──────────────────────┐  ┌─────────────────────────────────┐ │
│  │ apple-silicon-       │  │ LLM Inference Server            │ │
│  │ exporter             │  │ (with llm-telemetry SDK)        │ │
│  │ :9101/metrics        │  │                                 │ │
│  └──────────┬───────────┘  └──────────────┬──────────────────┘ │
│             │                              │ OTLP              │
│             │ scrape                       │                   │
│  ┌──────────▼──────────────────────────────▼──────────────────┐│
│  │              OTEL Collector Agent                          ││
│  │              :8889 (prometheus) or → Gateway               ││
│  └────────────────────────────┬───────────────────────────────┘│
└───────────────────────────────┼─────────────────────────────────┘
                                │ OTLP/gRPC
                                ▼
                    ┌───────────────────────┐
                    │   OTEL Gateway Tier   │
                    │   (2+ instances)      │
                    └───────────┬───────────┘
                                │ remote_write
                                ▼
                    ┌───────────────────────┐
                    │  Prometheus / Mimir   │
                    └───────────┬───────────┘
                                │
                    ┌───────────▼───────────┐
                    │  Grafana + Alertmgr   │
                    └───────────────────────┘
```

### Multi-Hardware Fleet

```
┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│   Mac Mini (Apple Si)   │  │   Linux (NVIDIA GPU)    │  │   Linux (AMD GPU)       │
│                         │  │                         │  │                         │
│  apple-silicon-exporter │  │  dcgm-exporter          │  │  amdgpu_exporter        │
│  + llm-telemetry SDK    │  │  + node_exporter        │  │  + node_exporter        │
│  + OTEL Agent           │  │  + llm-telemetry SDK    │  │  + llm-telemetry SDK    │
└────────────┬────────────┘  │  + OTEL Agent           │  │  + OTEL Agent           │
             │               └────────────┬────────────┘  └────────────┬────────────┘
             │                            │                            │
             └────────────────┬───────────┴────────────────────────────┘
                              │ OTLP/gRPC
                              ▼
                  ┌───────────────────────┐
                  │   OTEL Gateway Tier   │
                  │   (2+ instances)      │
                  └───────────┬───────────┘
                              │ remote_write
                  ┌───────────▼───────────┐
                  │  Prometheus / Mimir   │
                  └───────────┬───────────┘
                  ┌───────────▼───────────┐
                  │  Grafana + Alertmgr   │
                  └───────────────────────┘
```

## Components

### 1. Apple Silicon Exporter (`apple-silicon-exporter/`)

Native Go exporter for Apple Silicon hardware metrics using IOKit, Metal, and powermetrics:
- GPU utilization, memory, temperature, power
- CPU cluster power (efficiency/performance cores)
- Neural Engine utilization and power
- Thermal pressure and throttling state
- System power consumption

> **Note:** This component is macOS-only. For NVIDIA GPUs, use [dcgm-exporter](https://github.com/NVIDIA/dcgm-exporter). For AMD GPUs, use [amdgpu_exporter](https://github.com/amdgpu-exporter/amdgpu_exporter). See [Multi-Hardware Setup](#multi-hardware-setup) below.

```bash
cd apple-silicon-exporter
make build
sudo make install
sudo make start
```

### 2. OTEL Collector Configurations (`otel-config/`)

Pre-configured OpenTelemetry Collector configs for:
- **Agent mode** (`agent-config.yaml`): Runs on each node (Mac Mini, Linux server, etc.)
- **Gateway mode** (`gateway-config.yaml`): Central aggregation tier
- **Prometheus mode** (`agent-config-prometheus-mode.yaml`): Alternative scrape-based pipeline

Supports dual export modes:
- **Mode A** — OTLP push to gateway (recommended at scale, 100+ nodes)
- **Mode B** — Prometheus scrape endpoint (simpler for small deployments)

### 3. LLM Telemetry SDK (`llm-telemetry/`)

Hardware-agnostic instrumentation libraries for LLM inference applications. Works identically on Apple Silicon, NVIDIA, AMD, and CPU-only setups.

**Python:**
```python
from llm_telemetry import LLMTelemetry, init_telemetry

init_telemetry(endpoint="localhost:4317")
telemetry = LLMTelemetry(model_name="llama-3-70b")

telemetry.record_inference(
    prompt_tokens=100,
    output_tokens=50,
    prompt_latency_s=0.5,
    inference_latency_s=2.0,
    token_probs=[0.9, 0.85, ...],
    output_text="Generated response..."
)
```

**Go:**
```go
telemetry, _ := llmtelemetry.New("llama-3-70b",
    llmtelemetry.WithEndpoint("localhost:4317"),
)
defer telemetry.Shutdown(ctx)

telemetry.RecordInference(ctx, llmtelemetry.InferenceResult{
    PromptTokens:     100,
    OutputTokens:     50,
    PromptLatency:    500 * time.Millisecond,
    InferenceLatency: 2 * time.Second,
})
```

### 4. Hallucination Detection (`llm-telemetry/python/llm_telemetry/detection.py`)

Built-in algorithms for detecting potential hallucinations:
- **Shannon entropy** — token probability distribution uncertainty
- **Perplexity** — model confidence scoring
- **N-gram repetition** — repetitive output detection
- **Confidence statistics** — mean and standard deviation of token probabilities
- **Refusal pattern matching** — detects refusal responses

### 5. Alert Rules (`alerts/`)

50+ Prometheus/Mimir alert rules across three categories:
- **Hardware** (`apple-silicon-hardware.yaml`): GPU starvation, thermal throttling, power spikes, exporter down
- **Inference** (`llm-inference.yaml`): Model stalls, P99 latency, throughput drops, OOM errors, Metal crashes
- **Hallucination** (`hallucination-detection.yaml`): High entropy, repetition spikes, confidence drift, quality degradation

### 6. Grafana Dashboards (`dashboards/`)

- **Fleet Overview** (`fleet-overview.json`): Cluster-wide health, GPU heatmap across nodes, throughput trends
- **Node Deep Dive** (`node-deep-dive.json`): Individual node GPU/CPU/thermal/power metrics, per-model latency breakdown
- **Quality Monitor** (`quality-monitor.json`): Hallucination risk scoring, entropy distribution, confidence anomaly bands

### 7. Deployment Configs (`deploy/`)

- **macOS** (`deploy/launchdaemons/`): LaunchDaemon plists for auto-start on boot
- **Linux** (`deploy/systemd/`): Systemd service units for OTEL Gateway
- **Kubernetes** (`deploy/kubernetes/`): Gateway deployment manifests

## Quick Start

### Option A: Try Locally with Docker (Recommended First Step)

Spin up a full 20-node simulated environment with mock Apple Silicon exporters and LLM servers:

```bash
docker compose up -d

# Wait ~30s for services to start, then verify
bash test-setup.sh
```

Access the stack:
- **Grafana**: http://localhost:5940 (admin/admin) — 3 dashboards auto-provisioned
- **Prometheus**: http://localhost:5930 — query metrics directly
- **Alertmanager**: http://localhost:5950 — view firing alerts

The test environment simulates 20 Mac Mini nodes running Llama 3 70B, Mixtral 8x7B, Codellama 34B, and Qwen 72B models.

### Option B: Production Deployment on Apple Silicon

#### Prerequisites

- macOS 12+ (Monterey or later)
- Apple Silicon Mac (M1/M2/M3/M4)
- Go 1.22+ (for building the exporter)
- OpenTelemetry Collector Contrib (`otelcol-contrib`)
- Prometheus/Mimir for metrics storage
- Grafana for visualization

#### Installation

1. **Build and install the exporter:**
   ```bash
   cd apple-silicon-exporter
   make build
   sudo make install
   sudo make start
   ```

2. **Install OTEL Collector:**
   ```bash
   # Download otelcol-contrib for macOS arm64
   curl -LO https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v0.96.0/otelcol-contrib_0.96.0_darwin_arm64.tar.gz
   tar -xzf otelcol-contrib_0.96.0_darwin_arm64.tar.gz
   sudo mv otelcol-contrib /usr/local/bin/

   # Install config
   sudo mkdir -p /etc/otel-collector
   sudo cp otel-config/agent-config.yaml /etc/otel-collector/
   sudo cp deploy/launchdaemons/com.company.otel-collector-agent.plist /Library/LaunchDaemons/
   sudo launchctl load /Library/LaunchDaemons/com.company.otel-collector-agent.plist
   ```

3. **Install Python SDK:**
   ```bash
   cd llm-telemetry/python
   pip install -e .
   ```

4. **Import dashboards:**
   Import JSON files from `dashboards/` into Grafana.

5. **Configure alerts:**
   Apply alert rules from `alerts/` to Prometheus/Mimir.

### Option C: Production Deployment on NVIDIA GPU (Linux)

See [Multi-Hardware Setup](#multi-hardware-setup) below.

## Multi-Hardware Setup

The LLM telemetry SDK, OTEL pipeline, dashboards, and alert rules are hardware-agnostic. Only the **hardware exporter** differs per platform.

### NVIDIA GPU (Linux)

1. **Deploy the GPU exporter** — use [dcgm-exporter](https://github.com/NVIDIA/dcgm-exporter) for data center GPUs or [nvidia-gpu-exporter](https://github.com/utkuozdemir/nvidia_gpu_exporter) for consumer GPUs:
   ```bash
   # dcgm-exporter (recommended for data center GPUs)
   docker run -d --gpus all -p 9400:9400 nvcr.io/nvidia/k8s/dcgm-exporter:latest

   # Or nvidia-gpu-exporter (works with nvidia-smi)
   docker run -d --gpus all -p 9835:9835 utkuozdemir/nvidia_gpu_exporter:latest
   ```

2. **Deploy node_exporter** for CPU/memory/disk metrics:
   ```bash
   docker run -d -p 9100:9100 prom/node-exporter:latest
   ```

3. **Configure the OTEL Agent** — update the Prometheus receiver in `agent-config.yaml`:
   ```yaml
   receivers:
     prometheus/hardware:
       config:
         scrape_configs:
           - job_name: 'nvidia-gpu'
             static_configs:
               - targets: ['localhost:9400']  # dcgm-exporter
           - job_name: 'node'
             static_configs:
               - targets: ['localhost:9100']  # node_exporter
   ```

4. **Instrument your LLM server** with the telemetry SDK (same as Apple Silicon):
   ```python
   from llm_telemetry import LLMTelemetry, init_telemetry
   init_telemetry(endpoint="localhost:4317")
   telemetry = LLMTelemetry(model_name="llama-3-70b", gpu_id="gpu0")
   ```

### AMD GPU (Linux, ROCm)

1. **Deploy amdgpu_exporter** for GPU metrics
2. **Deploy node_exporter** for system metrics
3. Configure the OTEL Agent and instrument your LLM server as above

### Unified Metric Mapping

When using mixed hardware, the OTEL Gateway's transform processor can normalize metrics to a common schema:

| Unified Name | Apple Silicon | NVIDIA (DCGM) | AMD |
|-------------|--------------|----------------|-----|
| GPU utilization | `apple_gpu_utilization_percent` | `DCGM_FI_DEV_GPU_UTIL` | `amdgpu_gpu_busy_percent` |
| GPU memory used | `apple_gpu_memory_used_bytes` | `DCGM_FI_DEV_FB_USED` | `amdgpu_vram_used_bytes` |
| GPU temperature | `apple_gpu_temperature_celsius` | `DCGM_FI_DEV_GPU_TEMP` | `amdgpu_temperature_edge` |
| GPU power | `apple_gpu_power_watts` | `DCGM_FI_DEV_POWER_USAGE` | `amdgpu_power_avg_watts` |

## Metrics Reference

### Hardware Metrics (from apple-silicon-exporter)

| Metric | Type | Description |
|--------|------|-------------|
| `apple_gpu_utilization_percent` | Gauge | GPU utilization % |
| `apple_gpu_memory_used_bytes` | Gauge | GPU memory in use |
| `apple_gpu_memory_total_bytes` | Gauge | Total GPU memory |
| `apple_gpu_temperature_celsius` | Gauge | GPU temperature |
| `apple_gpu_power_watts` | Gauge | GPU power consumption |
| `apple_cpu_power_watts` | Gauge | CPU cluster power |
| `apple_ane_power_watts` | Gauge | Neural Engine power |
| `apple_ane_utilization_percent` | Gauge | Neural Engine utilization |
| `apple_system_power_watts` | Gauge | Total system power |
| `apple_memory_used_bytes` | Gauge | System memory in use |
| `apple_memory_total_bytes` | Gauge | Total system memory |
| `apple_thermal_pressure` | Gauge | Thermal pressure level |
| `apple_thermal_throttle_active` | Gauge | Throttling state |

### LLM Metrics (from llm-telemetry SDK)

| Metric | Type | Description |
|--------|------|-------------|
| `llm_inference_requests_total` | Counter | Total requests by status/model |
| `llm_inference_duration_seconds` | Histogram | Latency by phase (prompt/generation/total) |
| `llm_tokens_processed_total` | Counter | Tokens processed (input/output) |
| `llm_tokens_per_second` | Gauge | Current throughput |
| `llm_gpu_memory_allocated_bytes` | Gauge | Per-model GPU memory |
| `llm_kv_cache_utilization` | Gauge | KV cache usage (0-1) |
| `llm_queue_depth` | Gauge | Pending requests |
| `llm_batch_size` | Histogram | Batch size distribution |
| `llm_context_length` | Histogram | Context window utilization |
| `llm_error_total` | Counter | Errors by type |
| `llm_model_loaded` | Gauge | Model availability (0/1) |
| `llm_model_health_score` | Gauge | Composite health (0-100) |

### Hallucination Detection Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `llm_output_entropy` | Histogram | Token entropy (0-5, higher = uncertain) |
| `llm_confidence_mean` | Gauge | Mean token confidence (0-1) |
| `llm_confidence_std` | Gauge | Confidence variance |
| `llm_repetition_score` | Gauge | N-gram repetition (0-1) |
| `llm_perplexity` | Histogram | Output perplexity (1-100+) |
| `llm_refusal_rate` | Gauge | Refusal response rate (0-1) |

## Configuration

### Environment Variables

```bash
# Node identification
export HOSTNAME=mac-mini-001
export CLUSTER_NAME=llm-mac-cluster-prod
export ENVIRONMENT=production

# OTEL Gateway
export OTEL_GATEWAY_ENDPOINT=otel-gateway.internal:4317

# TLS (optional)
export OTEL_TLS_INSECURE=false
export OTEL_TLS_CERT=/etc/otel/certs/client.crt
export OTEL_TLS_KEY=/etc/otel/certs/client.key
```

### Switching Modes

**Mode A (OTLP Push - Default):**
```yaml
# In agent-config.yaml
exporters: [otlp/gateway]
```

**Mode B (Prometheus Scrape):**
```yaml
# In agent-config.yaml
exporters: [prometheus]
```

## Scaling to 100+ Nodes

For large deployments:

1. **Use OTLP push mode** — agents push to gateway, avoiding Prometheus scrape overhead
2. **Deploy 2+ gateway instances** — load balance across gateways for failover resilience
3. **Enable sending queue** — queue size: 1000 metrics, retry up to 300s max elapsed time
4. **Configure memory limits** — agent: 100 MiB, gateway: 2-4 GB (10-20 nodes per gateway)
5. **Monitor cardinality** — drop debug/internal metrics, group by host/cluster/environment
6. **Use Mimir** — for long-term storage beyond Prometheus's single-node limits

### Resource Allocation Guide

| Component | Memory | Storage | Nodes Supported |
|-----------|--------|---------|-----------------|
| OTEL Agent (per node) | 100 MiB | — | 1 |
| OTEL Gateway | 2-4 GB | — | 10-20 nodes each |
| Prometheus | 4-8 GB | 50 GB+ SSD (7d retention) | Up to 50 nodes |
| Mimir (distributed) | 8+ GB | Object storage | 100+ nodes |

## Security Considerations

- Exporter binds to `127.0.0.1` by default — never expose directly to the network
- TLS recommended for gateway communication (configure in `agent.env` / `gateway.env`)
- Use IOKit entitlements instead of root where possible on macOS
- Restrict Alertmanager webhook access to internal networks
- The LLM telemetry SDK sends metrics only — no prompt/response content is transmitted

## Troubleshooting

### Exporter not starting (macOS)
```bash
# Check logs
tail -f /var/log/apple-silicon-exporter/exporter.log

# Verify LaunchDaemon
sudo launchctl list | grep apple-silicon
```

### Missing GPU metrics
- **Apple Silicon**: Ensure running as root or with IOKit entitlements; check powermetrics access
- **NVIDIA**: Verify `nvidia-smi` works; check dcgm-exporter logs
- **AMD**: Verify ROCm is installed; check `rocm-smi` output

### High memory usage in OTEL Collector
- Reduce batch sizes in OTEL config
- Enable the `memory_limiter` processor (already configured in provided configs)
- Check for cardinality explosion (too many unique label combinations)

### No data in Grafana
1. Verify the exporter is producing metrics: `curl http://localhost:9101/metrics`
2. Check OTEL agent health: `curl http://localhost:13133`
3. Check Prometheus targets: visit http://localhost:5930/targets
4. Verify Grafana datasource configuration

## Project Structure

```
apple-silicon-llm-monitoring/
├── apple-silicon-exporter/       # Native Go exporter (macOS only)
│   ├── cmd/exporter/             # Entry point
│   └── internal/                 # IOKit, Metal, powermetrics, system collectors
├── llm-telemetry/                # Instrumentation SDKs (hardware-agnostic)
│   ├── go/                       # Go SDK
│   └── python/                   # Python SDK with hallucination detection
├── otel-config/                  # OpenTelemetry Collector configurations
│   ├── agent-config.yaml         # Per-node agent mode
│   ├── gateway-config.yaml       # Central gateway mode
│   └── agent-config-prometheus-mode.yaml
├── alerts/                       # Prometheus/Mimir alert rules
│   ├── apple-silicon-hardware.yaml
│   ├── llm-inference.yaml
│   ├── hallucination-detection.yaml
│   └── alertmanager.yaml
├── dashboards/                   # Grafana dashboard JSON
│   ├── fleet-overview.json
│   ├── node-deep-dive.json
│   └── quality-monitor.json
├── docker/                       # Docker configs for test environment
├── deploy/                       # Production deployment configs
│   ├── launchdaemons/            # macOS LaunchDaemon plists
│   ├── systemd/                  # Linux systemd units
│   └── kubernetes/               # K8s manifests
├── mock-server/                  # Mock exporters for testing
├── docker-compose.yaml           # 20-node test environment
└── test-setup.sh                 # Health check script
```

## License

Apache 2.0

## Contributing

1. Fork the repository
2. Create a feature branch
3. Submit a pull request

For major changes, please open an issue first.
