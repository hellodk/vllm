#!/usr/bin/env bash
# run-phase1.sh — Master Phase 1 benchmark script for Hydra LLM benchmarking
#
# Runs llama-bench + llama-batched-bench for the Phase 1 test matrix defined in
# benchmarkings.md Section 2.4.
#
# Usage:
#   ./scripts/bench/run-phase1.sh --machine <id> --model <size> --runtime <rt> [options]
#
# Required args:
#   --machine   m2-8g | m3-16g | i7-rtx3050
#   --model     3b | 7b | 8b | 14b | 70b
#   --runtime   llamacpp | ollama
#
# Optional args:
#   --ngl       Number of GPU layers (default: 99 on macOS, 0 on Linux/NVIDIA)
#   --model-path  Explicit path to .gguf file (overrides auto-detection)
#   --models-dir  Directory containing models (default: ~/models)
#   --dry-run   Print commands without executing
#   --help      Show this message
#
# Output:
#   results/MACHINE_MODEL_RUNTIME_YYYYMMDD_HHMMSS.txt
#
# Examples:
#   # macOS M3 16 GB — 8B model
#   ./scripts/bench/run-phase1.sh --machine m3-16g --model 8b --runtime llamacpp
#
#   # NVIDIA i7+RTX3050 — 8B model, 20 GPU layers
#   ./scripts/bench/run-phase1.sh --machine i7-rtx3050 --model 8b --runtime llamacpp --ngl 20
#
#   # CPU-only baseline on NVIDIA machine
#   ./scripts/bench/run-phase1.sh --machine i7-rtx3050 --model 8b --runtime llamacpp --ngl 0

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
MACHINE=""
MODEL_SIZE=""
RUNTIME=""
NGL=""
MODEL_PATH=""
MODELS_DIR="${HOME}/models"
DRY_RUN=false

# Detect project root relative to this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

LLAMA_BENCH="${PROJECT_ROOT}/llama.cpp/build/bin/llama-bench"
LLAMA_BATCHED_BENCH="${PROJECT_ROOT}/llama.cpp/build/bin/llama-batched-bench"
RESULTS_DIR="${PROJECT_ROOT}/results"

# Phase 1 llama-bench parameters (Section 2.4 / Section 4.2)
BENCH_PP="512,2048,4096,8192"
BENCH_TG="32,256"
BENCH_BATCH="1,4,8,32"
BENCH_FLASH_ATTN=1
BENCH_REPEAT=3

# Phase 1 llama-batched-bench parameters
BATCHED_PP="512,2048,4096"
BATCHED_TG="32"
BATCHED_PARALLEL="1,2,4,8,16,32"

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

run_cmd() {
    if "${DRY_RUN}"; then
        echo "[DRY-RUN] $*"
    else
        "$@"
    fi
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --machine)    MACHINE="$2";     shift 2 ;;
        --model)      MODEL_SIZE="$2";  shift 2 ;;
        --runtime)    RUNTIME="$2";     shift 2 ;;
        --ngl)        NGL="$2";         shift 2 ;;
        --model-path) MODEL_PATH="$2";  shift 2 ;;
        --models-dir) MODELS_DIR="$2";  shift 2 ;;
        --dry-run)    DRY_RUN=true;     shift   ;;
        --help|-h)    usage ;;
        *) die "Unknown argument: $1. Run with --help for usage." ;;
    esac
done

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
[[ -n "${MACHINE}" ]]     || die "--machine is required (m2-8g|m3-16g|i7-rtx3050)"
[[ -n "${MODEL_SIZE}" ]]  || die "--model is required (3b|7b|8b|14b|70b)"
[[ -n "${RUNTIME}" ]]     || die "--runtime is required (llamacpp|ollama)"

case "${MACHINE}" in
    m2-8g|m3-16g|i7-rtx3050) ;;
    *) die "Invalid --machine '${MACHINE}'. Must be one of: m2-8g, m3-16g, i7-rtx3050" ;;
esac

case "${MODEL_SIZE}" in
    3b|7b|8b|14b|70b) ;;
    *) die "Invalid --model '${MODEL_SIZE}'. Must be one of: 3b, 7b, 8b, 14b, 70b" ;;
esac

case "${RUNTIME}" in
    llamacpp|ollama) ;;
    *) die "Invalid --runtime '${RUNTIME}'. Must be one of: llamacpp, ollama" ;;
esac

# ---------------------------------------------------------------------------
# Detect OS and set NGL default
# ---------------------------------------------------------------------------
OS_TYPE="$(uname -s)"

