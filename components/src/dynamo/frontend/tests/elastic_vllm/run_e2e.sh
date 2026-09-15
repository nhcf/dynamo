#!/bin/bash
# =============================================================================
# E2E test: traffic-triggered TP/PP switch, boot-at-2x2 variant.
#
#   backend  : dynamo.vllm, infinicore plugin, STARTS AT tp=2 pp=2
#              (args mirror 0920/start_vllm_infinicore.sh, only tp/pp differ)
#   frontend : dynamo.frontend + elastic controller
#              (DYN_ELASTIC_SWITCH_*, threshold = FACTOR_UP * EXPECTED_WORKERS)
#   load     : closed-loop benchmark, CONC=20 concurrent chat completions
#
# Expected causal chain:
#   load starts -> dynamo_frontend_active_requests rises to ~20 (> threshold 10)
#   -> controller sees it for STABLE_POLLS=3 consecutive polls (~6s)
#   -> POST /engine/control/switch_parallel_strategy (wait+queue)
#   -> backend parks new requests, drains in-flight, switches 2x2 -> 4x1
#   -> control-plane state reports tp=4 pp=1, failed=false
#   -> benchmark finishes with ZERO failed requests.
#
# Usage:  ./run_e2e.sh            (full run; leaves the service UP at the end)
# Env overrides: E2E_CONC (20) E2E_DURATION (200) E2E_FACTOR_UP (10)
#                E2E_GPU_MEM (0.80) E2E_MAX_TOKENS (32)
# =============================================================================
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS=/workspace/dynamo/logs
OUT="$BASE/e2e_run"
mkdir -p "$LOGS" "$OUT"
exec > >(tee "$OUT/run.log") 2>&1

PYTHON_BIN=/opt/conda/bin/python
MODEL=${E2E_MODEL:-/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/}
FRONTEND_PORT=9090
CONTROL_PORT=9091
CTRL="http://localhost:${CONTROL_PORT}/engine/control"
FE="http://localhost:${FRONTEND_PORT}"

CONC=${E2E_CONC:-20}
DURATION=${E2E_DURATION:-200}
FACTOR_UP=${E2E_FACTOR_UP:-10}
GPU_MEM=${E2E_GPU_MEM:-0.80}
MAX_TOKENS=${E2E_MAX_TOKENS:-32}

PASS=1
note() { printf '\n===== %s =====\n' "$*"; }
fail() { echo "FAIL: $*"; PASS=0; }

state() { curl -s -m 10 -X POST "$CTRL/parallel_strategy_state" -H 'Content-Type: application/json' -d '{}'; }
state_field() { state | "$PYTHON_BIN" -c "import sys,json; print(json.load(sys.stdin).get('$1'))" 2>/dev/null; }

# ---------------------------------------------------------------- 0. teardown
note "0. stop any running service"
if [[ -x /workspace/dynamo/recipes/elastic-vllm/0920/service_qwen3.8-27b.sh ]]; then
  /workspace/dynamo/recipes/elastic-vllm/0920/service_qwen3.8-27b.sh stop 2>&1 | tail -2
fi
sleep 5

# ------------------------------------------------------------------- 1. env
note "1. infinicore environment (mirrors start_vllm_infinicore.sh)"
export PATH="$(dirname "$PYTHON_BIN"):$PATH"
export VLLM_PLUGINS=infinicore
export VLLM_INFINICORE_GDN_SINGLE_STAGE=1
export VLLM_DISTRIBUTED_EXECUTOR_BACKEND=mp
export VLLM_SERVER_DEV_MODE=1
export MACA_PATH="${MACA_PATH:-/opt/maca-3.8.0}"
export MACA_HOME="$MACA_PATH" MACA_ROOT="$MACA_PATH"
export FLASH_ATTN_2_CUDA_SO=$("$PYTHON_BIN" -c 'import importlib.util,pathlib;print(pathlib.Path(importlib.util.find_spec("flash_attn_2_cuda").origin).resolve())')
echo "VLLM_PLUGINS=$VLLM_PLUGINS FLASH_ATTN_2_CUDA_SO=$FLASH_ATTN_2_CUDA_SO"

