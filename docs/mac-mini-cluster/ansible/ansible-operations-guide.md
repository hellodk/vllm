# Hydra Pilot — Ansible Operations Guide

## For: Platform engineers deploying and operating the Hydra pilot cluster

---

## 1. Prerequisites

### 1.1 Your Control Machine (laptop/desktop — any OS)

```bash
# Install Ansible
pip install ansible

# Install required Ansible collections
cd mac-mini-cluster/ansible
make install
# This runs: ansible-galaxy install -r requirements.yml
# Installs: community.general, community.docker
```

### 1.2 Both Mac Minis

```bash
# Deploy your SSH key to each Mac Mini
ssh-copy-id admin@192.168.1.10    # Gateway (M1)
ssh-copy-id admin@192.168.1.11    # Inference (M3)

# Verify connectivity
cd mac-mini-cluster/ansible
make ping
# Should show: SUCCESS for both nodes
```

### 1.3 Edit Inventory

Edit `inventory/hosts.yml` — change the two IP addresses:

```yaml
gateway:
  hosts:
    hydra-gw:
      ansible_host: 192.168.1.10      # ← Your M1 Mac Mini IP

inference:
  hosts:
    hydra-inf-1:
      ansible_host: 192.168.1.11      # ← Your M3 Mac Mini IP
```

### 1.4 Edit Secrets

Edit `inventory/group_vars/all.yml`:

```yaml
litellm_api_key: "sk-hydra-pilot-CHANGE-ME"   # ← Set a real key
grafana_admin_password: "admin"                 # ← Set a real password
```

---

## 2. Deployment

### 2.1 Full Deployment (One Command)

```bash
make deploy
```

This runs `ansible-playbook playbooks/site.yml` which executes 7 phases in order:

```
Phase 1: common     → Homebrew, Python, node_exporter on BOTH nodes
Phase 2: docker     → Colima (Docker) on gateway node
Phase 3: vllm       → vLLM-MLX + model download on inference node
Phase 4: redis      → Redis on gateway node
Phase 5: monitoring → Prometheus, Grafana, Loki on gateway node
Phase 6: gateway    → LiteLLM proxy on gateway node
Phase 7: validate   → End-to-end test: gateway → inference → response
```

**Expected time**: 15–30 minutes (mostly model download in Phase 3).

### 2.2 Step-by-Step Deployment

If you prefer to deploy one phase at a time:

```bash
# Deploy base packages to both nodes
ansible-playbook playbooks/site.yml --tags common

# Deploy Docker to gateway
ansible-playbook playbooks/site.yml --tags docker

# Deploy vLLM-MLX to inference node (downloads ~4.5 GB model)
ansible-playbook playbooks/site.yml --tags vllm

# Deploy Redis to gateway
ansible-playbook playbooks/site.yml --tags redis

# Deploy monitoring stack
ansible-playbook playbooks/site.yml --tags monitoring

# Deploy LiteLLM gateway
ansible-playbook playbooks/site.yml --tags gateway

# Run validation test
ansible-playbook playbooks/site.yml --tags validate
```

### 2.3 Deploy to One Node Only

```bash
make deploy-gateway     # Gateway node only
make deploy-inference   # Inference node only
```

### 2.4 Dry Run (See What Would Change)

```bash
make check
# Shows diffs without applying any changes
```

---

## 3. Project Structure

