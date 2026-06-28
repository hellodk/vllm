#!/usr/bin/env bash
# nccl_probe.sh (SRE-4)
# Runs a periodic collective AllReduce benchmark and publishes results as a
# Prometheus textfile:
#   nccl_allreduce_latency_seconds{tp_group,rank,model}
#   nccl_busbw_gbps{tp_group,rank,model}
#
# The actual collective binary (nccl-tests all_reduce_perf on NVIDIA, or the
# Thunderbolt/RoCE equivalent on Apple Silicon largepool) is staged air-gapped
# and passed via $NCCL_PROBE_BIN. If it is absent, this script publishes a
# probe-up=0 sample so the absence is itself observable.
#
# Env (exported by the launchd plist / systemd unit):
#   NCCL_PROBE_TEXTFILE  destination .prom file
#   NCCL_PROBE_BIN       collective benchmark binary (optional)
#   TP_GROUP RANK MODEL  topology labels
set -euo pipefail

TEXTFILE="${NCCL_PROBE_TEXTFILE:?NCCL_PROBE_TEXTFILE required}"
TP_GROUP="${TP_GROUP:-unknown}"
RANK="${RANK:-0}"
MODEL="${MODEL:-unknown}"
BIN="${NCCL_PROBE_BIN:-}"
TMP="$(mktemp)"
LABELS="tp_group=\"${TP_GROUP}\",rank=\"${RANK}\",model=\"${MODEL}\""

emit() {
  {
    echo "# HELP nccl_allreduce_latency_seconds AllReduce latency from the collective probe."
    echo "# TYPE nccl_allreduce_latency_seconds gauge"
    echo "nccl_allreduce_latency_seconds{${LABELS}} ${1}"
    echo "# HELP nccl_busbw_gbps AllReduce bus bandwidth (GB/s) from the collective probe."
    echo "# TYPE nccl_busbw_gbps gauge"
    echo "nccl_busbw_gbps{${LABELS}} ${2}"
    echo "# HELP nccl_probe_up Whether the collective probe binary ran successfully."
    echo "# TYPE nccl_probe_up gauge"
    echo "nccl_probe_up{${LABELS}} ${3}"
  } > "${TMP}"
  mv "${TMP}" "${TEXTFILE}"
}

if [[ -z "${BIN}" || ! -x "${BIN}" ]]; then
  # Collective benchmark binary not staged — surface the gap, do not fake data.
  emit "NaN" "NaN" "0"
  exit 0
fi

# all_reduce_perf prints a results table; the busbw column (GB/s) is the last
# numeric field and time (us) is column 5 on the largest-size row.
OUT="$("${BIN}" -b 256M -e 256M -f 2 -g 1 2>/dev/null | awk '/^ *[0-9]/ {t=$5; bw=$NF} END {print t, bw}')"
TIME_US="$(awk '{print $1}' <<<"${OUT}")"
BUSBW="$(awk '{print $2}' <<<"${OUT}")"
LAT_SECONDS="$(awk -v u="${TIME_US:-0}" 'BEGIN {printf "%.9f", u/1e6}')"
emit "${LAT_SECONDS}" "${BUSBW:-NaN}" "1"
