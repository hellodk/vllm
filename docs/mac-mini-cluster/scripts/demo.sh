#!/bin/bash
# ============================================================
# End-to-End Demo Script
# Run this to demonstrate the full cluster to your team
# ============================================================

set -euo pipefail

MAC_MINI_1_IP="${MAC_MINI_1_IP:-192.168.1.10}"
LITELLM_KEY="${LITELLM_KEY:-sk-cluster-demo-key-change-me}"
BASE_URL="http://$MAC_MINI_1_IP:4000/v1"

BOLD='\033[1m'
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

section() {
    echo ""
    echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${CYAN}  $1${NC}"
    echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

pause() {
    echo ""
    echo -e "${YELLOW}  Press Enter to continue...${NC}"
    read -r
}

# ============================================================
section "1. CLUSTER OVERVIEW"
# ============================================================

echo "Your ML Cluster:"
echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  Mac Mini 1 (M1, 8GB)                       │"
echo "  │  Role: Gateway + Monitoring                  │"
echo "  │  Models: phi3:mini, qwen2.5-coder:1.5b      │"
echo "  │  Services: LiteLLM, Prometheus, Grafana,     │"
echo "  │            Loki, Nginx                       │"
echo "  └──────────────────┬──────────────────────────┘"
echo "                     │ HTTP (port 4000)"
echo "  ┌──────────────────┴──────────────────────────┐"
echo "  │  Mac Mini 2 (M3, 16GB)                      │"
echo "  │  Role: Primary Inference                     │"
echo "  │  Models: qwen2.5-coder:7b, qwen2.5-coder:3b │"
echo "  └─────────────────────────────────────────────┘"
echo ""
echo "  Endpoints:"
echo "    API:        http://$MAC_MINI_1_IP:4000"
echo "    Grafana:    http://$MAC_MINI_1_IP:3000"
echo "    Prometheus: http://$MAC_MINI_1_IP:9090"

pause

# ============================================================
section "2. CODE COMPLETION (Primary Model — Qwen 2.5 7B)"
# ============================================================

echo "Prompt: 'Write a Python class for a thread-safe LRU cache'"
echo ""

RESPONSE=$(curl -sf "$BASE_URL/chat/completions" \
    -H "Authorization: Bearer $LITELLM_KEY" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "code-main",
        "messages": [
            {"role": "system", "content": "You are a precise coding assistant. Write clean, production-ready code."},
            {"role": "user", "content": "Write a Python class for a thread-safe LRU cache with get, put, and size methods. Include type hints."}
        ],
        "max_tokens": 512,
        "temperature": 0.2
    }')

echo "$RESPONSE" | jq -r '.choices[0].message.content' 2>/dev/null || echo "(Error: $RESPONSE)"
echo ""
echo -e "${GREEN}Tokens used:${NC}"
echo "$RESPONSE" | jq '.usage' 2>/dev/null

pause

# ============================================================
section "3. FAST AUTOCOMPLETE (Phi-3 Mini on M1)"
# ============================================================

echo "Simulating tab autocomplete: 'def binary_search(arr, target):'"
echo ""

RESPONSE=$(curl -sf "$BASE_URL/chat/completions" \
    -H "Authorization: Bearer $LITELLM_KEY" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "code-autocomplete",
        "messages": [
            {"role": "user", "content": "Complete this function:\ndef binary_search(arr, target):"}
        ],
        "max_tokens": 128,
        "temperature": 0.05
    }')

echo "$RESPONSE" | jq -r '.choices[0].message.content' 2>/dev/null || echo "(Error: $RESPONSE)"
echo ""
echo -e "${GREEN}Tokens used:${NC}"
echo "$RESPONSE" | jq '.usage' 2>/dev/null

pause

# ============================================================
section "4. GUARDRAILS DEMO — Secret Detection"
# ============================================================

echo "Sending a prompt that contains an API key..."
echo "The guardrail should flag this in the logs."
echo ""

RESPONSE=$(curl -sf "$BASE_URL/chat/completions" \
    -H "Authorization: Bearer $LITELLM_KEY" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "code-main",
        "messages": [
            {"role": "user", "content": "Fix this config: api_key=\"sk-proj-abcdefghijklmnop123456789\" and make it work with the new API"}
        ],
        "max_tokens": 256,
        "temperature": 0.2
    }')

echo "$RESPONSE" | jq -r '.choices[0].message.content' 2>/dev/null || echo "(Error)"
echo ""
echo -e "${YELLOW}Check Grafana > Guardrails panel to see the flagged request${NC}"
echo -e "${YELLOW}Check logs: grep 'secrets' /tmp/litellm.log${NC}"

pause

# ============================================================
section "5. CONTEXT WINDOW MANAGEMENT"
# ============================================================

echo "Sending a request that approaches the context limit..."
echo ""

# Generate a large context
LARGE_CONTEXT=$(python3 -c "print('# ' + 'This is line {} of a large file. '.format(i) * 3 for i in range(200))" 2>/dev/null || \
    printf 'x%.0s' {1..8000})

RESPONSE=$(curl -sf "$BASE_URL/chat/completions" \
    -H "Authorization: Bearer $LITELLM_KEY" \
    -H "Content-Type: application/json" \
    -d "{
        \"model\": \"code-main\",
        \"messages\": [
            {\"role\": \"user\", \"content\": \"Summarize this code:\\n$LARGE_CONTEXT\"}
        ],
        \"max_tokens\": 256,
        \"temperature\": 0.2
    }" 2>/dev/null) || RESPONSE='{"info":"Context was likely truncated by guardrails"}'

echo "Response received (context may have been truncated by guardrails)."
echo "$RESPONSE" | jq '.usage' 2>/dev/null || echo "$RESPONSE"
echo ""
echo -e "${YELLOW}Check Grafana > Context Window Usage panel${NC}"

pause

# ============================================================
section "6. MONITORING DASHBOARDS"
# ============================================================

echo "Open these URLs in your browser:"
echo ""
echo "  Grafana Dashboard:"
echo "    http://$MAC_MINI_1_IP:3000/d/ml-cluster-overview"
echo "    Login: admin / admin"
echo ""
echo "  You'll see:"
echo "    - Cluster health (both nodes UP/DOWN)"
echo "    - Request latency (P50/P95/P99)"
echo "    - Token throughput (input/output per minute)"
echo "    - Context window usage gauges"
echo "    - Guardrail blocks and flags"
echo "    - Live logs from all services"
echo ""
echo "  Prometheus (raw metrics):"
echo "    http://$MAC_MINI_1_IP:9090/targets"

pause

# ============================================================
section "7. CONTINUE.DEV SETUP FOR DEVELOPERS"
# ============================================================

echo "Each developer needs to:"
echo ""
echo "  1. Install Continue.dev extension in VS Code"
echo ""
echo "  2. Copy the config file:"
echo "     cp continue-dev/config.yaml ~/.continue/config.yaml"
echo ""
echo "  3. Replace GATEWAY_IP with: $MAC_MINI_1_IP"
echo ""
echo "  4. Pull the embedding model (for codebase search):"
echo "     (This runs on the cluster, not locally)"
echo ""
echo "  5. Open VS Code, press Cmd+L for chat, Tab for autocomplete"
echo ""
echo "  Features available:"
echo "    - Chat with code context (Cmd+L)"
echo "    - Tab autocomplete"
echo "    - /review — code review"
echo "    - /test — generate tests"
echo "    - /explain — explain code"
echo "    - /optimize — performance suggestions"
echo "    - @codebase — search entire project"

echo ""
echo -e "${BOLD}${GREEN}============================================${NC}"
echo -e "${BOLD}${GREEN}  Demo Complete!${NC}"
echo -e "${BOLD}${GREEN}============================================${NC}"
echo ""
