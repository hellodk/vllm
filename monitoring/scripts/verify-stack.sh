#!/usr/bin/env bash
# monitoring/scripts/verify-stack.sh
# Quick health check for all monitoring services after stack start.
set -euo pipefail

GW="${1:-localhost}"   # pass hydra-svc-01 IP as first arg if running remotely

ok() { echo "  [OK]  $1"; }
fail() { echo "  [FAIL] $1"; FAILED=1; }

FAILED=0

check() {
  local name="$1" url="$2" expected="${3:-200}"
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
  if [[ "$code" == "$expected" ]]; then
    ok "$name ($url) → HTTP $code"
  else
    fail "$name ($url) → HTTP $code (expected $expected)"
  fi
}

echo "==> Verifying Hydra monitoring stack on $GW ..."
echo ""

check "Victoria Metrics"  "http://$GW:8428/health"
check "Prometheus"         "http://$GW:9090/-/ready"
check "Grafana"            "http://$GW:3000/api/health"
check "Alertmanager"       "http://$GW:9093/-/ready"
check "OTEL Collector"     "http://$GW:4318/"             "405"
check "Loki"               "http://$GW:3100/ready"
check "Opik Backend"       "http://$GW:8080/api/v1/health" "200"
check "Opik Frontend"      "http://$GW:5173/"

echo ""
if [[ $FAILED -eq 0 ]]; then
  echo "==> All services healthy."
else
  echo "==> Some services failed — check: docker compose -f monitoring/docker-compose.yml logs"
  exit 1
fi
