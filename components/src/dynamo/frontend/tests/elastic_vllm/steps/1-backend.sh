#!/bin/bash
# =============================================================================
# STEP 1/4 (manual run) — stop old service, set infinicore env, start the
# Dynamo vLLM backend (worker) at tp=2 pp=2 with control plane on :9091.
#
# Run order: step1 (backend) -> step2 (frontend) -> step3 (load) -> step4 (check)
#
# Boots at 2x2 so the controller has somewhere to switch UP to (target 4x1).
# Boot takes ~2 min; the script waits and verifies readiness + topology.
# =============================================================================
set -uo pipefail

# --- stop anything running ---------------------------------------------------
/workspace/dynamo/recipes/elastic-vllm/0920/service_qwen3.8-27b.sh stop 2>&1 | tail -2
sleep 5

# --- infinicore environment (MUST be in the same shell that starts backend) --
export PATH=/opt/conda/bin:$PATH          # 'python' is missing in non-interactive ssh shells
export VLLM_PLUGINS=infinicore            # NOT metax — metax breaks PP=2 inference (FA2/libcudart.so.13)
export VLLM_INFINICORE_GDN_SINGLE_STAGE=1
export VLLM_DISTRIBUTED_EXECUTOR_BACKEND=mp
export VLLM_SERVER_DEV_MODE=1
export MACA_PATH="${MACA_PATH:-/opt/maca-3.8.0}"   # own line: $MACA_PATH below expands BEFORE same-line assignment under set -u
export MACA_HOME="$MACA_PATH" MACA_ROOT="$MACA_PATH"
export FLASH_ATTN_2_CUDA_SO=$(python -c 'import importlib.util,pathlib;print(pathlib.Path(importlib.util.find_spec("flash_attn_2_cuda").origin).resolve())')
echo "VLLM_PLUGINS=$VLLM_PLUGINS FLASH_ATTN_2_CUDA_SO=$FLASH_ATTN_2_CUDA_SO"

# --- start backend at tp=2 pp=2 ----------------------------------------------
LOGS=/workspace/dynamo/logs
mkdir -p "$LOGS"
rm -rf /tmp/dynamo_store_kv
cd /workspace/dynamo
: > "$LOGS/backend.log"
DYN_SYSTEM_PORT=9091 nohup python -m dynamo.vllm \
  --discovery-backend file --disaggregation-mode agg \
  --max-num-batched-tokens 81920 \
  --model "${E2E_MODEL:-/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/}" \
  --distributed-executor-backend mp \
  --gpu-memory-utilization 0.80 \
  --tensor_parallel_size 2 --pipeline_parallel_size 2 \
  --tp-pp-switch-prebuild-strategies 4x1,2x2,1x4 \
  --tp-pp-switch-kv-transfer-window-size 2 \
  --tp-pp-switch-kv-transfer-max-scratch-size-mb 256 \
  --enforce-eager >> "$LOGS/backend.log" 2>&1 &
echo $! > "$LOGS/backend.pid"
BACKEND_PID=$(cat "$LOGS/backend.pid")
echo "backend pid=$BACKEND_PID  log=$LOGS/backend.log"

# --- wait for readiness (kill -0 guard FIRST: dead process must not pass) -----
for i in $(seq 1 120); do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "FATAL: backend died during startup; last 20 log lines:"
    tail -20 "$LOGS/backend.log"; exit 1
  fi
  ST=$(curl -s -m 10 -X POST http://localhost:9091/engine/control/parallel_strategy_state \
       -H 'Content-Type: application/json' -d '{}')
  [[ "$ST" == *'"status"'* ]] && { echo "backend ready after ~$((i*5))s"; echo "$ST"; break; }
  sleep 5
  [[ $i -eq 120 ]] && { echo "FATAL: backend not ready within 600s"; tail -20 "$LOGS/backend.log"; exit 1; }
done


# send a request to warm up
# curl -s http://127.0.0.1:9091/v1/completions \
#   -H 'Content-Type: application/json' \
#   -d '{
#     "model": "/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/",
#     "prompt": "Warmup request",
#     "max_tokens": 1024,
#     "temperature": 0
#   }' >/dev/null


# --- verify boot topology is 2x2 ----------------------------------------------
echo "$ST" | grep -q '"tensor_parallel_size": *2' && echo "$ST" | grep -q '"pipeline_parallel_size": *2' \
  && echo "OK: booted at tp=2 pp=2" \
  || { echo "FATAL: not at 2x2: $ST"; exit 1; }

echo
echo "STEP 1 DONE. Next: ./2-frontend.sh"