```
ansible/
├── ansible.cfg                    # SSH settings, output format
├── Makefile                       # make deploy, make health, etc.
├── requirements.yml               # Ansible Galaxy dependencies
│
├── inventory/
│   ├── hosts.yml                  # ★ Your Mac Mini IPs go here
│   └── group_vars/
│       ├── all.yml                # ★ Global config (API key, ports, etc.)
│       ├── gateway.yml            # Gateway-specific: LiteLLM model routing
│       └── inference.yml          # Inference-specific: vLLM-MLX model config
│
├── playbooks/
│   ├── site.yml                   # Full deployment (7 phases)
│   ├── health-check.yml           # Verify all services are running
│   ├── rolling-restart.yml        # Restart services (--tags: vllm, litellm, monitoring)
│   ├── setup-developers.yml       # Generate IDE configs for dev team
│   ├── deploy-tabby.yml           # (Optional) Deploy Tabby autocomplete
│   └── update-models.yml          # Pull new model versions
│
├── roles/
│   ├── common/                    # Homebrew, Python, node_exporter
│   │   └── tasks/main.yml
│   │
│   ├── docker/                    # Colima (lightweight Docker for macOS)
│   │   └── tasks/main.yml
│   │
│   ├── vllm_mlx/                  # ★ vLLM-MLX inference runtime
│   │   ├── tasks/main.yml         #   Install vllm-mlx, download model, start
│   │   ├── templates/             #   launchd plist (auto-start on boot)
│   │   └── handlers/main.yml      #   Restart handler
│   │
│   ├── redis/                     # Redis for rate limiting
│   │   ├── tasks/main.yml
│   │   └── handlers/main.yml
│   │
│   ├── gateway/                   # LiteLLM proxy
│   │   ├── tasks/main.yml         #   Install litellm, deploy config
│   │   ├── templates/             #   litellm-config.yaml.j2, launchd plist
│   │   └── handlers/main.yml      #   Restart handler
│   │
│   ├── monitoring/                # Prometheus + Grafana + Loki + Promtail
│   │   ├── tasks/main.yml         #   Docker Compose for monitoring stack
│   │   ├── templates/             #   prometheus.yml.j2, alerts.yml.j2, etc.
│   │   └── handlers/main.yml      #   Restart handler
│   │
│   ├── inference/                 # Promtail log shipping (inference nodes)
│   │   ├── tasks/main.yml
│   │   └── templates/
│   │
│   └── continue_dev/              # IDE config generation
│       ├── tasks/main.yml
│       └── templates/             #   config.yaml.j2, cursor/cline settings
│
└── ansible-operations-guide.md    # ★ This file
```

---

## 4. Roles Reference

### 4.1 `common` — Base Setup

**Runs on**: All nodes
**What it does**:
- Installs Homebrew (if missing)
- Installs Python 3.11, curl, jq, htop
- Installs and starts `node_exporter` (system metrics for Prometheus)
- Creates `/opt/hydra/` directory structure

### 4.2 `docker` — Container Runtime

**Runs on**: Gateway only
**What it does**:
- Installs Docker CLI + Docker Compose via Homebrew
- Installs Colima (lightweight Docker alternative to Docker Desktop)
- Starts Colima with constrained resources (4 CPU, 2 GB RAM — leaves room for LiteLLM + Redis)

**Why Colima, not Docker Desktop?** Docker Desktop runs a full Linux VM and consumes 2–4 GB RAM. Colima is lighter. On the M1 with only 8 GB, every megabyte counts.

### 4.3 `vllm_mlx` — Inference Runtime

**Runs on**: Inference nodes
**What it does**:
- Installs Rust toolchain (required to build vLLM-MLX from source)
- Installs `vllm-mlx` via pip
- Creates a launchd plist that auto-starts vLLM-MLX on boot with:
  - Model: `Qwen/Qwen2.5-Coder-7B-Instruct`
  - Quantization: `mlx-4bit`
  - Max context: `8192` tokens
  - Max concurrent: `24` sequences
  - GPU utilization: `90%`
- Waits for vLLM-MLX to download the model (~4.5 GB) and start serving
- Verifies the model is loaded via `/v1/models`

**First run takes 10–20 minutes** (model download). Subsequent runs take ~30 seconds (model cached on SSD).

**Key config**: Controlled by variables in `inventory/group_vars/inference.yml` and `all.yml`. To change the model, edit `vllm_model` in `all.yml` and re-run `make deploy-inference`.

