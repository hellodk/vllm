# Hydra — Commands Reference

All commands run from the **project root** (`/home/dk/Documents/git/hydra`) unless noted.

---

## 1. Monitoring Stack — Deploy & Configure

### First-time setup

```bash
# Install ansible collections (run once)
cd ansible
ansible-galaxy collection install -r requirements.yml
```

### Edit cluster config

`ansible/cluster.yml` is the **only file you need to edit** to add or change nodes.

```yaml
# Minimal node entry — copy and adjust for each machine
- id: my-node
  hostname: my-hostname
  ip: 192.168.1.XX
  user: dk
  os: macos          # macos | linux
  gpu_provider: apple  # apple | nvidia | amd | none
  chip: m3
  ram_gb: 16
  pools: [fast]
  maintenance: false
  llm_endpoints:
    - provider: ollama
      url: "http://127.0.0.1:11434/v1"
      api_key: ~
```

Set `maintenance: true` to temporarily exclude a node without removing it.

### Verify inventory

```bash
cd ansible
python inventory_plugin.py --list | python -m json.tool | grep -E '"hydra_node_id|hydra_gpu_provider|hydra_pools"'
```

### Dry-run (no changes applied)

```bash
cd ansible
ansible-playbook site.yml --check --diff
```

### Deploy to all nodes

```bash
cd ansible
ansible-playbook site.yml
```

### Deploy to specific node

```bash
cd ansible
ansible-playbook site.yml --limit p1-m3-16g
```

### Run a single role (e.g., after changing models)

```bash
cd ansible
# Re-discover LLM providers + re-render OTEL config
ansible-playbook site.yml --tags llm-discovery,monitoring-cfg

# Re-deploy only the hardware exporter
ansible-playbook site.yml --tags hw-exporter

# Re-render OTEL config only (fastest)
ansible-playbook site.yml --tags monitoring-cfg
```

### Verify OTEL agent is running (on target node)

```bash
# Linux
ssh dk@192.168.1.30 systemctl status otel-agent

# macOS
ssh dk@192.168.1.24 sudo launchctl list com.hydra.otel-agent
```

---

## 2. Benchmarks — Phase 1 Pilot

### Run the standard benchmark on a single machine

```bash
# Usage: --machine (m2-8g|m3-16g|i7-rtx3050) --model (3b|7b|8b|14b|70b) --runtime (llamacpp|ollama)
scripts/bench/run-phase1.sh --machine m3-16g --model 8b --runtime llamacpp

# NVIDIA node — sweep GPU layers (0 = CPU only, 20 = partial GPU, 33 = max GPU)
scripts/bench/run-phase1.sh --machine i7-rtx3050 --model 8b --runtime llamacpp --ngl 20

# Results written to: results/MACHINE_MODEL_RUNTIME_YYYYMMDD_HHMMSS.txt
```

### Ingest benchmark results into benchmarkings.md

After a run, parse the output file and update the benchmark tables automatically:

```bash
python scripts/ingest_results.py \
  --machine p1-m3-16g \
  --runtime llamacpp \
  results/m3-16g_8b_llamacpp_*.txt
```

Dry-run (prints what would change, writes nothing):

```bash
python scripts/ingest_results.py --dry-run --machine p1-m3-16g --runtime llamacpp results/*.txt
```

### Concurrent user load test — Ollama

```bash
# --vus: virtual users  --duration: seconds  --model: model name
scripts/bench/ollama-concurrent.sh \
  --vus 10 \
  --duration 120 \
  --model llama3:8b-instruct-q4_K_M \
  --url http://192.168.1.24:11434/v1
```

### k6 chat load test (requires k6 installed)

```bash
TARGET_URL=http://192.168.1.24:4000/v1 \
MODEL=llama3-8b-q4 \
k6 run scripts/bench/k6-chat.js
```

### k6 autocomplete load test

```bash
TARGET_URL=http://192.168.1.24:4000/v1 \
MODEL=llama3-8b-q4 \
k6 run scripts/bench/k6-autocomplete.js
```

---

## 3. Dashboards — Import & Use

### Import updated dashboards into Grafana

After modifying dashboards (or after a fresh install):

1. Open Grafana → **Dashboards → Import**
2. Upload the JSON file:
   - `apple-silicon-monitoring/dashboards/fleet-overview.json`
   - `apple-silicon-monitoring/dashboards/node-deep-dive.json`
   - `apple-silicon-monitoring/dashboards/quality-monitor.json`
3. Set the Prometheus datasource UID to match your Grafana instance

### Re-apply dashboard patches (after editing source JSON or adding new nodes)

```bash
python apple-silicon-monitoring/scripts/patch_dashboards.py
# Then re-import in Grafana (step above)
```