if [[ -z "${NGL}" ]]; then
    case "${OS_TYPE}" in
        Darwin)
            NGL=99
            info "macOS detected — Metal auto-enabled, setting ngl=${NGL}"
            ;;
        Linux)
            if [[ "${MACHINE}" == "i7-rtx3050" ]]; then
                NGL=0
                warn "Linux/NVIDIA machine: defaulting ngl=0 (CPU-only). Pass --ngl N for partial GPU offload."
            else
                NGL=99
            fi
            ;;
        *)
            NGL=99
            warn "Unknown OS '${OS_TYPE}' — defaulting ngl=${NGL}"
            ;;
    esac
fi

# ---------------------------------------------------------------------------
# Resolve model file path
# ---------------------------------------------------------------------------
resolve_model_path() {
    local size="$1"
    local models_dir="$2"

    # Candidate filename patterns — most specific first
    declare -a candidates
    case "${size}" in
        3b)
            candidates=(
                "${models_dir}/llama3.2-3b-q4_k_m.gguf"
                "${models_dir}/llama3-3b-q4_k_m.gguf"
                "${models_dir}/llama3.2-3b-instruct-q4_k_m.gguf"
                "${models_dir}/llama-3.2-3b-instruct-q4_k_m.gguf"
            )
            ;;
        7b)
            candidates=(
                "${models_dir}/qwen2.5-7b-q4_k_m.gguf"
                "${models_dir}/qwen2.5-7b-instruct-q4_k_m.gguf"
                "${models_dir}/mistral-7b-q4_k_m.gguf"
                "${models_dir}/mistral-7b-v0.3-q4_k_m.gguf"
            )
            ;;
        8b)
            candidates=(
                "${models_dir}/llama3-8b-q4_k_m.gguf"
                "${models_dir}/llama3.1-8b-q4_k_m.gguf"
                "${models_dir}/llama-3-8b-instruct-q4_k_m.gguf"
                "${models_dir}/llama3.1-8b-instruct-q4_k_m.gguf"
            )
            ;;
        14b)
            candidates=(
                "${models_dir}/qwen2.5-14b-q4_k_m.gguf"
                "${models_dir}/qwen2.5-14b-instruct-q4_k_m.gguf"
                "${models_dir}/phi4-14b-q4_k_m.gguf"
                "${models_dir}/phi-4-14b-q4_k_m.gguf"
            )
            ;;
        70b)
            candidates=(
                "${models_dir}/llama3-70b-q4_k_m.gguf"
                "${models_dir}/llama3.1-70b-q4_k_m.gguf"
                "${models_dir}/llama-3-70b-instruct-q4_k_m.gguf"
                "${models_dir}/llama3.1-70b-instruct-q4_k_m.gguf"
            )
            ;;
    esac

    for f in "${candidates[@]}"; do
        if [[ -f "${f}" ]]; then
            echo "${f}"
            return 0
        fi
    done

    # Fallback: glob search
    local found
    found="$(find "${models_dir}" -maxdepth 2 -name "*${size}*q4_k_m*.gguf" 2>/dev/null | head -1)"
    if [[ -n "${found}" ]]; then
        warn "Auto-detected model via glob: ${found}"
        echo "${found}"
        return 0
    fi

    return 1
}

if [[ -z "${MODEL_PATH}" ]]; then
    if ! MODEL_PATH="$(resolve_model_path "${MODEL_SIZE}" "${MODELS_DIR}")"; then
        die "Cannot find a ${MODEL_SIZE} Q4_K_M model in ${MODELS_DIR}. " \
            "Pass --model-path /path/to/model.gguf or --models-dir /path/to/dir"
    fi
fi

[[ -f "${MODEL_PATH}" ]] || die "Model file not found: ${MODEL_PATH}"

# ---------------------------------------------------------------------------
# Verify binaries (skip for ollama-only runs)
# ---------------------------------------------------------------------------
if [[ "${RUNTIME}" == "llamacpp" ]]; then
    [[ -x "${LLAMA_BENCH}" ]] || \
        die "llama-bench not found or not executable at: ${LLAMA_BENCH}\n" \
            "Build with: cd llama.cpp && cmake -B build && cmake --build build -j"
    [[ -x "${LLAMA_BATCHED_BENCH}" ]] || \
        die "llama-batched-bench not found at: ${LLAMA_BATCHED_BENCH}"
fi

# ---------------------------------------------------------------------------
# Output file setup
# ---------------------------------------------------------------------------
mkdir -p "${RESULTS_DIR}"
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
RESULT_FILE="${RESULTS_DIR}/${MACHINE}_${MODEL_SIZE}_${RUNTIME}_${TIMESTAMP}.txt"

info "Output will be written to: ${RESULT_FILE}"

