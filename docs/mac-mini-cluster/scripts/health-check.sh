#!/bin/bash
# ============================================================
# Health Check Script — Run from any machine to verify cluster
# ============================================================

set -euo pipefail

MAC_MINI_1_IP="${MAC_MINI_1_IP:-192.168.1.10}"
MAC_MINI_2_IP="${MAC_MINI_2_IP:-192.168.1.11}"
LITELLM_KEY="${LITELLM_KEY:-sk-cluster-demo-key-change-me}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass=0
fail=0
warn=0

check() {
    local name="$1"
    local cmd="$2"
    local expected="${3:-}"

    printf "  %-40s" "$name"
    result=$(eval "$cmd" 2>/dev/null) || result="FAIL"

    if [ -n "$expected" ]; then
        if echo "$result" | grep -q "$expected"; then
            echo -e "${GREEN}PASS${NC}"
            ((pass++))
        else
            echo -e "${RED}FAIL${NC} (got: ${result:0:50})"
            ((fail++))
        fi
    elif [ "$result" != "FAIL" ] && [ -n "$result" ]; then
        echo -e "${GREEN}PASS${NC}"
        ((pass++))
    else
        echo -e "${RED}FAIL${NC}"
        ((fail++))
    fi
}

check_warn() {
    local name="$1"
    local cmd="$2"

    printf "  %-40s" "$name"
    result=$(eval "$cmd" 2>/dev/null) || result="FAIL"

    if [ "$result" != "FAIL" ] && [ -n "$result" ]; then
        echo -e "${GREEN}OK${NC} ($result)"
    else
        echo -e "${YELLOW}WARN${NC}"
        ((warn++))
    fi
}

echo ""
echo "============================================"
echo "  ML Cluster Health Check"
echo "============================================"
echo ""

# --- Connectivity ---
echo "Network Connectivity:"
check "Mac Mini 1 (M1) reachable" \
    "ping -c 1 -W 2 $MAC_MINI_1_IP | grep -c '1 received'" "1"
check "Mac Mini 2 (M3) reachable" \
    "ping -c 1 -W 2 $MAC_MINI_2_IP | grep -c '1 received'" "1"
echo ""

# --- Ollama ---
echo "Ollama Instances:"
check "Ollama M1 responding" \
    "curl -sf http://$MAC_MINI_1_IP:11434/api/tags | jq -r '.models | length'" ""
check "Ollama M3 responding" \
    "curl -sf http://$MAC_MINI_2_IP:11434/api/tags | jq -r '.models | length'" ""

echo ""
echo "  Models loaded:"
echo "  Mac Mini 1 (M1):"
curl -sf "http://$MAC_MINI_1_IP:11434/api/tags" 2>/dev/null | \
    jq -r '.models[] | "    - \(.name) (\(.size / 1024 / 1024 | floor)MB)"' 2>/dev/null || \
    echo "    (unreachable)"
echo "  Mac Mini 2 (M3):"
curl -sf "http://$MAC_MINI_2_IP:11434/api/tags" 2>/dev/null | \
    jq -r '.models[] | "    - \(.name) (\(.size / 1024 / 1024 | floor)MB)"' 2>/dev/null || \
    echo "    (unreachable)"
echo ""

# --- LiteLLM Proxy ---
echo "LiteLLM Proxy:"
check "LiteLLM health endpoint" \
    "curl -sf http://$MAC_MINI_1_IP:4000/health | jq -r '.status'" "healthy"
check "LiteLLM model list" \
    "curl -sf http://$MAC_MINI_1_IP:4000/v1/models -H 'Authorization: Bearer $LITELLM_KEY' | jq -r '.data | length'" ""
echo ""

# --- Monitoring Stack ---
echo "Monitoring Stack:"
check "Prometheus up" \
    "curl -sf http://$MAC_MINI_1_IP:9090/-/ready" "Prometheus Server is Ready"
check "Grafana up" \
    "curl -sf http://$MAC_MINI_1_IP:3000/api/health | jq -r '.database'" "ok"
check "Loki up" \
    "curl -sf http://$MAC_MINI_1_IP:3100/ready" "ready"
echo ""

# --- Inference Test ---
echo "Inference Tests:"
printf "  %-40s" "Chat completion (code-main)"
INFERENCE_START=$(date +%s%N)
RESPONSE=$(curl -sf "http://$MAC_MINI_1_IP:4000/v1/chat/completions" \
    -H "Authorization: Bearer $LITELLM_KEY" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "code-main",
        "messages": [{"role": "user", "content": "Write a Python hello world in one line"}],
        "max_tokens": 50,
        "temperature": 0.1
    }' 2>/dev/null)
INFERENCE_END=$(date +%s%N)

if echo "$RESPONSE" | jq -r '.choices[0].message.content' 2>/dev/null | grep -qi "print\|hello"; then
    LATENCY_MS=$(( (INFERENCE_END - INFERENCE_START) / 1000000 ))
    echo -e "${GREEN}PASS${NC} (${LATENCY_MS}ms)"
    ((pass++))
else
    echo -e "${RED}FAIL${NC}"
    echo "    Response: $(echo "$RESPONSE" | head -c 200)"
    ((fail++))
fi

printf "  %-40s" "Chat completion (code-autocomplete)"
RESPONSE=$(curl -sf "http://$MAC_MINI_1_IP:4000/v1/chat/completions" \
    -H "Authorization: Bearer $LITELLM_KEY" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "code-autocomplete",
        "messages": [{"role": "user", "content": "def fibonacci(n):"}],
        "max_tokens": 100,
        "temperature": 0.05
    }' 2>/dev/null)

if echo "$RESPONSE" | jq -r '.choices[0].message.content' 2>/dev/null | grep -qi "return\|fib\|def"; then
    echo -e "${GREEN}PASS${NC}"
    ((pass++))
else
    echo -e "${RED}FAIL${NC}"
    ((fail++))
fi
echo ""

# --- Resource Usage ---
echo "Resource Estimates:"
check_warn "M1 Ollama memory (target: <5GB)" \
    "curl -sf http://$MAC_MINI_1_IP:11434/api/ps | jq -r '[.models[].size] | add / 1024 / 1024 / 1024 | floor | tostring + \"GB\"'"
check_warn "M3 Ollama memory (target: <12GB)" \
    "curl -sf http://$MAC_MINI_2_IP:11434/api/ps | jq -r '[.models[].size] | add / 1024 / 1024 / 1024 | floor | tostring + \"GB\"'"
echo ""

# --- Summary ---
echo "============================================"
echo -e "  Results: ${GREEN}${pass} passed${NC}, ${RED}${fail} failed${NC}, ${YELLOW}${warn} warnings${NC}"
echo "============================================"

if [ $fail -gt 0 ]; then
    echo ""
    echo "Troubleshooting tips:"
    echo "  - Ensure Ollama is running: ollama serve"
    echo "  - Check Docker: docker ps"
    echo "  - View LiteLLM logs: tail -f /tmp/litellm.log"
    echo "  - Check firewall: both machines need ports 11434, 4000, 3000, 9090, 3100 open"
    exit 1
fi
