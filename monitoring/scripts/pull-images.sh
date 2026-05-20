#!/usr/bin/env bash
# monitoring/scripts/pull-images.sh
# Run on a machine WITH internet access to pull and save all monitoring images.
# Then copy monitoring/images/ to hydra-svc-01 and run load-images.sh there.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_DIR="$SCRIPT_DIR/../images"
mkdir -p "$IMAGES_DIR"

declare -A IMAGES=(
  [victoria-metrics]="victoriametrics/victoria-metrics:v1.101.0"
  [prometheus]="prom/prometheus:v2.53.0"
  [grafana]="grafana/grafana:11.1.0"
  [alertmanager]="prom/alertmanager:v0.27.0"
  [otel-collector]="otel/opentelemetry-collector-contrib:0.104.0"
  [loki]="grafana/loki:3.1.0"
  [opik-mysql]="mysql:8.0"
  [opik-clickhouse]="clickhouse/clickhouse-server:24.6"
  [opik-redis]="redis:7.2-alpine"
  [opik-backend]="comet/opik-backend:latest"
  [opik-frontend]="comet/opik-frontend:latest"
)

echo "==> Pulling monitoring images..."
for name in "${!IMAGES[@]}"; do
  image="${IMAGES[$name]}"
  out="$IMAGES_DIR/${name}.tar.gz"
  if [[ -f "$out" ]]; then
    echo "    [skip] $out already exists"
    continue
  fi
  echo "    pulling $image ..."
  docker pull "$image"
  echo "    saving  → $out"
  docker save "$image" | gzip > "$out"
done

echo ""
echo "==> Done. Image tarballs saved to: $IMAGES_DIR/"
echo ""
echo "    Next: copy the images/ directory to hydra-svc-01, then run:"
echo "    bash monitoring/scripts/load-images.sh"
