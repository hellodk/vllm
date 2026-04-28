# Hydra Monitoring & Observability — Multi-Hardware Ansible + Dashboard Design

**Date**: 2026-04-28  
**Status**: Approved — ready for implementation  
**Scope**: Multi-OS ansible deployment, unified Grafana dashboards, LLM auto-detection, consistent label taxonomy  
**Target hardware**: Apple Silicon (M1/M2/M3/M4), NVIDIA CUDA, AMD ROCm, CPU-only  

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Design Decisions](#2-design-decisions)
3. [cluster.yml — Cluster Configuration File](#3-clusteryml--cluster-configuration-file)
4. [Ansible Role Architecture](#4-ansible-role-architecture)
5. [Label Taxonomy](#5-label-taxonomy)
6. [OTEL Agent Config Templates](#6-otel-agent-config-templates)
7. [LLM Auto-Detection](#7-llm-auto-detection)
8. [Unified Multi-Hardware Dashboards](#8-unified-multi-hardware-dashboards)
9. [Alert Rule Fixes](#9-alert-rule-fixes)
10. [Data Flow](#10-data-flow)
11. [File Layout](#11-file-layout)
12. [v2 Deferred Items](#12-v2-deferred-items)

---

## 1. Problem Statement

The current monitoring stack has three critical failures when deployed beyond Apple Silicon:

1. **Dashboard breakage** — all three Grafana dashboards are hardcoded to `apple_*` metrics and `job="apple-silicon-exporter"`. On NVIDIA or Linux nodes every panel shows no data.
2. **No label consistency** — `node_id`, `gpu_provider`, `pool`, `provider` (LLM runtime), and `quantization` labels do not exist. Grafana cannot filter by pool, hardware type, or LLM provider.
3. **Ansible scope too narrow** — existing playbooks only configure one macOS node for opik SDK. No monitoring agent installation, no Linux support, no multi-node parameterization.

This design fixes all three.

---

## 2. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Config format | Single `cluster.yml` as dynamic inventory source | One file to edit; drives ansible, OTEL templates, and dashboard variables |
| Ansible structure | Layered roles (base → hw-exporter → llm-discovery → monitoring-cfg) | Each role has one job; can be re-run independently |
| Dashboard approach | Unified multi-hardware with template variable abstraction | Single fleet view across all hardware types |
| LLM detection | Port probe + `/v1/models` / `/api/tags` API query at deploy time | Zero-config by default; explicit `llm_endpoints` overrides auto-detection |
| Label injection | OTEL resource processor via env vars set by ansible | Labels applied uniformly at ingest, not scattered across exporters |
| Secrets (v1) | Plain text in `cluster.yml` | Acceptable for v1; vault integration deferred to v2 |

---

## 3. `cluster.yml` — Cluster Configuration File

Single source of truth. Lives at `ansible/cluster.yml`. Parsed by `ansible/inventory_plugin.py` which generates the ansible inventory dynamically.

```yaml
# ansible/cluster.yml

cluster:
  name: hydra-prod
  environment: production
  otel_gateway:
    primary: "192.168.1.10:4317"
    secondary: "192.168.1.11:4317"       # failover; omit if only one gateway
  prometheus_url: "http://192.168.1.10:9090"
  models_dir: "~/models"                  # cluster-wide default; override per node
  versions:
    otel_agent: "0.96.0"
    dcgm_exporter: "3.3.6"
    apple_silicon_exporter: "1.0.0"       # local build tag
    node_exporter: "1.8.2"
    amdgpu_exporter: "1.0.0"

nodes:
  - id: p1-m2-8g
    hostname: mac-m2-8g                   # must match actual system hostname
    ip: 192.168.1.21
    user: dk
    ssh_port: 22
    become: true
    become_method: sudo
    os: macos                             # macos | linux
    gpu_provider: apple                   # apple | nvidia | amd | none
    chip: m2                              # m1 | m2 | m3 | m4 | i7-12700k | ...
    ram_gb: 8
    models_dir: "/Users/dk/models"        # node override
    pools: [embed]
    maintenance: false
    metric_labels: {}                     # extra labels merged into resource attrs
    llm_endpoints:                        # omit to enable port-probe auto-detection
      - provider: llamacpp                # llamacpp | ollama | vllm-mlx | litellm
        url: "http://127.0.0.1:21434/v1"
        api_key: ~                        # null = no auth; plain text for v1

  - id: p1-m3-16g
    hostname: mac-m3-16g
    ip: 192.168.1.24
    user: dk
    ssh_port: 22
    become: true
    become_method: sudo
    os: macos
    gpu_provider: apple
    chip: m3
    ram_gb: 16
    models_dir: "/Users/dk/models"
    pools: [fast, reason]
    maintenance: false
    metric_labels: {}
    llm_endpoints:
      - provider: llamacpp
        url: "http://127.0.0.1:21434/v1"
        api_key: ~
      - provider: ollama
        url: "http://127.0.0.1:11434/v1"
        api_key: ~

  - id: p1-i7-rtx3050
    hostname: linux-i7-rtx
    ip: 192.168.1.30
    user: dk
    ssh_port: 22
    become: true
    become_method: sudo
    os: linux
    gpu_provider: nvidia
    gpu_model: "RTX 3050"
    gpu_index: 0
    vram_gb: 4
    ram_gb: 64
    cpu_inference: true                   # enables node_exporter + CPU-offload path
    models_dir: "/home/dk/models"
    pools: [fast]
    maintenance: false
    metric_labels: {}
    llm_endpoints:
      - provider: ollama
        url: "http://127.0.0.1:11434/v1"
        api_key: ~
      - provider: llamacpp
        url: "http://127.0.0.1:21434/v1"
        api_key: ~
```

### 3.1 Allowed enum values

Validated by `inventory_plugin.py` at parse time; ansible fails fast on invalid values.

| Field | Allowed values |
|-------|---------------|
| `os` | `macos`, `linux` |
| `gpu_provider` | `apple`, `nvidia`, `amd`, `none` |
| `provider` (in `llm_endpoints`) | `llamacpp`, `ollama`, `vllm-mlx`, `litellm` |

### 3.2 Auto-detection fallback

If `llm_endpoints` is omitted for a node, `llm-discovery` role probes these ports in order:

| Port | Provider assumed |
|------|-----------------|
| 11434 | ollama |
| 21434 | llamacpp |
| 8000 | vllm-mlx |
| 8080 | litellm |

---

## 4. Ansible Role Architecture

```
ansible/
├── cluster.yml                    ← single config file (Section 3)
├── inventory_plugin.py            ← reads cluster.yml → ansible inventory
├── site.yml                       ← master playbook; runs all roles in order
├── group_vars/
│   └── all.yml                    ← version pins, default port list, enums
├── roles/
│   ├── base/
│   │   ├── tasks/main.yml
│   │   └── templates/
│   │       ├── otel-agent.service.j2     (Linux systemd)
│   │       └── otel-agent.plist.j2       (macOS LaunchDaemon)
│   ├── hw-exporter/
│   │   ├── tasks/
│   │   │   ├── main.yml           ← dispatches to OS/GPU sub-task
│   │   │   ├── apple.yml
│   │   │   ├── nvidia.yml
│   │   │   ├── amd.yml
│   │   │   └── cpu.yml
│   │   └── templates/
│   │       ├── apple-exporter.plist.j2
│   │       └── dcgm-exporter.service.j2
│   ├── llm-discovery/
│   │   ├── tasks/main.yml         ← probe ports, query /v1/models, write discovered.yml
│   │   └── templates/
│   │       └── discovered.yml.j2
│   └── monitoring-cfg/
│       ├── tasks/main.yml         ← render otel config, write env file, restart agent
│       └── templates/
│           ├── otel-agent-config.yaml.j2
│           └── otel-agent.env.j2
```

### 4.1 `site.yml` master playbook

```yaml
# ansible/site.yml
- hosts: all
  gather_facts: true
  serial: 1                         # roll one node at a time; safe for prod
  roles:
    - { role: base,           tags: [base] }
    - { role: hw-exporter,    tags: [hw-exporter] }
    - { role: llm-discovery,  tags: [llm-discovery] }
    - { role: monitoring-cfg, tags: [monitoring-cfg] }
```

Run a single role: `ansible-playbook site.yml --tags hw-exporter`  
Skip maintenance nodes: handled by `inventory_plugin.py` — nodes with `maintenance: true` are excluded from the generated inventory.

### 4.2 Role: `base`

**Responsibility**: Install OTEL agent binary; create directories; install and enable OS service.

Tasks:
1. Create `/etc/hydra/` and `/var/lib/hydra/` directories (Linux) or `/Library/Application Support/Hydra/` (macOS)
2. Download OTEL contrib collector binary matching `versions.otel_agent` from cluster.yml
3. Render and install service unit:
   - Linux: `systemd` unit at `/etc/systemd/system/otel-agent.service`
   - macOS: LaunchDaemon plist at `/Library/LaunchDaemons/com.hydra.otel-agent.plist`
4. Enable service (do not start yet — `monitoring-cfg` starts it after config is rendered)

### 4.3 Role: `hw-exporter`

**Responsibility**: Install and start the hardware metrics exporter appropriate for the node's `gpu_provider`.

| `gpu_provider` | Exporter installed | Service |
|---------------|-------------------|---------|
| `apple` | `apple-silicon-exporter` (build from source or binary) | macOS LaunchDaemon |
| `nvidia` | `dcgm-exporter` (Docker or binary) | systemd |
| `amd` | `amdgpu_exporter` (binary from release) | systemd |
| `none` | `node_exporter` only | systemd |

If `cpu_inference: true` on a `nvidia` or `amd` node, `node_exporter` is also installed alongside the GPU exporter.

Exporter ports are fixed:

| Exporter | Port |
|----------|------|
| apple-silicon-exporter | 9101 |
| dcgm-exporter | 9400 |
| amdgpu_exporter | 9102 |
| node_exporter | 9100 |

### 4.4 Role: `llm-discovery`

**Responsibility**: Discover running LLM providers and loaded models on the target node; write `/etc/hydra/discovered.yml`.

Tasks:
1. If `llm_endpoints` is defined in `cluster.yml` for this node: use those directly, skip port probing
2. Otherwise: probe each default port (11434, 21434, 8000, 8080) with a 2-second timeout HTTP GET
3. For each live endpoint: query `/api/tags` (Ollama) or `/v1/models` (OpenAI-compat) to get loaded models
4. Write `/etc/hydra/discovered.yml`:

```yaml
# /etc/hydra/discovered.yml — managed by llm-discovery role
node_id: p1-m3-16g
gpu_provider: apple
pools: [fast, reason]
providers:
  - name: llamacpp
    url: "http://127.0.0.1:21434/v1"
    port: 21434
    models:
      - name: "qwen2.5-coder-3b-q4_k_m"
        quantization: "q4_k_m"
    status: up
  - name: ollama
    url: "http://127.0.0.1:11434/v1"
    port: 11434
    models:
      - name: "llama3:8b-instruct-q4_K_M"
        quantization: "q4_k_m"
    status: up
```

Quantization is extracted from the model name via regex: captures `q[0-9]+[_k_ms]*`, `f16`, `f32`, `mxfp4`.

Re-run this role any time models change: `ansible-playbook site.yml --tags llm-discovery,monitoring-cfg`

### 4.5 Role: `monitoring-cfg`

**Responsibility**: Render OTEL agent config and env file from `discovered.yml` + `cluster.yml`; restart OTEL agent.

Tasks:
1. Render `/etc/hydra/otel-agent.env` from `otel-agent.env.j2`
2. Render `/etc/hydra/otel-agent-config.yaml` from `otel-agent-config.yaml.j2`
3. Validate rendered config: run `otelcol-contrib --config /etc/hydra/otel-agent-config.yaml --dry-run`
4. Restart OTEL agent service
5. Health check: poll `http://localhost:13133` for 200 (30s timeout)

---

## 5. Label Taxonomy

Every metric in the system carries this consistent label set. Labels are injected at the OTEL layer so no exporter modifications are needed.

### 5.1 Complete label reference

```
┌─────────────────┬──────────────────────────────┬────────────────────────────────┐
│ Label           │ Example value                │ Set by                         │
├─────────────────┼──────────────────────────────┼────────────────────────────────┤
│ NODE IDENTITY   │                              │                                │
│ node_id         │ "p1-m3-16g"                  │ OTEL resource processor        │
│ hostname        │ "mac-m3-16g"                 │ OTEL resource processor        │
│ cluster         │ "hydra-prod"                 │ OTEL resource processor        │
│ environment     │ "production"                 │ OTEL resource processor        │
├─────────────────┼──────────────────────────────┼────────────────────────────────┤
│ HARDWARE        │                              │                                │
│ os              │ "macos" | "linux"             │ OTEL resource processor        │
│ gpu_provider    │ "apple" | "nvidia" | "amd"   │ OTEL resource processor        │
│ chip            │ "m3" | "i7-12700k"           │ OTEL resource processor        │
│ pool            │ "fast,reason"                │ OTEL resource processor        │
├─────────────────┼──────────────────────────────┼────────────────────────────────┤
│ LLM INFERENCE   │                              │                                │
│ provider        │ "llamacpp" | "ollama"        │ OTEL metric_relabel (per job)  │
│ model           │ "llama3-8b-q4_k_m"           │ LLM server native export       │
│ quantization    │ "q4_k_m" | "f16"             │ OTEL transform (regex on model)│
│ phase           │ "prompt" | "generation"      │ LLM server native export       │
│ error_type      │ "oom" | "timeout"            │ LLM server native export       │
├─────────────────┼──────────────────────────────┼────────────────────────────────┤
│ HW EXPORTER     │                              │                                │
│ gpu             │ "0" | "1"                    │ Hardware exporter native       │
│ type            │ "cpu" | "gpu"                │ Hardware exporter native       │
│ level           │ "nominal" | "critical"       │ Hardware exporter native       │
│ collector       │ "iokit" | "powermetrics"     │ Hardware exporter native       │
└─────────────────┴──────────────────────────────┴────────────────────────────────┘
```

### 5.2 Labels removed (were set, never queried, caused confusion)

| Removed label | Was set by | Reason removed |
|---------------|-----------|----------------|
| `platform` | OTEL attributes processor | Replaced by `gpu_provider` (more specific) |
| `host.name` | OTEL resource processor | Replaced by `hostname` (consistent naming) |
| `host.id` | OTEL resource processor | Never queried anywhere |
| `service.namespace` | OTEL resource processor | Never queried anywhere |
| `source` | Prometheus relabel | Redundant with `provider` |

### 5.3 `resource_to_telemetry_conversion`

The Prometheus exporter in the OTEL agent config must have this enabled so all resource attributes become metric labels:

```yaml
exporters:
  prometheus:
    resource_to_telemetry_conversion:
      enabled: true
```

---

## 6. OTEL Agent Config Templates

### 6.1 `otel-agent.env.j2`

```jinja2
# Rendered by monitoring-cfg role from cluster.yml + discovered.yml
NODE_ID={{ node.id }}
HOSTNAME={{ node.hostname }}
CLUSTER={{ cluster.name }}
ENVIRONMENT={{ cluster.environment }}
OS={{ node.os }}
GPU_PROVIDER={{ node.gpu_provider }}
CHIP={{ node.chip | default('unknown') }}
POOL={{ node.pools | join(',') }}
OTEL_GATEWAY_PRIMARY={{ cluster.otel_gateway.primary }}
OTEL_GATEWAY_SECONDARY={{ cluster.otel_gateway.secondary | default('') }}
GOMAXPROCS=2
GOMEMLIMIT=100MiB
```

### 6.2 `otel-agent-config.yaml.j2` — key sections

**Receivers** — one per discovered LLM provider + hardware exporter:

```jinja2
{# hw_exporter_port resolved from group_vars/all.yml:
   apple → 9101, nvidia → 9400, amd → 9102, none → 9100 #}
receivers:
  # Hardware exporter — port depends on gpu_provider
  prometheus/hw-exporter:
    config:
      scrape_configs:
        - job_name: hw-exporter
          scrape_interval: 15s
          static_configs:
            - targets: ["127.0.0.1:{{ hw_exporter_ports[node.gpu_provider] }}"]

{% for p in discovered.providers %}
  # LLM provider: {{ p.name }}
  prometheus/{{ p.name }}:
    config:
      scrape_configs:
        - job_name: llm-{{ p.name }}
          scrape_interval: 15s
          static_configs:
            - targets: ["127.0.0.1:{{ p.port }}"]
          metric_relabel_configs:
            - target_label: provider
              replacement: "{{ p.name }}"
{% endfor %}

  otlp:
    protocols:
      grpc:
        endpoint: "127.0.0.1:4317"
      http:
        endpoint: "127.0.0.1:4318"
```

**Processors** — resource labels injected here:

```yaml
processors:
  resource:
    attributes:
      - { key: node_id,      value: "${NODE_ID}",      action: upsert }
      - { key: hostname,     value: "${HOSTNAME}",     action: upsert }
      - { key: cluster,      value: "${CLUSTER}",      action: upsert }
      - { key: environment,  value: "${ENVIRONMENT}",  action: upsert }
      - { key: os,           value: "${OS}",           action: upsert }
      - { key: gpu_provider, value: "${GPU_PROVIDER}", action: upsert }
      - { key: chip,         value: "${CHIP}",         action: upsert }
      - { key: pool,         value: "${POOL}",         action: upsert }

  transform/quantization:
    metric_statements:
      - context: datapoint
        statements:
          - set(attributes["quantization"],
              ExtractPatterns(attributes["model"],
                "[-_](q[0-9]+[_k_msx]*|f16|f32|mxfp4)")[0])
              where attributes["model"] != nil

  filter/noise:
    metrics:
      exclude:
        match_type: regexp
        metric_names: [".*_debug_.*", "go_.*", "process_.*"]

  memory_limiter:
    check_interval: 1s
    limit_mib: 100
    spike_limit_mib: 25

  batch:
    send_batch_size: 200
    timeout: 5s
```

**Exporters**:

```yaml
exporters:
  otlp/gateway:
    endpoint: "${OTEL_GATEWAY_PRIMARY}"
    tls:
      insecure: true                       # v1: plain; v2: use certs
    retry_on_failure:
      enabled: true
      initial_interval: 5s
      max_interval: 30s
      max_elapsed_time: 300s
    sending_queue:
      num_consumers: 4
      queue_size: 1000

  prometheus:
    endpoint: "0.0.0.0:8889"
    resource_to_telemetry_conversion:
      enabled: true                        # promotes resource attrs to metric labels
    metric_expiration: 5m
```

**Service pipeline** — dynamic per node, all discovered providers included:

```jinja2
service:
  pipelines:
    metrics:
      receivers:
        - prometheus/hw-exporter
{% for p in discovered.providers %}
        - prometheus/{{ p.name }}
{% endfor %}
        - otlp
      processors:
        - memory_limiter
        - resource
        - transform/quantization
        - filter/noise
        - batch
      exporters:
        - otlp/gateway
        - prometheus
```

---

## 7. LLM Auto-Detection

### 7.1 Detection algorithm

```
for each node:
  if node.llm_endpoints defined in cluster.yml:
    use those directly — skip probing
  else:
    for port in [11434, 21434, 8000, 8080]:
      GET http://127.0.0.1:{port}/health   (timeout: 2s)
      if 200: mark provider as up
        GET /api/tags          → Ollama model list
        GET /v1/models         → OpenAI-compat model list
        extract model names + infer quantization from name

write /etc/hydra/discovered.yml
```

### 7.2 Quantization extraction regex

Applied to model name string, captures first match:

```
[-_](q[0-9]+[_k_msx]*|f16|f32|f64|mxfp4|gguf)
```

Examples:
- `qwen2.5-coder-3b-q4_k_m` → `q4_k_m`
- `llama3:8b-instruct-q4_K_M` → `q4_k_m` (lowercased)
- `gpt-oss-20B-MXFP4-MoE` → `mxfp4`
- `bge-m3-f16` → `f16`

### 7.3 Re-running discovery

Any time models are added or removed, re-run:

```bash
ansible-playbook site.yml --tags llm-discovery,monitoring-cfg
```

This updates `/etc/hydra/discovered.yml` and re-renders the OTEL config with new scrape targets.

---

## 8. Unified Multi-Hardware Dashboards

### 8.1 New Grafana template variables

These replace and extend the existing `$node`, `$cluster`, `$model` variables:

| Variable | Query | Purpose |
|----------|-------|---------|
| `$cluster` | `label_values(up, cluster)` | Filter by cluster |
| `$node` | `label_values(up{cluster="$cluster"}, node_id)` | Select node (all hardware) |
| `$pool` | `label_values(up{cluster="$cluster"}, pool)` | Filter by pool |
| `$gpu_provider` | `label_values(up{node_id=~"$node"}, gpu_provider)` | Current node's HW type |
| `$model` | `label_values(llm_model_loaded{node_id=~"$node"}, model)` | Filter by model |
| `$provider` | `label_values(llm_model_loaded{node_id=~"$node"}, provider)` | Filter by LLM runtime |
| `$gpu_util_metric` | Custom: `apple_gpu_utilization_percent,dcgm_fi_dev_gpu_util,amdgpu_utilization_percent` | Hardware-abstracted GPU util |
| `$gpu_mem_used_metric` | Custom: `apple_gpu_memory_used_bytes,dcgm_fi_dev_fb_used,amdgpu_vram_used_bytes` | Hardware-abstracted VRAM used |
| `$gpu_temp_metric` | Custom: `apple_gpu_temperature_celsius,dcgm_fi_dev_gpu_temp,amdgpu_temp` | Hardware-abstracted GPU temp |
| `$power_metric` | Custom: `apple_system_power_watts,dcgm_fi_dev_power_usage,amdgpu_power_avg` | Hardware-abstracted power |

### 8.2 Panel PromQL pattern

All hardware-metric panels use the abstracted variables:

```promql
# GPU utilization — works on Apple, NVIDIA, AMD
{__name__=~"$gpu_util_metric", node_id=~"$node"}

# Fleet nodes online — works across all hardware types
count(up{cluster="$cluster"} == 1) / count(up{cluster="$cluster"})

# Inference latency — hardware-agnostic (LLM metrics are always same)
histogram_quantile(0.95,
  sum(rate(llm_inference_duration_seconds_bucket{
    node_id=~"$node", phase="total", provider=~"$provider"
  }[5m])) by (le, model)
)
```

### 8.3 Apple-only panels

Panels using `apple_thermal_pressure`, `apple_ane_utilization_percent` get a `repeat` visibility condition:

```json
"options": {
  "reduceOptions": {},
  "orientation": "auto"
},
"fieldConfig": {
  "overrides": [{
    "matcher": { "id": "byFrameRefID", "options": "A" },
    "properties": [{
      "id": "custom.hideFrom",
      "value": { "viz": "$gpu_provider != 'apple'" }
    }]
  }]
}
```

### 8.4 New columns in Node Status Table (fleet-overview)

Add `pool`, `gpu_provider`, `chip`, `provider` columns to the node table. Queries:

```promql
# Pool label
label_replace(up{cluster="$cluster"}, "pool", "$1", "pool", "(.*)")

# GPU provider
label_replace(up{cluster="$cluster"}, "gpu_provider", "$1", "gpu_provider", "(.*)")
```

---

## 9. Alert Rule Fixes

### 9.1 Replace hardcoded job selectors

All alert rules that use `{job="apple-silicon-exporter"}` are replaced with `{cluster="hydra-prod"}` so they fire for any hardware type.

| Old expression | Fixed expression |
|---------------|-----------------|
| `up{job="apple-silicon-exporter"} == 0` | `up{cluster=~".+"} == 0` |
| `apple_scrape_duration_seconds{collector="iokit"} > 5` | Stays — Apple-only alert, correct |
| `apple_scrape_success{collector=~"iokit\|powermetrics\|metal"} == 0` | Stays — Apple-only alert, correct |

### 9.2 Add `node_id` to alert labels

All alert `labels:` blocks add `node_id: "{{ $labels.node_id }}"` so Grafana alert annotations show the human-readable node name, not an IP:port.

### 9.3 New alert: exporter down (hardware-agnostic)

```yaml
- alert: HardwareExporterDown
  expr: up{cluster=~".+", job=~"hw-exporter"} == 0
  for: 2m
  labels:
    severity: critical
    team: infra
  annotations:
    summary: "Hardware exporter down on {{ $labels.node_id }} ({{ $labels.gpu_provider }})"
```

---

## 10. Data Flow

```
┌─────────────────────────────────────────────────────────┐
│  cluster.yml                                            │
│  (node IPs, hw type, pools, llm endpoints)              │
└──────────┬──────────────────────────────────────────────┘
           │ ansible-playbook site.yml
           ▼
┌──────────────────────┐   ┌─────────────────────────────┐
│  base role           │   │  hw-exporter role            │
│  OTEL agent binary   │   │  apple-silicon-exporter      │
│  systemd/launchd     │   │  dcgm-exporter (NVIDIA)      │
└──────────┬───────────┘   │  node_exporter (CPU)         │
           │               └────────────┬────────────────┘
           │                            │ scrape :9101/:9400/:9100
           ▼                            ▼
┌──────────────────────────────────────────────────────────┐
│  llm-discovery role                                      │
│  probe :11434 :21434 :8000 :8080                        │
│  query /v1/models + /api/tags                            │
│  write /etc/hydra/discovered.yml                         │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  monitoring-cfg role                                     │
│  render otel-agent.env + otel-agent-config.yaml          │
│  (injects node_id, gpu_provider, pool, chip, os labels)  │
│  (adds provider label per LLM scrape job)                │
│  restart OTEL agent                                      │
└──────────────────────────┬───────────────────────────────┘
                           │ OTLP/gRPC
                           ▼
              ┌────────────────────────┐
              │  OTEL Gateway          │
              │  (existing)            │
              └────────────┬───────────┘
                           │ remote_write
                           ▼
              ┌────────────────────────┐
              │  Prometheus / Mimir    │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  Grafana               │
              │  unified dashboards    │
              │  $node → node_id       │
              │  $pool → pool          │
              │  $gpu_provider → hw    │
              │  $provider → runtime   │
              └────────────────────────┘
```

---

## 11. File Layout

```
ansible/
├── cluster.yml                               ← edit this to add/change nodes
├── inventory_plugin.py                       ← dynamic inventory from cluster.yml
├── site.yml                                  ← master playbook
├── group_vars/
│   └── all.yml                               ← version pins, default probe ports
└── roles/
    ├── base/
    │   ├── tasks/main.yml
    │   └── templates/
    │       ├── otel-agent.service.j2
    │       └── otel-agent.plist.j2
    ├── hw-exporter/
    │   ├── tasks/
    │   │   ├── main.yml
    │   │   ├── apple.yml
    │   │   ├── nvidia.yml
    │   │   ├── amd.yml
    │   │   └── cpu.yml
    │   └── templates/
    │       ├── apple-exporter.plist.j2
    │       └── dcgm-exporter.service.j2
    ├── llm-discovery/
    │   ├── tasks/main.yml
    │   └── templates/
    │       └── discovered.yml.j2
    └── monitoring-cfg/
        ├── tasks/main.yml
        └── templates/
            ├── otel-agent-config.yaml.j2
            └── otel-agent.env.j2

apple-silicon-monitoring/
├── dashboards/
│   ├── fleet-overview.json                   ← update: new variables, fixed queries
│   ├── node-deep-dive.json                   ← update: new variables, hw abstraction
│   └── quality-monitor.json                  ← update: add $provider variable
└── alerts/
    ├── apple-silicon-hardware.yaml           ← update: fix job selectors, add node_id
    ├── llm-inference.yaml                    ← update: fix job selectors, add node_id
    └── hallucination-detection.yaml          ← no changes needed
```

---

## 12. v2 Deferred Items

These are explicitly out of scope for this implementation:

| Item | Reason deferred |
|------|----------------|
| Secrets management (Vault / ansible-vault) | API keys are plain text in v1; acceptable for internal LAN cluster |
| TLS for OTEL agent → gateway | `insecure: true` in v1; TLS cert rotation adds complexity |
| Continuous LLM discovery (daemon) | Re-run ansible tag on model change is sufficient for v1 |
| AMD ROCm exporter | No AMD hardware in Phase 1 pilot; add when hardware is available |
| Multi-GPU nodes (gpu_index > 0) | Single GPU per node in current fleet |
| Grafana provisioning automation | Dashboards updated as JSON files; import is manual in v1 |