### 4.4 `redis` — Rate Limiting

**Runs on**: Gateway only
**What it does**:
- Installs Redis via Homebrew
- Starts Redis as a brew service (auto-starts on boot)
- Verifies Redis responds to PING

**Why Redis?** Per Hydra §9.5, rate limits and token budgets use Redis sorted-set sliding windows. This survives gateway restarts and (at scale) can be shared across multiple gateways.

### 4.5 `gateway` — API Gateway

**Runs on**: Gateway only
**What it does**:
- Installs LiteLLM proxy + tiktoken via pip
- Generates `litellm-config.yaml` from template (routes `hydra-coder` and `hydra-autocomplete` to vLLM-MLX on the inference node)
- Copies `guardrails.py` (secret/PII scanner, hallucination controls)
- Creates launchd plist for auto-start
- Verifies LiteLLM health and model list

**Key config**: Model routing defined in `inventory/group_vars/gateway.yml`. To add a new model or change routing, edit that file and re-run `make deploy-gateway`.

### 4.6 `monitoring` — Observability

**Runs on**: Gateway only (via Docker Compose)
**What it does**:
- Deploys `docker-compose.yml` with:
  - **Prometheus** (:9090) — scrapes vLLM-MLX, LiteLLM, node_exporter
  - **Grafana** (:3000) — dashboards (cluster health, request perf, tokens, guardrails)
  - **Loki** (:3100) — log aggregation
  - **Promtail** — ships logs from `/opt/hydra/logs/` to Loki
- Deploys Prometheus alert rules (node down, KV cache near full, high latency)
- Deploys Grafana datasources (Prometheus + Loki) and dashboard JSON

### 4.7 `continue_dev` — IDE Configs

**Runs on**: Localhost (your machine)
**What it does**:
- Generates `config.yaml` for Continue.dev (VS Code)
- Generates `settings.json` for Cursor
- Generates `settings.json` for Cline
- All configs point to `http://<gateway_ip>:4000/v1`

---

## 5. Day-to-Day Operations

### 5.1 Health Check

```bash
make health
```

Checks:
- node_exporter on both nodes
- vLLM-MLX is serving on inference node
- Redis is responding on gateway
- LiteLLM health endpoint
- Prometheus, Grafana, Loki are up
- End-to-end inference test (gateway → vLLM-MLX → response)
- Cross-node connectivity (gateway can reach inference node)

### 5.2 Restart Services

```bash
make restart              # Restart everything
make restart-vllm         # Restart vLLM-MLX only (inference node)
make restart-litellm      # Restart LiteLLM only (gateway)
make restart-monitoring   # Restart Prometheus/Grafana/Loki
```

All restarts are rolling (one node at a time) and verify health after restart.

### 5.3 View Logs

```bash
make logs-vllm            # Last 50 lines of vLLM-MLX log
make logs-litellm         # Last 50 lines of LiteLLM log
make logs-errors          # Recent errors across all nodes
```

Or SSH directly:
```bash
ssh admin@192.168.1.11 tail -f /opt/hydra/logs/vllm-mlx.log
```

Or use Grafana → Explore → Loki for searchable logs.

### 5.4 Monitor in Grafana

Open `http://<gateway_ip>:3000` (admin/admin).

**Key things to watch:**

| Panel | What to look for | Action if abnormal |
|-------|-----------------|-------------------|
| Node UP/DOWN | Both green | SSH to failed node, check process |
| KV cache % | Should be < 85% | Reduce `--max-num-seqs` or context limit |
| TTFT P95 | Should be < 5s | Check thermal throttling or load |
| Error rate | Should be < 5% | Check logs for specifics |
| Rate limit hits | Occasional OK | If frequent, increase limit in `all.yml` |

### 5.5 Change Configuration

All config is in `inventory/group_vars/`. After editing, re-deploy:

