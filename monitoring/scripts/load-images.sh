#!/usr/bin/env bash
# monitoring/scripts/load-images.sh
# Run on hydra-svc-01 (air-gapped) after copying monitoring/images/ here.
# Loads all pre-saved Docker image tarballs into the local Docker daemon.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_DIR="$SCRIPT_DIR/../images"

if [[ ! -d "$IMAGES_DIR" ]]; then
  echo "ERROR: images/ directory not found at $IMAGES_DIR"
  echo "Copy it from the internet-connected machine first."
  exit 1
fi

echo "==> Loading monitoring images from $IMAGES_DIR ..."
shopt -s nullglob
tarballs=("$IMAGES_DIR"/*.tar.gz)

if [[ ${#tarballs[@]} -eq 0 ]]; then
  echo "ERROR: No .tar.gz files found in $IMAGES_DIR"
  exit 1
fi

for f in "${tarballs[@]}"; do
  echo "    loading $(basename "$f") ..."
  docker load < "$f"
done

echo ""
echo "==> All images loaded. Loaded images:"
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}" | grep -E "victoriametrics|prom/|grafana|otel|loki|mysql|clickhouse|redis|opik" || true

echo ""
echo "    To start the stack:"
echo "    cd monitoring/ && docker compose up -d"