### Dashboard variable setup after import

On first load, set these variables in the Grafana toolbar:

| Variable | What to select |
|----------|---------------|
| `cluster` | Your cluster name (e.g., `hydra-prod`) |
| `node` | Any specific node, or leave empty for all |
| `gpu_util_metric` | `apple_gpu_utilization_percent` for Apple nodes, `dcgm_fi_dev_gpu_util` for NVIDIA |
| `power_metric` | `apple_system_power_watts` for Apple, `dcgm_fi_dev_power_usage` for NVIDIA |

---

## 4. Alerts — Deploy to Prometheus/Alertmanager

### Copy alert rules to Prometheus rules directory

```bash
cp apple-silicon-monitoring/alerts/*.yaml /etc/prometheus/rules/
# or for Mimir ruler:
mimirtool rules load apple-silicon-monitoring/alerts/*.yaml --address http://mimir:9009 --id hydra
```

### Validate alert YAML

```bash
python3 -c "
import yaml
for f in ['apple-silicon-monitoring/alerts/apple-silicon-hardware.yaml',
          'apple-silicon-monitoring/alerts/llm-inference.yaml']:
    with open(f) as fh: data = yaml.safe_load(fh)
    n = len(data['groups'][0]['rules'])
    print(f'{f}: {n} rules OK')
"
```

---

## 5. Tests

### Run all tests

```bash
# Ansible (inventory + quantization logic)
cd ansible && python -m pytest tests/ -v

# Dashboard patches
python -m pytest apple-silicon-monitoring/tests/ -v
```

### Run ansible inventory tests only

```bash
cd ansible && python -m pytest tests/test_inventory_plugin.py -v
```

---

## 6. Observability — Quick Checks

### Check OTEL agent health on a node

```bash
curl http://NODE_IP:13133   # should return 200
```

### Check hardware exporter metrics

```bash
# Apple Silicon
curl http://NODE_IP:9101/metrics | grep apple_gpu

# NVIDIA
curl http://NODE_IP:9400/metrics | grep dcgm

# CPU/Linux
curl http://NODE_IP:9100/metrics | grep node_cpu
```

### Check LLM provider metrics

```bash
# Ollama
curl http://NODE_IP:11434/metrics

# llama.cpp server (requires --metrics flag when starting)
curl http://NODE_IP:21434/metrics
```

---

## 7. Common Operations

### Add a new node to the cluster

1. Edit `ansible/cluster.yml` — add the node entry
2. Verify: `cd ansible && python inventory_plugin.py --list | python -m json.tool`
3. Deploy: `cd ansible && ansible-playbook site.yml --limit NEW_NODE_ID`

### Change a node's pool assignment

1. Edit `pools:` in `ansible/cluster.yml`
2. Re-render OTEL config: `cd ansible && ansible-playbook site.yml --tags monitoring-cfg --limit NODE_ID`

### Put a node into maintenance mode

1. Set `maintenance: true` in `ansible/cluster.yml`
2. Node is automatically excluded from all ansible runs until restored

### Upgrade OTEL agent version

1. Update `versions.otel_agent` in `ansible/cluster.yml`
2. Run: `cd ansible && ansible-playbook site.yml --tags base`

### Re-run benchmark after adding a model

```bash
# Discover newly loaded models on a node
cd ansible && ansible-playbook site.yml --tags llm-discovery,monitoring-cfg --limit p1-m3-16g

# Run benchmark
scripts/bench/run-phase1.sh --machine m3-16g --model 14b --runtime llamacpp

# Ingest results
python scripts/ingest_results.py --machine p1-m3-16g --runtime llamacpp results/m3-16g_14b_*.txt
```

---

## 8. Git Commit Log (what was built)

```
fdec6ec fix(dashboards): fix variable ordering, job regex exact match
ecd7abd feat(dashboards): multi-hardware variables and abstracted metric expressions
ae04467 fix(alerts): add node_id annotation to all alerts, add HardwareExporterDown
963fe03 fix(ansible): address all external audit findings (12 critical/important fixes)
ef1b4a2 fix(ansible): env injection, executable bit, dead handler, YAML formatting
8c1d207 feat(ansible): add ansible.cfg; project permissions
b7684b2 feat(ansible): llm-discovery role
bf46f08 feat(ansible): monitoring-cfg role
0ed470b feat(ansible): hw-exporter role
51b5c52 feat(ansible): base role
168c09f feat(ansible): inventory plugin
12c8fbc feat(ansible): cluster.yml + group_vars
0e53f15 feat: Phase 1 benchmark scripts + results ingestion
b784a70 feat: benchmarkings.md plan and live result log
```
