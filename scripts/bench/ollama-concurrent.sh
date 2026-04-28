#!/usr/bin/env bash
# ollama-concurrent.sh — Concurrent load test for Ollama using plain curl
#
# Fires parallel curl requests against the Ollama (or OpenAI-compatible)
# /chat/completions endpoint. Measures wall-clock time per request and
# reports mean latency, p95 latency, requests/sec, and error rate.
#
# No Python, no k6 — just bash, curl, awk, and sort.
#
# Usage:
#   ./scripts/bench/ollama-concurrent.sh [options]
#
# Options:
#   --vus       Number of virtual users (parallel workers), default: 10
#   --duration  Test duration in seconds, default: 120
#   --model     Ollama model name or tag, default: llama3:8b
#   --url       Base URL of Ollama or LiteLLM gateway,
#               default: http://localhost:11434/v1
#   --max-tokens  Max tokens to generate per request, default: 64
#   --prompt    User message to send, default: a short autocomplete prompt
#   --api-key   Bearer token (pass empty string if not needed), default: ""
#   --output    File path to append result row (CSV), default: none
#   --help      Show this message
#
# Example:
#   # Quick 30-second test, 5 workers, against Ollama
#   ./scripts/bench/ollama-concurrent.sh --vus 5 --duration 30 --model qwen2.5:7b
#
#   # Longer test against LiteLLM gateway
#   ./scripts/bench/ollama-concurrent.sh \
#     --vus 20 --duration 120 \
#     --model llama3:8b \
#     --url http://10.0.0.5:4000/v1 \
#     --api-key my-litellm-key
#
# Output example:
#   [RESULT] vus=10 duration=120s model=llama3:8b
#   [RESULT] requests=347 errors=2 error_rate=0.58%
#   [RESULT] mean_latency=3421ms p95_latency=6102ms
#   [RESULT] requests_per_sec=2.89

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
VUS=10
DURATION=120
MODEL="llama3:8b"
BASE_URL="http://localhost:11434/v1"
MAX_TOKENS=64
API_KEY=""
OUTPUT_FILE=""
DRY_RUN=false

DEFAULT_PROMPT="Explain in one sentence what a transformer neural network is."

USER_PROMPT="${DEFAULT_PROMPT}"

# Temp directory for per-request timing files
TMPDIR_BASE="$(mktemp -d /tmp/ollama-bench-XXXXXX)"
trap 'rm -rf "${TMPDIR_BASE}"' EXIT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
usage() {
    grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,1\}//'
    exit 0
}

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
info() { echo "[INFO]  $*"; }
warn() { echo "[WARN]  $*" >&2; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --vus)        VUS="$2";         shift 2 ;;
        --duration)   DURATION="$2";    shift 2 ;;
        --model)      MODEL="$2";       shift 2 ;;
        --url)        BASE_URL="$2";    shift 2 ;;
        --max-tokens) MAX_TOKENS="$2";  shift 2 ;;
        --prompt)     USER_PROMPT="$2"; shift 2 ;;
        --api-key)    API_KEY="$2";     shift 2 ;;
        --output)     OUTPUT_FILE="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=true;     shift   ;;
        --help|-h)    usage ;;
        *) die "Unknown argument: $1. Run with --help for usage." ;;
    esac
done

# Validate numeric args
[[ "${VUS}" =~ ^[0-9]+$ ]]      || die "--vus must be a positive integer"
[[ "${DURATION}" =~ ^[0-9]+$ ]] || die "--duration must be a positive integer (seconds)"
[[ "${MAX_TOKENS}" =~ ^[0-9]+$ ]] || die "--max-tokens must be a positive integer"

# ---------------------------------------------------------------------------
# Verify dependencies
# ---------------------------------------------------------------------------
for dep in curl awk sort; do
    command -v "${dep}" &>/dev/null || die "Required tool not found: ${dep}"
done

# ---------------------------------------------------------------------------
# Build request payload
# ---------------------------------------------------------------------------
# Escape USER_PROMPT for JSON embedding
ESCAPED_PROMPT="$(printf '%s' "${USER_PROMPT}" | \
    sed 's/\\/\\\\/g; s/"/\\"/g; s/	/\\t/g' | \
    tr -d '\n')"

PAYLOAD=$(cat <<EOF
{
  "model": "${MODEL}",
  "stream": false,
  "max_tokens": ${MAX_TOKENS},
  "temperature": 0.2,
  "messages": [
    {"role": "system", "content": "You are a helpful, concise assistant."},
    {"role": "user",   "content": "${ESCAPED_PROMPT}"}
  ]
}
EOF
)

ENDPOINT="${BASE_URL}/chat/completions"