```bash
# Example: Change API key
vim inventory/group_vars/all.yml    # Edit litellm_api_key
make deploy-gateway                 # Re-deploy gateway only

# Example: Change model
vim inventory/group_vars/all.yml    # Edit vllm_model
make deploy-inference               # Re-deploy inference (will download new model)

# Example: Change rate limits
vim inventory/group_vars/all.yml    # Edit governance section
make deploy-gateway                 # Re-deploy gateway
```

### 5.6 Add a Developer

1. Install Continue.dev extension in VS Code
2. Generate the config: `make setup-devs`
3. Copy `ide-configs/continue-dev/config.yaml` to `~/.continue/config.yaml`
4. Give them the API key from `inventory/group_vars/all.yml`

---

## 6. Troubleshooting

### 6.1 vLLM-MLX won't start

```bash
# Check the log
ssh admin@<inference_ip> cat /opt/hydra/logs/vllm-mlx-error.log

# Common causes:
# - Model download failed: re-run `make deploy-inference`
# - Out of memory: check if another process is using RAM
# - Rust not installed: the role should handle this, but check:
ssh admin@<inference_ip> rustc --version
```

### 6.2 LiteLLM can't reach inference node

```bash
# Test connectivity from gateway
ssh admin@<gateway_ip> curl http://<inference_ip>:8000/v1/models

# If unreachable:
# - Check firewall: port 8000 must be open on inference node
# - Check vLLM-MLX is running: ssh to inference node, check process
# - Check IPs in inventory/hosts.yml match actual IPs
```

### 6.3 Grafana shows no data

```bash
# Check Prometheus targets
open http://<gateway_ip>:9090/targets

# All targets should show "UP". If "DOWN":
# - vllm-* target down: vLLM-MLX not running or wrong IP
# - node-* target down: node_exporter not running
# - litellm target down: use host.docker.internal issue — check Docker networking
```

### 6.4 Model download stuck

First run downloads ~4.5 GB from HuggingFace. If it stalls:

```bash
# Check progress
ssh admin@<inference_ip> ls -la ~/.cache/huggingface/

# If network is slow, download manually:
ssh admin@<inference_ip>
pip3 install huggingface-hub
huggingface-cli download Qwen/Qwen2.5-Coder-7B-Instruct
# Then re-run: make deploy-inference
```

### 6.5 "Rate limit exceeded" for developers

Edit `inventory/group_vars/all.yml`:

```yaml
governance:
  pilot_user:
    requests_per_minute: 60    # Increase from 30
    tokens_per_day: 500000     # Increase from 200000
```

Then: `make deploy-gateway`

---

## 7. Variable Reference

### 7.1 all.yml — Global Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `vllm_port` | `8000` | vLLM-MLX inference API port |
| `vllm_model` | `Qwen/Qwen2.5-Coder-7B-Instruct` | HuggingFace model ID |
| `vllm_quantization` | `mlx-4bit` | Quantization (mlx-4bit = Q4_K_M equivalent) |
| `vllm_max_model_len` | `8192` | Max context window (tokens) |
| `vllm_max_num_seqs` | `24` | Max concurrent requests (from KV cache math) |
| `vllm_gpu_memory_utilization` | `0.90` | GPU memory fraction for model+KV |
| `litellm_port` | `4000` | LiteLLM gateway API port |
| `litellm_api_key` | `sk-hydra-pilot-CHANGE-ME` | API key for all requests |
| `redis_port` | `6379` | Redis port |
| `prometheus_port` | `9090` | Prometheus port |
| `grafana_port` | `3000` | Grafana port |
| `grafana_admin_password` | `admin` | Grafana admin password |
| `loki_port` | `3100` | Loki port |
| `guardrails_temperature_cap` | `0.3` | Max temperature for code requests |
| `hydra_base_dir` | `/opt/hydra` | Base directory on Mac Minis |

### 7.2 gateway.yml — Model Routing