# ---------------------------------------------------------------------------
# Collect system info
# ---------------------------------------------------------------------------
collect_sysinfo() {
    local out="$1"
    {
        echo "============================================================"
        echo "  Hydra Phase 1 Benchmark — System Info"
        echo "  Date     : $(date '+%Y-%m-%d %H:%M:%S %Z')"
        echo "  Machine  : ${MACHINE}"
        echo "  Model    : ${MODEL_SIZE} (${MODEL_PATH})"
        echo "  Runtime  : ${RUNTIME}"
        echo "  NGL      : ${NGL}"
        echo "============================================================"
        echo ""
        echo "--- uname ---"
        uname -a
        echo ""

        echo "--- CPU info ---"
        if [[ "${OS_TYPE}" == "Darwin" ]]; then
            sysctl -n machdep.cpu.brand_string 2>/dev/null || true
            sysctl -n hw.memsize 2>/dev/null | awk '{printf "RAM: %.1f GB\n", $1/1073741824}' || true
        else
            grep "model name" /proc/cpuinfo | head -1 | sed 's/model name\s*:\s*//' || true
            grep MemTotal /proc/meminfo || true
        fi
        echo ""

        echo "--- llama.cpp git revision ---"
        (cd "${PROJECT_ROOT}/llama.cpp" && git rev-parse HEAD 2>/dev/null) || echo "N/A"
        echo ""

        echo "--- GPU info ---"
        if command -v nvidia-smi &>/dev/null; then
            nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap \
                       --format=csv,noheader 2>/dev/null || true
            echo ""
            nvidia-smi 2>/dev/null | grep -E "CUDA Version|Driver Version" || true
        elif [[ "${OS_TYPE}" == "Darwin" ]]; then
            system_profiler SPDisplaysDataType 2>/dev/null | \
                grep -E "Chipset Model|VRAM|Metal|Vendor" | head -8 || true
        else
            echo "No GPU info tool found"
        fi
        echo ""

        echo "--- Model file ---"
        ls -lh "${MODEL_PATH}" 2>/dev/null || echo "N/A"
        echo ""
        echo "============================================================"
        echo ""
    } | tee "${out}"
}

# ---------------------------------------------------------------------------
# Run llama-bench
# ---------------------------------------------------------------------------
run_llama_bench() {
    local out="$1"
    {
        echo ""
        echo "============================================================"
        echo "  llama-bench"
        echo "  pp=${BENCH_PP}  tg=${BENCH_TG}  batch=${BENCH_BATCH}"
        echo "  flash-attn=${BENCH_FLASH_ATTN}  repeat=${BENCH_REPEAT}  ngl=${NGL}"
        echo "============================================================"
        echo ""
    } | tee -a "${out}"

    local bench_args=(
        -m "${MODEL_PATH}"
        -p "${BENCH_PP}"
        -n "${BENCH_TG}"
        -b "${BENCH_BATCH}"
        --flash-attn "${BENCH_FLASH_ATTN}"
        -r "${BENCH_REPEAT}"
    )

    # Only pass --ngl for NVIDIA (Metal is auto-detected on macOS)
    if [[ "${OS_TYPE}" != "Darwin" ]]; then
        bench_args+=(--ngl "${NGL}")
    fi

    run_cmd "${LLAMA_BENCH}" "${bench_args[@]}" 2>&1 | tee -a "${out}"
}

# ---------------------------------------------------------------------------
# Run llama-batched-bench
# ---------------------------------------------------------------------------
run_llama_batched_bench() {
    local out="$1"
    {
        echo ""
        echo "============================================================"
        echo "  llama-batched-bench"
        echo "  pp=${BATCHED_PP}  tg=${BATCHED_TG}  parallel=${BATCHED_PARALLEL}"
        echo "  flash-attn=${BENCH_FLASH_ATTN}  ngl=${NGL}"
        echo "============================================================"
        echo ""
    } | tee -a "${out}"

    local batched_args=(
        -m "${MODEL_PATH}"
        --n-pp "${BATCHED_PP}"
        --n-tg "${BATCHED_TG}"
        --n-pl "${BATCHED_PARALLEL}"
        --flash-attn "${BENCH_FLASH_ATTN}"
    )

    if [[ "${OS_TYPE}" != "Darwin" ]]; then
        batched_args+=(--ngl "${NGL}")
    fi

    run_cmd "${LLAMA_BATCHED_BENCH}" "${batched_args[@]}" 2>&1 | tee -a "${out}"
}