# Build curl auth header
if [[ -n "${API_KEY}" ]]; then
    AUTH_HEADER="Authorization: Bearer ${API_KEY}"
else
    AUTH_HEADER="X-Hydra-Bench: 1"  # dummy header when no auth required
fi

# ---------------------------------------------------------------------------
# Worker function — runs in a subshell for each VU
# ---------------------------------------------------------------------------
worker() {
    local worker_id="$1"
    local end_time="$2"
    local results_dir="$3"
    local req_num=0

    while [[ "$(date +%s)" -lt "${end_time}" ]]; do
        req_num=$((req_num + 1))
        local result_file="${results_dir}/w${worker_id}_r${req_num}"

        local t_start
        t_start=$(date +%s%3N)  # milliseconds

        local http_code
        http_code=$(
            curl -s -o /dev/null -w "%{http_code}" \
                --connect-timeout 10 \
                --max-time 120 \
                -X POST "${ENDPOINT}" \
                -H "Content-Type: application/json" \
                -H "${AUTH_HEADER}" \
                -d "${PAYLOAD}" \
                2>/dev/null
        ) || http_code="000"

        local t_end
        t_end=$(date +%s%3N)
        local elapsed_ms=$(( t_end - t_start ))

        # Write: elapsed_ms http_code
        echo "${elapsed_ms} ${http_code}" > "${result_file}"
    done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    if "${DRY_RUN}"; then
        info "DRY RUN — would send to: ${ENDPOINT}"
        info "Model: ${MODEL}  VUs: ${VUS}  Duration: ${DURATION}s"
        info "Payload:"
        printf '%s\n' "${PAYLOAD}"
        return
    fi

    log "Starting Ollama concurrent benchmark"
    log "  Endpoint  : ${ENDPOINT}"
    log "  Model     : ${MODEL}"
    log "  VUs       : ${VUS}"
    log "  Duration  : ${DURATION}s"
    log "  Max tokens: ${MAX_TOKENS}"

    # Sanity-check: test one request before spawning all workers
    log "Sending warm-up request..."
    local warmup_code
    warmup_code=$(
        curl -s -o /dev/null -w "%{http_code}" \
            --connect-timeout 10 \
            --max-time 60 \
            -X POST "${ENDPOINT}" \
            -H "Content-Type: application/json" \
            -H "${AUTH_HEADER}" \
            -d "${PAYLOAD}" \
            2>/dev/null
    ) || warmup_code="000"

    if [[ "${warmup_code}" != "200" ]]; then
        warn "Warm-up request returned HTTP ${warmup_code} — proceeding anyway"
    else
        log "Warm-up OK (HTTP 200)"
    fi

    local results_dir="${TMPDIR_BASE}/results"
    mkdir -p "${results_dir}"

    local start_time
    start_time=$(date +%s)
    local end_time=$(( start_time + DURATION ))

    log "Spawning ${VUS} workers..."
    local pids=()
    for ((i=1; i<=VUS; i++)); do
        worker "${i}" "${end_time}" "${results_dir}" &
        pids+=($!)
    done

    # Progress indicator
    local elapsed=0
    while [[ "${elapsed}" -lt "${DURATION}" ]]; do
        sleep 5
        elapsed=$(( $(date +%s) - start_time ))
        local done_files
        done_files=$(find "${results_dir}" -type f | wc -l)
        printf "\r[%3ds / %ds]  requests so far: %d    " \
            "${elapsed}" "${DURATION}" "${done_files}"
    done
    printf "\n"

    # Wait for all workers to finish
    log "Test duration elapsed — waiting for workers to finish..."
    for pid in "${pids[@]}"; do
        wait "${pid}" 2>/dev/null || true
    done

    # ---------------------------------------------------------------------------
    # Aggregate results
    # ---------------------------------------------------------------------------
    local all_files
    all_files=$(find "${results_dir}" -type f | sort)

    if [[ -z "${all_files}" ]]; then
        die "No result files found — all requests may have failed to even start"
    fi

    local total=0 errors=0
    local latencies_file="${TMPDIR_BASE}/latencies.txt"
    : > "${latencies_file}"

    while IFS= read -r f; do
        if [[ ! -s "${f}" ]]; then continue; fi
        read -r elapsed_ms http_code < "${f}" || continue
        total=$(( total + 1 ))
        if [[ "${http_code}" != "200" ]]; then
            errors=$(( errors + 1 ))
        fi
        echo "${elapsed_ms}" >> "${latencies_file}"
    done <<< "${all_files}"

    if [[ "${total}" -eq 0 ]]; then
        die "Zero completed requests — check that the server is reachable at ${ENDPOINT}"
    fi

    local actual_duration
    actual_duration=$(( $(date +%s) - start_time ))

    # Compute stats via awk
    local stats
    stats=$(sort -n "${latencies_file}" | awk -v total="${total}" '
    BEGIN { sum=0; count=0 }
    {
        vals[count++] = $1
        sum += $1
    }
    END {
        mean = sum / count
        p50_idx  = int(count * 0.50)
        p95_idx  = int(count * 0.95)
        p99_idx  = int(count * 0.99)
        if (p50_idx >= count) p50_idx = count - 1
        if (p95_idx >= count) p95_idx = count - 1
        if (p99_idx >= count) p99_idx = count - 1
        printf "mean=%.0f p50=%.0f p95=%.0f p99=%.0f min=%.0f max=%.0f\n",
            mean, vals[p50_idx], vals[p95_idx], vals[p99_idx], vals[0], vals[count-1]
    }')

    local mean_ms p50_ms p95_ms p99_ms min_ms max_ms
    mean_ms=$(echo "${stats}" | grep -oP 'mean=\K[0-9]+')
    p50_ms=$(echo  "${stats}" | grep -oP 'p50=\K[0-9]+')
    p95_ms=$(echo  "${stats}" | grep -oP 'p95=\K[0-9]+')
    p99_ms=$(echo  "${stats}" | grep -oP 'p99=\K[0-9]+')
    min_ms=$(echo  "${stats}" | grep -oP 'min=\K[0-9]+')
    max_ms=$(echo  "${stats}" | grep -oP 'max=\K[0-9]+')

    local rps
    rps=$(awk "BEGIN {printf \"%.2f\", ${total} / ${actual_duration}}")

    local error_pct
    error_pct=$(awk "BEGIN {printf \"%.2f\", ${errors} * 100 / ${total}}")

    # ---------------------------------------------------------------------------
    # Print results
    # ---------------------------------------------------------------------------
    echo ""
    echo "============================================================"
    echo "  Hydra Ollama Concurrent Benchmark — Results"
    echo "  $(date '+%Y-%m-%d %H:%M:%S')"
    echo "============================================================"
    printf "  Endpoint      : %s\n" "${ENDPOINT}"
    printf "  Model         : %s\n" "${MODEL}"
    printf "  VUs           : %d\n" "${VUS}"
    printf "  Test duration : %ds\n" "${actual_duration}"
    echo ""
    printf "  Total requests  : %d\n" "${total}"
    printf "  Errors          : %d (HTTP non-200)\n" "${errors}"
    printf "  Error rate      : %s%%\n" "${error_pct}"
    printf "  Requests/sec    : %s req/s\n" "${rps}"
    echo ""
    printf "  Latency (wall-clock, ms):\n"
    printf "    min   : %s ms\n" "${min_ms}"
    printf "    mean  : %s ms\n" "${mean_ms}"
    printf "    p50   : %s ms\n" "${p50_ms}"
    printf "    p95   : %s ms\n" "${p95_ms}"
    printf "    p99   : %s ms\n" "${p99_ms}"
    printf "    max   : %s ms\n" "${max_ms}"
    echo "============================================================"
    echo ""

    # SLA check (FastPool: p95 < 1500 ms; ReasonPool: p95 < 5000 ms)
    if [[ "${p95_ms}" -lt 1500 ]]; then
        log "SLA: FastPool p95 < 1500 ms  PASS  (actual: ${p95_ms} ms)"
    elif [[ "${p95_ms}" -lt 5000 ]]; then
        log "SLA: FastPool p95 < 1500 ms  FAIL  (actual: ${p95_ms} ms)"
        log "SLA: ReasonPool p95 < 5000 ms  PASS  (actual: ${p95_ms} ms)"
    else
        warn "SLA: ReasonPool p95 < 5000 ms  FAIL  (actual: ${p95_ms} ms)"
    fi

    # Optional CSV append
    if [[ -n "${OUTPUT_FILE}" ]]; then
        local csv_line
        csv_line="$(date '+%Y-%m-%d %H:%M:%S'),${MODEL},${VUS},${actual_duration},${total},${errors},${error_pct},${mean_ms},${p50_ms},${p95_ms},${p99_ms},${rps}"
        # Write header if file doesn't exist
        if [[ ! -f "${OUTPUT_FILE}" ]]; then
            echo "date,model,vus,duration_s,requests,errors,error_pct,mean_ms,p50_ms,p95_ms,p99_ms,rps" >> "${OUTPUT_FILE}"
        fi
        echo "${csv_line}" >> "${OUTPUT_FILE}"
        log "Result appended to: ${OUTPUT_FILE}"
    fi
}

main "$@"
