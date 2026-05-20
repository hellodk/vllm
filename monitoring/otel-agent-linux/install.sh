#!/usr/bin/env bash
# monitoring/otel-agent-linux/install.sh
# Installs otelcol-contrib on a Linux x86_64 machine and configures it
# to push host metrics to the Hydra central OTEL Collector.
# Run as root or with sudo.
set -euo pipefail

OTEL_VERSION="0.104.0"
INSTALL_DIR="/usr/local/bin"
CONFIG_DIR="/etc/otel"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Detect architecture ───────────────────────────────────────────────────────
ARCH=$(uname -m)
case "$ARCH" in
  x86_64)  OTEL_ARCH="amd64" ;;
  aarch64) OTEL_ARCH="arm64" ;;
  *)       echo "ERROR: Unsupported arch $ARCH"; exit 1 ;;
esac

echo "==> Installing otelcol-contrib ${OTEL_VERSION} for linux/${OTEL_ARCH}"

# ── Download binary ───────────────────────────────────────────────────────────
if [[ ! -f "$INSTALL_DIR/otelcol-contrib" ]]; then
  TMP=$(mktemp -d)
  trap "rm -rf $TMP" EXIT

  echo "    downloading..."
  curl -fsSL \
    "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${OTEL_VERSION}/otelcol-contrib_${OTEL_VERSION}_linux_${OTEL_ARCH}.tar.gz" \
    -o "$TMP/otelcol.tar.gz"

  tar -xzf "$TMP/otelcol.tar.gz" -C "$TMP"
  install -m 0755 "$TMP/otelcol-contrib" "$INSTALL_DIR/otelcol-contrib"
  echo "    installed → $INSTALL_DIR/otelcol-contrib"
else
  echo "    [skip] otelcol-contrib already installed"
fi

# ── Install config ────────────────────────────────────────────────────────────
mkdir -p "$CONFIG_DIR"
cp "$SCRIPT_DIR/otel-agent-config.yaml" "$CONFIG_DIR/agent-config.yaml"
echo "    config → $CONFIG_DIR/agent-config.yaml"
echo ""
echo "    IMPORTANT: Edit $CONFIG_DIR/agent-config.yaml"
echo "    Set the correct OTEL gateway endpoint:"
echo "      exporters.otlp.endpoint: \"<hydra-svc-01-ip>:4317\""
echo "    If running the monitoring stack on this machine, use: \"localhost:4317\""
echo ""

# ── Install systemd unit ──────────────────────────────────────────────────────
cp "$SCRIPT_DIR/otelcol-agent.service" /etc/systemd/system/otelcol-agent.service
systemctl daemon-reload
systemctl enable otelcol-agent
systemctl restart otelcol-agent
echo "    service enabled and started"
echo ""

# ── Verify ────────────────────────────────────────────────────────────────────
sleep 3
if systemctl is-active --quiet otelcol-agent; then
  echo "==> otelcol-agent is running."
  echo "    Logs:    journalctl -u otelcol-agent -f"
  echo "    Metrics: curl -s http://localhost:8888/metrics | grep otelcol_"
else
  echo "ERROR: otelcol-agent failed to start."
  echo "       journalctl -u otelcol-agent --no-pager -n 30"
  exit 1
fi