# ------------------------------------------------------- 2. backend at 2x2
note "2. start backend: dynamo.vllm, tp=2 pp=2 (control plane :$CONTROL_PORT)"
[[ -d /tmp/dynamo_store_kv ]] && rm -rf /tmp/dynamo_store_kv
export DYN_SYSTEM_PORT="$CONTROL_PORT"
cd /workspace/dynamo
: > "$LOGS/backend.log"
nohup "$PYTHON_BIN" -m dynamo.vllm \
  --discovery-backend file --disaggregation-mode agg \
  --model "$MODEL" \
  --distributed-executor-backend mp \
  --gpu-memory-utilization "$GPU_MEM" \
  --tensor_parallel_size 2 \
  --pipeline_parallel_size 2 \
  --tp-pp-switch-prebuild-strategies 4x1,2x2,1x4 \
  --tp-pp-switch-kv-transfer-window-size 2 \
  --tp-pp-switch-kv-transfer-max-scratch-size-mb 256 \
  --enforce-eager >> "$LOGS/backend.log" 2>&1 &
echo $! > "$LOGS/backend.pid"
BACKEND_PID=$(cat "$LOGS/backend.pid")
echo "backend pid=$BACKEND_PID"

READY=0
for i in $(seq 1 120); do
  # kill -0 guard FIRST: the service script's readiness check lacks this and
  # reports false positives when the process died instantly (seen with metax/no-python).
  kill -0 "$BACKEND_PID" 2>/dev/null || { fail "backend died during startup; tail:"; tail -20 "$LOGS/backend.log"; exit 1; }
  if state | grep -q '"status"'; then READY=1; echo "backend ready after ~$((i*5))s"; break; fi
  sleep 5
done
[[ $READY -eq 1 ]] || { fail "backend not ready within 600s"; tail -20 "$LOGS/backend.log"; exit 1; }

# ---------------------------------------------------- 3. verify boot at 2x2
note "3. verify boot topology is tp=2 pp=2"
ST=$(state); echo "$ST"
[[ "$(state_field tensor_parallel_size)" == "2" && "$(state_field pipeline_parallel_size)" == "2" ]] \
  || fail "backend did not boot at 2x2"
[[ "$(state_field failed)" == "False" ]] || fail "backend reports failed=true at boot"

# ------------------------------------------- 4. frontend with controller env
note "4. start frontend + elastic controller (target 4x1, threshold $FACTOR_UP x 1 workers)"
: > "$LOGS/frontend.log"
DYN_ELASTIC_SWITCH_ENABLE=1 \
DYN_ELASTIC_SWITCH_WORKER_URLS="http://localhost:${CONTROL_PORT}" \
DYN_ELASTIC_SWITCH_EXPECTED_WORKERS=1 \
DYN_ELASTIC_SWITCH_METRICS_URL="http://localhost:${FRONTEND_PORT}/metrics" \
DYN_ELASTIC_SWITCH_POLL_INTERVAL_S=2 \
DYN_ELASTIC_SWITCH_STABLE_POLLS=3 \
DYN_ELASTIC_SWITCH_FACTOR_UP="$FACTOR_UP" \
DYN_ELASTIC_SWITCH_STRATEGY_UP=4x1 \
DYN_ELASTIC_SWITCH_COOLDOWN_S=600 \
  nohup "$PYTHON_BIN" -m dynamo.frontend --discovery-backend file --http-port "$FRONTEND_PORT" >> "$LOGS/frontend.log" 2>&1 &
echo $! > "$LOGS/frontend.pid"
FRONTEND_PID=$(cat "$LOGS/frontend.pid")
echo "frontend pid=$FRONTEND_PID"

FE_READY=0
for i in $(seq 1 40); do
  kill -0 "$FRONTEND_PID" 2>/dev/null || { fail "frontend died; tail:"; tail -20 "$LOGS/frontend.log"; exit 1; }
  curl -s -m 2 -o /dev/null "$FE/health" 2>/dev/null && { FE_READY=1; echo "frontend ready after ~$((i*3))s"; break; }
  sleep 3
done
[[ $FE_READY -eq 1 ]] || { fail "frontend not ready within 120s"; exit 1; }

grep -m1 "Elastic TP/PP switch controller started" "$LOGS/frontend.log" \
  || fail "controller did not start (check DYN_ELASTIC_SWITCH_* and frontend.log)"

