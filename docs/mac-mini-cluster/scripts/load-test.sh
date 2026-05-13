#!/bin/bash
# ============================================================
# Load Test Script — Simulate developer team usage
# Requires: curl, jq, bc
# ============================================================

set -euo pipefail

MAC_MINI_1_IP="${MAC_MINI_1_IP:-192.168.1.10}"
LITELLM_KEY="${LITELLM_KEY:-sk-cluster-demo-key-change-me}"
BASE_URL="http://$MAC_MINI_1_IP:4000/v1/chat/completions"

CONCURRENT="${1:-3}"      # Number of concurrent "developers"
REQUESTS="${2:-10}"       # Requests per developer
MODEL="${3:-code-main}"   # Model to test

echo ""
echo "============================================"
echo "  ML Cluster Load Test"
echo "============================================"
echo "  Gateway:     $BASE_URL"
echo "  Model:       $MODEL"
echo "  Concurrent:  $CONCURRENT simulated developers"
echo "  Requests:    $REQUESTS per developer"
echo "  Total:       $((CONCURRENT * REQUESTS)) requests"
echo "============================================"
echo ""

# Sample prompts simulating developer usage
PROMPTS=(
    "Write a Python function to validate email addresses"
    "Fix this TypeScript: const x: string = 42"
    "Explain what a mutex is and when to use one"
    "Write unit tests for a REST API login endpoint"
    "Refactor this to use async/await instead of callbacks"
    "What's the time complexity of this sorting algorithm?"
    "Generate a Dockerfile for a Node.js app"
    "Write a SQL query to find duplicate records"
    "How do I handle CORS in Express.js?"
    "Write a bash script to backup a PostgreSQL database"
)

RESULTS_DIR=$(mktemp -d)
echo "Results dir: $RESULTS_DIR"

# Worker function
run_worker() {
    local worker_id=$1
    local num_requests=$2
    local results_file="$RESULTS_DIR/worker_${worker_id}.csv"

    echo "timestamp,worker,request,model,status,latency_ms,input_tokens,output_tokens" > "$results_file"

    for i in $(seq 1 "$num_requests"); do
        prompt_idx=$(( (worker_id * num_requests + i) % ${#PROMPTS[@]} ))
        prompt="${PROMPTS[$prompt_idx]}"

        start_time=$(date +%s%N)

        response=$(curl -sf "$BASE_URL" \
            -H "Authorization: Bearer $LITELLM_KEY" \
            -H "Content-Type: application/json" \
            -d "{
                \"model\": \"$MODEL\",
                \"messages\": [{\"role\": \"user\", \"content\": \"$prompt\"}],
                \"max_tokens\": 256,
                \"temperature\": 0.2
            }" 2>/dev/null) || response='{"error": "connection_failed"}'

        end_time=$(date +%s%N)
        latency_ms=$(( (end_time - start_time) / 1000000 ))

        status="ok"
        input_tokens=0
        output_tokens=0

        if echo "$response" | jq -e '.error' > /dev/null 2>&1; then
            status="error"
        else
            input_tokens=$(echo "$response" | jq -r '.usage.prompt_tokens // 0' 2>/dev/null)
            output_tokens=$(echo "$response" | jq -r '.usage.completion_tokens // 0' 2>/dev/null)
        fi

        timestamp=$(date +%s)
        echo "$timestamp,$worker_id,$i,$MODEL,$status,$latency_ms,$input_tokens,$output_tokens" >> "$results_file"

        printf "  [Worker %d] Request %d/%d: %s (%dms, %d tokens out)\n" \
            "$worker_id" "$i" "$num_requests" "$status" "$latency_ms" "$output_tokens"
    done
}

# Launch workers concurrently
echo "Starting $CONCURRENT workers..."
echo ""

for w in $(seq 0 $((CONCURRENT - 1))); do
    run_worker "$w" "$REQUESTS" &
done

# Wait for all workers
wait
echo ""

# --- Aggregate Results ---
echo "============================================"
echo "  Results Summary"
echo "============================================"

# Combine all CSV files
COMBINED="$RESULTS_DIR/combined.csv"
head -1 "$RESULTS_DIR/worker_0.csv" > "$COMBINED"
for f in "$RESULTS_DIR"/worker_*.csv; do
    tail -n +2 "$f" >> "$COMBINED"
done

total=$(tail -n +2 "$COMBINED" | wc -l | tr -d ' ')
errors=$(tail -n +2 "$COMBINED" | grep -c "error" || true)
successes=$((total - errors))

# Calculate latency stats
latencies=$(tail -n +2 "$COMBINED" | grep ",ok," | cut -d',' -f6 | sort -n)
if [ -n "$latencies" ]; then
    count=$(echo "$latencies" | wc -l | tr -d ' ')
    sum=$(echo "$latencies" | paste -sd+ - | bc)
    avg=$((sum / count))
    p50=$(echo "$latencies" | sed -n "$((count / 2))p")
    p95=$(echo "$latencies" | sed -n "$((count * 95 / 100))p")
    p99=$(echo "$latencies" | sed -n "$((count * 99 / 100))p")
    min_lat=$(echo "$latencies" | head -1)
    max_lat=$(echo "$latencies" | tail -1)

    total_tokens=$(tail -n +2 "$COMBINED" | grep ",ok," | cut -d',' -f8 | paste -sd+ - | bc)
    total_input_tokens=$(tail -n +2 "$COMBINED" | grep ",ok," | cut -d',' -f7 | paste -sd+ - | bc)
fi

echo ""
echo "  Requests:     $total total, $successes ok, $errors errors"
echo "  Error rate:   $(echo "scale=1; $errors * 100 / $total" | bc)%"
echo ""

if [ -n "${latencies:-}" ]; then
    echo "  Latency:"
    echo "    Min:  ${min_lat}ms"
    echo "    Avg:  ${avg}ms"
    echo "    P50:  ${p50}ms"
    echo "    P95:  ${p95}ms"
    echo "    P99:  ${p99}ms"
    echo "    Max:  ${max_lat}ms"
    echo ""
    echo "  Tokens:"
    echo "    Total input:  $total_input_tokens"
    echo "    Total output: $total_tokens"
    echo "    Avg output/req: $((total_tokens / successes))"
fi

echo ""
echo "  Raw data: $COMBINED"
echo "============================================"

# Throughput
if [ -n "${latencies:-}" ]; then
    first_ts=$(tail -n +2 "$COMBINED" | head -1 | cut -d',' -f1)
    last_ts=$(tail -n +2 "$COMBINED" | tail -1 | cut -d',' -f1)
    duration=$((last_ts - first_ts + 1))
    throughput=$(echo "scale=2; $successes / $duration" | bc)
    echo ""
    echo "  Throughput: ${throughput} req/s over ${duration}s"
fi