# ---------------------------------------------------------------------------
# Ollama concurrent test
# ---------------------------------------------------------------------------
run_ollama_bench() {
    local out="$1"
    {
        echo ""
        echo "============================================================"
        echo "  Ollama concurrent load test"
        echo "============================================================"
        echo ""
    } | tee -a "${out}"

    if ! command -v ollama &>/dev/null; then
        warn "ollama not found in PATH — skipping Ollama load test"
        return
    fi

    # Map model size to likely Ollama tag
    local ollama_model
    case "${MODEL_SIZE}" in
        3b)  ollama_model="llama3.2:3b" ;;
        7b)  ollama_model="qwen2.5:7b" ;;
        8b)  ollama_model="llama3:8b" ;;
        14b) ollama_model="qwen2.5:14b" ;;
        70b) ollama_model="llama3:70b" ;;
        *)   ollama_model="llama3:${MODEL_SIZE}" ;;
    esac

    info "Running Ollama concurrent test with model: ${ollama_model}"
    info "Using ollama-concurrent.sh for VU sweep..."

    local concurrent_script="${SCRIPT_DIR}/ollama-concurrent.sh"
    if [[ -x "${concurrent_script}" ]]; then
        for vus in 1 2 5 10; do
            echo "--- VUs: ${vus} ---" | tee -a "${out}"
            run_cmd "${concurrent_script}" \
                --vus "${vus}" \
                --duration 60 \
                --model "${ollama_model}" 2>&1 | tee -a "${out}"
        done
    else
        warn "${concurrent_script} not found or not executable — skipping Ollama concurrent test"
        echo "(ollama-concurrent.sh not available)" | tee -a "${out}"
    fi
}

# ---------------------------------------------------------------------------
# Extract summary stats from result file
# ---------------------------------------------------------------------------
extract_summary() {
    local result_file="$1"

    # Try to extract PP t/s and TG t/s from llama-bench CSV output
    # llama-bench outputs CSV with columns: model,size,params,backend,ngl,n_batch,n_ubatch,
    #   n_threads,type_k,type_v,n_gpu_layers,main_gpu,no_kv_offload,split_mode,
    #   flash_attn,n_prompt,n_gen,test,t_ms,s_ms,t_ms_mean,t_ms_std,avg_ns,std_ns
    # The "pp" test row has n_prompt>0,n_gen=0; "tg" row has n_prompt=0,n_gen>0
    local pp_ts="" tg_ts=""

    if grep -q "^|" "${result_file}" 2>/dev/null; then
        # Markdown table format from llama-bench
        pp_ts="$(grep -E "^\| *pp" "${result_file}" | awk -F'|' '{print $NF}' | \
                 tr -d ' ' | sort -t'/' -k1 -n | tail -1 | grep -oP '[0-9]+\.[0-9]+')" || true
        tg_ts="$(grep -E "^\| *tg" "${result_file}" | awk -F'|' '{print $NF}' | \
                 tr -d ' ' | sort -n | tail -1 | grep -oP '[0-9]+\.[0-9]+')" || true
    fi

    [[ -z "${pp_ts}" ]] && pp_ts="N/A"
    [[ -z "${tg_ts}" ]] && tg_ts="N/A"

    echo "${MACHINE}|${MODEL_SIZE}|${RUNTIME}|pp=${pp_ts}t/s|tg=${tg_ts}t/s|ngl=${NGL}|$(date '+%Y-%m-%d')"
}

# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
main() {
    log "Starting Hydra Phase 1 benchmark"
    log "  Machine : ${MACHINE}"
    log "  Model   : ${MODEL_SIZE} → ${MODEL_PATH}"
    log "  Runtime : ${RUNTIME}"
    log "  NGL     : ${NGL}"
    log "  Output  : ${RESULT_FILE}"
    echo ""

    # 1. System info
    collect_sysinfo "${RESULT_FILE}"

    # 2. Benchmarks
    case "${RUNTIME}" in
        llamacpp)
            run_llama_bench        "${RESULT_FILE}"
            run_llama_batched_bench "${RESULT_FILE}"
            ;;
        ollama)
            run_ollama_bench "${RESULT_FILE}"
            ;;
    esac

    # 3. Footer
    {
        echo ""
        echo "============================================================"
        echo "  Benchmark complete: $(date '+%Y-%m-%d %H:%M:%S %Z')"
        echo "  Result file: ${RESULT_FILE}"
        echo "============================================================"
    } | tee -a "${RESULT_FILE}"

    # 4. Summary line (machine|model|runtime|pp_t/s|tg_t/s|date)
    echo ""
    log "Summary:"
    extract_summary "${RESULT_FILE}"
    echo ""
    log "Full results: ${RESULT_FILE}"
}

main "$@"
