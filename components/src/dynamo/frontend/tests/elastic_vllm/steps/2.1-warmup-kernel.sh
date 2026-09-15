#!/usr/bin/env bash
set -euo pipefail

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:9090}"
MODEL="${E2E_MODEL:-/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/}"
CONCURRENCY="${CONCURRENCY:-8}"
MAX_TOKENS="${MAX_TOKENS:-16}"

request() {
  curl -sS -m 600 "$BACKEND_URL/v1/completions" \
    -H 'Content-Type: application/json' \
    -d "$(cat <<JSON
{"model":"$MODEL","prompt":"Kernel warmup request $1","max_tokens":$MAX_TOKENS,"temperature":0}
JSON
)" >/dev/null
}

pids=()
for i in $(seq 1 "$CONCURRENCY"); do request "$i" & pids+=("$!"); done
status=0
for pid in "${pids[@]}"; do wait "$pid" || status=1; done
(( status == 0 )) && echo "Warmup completed: $CONCURRENCY concurrent requests" || exit "$status"
