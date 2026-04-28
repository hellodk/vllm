# Docker Test Environment for Apple Silicon LLM Monitoring

This directory contains Docker and Docker Compose configurations for the monitoring stack. The main `docker-compose.yaml` (in the project root) simulates a **20-node Mac Mini cluster** running 4 different LLM models.

## Quick Start

```bash
# From the project root (not this directory)
docker compose up -d

# Wait ~30s for services to start, then run health checks
bash test-setup.sh

# View logs
docker compose logs -f
```

## What Gets Deployed

The test environment creates:
- **20 mock Apple Silicon exporters** — simulating 20 Mac Mini nodes with realistic GPU, thermal, and power metrics
- **20 mock LLM servers** — simulating inference for Llama 3 70B, Mixtral 8x7B, Codellama 34B, and Qwen 72B
- **OTEL Gateway** — central metrics aggregation
- **Prometheus** — metrics storage (7d retention, 15s scrape interval)
- **Grafana** — 3 dashboards auto-provisioned
- **Alertmanager** — alert routing

## Services & Ports

All ports are in the **5900-5999** range to avoid collisions:

| Service | Port(s) | Description |
|---------|---------|-------------|
| mock-exporter-01 to 20 | 5900-5919 | Mock Apple Silicon hardware metrics |
| mock-llm-server-01 to 20 | (internal) | Mock LLM inference servers |
| otel-gateway (gRPC) | 5920 | OTLP gRPC receiver |
| otel-gateway (HTTP) | 5921 | OTLP HTTP receiver |
| otel-gateway (Prom) | 5922 | Prometheus metrics endpoint |
| otel-gateway (health) | 5923 | Health check |
| otel-gateway (internal) | 5924 | Internal metrics |
| prometheus | 5930 | Prometheus UI and API |
| grafana | 5940 | Grafana dashboards |
| alertmanager | 5950 | Alert management |

## Access Points

- **Grafana**: http://localhost:5940 (admin/admin) — 3 dashboards auto-provisioned
- **Prometheus**: http://localhost:5930 — query metrics, view targets
- **Alertmanager**: http://localhost:5950 — view firing alerts
- **Mock Exporter Metrics**: http://localhost:5900/metrics (node 01)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    20 Simulated Mac Mini Nodes                   │
│                                                                  │
│  mock-exporter-01..20          mock-llm-server-01..20           │
│  :5900-5919/metrics            (Llama3, Mixtral, Codellama,     │
│  (GPU, thermal, power)          Qwen models via OTLP)           │
└──────────────────────────────────┬──────────────────────────────┘
                                   │ scraped + OTLP
                                   ▼
                        ┌──────────────────────┐
                        │    otel-gateway      │
                        │   :5920-5924         │
                        └──────────┬───────────┘
                                   │ remote_write
                                   ▼
                        ┌──────────────────────┐
                        │     prometheus       │
                        │      :5930           │
                        └──────────┬───────────┘
                                   │
                        ┌──────────┴───────────┐
                        ▼                      ▼
              ┌──────────────────┐   ┌─────────────────┐
              │     grafana      │   │  alertmanager   │
              │     :5940        │   │     :5950       │
              └──────────────────┘   └─────────────────┘
```

## Useful Commands

```bash
# Start all services
docker compose up -d

# Check service status
docker compose ps

# View logs for a specific service
docker compose logs -f otel-gateway

# View logs for a specific mock node
docker compose logs -f mock-exporter-05

# Restart a service
docker compose restart mock-exporter-01

# Stop all services
docker compose down

# Stop and remove volumes (clean slate)
docker compose down -v

# Rebuild mock services after code changes
docker compose build --no-cache
docker compose up -d
```

## Verify Metrics

```bash
# Run the full health check suite
bash test-setup.sh

# Check a specific mock exporter
curl http://localhost:5900/metrics | grep apple_gpu

# Check OTEL gateway Prometheus endpoint
curl http://localhost:5922/metrics | grep llm_

# Query Prometheus directly
curl 'http://localhost:5930/api/v1/query?query=apple_gpu_utilization_percent'

# Check active alerts
curl http://localhost:5930/api/v1/alerts

# Check Prometheus targets (should show 20 exporters)
curl http://localhost:5930/api/v1/targets | python3 -m json.tool
```

## Grafana Dashboards

The following dashboards are automatically provisioned via `grafana/provisioning/`:
- **Fleet Overview**: Cluster-wide GPU heatmap, throughput, thermal status across 20 nodes
- **Node Deep Dive**: Per-node GPU, CPU, thermal, power, and LLM performance
- **Quality Monitor**: Hallucination risk scoring, entropy, confidence, repetition

## Docker Directory Contents

```
docker/
├── otel-agent.yaml              # OTEL agent config (containerized)
├── otel-gateway.yaml            # OTEL gateway config (containerized)
├── prometheus/
│   ├── prometheus.yml            # Single-node Prometheus config
│   ├── prometheus-20nodes.yml    # 20-node scrape config
│   └── rules/                    # Alert rules (copied from alerts/)
├── alertmanager/
│   └── alertmanager.yml          # Alert routing config
└── grafana/provisioning/
    ├── datasources/datasources.yaml
    └── dashboards/dashboards.yaml
```

## Troubleshooting

### Services not starting
```bash
# Check for port conflicts
ss -tlnp | grep -E '59[0-5][0-9]'

# View service logs
docker compose logs otel-gateway
docker compose logs mock-exporter-01
```

### No metrics in Prometheus
1. Check a mock-exporter is producing metrics: `curl http://localhost:5900/metrics`
2. Check OTEL gateway health: `curl http://localhost:5923`
3. Check Prometheus targets: http://localhost:5930/targets
4. Verify scrape config matches port mapping

### Grafana shows no data
1. Verify Prometheus datasource: http://localhost:5940/datasources
2. Check time range in Grafana (set to "Last 15 minutes")
3. Verify metrics exist: query `up` in Prometheus
4. Check Grafana provisioning logs: `docker compose logs grafana`
