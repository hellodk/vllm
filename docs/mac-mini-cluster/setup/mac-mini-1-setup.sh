#!/bin/bash
set -euo pipefail

# ============================================================
# Mac Mini 1 Setup — M1, 8 Cores, 8GB RAM, 10 GPU cores
# Role: API Gateway + Monitoring + Light Inference
# ============================================================

echo "=== Mac Mini 1 (M1) — Gateway + Monitoring Node ==="

# --- Configuration ---
export MAC_MINI_1_IP="${MAC_MINI_1_IP:-192.168.1.10}"
export MAC_MINI_2_IP="${MAC_MINI_2_IP:-192.168.1.11}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# --- Run common setup first ---
source "$SCRIPT_DIR/common-setup.sh"

# --- Start Colima with constrained resources (leave room for monitoring) ---
echo ""
echo "=== Starting Colima (Docker runtime) ==="
colima stop 2>/dev/null || true
# Allocate 3GB to Docker, keep 5GB for Ollama + macOS
colima start --cpu 4 --memory 3 --disk 30 --arch aarch64

# --- Pull lightweight model for autocomplete ---
echo ""
echo "=== Pulling models for M1 (8GB RAM) ==="
echo "Starting Ollama..."
launchctl load "$HOME/Library/LaunchAgents/com.ollama.server.plist" 2>/dev/null || true
sleep 5

# Phi-3 Mini is excellent for autocomplete and fits in 8GB alongside monitoring
ollama pull phi3:mini
# Tiny model for health checks and fast responses
ollama pull qwen2.5-coder:1.5b

# --- Install LiteLLM ---
echo ""
echo "=== Installing LiteLLM Proxy ==="
pip3 install 'litellm[proxy]' --break-system-packages 2>/dev/null || pip3 install 'litellm[proxy]'

# --- Install guardrails dependencies ---
echo ""
echo "=== Installing Guardrails Dependencies ==="
pip3 install tiktoken pydantic --break-system-packages 2>/dev/null || pip3 install tiktoken pydantic

# --- Start Docker monitoring stack ---
echo ""
echo "=== Starting Monitoring Stack ==="
cd "$PROJECT_DIR/gateway"

# Replace IP placeholders in configs
export MAC_MINI_1_IP MAC_MINI_2_IP
envsubst < "$PROJECT_DIR/gateway/prometheus/prometheus.yml.template" > "$PROJECT_DIR/gateway/prometheus/prometheus.yml" 2>/dev/null || true

docker-compose up -d

echo ""
echo "=== Starting LiteLLM Proxy ==="
# Update LiteLLM config with actual IPs
sed "s/MAC_MINI_1_IP/$MAC_MINI_1_IP/g; s/MAC_MINI_2_IP/$MAC_MINI_2_IP/g" \
    "$PROJECT_DIR/gateway/litellm-config.yaml" > /tmp/litellm-config.yaml

# Start LiteLLM in background
nohup litellm --config /tmp/litellm-config.yaml \
    --port 4000 \
    --detailed_debug \
    --num_workers 4 \
    > /tmp/litellm.log 2>&1 &

echo "LiteLLM PID: $!"

# --- Print access info ---
echo ""
echo "============================================"
echo "  Mac Mini 1 (M1) Setup Complete!"
echo "============================================"
echo ""
echo "Services running:"
echo "  Ollama:      http://$MAC_MINI_1_IP:11434"
echo "  LiteLLM:     http://$MAC_MINI_1_IP:4000"
echo "  Grafana:     http://$MAC_MINI_1_IP:3000  (admin/admin)"
echo "  Prometheus:  http://$MAC_MINI_1_IP:9090"
echo "  Loki:        http://$MAC_MINI_1_IP:3100"
echo ""
echo "Models loaded:"
echo "  phi3:mini       — autocomplete (fast)"
echo "  qwen2.5-coder:1.5b — health checks"
echo ""
echo "Next: Run setup on Mac Mini 2 (M3)"
echo "============================================"
