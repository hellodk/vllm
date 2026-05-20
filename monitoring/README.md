# Hydra Monitoring Stack

Docker-compose based monitoring + telemetry for the Hydra Apple Silicon LLM cluster.
**Do not `docker compose up` until images are pre-pulled and saved offline.**

## Services

| Service | Port | Purpose |
|---|---|---|
| Victoria Metrics | 8428 | Long-term TSDB (90-day retention) |
| Prometheus | 9090 | Scrape agent → remote_write to VM |
| Grafana | 3000 | Dashboards (admin/hydra-grafana-admin) |
| Alertmanager | 9093 | Alert routing → Slack |
| OTEL Collector | 4317/4318 | OTLP ingest from all 28 nodes |
| Loki | 3100 | Log aggregation (31-day retention) |
| Opik Backend | 8080 | LLM trace/span observability API |
| Opik Frontend | 5173 | Opik UI |
| Opik MySQL | 3306 | Opik metadata |
| Opik ClickHouse | 8123 | Opik trace/span store |
| Opik Redis | 6379 | Opik cache / task queue |

## Offline image prep (run on a machine with internet)

```bash
cd monitoring/

# Pull all images
docker compose pull

# Save to tar archives
mkdir -p images/
docker save victoriametrics/victoria-metrics:v1.101.0    | gzip > images/victoria-metrics.tar.gz
docker save prom/prometheus:v2.53.0                      | gzip > images/prometheus.tar.gz
docker save grafana/grafana:11.1.0                       | gzip > images/grafana.tar.gz
docker save prom/alertmanager:v0.27.0                    | gzip > images/alertmanager.tar.gz
docker save otel/opentelemetry-collector-contrib:0.104.0 | gzip > images/otel-collector.tar.gz
docker save grafana/loki:3.1.0                           | gzip > images/loki.tar.gz
docker save mysql:8.0                                    | gzip > images/opik-mysql.tar.gz
docker save clickhouse/clickhouse-server:24.6            | gzip > images/opik-clickhouse.tar.gz
docker save redis:7.2-alpine                             | gzip > images/opik-redis.tar.gz
docker save comet/opik-backend:latest                    | gzip > images/opik-backend.tar.gz
docker save comet/opik-frontend:latest                   | gzip > images/opik-frontend.tar.gz
```

## Load images on hydra-svc-01 (air-gapped)

```bash
for f in monitoring/images/*.tar.gz; do
  docker load < "$f"
done
```

## Configure

Edit `monitoring/.env` — update Slack webhook, change passwords, etc.

## Start the stack

```bash
cd monitoring/
docker compose up -d
```

## Verify

```bash
docker compose ps
curl http://localhost:8428/health          # Victoria Metrics
curl http://localhost:9090/-/ready         # Prometheus
curl http://localhost:3100/ready           # Loki
curl http://localhost:8080/api/v1/health   # Opik
```

## OTEL node integration

Each Mac Mini node runs `otel-mac-agent` (ansible role) configured to send OTLP to `hydra-svc-01:4317`. Resource attributes tagged per node:
- `cluster=hydra`
- `pool=<fast|reason|largepool|vision|embed|speech>`
- `node_id=<hostname>`
- `salt.minion_id=<node_id>`
