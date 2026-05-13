#!/bin/bash
set -euo pipefail

# ============================================================
# Common setup for all Mac Minis in the cluster
# Run this first on every node
# ============================================================

echo "=== Mac Mini ML Cluster - Common Setup ==="

# --- Configuration ---
# EDIT THESE before running
export MAC_MINI_1_IP="${MAC_MINI_1_IP:-192.168.1.10}"   # M1 - Gateway node
export MAC_MINI_2_IP="${MAC_MINI_2_IP:-192.168.1.11}"   # M3 - Inference node
export CLUSTER_DOMAIN="${CLUSTER_DOMAIN:-ml-cluster.local}"

# --- Install Homebrew if missing ---
if ! command -v brew &> /dev/null; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# --- Install Ollama ---
if ! command -v ollama &> /dev/null; then
    echo "Installing Ollama..."
    brew install ollama
fi

# --- Install Docker (via Colima - lighter than Docker Desktop) ---
if ! command -v docker &> /dev/null; then
    echo "Installing Docker via Colima (lightweight alternative to Docker Desktop)..."
    brew install docker docker-compose colima
fi

# --- Install Python 3.11+ ---
if ! command -v python3 &> /dev/null; then
    brew install python@3.11
fi

# --- Install monitoring utilities ---
brew install curl jq htop

# --- Configure Ollama to listen on all interfaces ---
# Create launchd override for Ollama
OLLAMA_PLIST="$HOME/Library/LaunchAgents/com.ollama.server.plist"
if [ ! -f "$OLLAMA_PLIST" ]; then
    cat > "$OLLAMA_PLIST" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ollama.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/ollama</string>
        <string>serve</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>OLLAMA_HOST</key>
        <string>0.0.0.0</string>
        <key>OLLAMA_KEEP_ALIVE</key>
        <string>24h</string>
        <key>OLLAMA_MAX_LOADED_MODELS</key>
        <string>2</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/ollama.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/ollama-error.log</string>
</dict>
</plist>
PLIST
    echo "Ollama launchd plist created."
fi

# --- Network check ---
echo ""
echo "=== Network Connectivity Check ==="
echo "This node's IP addresses:"
ifconfig | grep "inet " | grep -v 127.0.0.1 | awk '{print "  " $2}'
echo ""
echo "Ensure both Mac Minis can reach each other:"
echo "  Mac Mini 1 (M1 Gateway): $MAC_MINI_1_IP"
echo "  Mac Mini 2 (M3 Inference): $MAC_MINI_2_IP"

echo ""
echo "=== Common setup complete ==="
echo "Now run the node-specific setup script for this machine."
