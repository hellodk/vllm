# salt/pillar/llm.sls
# Mirror of ansible/group_vars/all/main.yml — global defaults for every LLM node.
# Override per-node in pillar/nodes/<minion-id>.sls.

# ── Common directories ────────────────────────────────────────────────────────
llm_base_dir: /opt/hydra/llm
llm_log_dir: /var/log/hydra
llm_bin_dir: /opt/hydra/llm/bin
llm_perf_proxy_path: /opt/hydra/llm/bin/llm_perf_proxy.py

# ── Homebrew / Python ─────────────────────────────────────────────────────────
homebrew_prefix: /opt/homebrew
python_version: "3.11"

# ── MLX paths ─────────────────────────────────────────────────────────────────
mlx_dir: /opt/hydra/llm/mlx
mlx_venv: /opt/hydra/llm/mlx/venv
mlx_models_dir: /opt/hydra/models/mlx
mlx_default_model: /opt/hydra/models/mlx/Mistral-7B-Instruct-v0.3-4bit

# ── MLX port contract (must not change without updating dashboards) ───────────
mlx_port: 11500              # public API / vllm-mlx serving port
mlx_perf_scrape_port: 11501  # perf-proxy /metrics when sidecar active
mlx_logexporter_scrape_port: 11502  # log-exporter /metrics
mlx_internal_port: 11510     # engine loopback when sidecar fronts it
mlx_workers: 2
mlx_max_tokens: 2048
mlx_max_kv_size: 4096
mlx_max_concurrency: 8
mlx_memory_limit_mb: 12000

# ── MLX backend selector (parallel of hydra_mlx_backend in group_vars) ────────
# vllm_mlx  → vllm-mlx serves vllm:* natively on mlx_port (default, production)
# mlx_lm    → pure mlx_lm.server; perf-proxy sidecar enabled; engine on loopback
hydra_mlx_backend: vllm_mlx

# ── perf-proxy quality + tracing layers (parallel of group_vars) ──────────────
# quality: in-proxy hallucination/quality scoring (llm_* metrics). Scores only —
#          raw response text is never logged. logprobs gate entropy/perplexity.
# tracing: one OTLP/HTTP span per request → local OTEL agent (otel_traces_http_endpoint).
llm_quality_enabled: true
llm_traces_enabled: true
llm_otlp_traces_endpoint: "http://127.0.0.1:4318"

# ── MLX packages (JFrog Artifactory primary, offline wheels fallback) ─────────
mlx_use_jfrog: true
jfrog_pypi_index: "https://artifactory.hydra.local/artifactory/api/pypi/pypi-remote/simple"
jfrog_pypi_trusted_host: "artifactory.hydra.local"
mlx_packages:
  - "mlx-lm==0.31.3"
  - "mlx>=0.22.0"
  - "huggingface-hub>=0.27.0"
  - "transformers>=4.48.0"
  - "sentencepiece>=0.2.0"
  - "protobuf>=4.25.0"
  - "numpy>=1.26.0,<2.3.0"
mlx_vllm_packages:
  - "vllm-mlx==0.4.0rc1"
mlx_telemetry_packages:
  - "llm-telemetry>=0.1.0"
  - "prometheus-client>=0.20.0"

# ── exo P2P distributed inference ────────────────────────────────────────────
exo_dir: /opt/hydra/llm/exo
exo_venv: /opt/hydra/llm/exo/venv
exo_models_dir: /opt/hydra/models/mlx
exo_default_model: mlx-community/Mistral-7B-Instruct-v0.3-4bit
exo_model_local_path: /opt/hydra/models/mlx/Mistral-7B-Instruct-v0.3-4bit
exo_p2p_port: 52415
exo_api_port: 52416           # public exo chat API
exo_perf_scrape_port: 52417   # perf-proxy /metrics
exo_internal_api_port: 52426  # exo API loopback when sidecar fronts it
exo_max_kv: 8192
exo_max_concurrency: 4
exo_sidecar_enabled: true
exo_node_id: ""               # defaults to Salt minion ID (grains['id']) in templates
exo_peers: []                 # list of peer IPs; override per node