| Variable | Description |
|----------|-------------|
| `litellm_models` | List of model aliases routed through LiteLLM |
| `litellm_models[].model_name` | Name developers use (e.g., `hydra-coder`) |
| `litellm_models[].backend_model` | Actual model ID on vLLM-MLX |
| `litellm_models[].max_tokens` | Default max output tokens |
| `litellm_models[].temperature` | Default temperature |

### 7.3 inference.yml — Inference Config

| Variable | Description |
|----------|-------------|
| `vllm_node_model` | Model to serve (inherits from `all.yml`) |
| `vllm_node_quantization` | Quantization format |
| `vllm_node_max_model_len` | Context window cap |
| `vllm_node_max_num_seqs` | Concurrent request cap |

---

## 8. Playbook Reference

| Playbook | Command | What it does | When to use |
|----------|---------|-------------|-------------|
| `site.yml` | `make deploy` | Full 7-phase deployment | Initial setup or full redeploy |
| `health-check.yml` | `make health` | Verify all services + E2E test | Daily or after any change |
| `rolling-restart.yml` | `make restart` | Restart services with health checks | After config changes, stuck processes |
| `setup-developers.yml` | `make setup-devs` | Generate IDE configs | When onboarding new developers |
| `update-models.yml` | — | Pull new model versions | When switching models |

### Tags for Selective Deployment

```bash
ansible-playbook playbooks/site.yml --tags common       # Phase 1 only
ansible-playbook playbooks/site.yml --tags docker        # Phase 2 only
ansible-playbook playbooks/site.yml --tags vllm          # Phase 3 only
ansible-playbook playbooks/site.yml --tags redis         # Phase 4 only
ansible-playbook playbooks/site.yml --tags monitoring    # Phase 5 only
ansible-playbook playbooks/site.yml --tags gateway       # Phase 6 only
ansible-playbook playbooks/site.yml --tags validate      # Phase 7 only
```

### Limiting to Specific Nodes

```bash
ansible-playbook playbooks/site.yml --limit gateway      # Gateway node only
ansible-playbook playbooks/site.yml --limit inference     # Inference node only
ansible-playbook playbooks/site.yml --limit hydra-inf-1   # Specific host
```

---

## 9. Quick Reference Card

```
┌─────────────────────────────────────────────────────────────┐
│              HYDRA PILOT — QUICK REFERENCE                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  FIRST TIME SETUP:                                          │
│    1. Edit inventory/hosts.yml (set IPs)                    │
│    2. Edit inventory/group_vars/all.yml (set API key)       │
│    3. make install                                          │
│    4. make ping                                             │
│    5. make deploy                                           │
│    6. make health                                           │
│                                                             │
│  DAILY OPS:                                                 │
│    make health          → Is everything running?            │
│    make logs-vllm       → What's happening on inference?    │
│    make logs-litellm    → What's happening on gateway?      │
│    make restart-vllm    → Inference node stuck              │
│    make restart-litellm → Gateway stuck                     │
│                                                             │
│  CONFIG CHANGES:                                            │
│    Edit group_vars/*.yml → make deploy                      │
│                                                             │
│  ADD A DEVELOPER:                                           │
│    make setup-devs → give them config.yaml + API key        │
│                                                             │
│  URLS:                                                      │
│    API:        http://<gateway>:4000/v1                      │
│    Grafana:    http://<gateway>:3000                         │
│    Prometheus: http://<gateway>:9090/targets                 │
│                                                             │
│  KEY FILES:                                                 │
│    inventory/hosts.yml              ← Mac Mini IPs          │
│    inventory/group_vars/all.yml     ← All settings          │
│    inventory/group_vars/gateway.yml ← Model routing         │
│    playbooks/site.yml               ← Main deployment       │
│    roles/vllm_mlx/                  ← Inference runtime     │
│    roles/gateway/                   ← API gateway           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```
