#!/bin/bash
set -euo pipefail

# ============================================================
# Mac Mini 2 Setup — M3, 10 Cores, 16GB RAM, 10 GPU cores
# Role: Primary Inference Node
# ============================================================

echo "=== Mac Mini 2 (M3) — Primary Inference Node ==="

# --- Configuration ---
export MAC_MINI_1_IP="${MAC_MINI_1_IP:-192.168.1.10}"
export MAC_MINI_2_IP="${MAC_MINI_2_IP:-192.168.1.11}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# --- Run common setup first ---
source "$SCRIPT_DIR/common-setup.sh"

# --- Start Colima (minimal - just for Promtail) ---
echo ""
echo "=== Starting Colima (Docker runtime) ==="
colima stop 2>/dev/null || true
# Minimal Docker allocation - most RAM for Ollama
colima start --cpu 2 --memory 2 --disk 10 --arch aarch64

# --- Pull models for M3 (16GB RAM) ---
echo ""
echo "=== Pulling models for M3 (16GB RAM) ==="
echo "Starting Ollama..."
launchctl load "$HOME/Library/LaunchAgents/com.ollama.server.plist" 2>/dev/null || true
sleep 5

# Primary code model - excellent quality, fits in 16GB
ollama pull qwen2.5-coder:7b
# Backup smaller model for high-load fallback
ollama pull qwen2.5-coder:3b

# --- Start Promtail to ship logs to Loki on Mac Mini 1 ---
echo ""
echo "=== Starting Promtail (log shipper) ==="
cd "$PROJECT_DIR/gateway"

# Create promtail config with actual IPs
sed "s/MAC_MINI_1_IP/$MAC_MINI_1_IP/g" \
    "$PROJECT_DIR/gateway/promtail/promtail-config.yml" > /tmp/promtail-config.yml

docker-compose -f docker-compose-node.yml up -d

# --- Verify Ollama is accessible ---
echo ""
echo "=== Verifying Ollama ==="
sleep 3
if curl -s "http://localhost:11434/api/tags" | jq -r '.models[].name' 2>/dev/null; then
    echo "Ollama is running with the above models."
else
    echo "WARNING: Ollama may not be running. Check: ollama serve"
fi

# --- Print access info ---
echo ""
echo "============================================"
echo "  Mac Mini 2 (M3) Setup Complete!"
echo "============================================"
echo ""
echo "Services running:"
echo "  Ollama:    http://$MAC_MINI_2_IP:11434"
echo "  Promtail:  shipping logs to http://$MAC_MINI_1_IP:3100"
echo ""
echo "Models loaded:"
echo "  qwen2.5-coder:7b  — primary code model"
echo "  qwen2.5-coder:3b  — fallback model"
echo ""
echo "Verify from Mac Mini 1:"
echo "  curl http://$MAC_MINI_2_IP:11434/api/tags"
echo "============================================"
