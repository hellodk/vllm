#!/bin/bash
# Test script for Apple Silicon LLM Monitoring Stack
# Verifies all components are working correctly

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================"
echo "Apple Silicon LLM Monitoring Test Suite"
echo "========================================"
echo ""

# Function to check endpoint
check_endpoint() {
    local name="$1"
    local url="$2"
    local expected="$3"
    
    if curl -sf "$url" > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} $name is accessible at $url"
        return 0
    else
        echo -e "${RED}✗${NC} $name is NOT accessible at $url"
        return 1
    fi
}

# Function to check metrics
check_metrics() {
    local name="$1"
    local url="$2"
    local pattern="$3"
    
    if curl -sf "$url" 2>/dev/null | grep -q "$pattern"; then
        echo -e "${GREEN}✓${NC} $name metrics contain '$pattern'"
        return 0
    else
        echo -e "${RED}✗${NC} $name metrics do NOT contain '$pattern'"
        return 1
    fi
}

echo "1. Checking Service Health Endpoints..."
echo "----------------------------------------"
check_endpoint "Mock Exporter" "http://localhost:5900/health"
check_endpoint "Mock LLM Server" "http://localhost:5901/health"
check_endpoint "OTEL Agent" "http://localhost:5914"
check_endpoint "OTEL Gateway" "http://localhost:5923"
check_endpoint "Prometheus" "http://localhost:5930/-/healthy"
check_endpoint "Grafana" "http://localhost:5940/api/health"
check_endpoint "Alertmanager" "http://localhost:5950/-/healthy"

echo ""
echo "2. Checking Metrics Endpoints..."
echo "---------------------------------"
check_metrics "Mock Exporter" "http://localhost:5900/metrics" "apple_gpu_utilization_percent"
check_metrics "OTEL Agent (Apple metrics)" "http://localhost:5913/metrics" "apple_gpu"
check_metrics "OTEL Agent (LLM metrics)" "http://localhost:5913/metrics" "llm_inference"
check_metrics "OTEL Gateway" "http://localhost:5922/metrics" "llm_"

echo ""
echo "3. Checking Prometheus Data..."
echo "-------------------------------"
# Check GPU metrics
gpu_result=$(curl -sf "http://localhost:5930/api/v1/query?query=apple_gpu_utilization_percent" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data['data']['result']))" 2>/dev/null || echo "0")
if [ "$gpu_result" -gt 0 ]; then
    echo -e "${GREEN}✓${NC} GPU metrics found in Prometheus ($gpu_result series)"
else
    echo -e "${RED}✗${NC} GPU metrics NOT found in Prometheus"
fi

# Check LLM metrics
llm_result=$(curl -sf "http://localhost:5930/api/v1/query?query=llm_inference_requests_total" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data['data']['result']))" 2>/dev/null || echo "0")
if [ "$llm_result" -gt 0 ]; then
    echo -e "${GREEN}✓${NC} LLM metrics found in Prometheus ($llm_result series)"
else
    echo -e "${RED}✗${NC} LLM metrics NOT found in Prometheus"
fi

# Check hallucination metrics
hall_result=$(curl -sf "http://localhost:5930/api/v1/query?query=llm_confidence_mean" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data['data']['result']))" 2>/dev/null || echo "0")
if [ "$hall_result" -gt 0 ]; then
    echo -e "${GREEN}✓${NC} Hallucination metrics found in Prometheus ($hall_result series)"
else
    echo -e "${RED}✗${NC} Hallucination metrics NOT found in Prometheus"
fi

echo ""
echo "4. Checking Prometheus Targets..."
echo "----------------------------------"
targets=$(curl -sf "http://localhost:5930/api/v1/targets" | python3 -c "import sys, json; data=json.load(sys.stdin); up=[t for t in data['data']['activeTargets'] if t['health']=='up']; print(f'{len(up)} targets up')" 2>/dev/null || echo "unknown")
echo -e "${GREEN}✓${NC} $targets"

echo ""
echo "========================================"
echo "Test Complete!"
echo "========================================"
echo ""
echo "Access points:"
echo "  - Grafana:      http://localhost:5940 (admin/admin)"
echo "  - Prometheus:   http://localhost:5930"
echo "  - Alertmanager: http://localhost:5950"
echo ""
echo "Sample PromQL queries:"
echo "  - apple_gpu_utilization_percent"
echo "  - llm_inference_duration_seconds_bucket"
echo "  - llm_confidence_mean"
echo "  - rate(llm_inference_requests_total[5m])"