# model discovery: worker must appear before load
MODEL_ID=""
for i in $(seq 1 20); do
  MODEL_ID=$(curl -s -m 5 "$FE/v1/models" | "$PYTHON_BIN" -c 'import sys,json;d=json.load(sys.stdin)["data"];print(d[0]["id"] if d else "")' 2>/dev/null)
  [[ -n "$MODEL_ID" ]] && break
  sleep 3
done
[[ -n "$MODEL_ID" ]] || { fail "no model discovered at frontend"; exit 1; }
echo "model=$MODEL_ID"

note "5. baseline inference (one request on 2x2)"
BASE_RESP=$(curl -s -m 120 "$FE/v1/chat/completions" -H 'Content-Type: application/json' \
  -d "{\"model\": \"$MODEL_ID\", \"messages\": [{\"role\": \"user\", \"content\": \"What is 2+3? One number.\"}], \"max_tokens\": 64}")
echo "$BASE_RESP" | head -c 300; echo
echo "$BASE_RESP" | grep -q '"chatcmpl' || fail "baseline inference on 2x2 failed"

# ------------------------------------------------------------- 6. drive load
note "6. start benchmark: conc=$CONC duration=${DURATION}s max_tokens=$MAX_TOKENS"
export M3_MODEL="$MODEL_ID" M3_CONC="$CONC" M3_DURATION="$DURATION" M3_MAX_TOKENS="$MAX_TOKENS"
"$PYTHON_BIN" "$BASE/load_gen.py" > "$OUT/load_summary.json" 2> "$OUT/load_err.log" &
LOAD_PID=$!

# ------------------------------------------------- 7. watch the switch happen
note "7. poll control-plane state every 2s (expect: is_switching=true, then tp=4 pp=1)"
SWITCHED=0; SEEN_SWITCHING=0; T0=$(date +%s)
while (( $(date +%s) - T0 < DURATION + 60 )); do
  ST=$(state)
  TP=$(echo "$ST"  | "$PYTHON_BIN" -c 'import sys,json;print(json.load(sys.stdin).get("tensor_parallel_size"))' 2>/dev/null)
  PP=$(echo "$ST"  | "$PYTHON_BIN" -c 'import sys,json;print(json.load(sys.stdin).get("pipeline_parallel_size"))' 2>/dev/null)
  SW=$(echo "$ST"  | "$PYTHON_BIN" -c 'import sys,json;print(json.load(sys.stdin).get("is_switching"))' 2>/dev/null)
  echo "  t=$(( $(date +%s) - T0 ))s tp=$TP pp=$PP is_switching=$SW"
  [[ "$SW" == "True" ]] && SEEN_SWITCHING=1
  if [[ "$TP" == "4" && "$PP" == "1" ]]; then SWITCHED=1; echo "  >>> state reports 4x1 - switch completed"; break; fi
  sleep 2
done
[[ $SWITCHED -eq 1 ]]      || fail "worker never reached tp=4 pp=1"
[[ $SEEN_SWITCHING -eq 1 ]] && echo "  (observed is_switching=true mid-flight)" \
                            || echo "  NOTE: never observed is_switching=true (poll too coarse or switch too fast)"

note "8. controller log evidence"
grep -E "SWITCH UP|switching http|now at|campaign #" "$LOGS/frontend.log" || fail "no controller campaign lines in frontend.log"

note "9. wait for benchmark, check zero lost requests"
wait $LOAD_PID; LOAD_RC=$?
cat "$OUT/load_summary.json"; echo
ERR=$( "$PYTHON_BIN" -c "import json;d=json.load(open('$OUT/load_summary.json'));print(d['err'])" 2>/dev/null || echo "?" )
[[ "$ERR" == "0" && $LOAD_RC -eq 0 ]] || fail "benchmark lost requests (err=$ERR rc=$LOAD_RC)"

note "10. final state"
ST=$(state); echo "$ST"
[[ "$(state_field failed)" == "False" ]] || fail "worker reports failed=true after switch"

note "VERDICT"
if [[ $PASS -eq 1 ]]; then
  echo "E2E_PASS: booted 2x2 -> conc=$CONC load -> controller auto-switched to 4x1 -> 0 lost requests"
else
  echo "E2E_FAIL: see FAIL lines above; logs: $LOGS/{backend,frontend}.log, $OUT/"
fi
echo "(service left running; stop with: $BASE/../service_qwen3.8-27b.sh stop)"
exit $((1 - PASS))