exo_packages:
  - "exo-explore>=0.0.1"
  - "mlx>=0.16.0"
  - "mlx-lm>=0.19.0"
  - "grpcio>=1.64.0"
  - "grpcio-tools>=1.64.0"
  - "protobuf>=4.25.0"
  - "numpy>=1.26.0"
  - "huggingface-hub>=0.23.0"
  - "transformers>=4.41.0"
  - "prometheus-client>=0.20.0"

# ── Apple distributed AllReduce probe ────────────────────────────────────────
mlx_allreduce_enabled: false           # enable on tensor_parallel group leaders
mlx_allreduce_fabric_scrape_port: 11503
mlx_allreduce_size_mb: 256
mlx_allreduce_iters: 20
mlx_allreduce_interval_sec: 60
allreduce_dir: /opt/hydra/llm/allreduce
allreduce_textfile_dir: /var/lib/hydra/textfile
allreduce_textfile: /var/lib/hydra/textfile/mlx_allreduce.prom
allreduce_runner: /opt/hydra/llm/allreduce/run-allreduce-probe.sh
allreduce_python: /opt/hydra/llm/mlx/venv/bin/python
allreduce_mlx_launch: /opt/hydra/llm/mlx/venv/bin/mlx.launch
allreduce_group: ""           # set by monitoring-common/mlx-allreduce-probe logic
allreduce_peer_hosts: []      # list of peer IPs for this TP group

# ── Tensor-parallel groups (for allreduce probe + OTEL labels) ───────────────
tensor_parallel_groups:
  A:
    - hydra-large-01
    - hydra-large-02

# ── LLM engine catalog (mirrors group_vars llm_engine_catalog) ────────────────
llm_engine_catalog:
  mlx:
    port: 11500
    scheme: http
    health_path: /v1/models
    metrics_path: /metrics
    auth: none
  vllm-mlx:
    port: 11500
    scheme: http
    health_path: /health
    metrics_path: /metrics
    auth: none
  mlx_perf:
    port: 11501
    scheme: http
    health_path: /metrics
    metrics_path: /metrics
    auth: none
  exo:
    port: 52417
    scheme: http
    health_path: /metrics
    metrics_path: /metrics
    auth: none

# ── LLM frameworks active on this node (drives discovered.yml logic) ──────────
# Override per node. Values: mlx, vllm-mlx, exo, etc.
llm_frameworks: []

# ── OTEL agent configuration ──────────────────────────────────────────────────
otel_gateway_endpoint: "otel-gateway.hydra.svc.cluster.local:4317"
otel_tls_insecure: true
otel_tls_ca_file: /etc/hydra/tls/ca.crt
otel_tls_cert_file: /etc/hydra/tls/client.crt
otel_tls_key_file: /etc/hydra/tls/client.key
mc_health_port: 13133
mc_grpc_port: 4317
mc_http_port: 4318
mc_prometheus_port: 8888
mc_memory_limit_mib: 256
mc_memory_spike_mib: 64
mc_collection_interval: 30s
mc_apple_exporter_port: 9101
mc_os: macos
mc_gpu_provider: apple
mc_chip: "unknown"
mc_cluster: hydra
mc_environment: production
mc_pool: fast
mc_victoria_url: ""
mc_fleet_otlp: ""
enable_hostmetrics: true
enable_apple_hw: true
enable_llm_scrape: true
enable_otlp_ingest: true
enable_rdma_scrape: false
enable_fleet_otlp: false
otel_traces_http_endpoint: "http://127.0.0.1:4318"

# ── Hydra node identity (override per node) ───────────────────────────────────
hydra_node_id: ""             # defaults to Salt minion ID in templates
hydra_hostname: ""            # defaults to grains['fqdn']
hydra_gpu_provider: apple
hydra_os: macos
hydra_pools:
  - fast

# ── Air-gap flag ──────────────────────────────────────────────────────────────
hydra_airgapped: true
local_model_cache_dir: /Users/dk/models
