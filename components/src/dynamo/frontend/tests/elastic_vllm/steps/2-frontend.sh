#!/bin/bash
# =============================================================================
# STEP 2/4 (manual run) — start the Dynamo frontend on :9090 WITH the elastic
# switch controller enabled.
#
# Controller config used here:
#   threshold      = FACTOR_UP(10) x EXPECTED_WORKERS(1) = concurrency 10
#   hysteresis     = STABLE_POLLS(3) consecutive polls x POLL_INTERVAL_S(2s) ~ 6s
#   target         = STRATEGY_UP 4x1
#   cooldown       = 600s (no second campaign even if load stays high)
#
# Prereq: step1 done (backend alive at 2x2, control plane :9091 answering).
# =============================================================================
set -uo pipefail
export PATH=/opt/conda/bin:$PATH
LOGS=/workspace/dynamo/logs

# --- prereq check: backend must be alive at 2x2 --------------------------------
ST=$(curl -s -m 10 -X POST http://localhost:9091/engine/control/parallel_strategy_state \
     -H 'Content-Type: application/json' -d '{}')
[[ "$ST" == *'"status"'* ]] || { echo "FATAL: backend control plane :9091 not answering — run ./1-bakend.sh first"; exit 1; }
echo "backend state: $ST"

# --- start frontend with controller env ---------------------------------------
: > "$LOGS/frontend.log"
cd /workspace/dynamo
DYN_ELASTIC_SWITCH_ENABLE=1 \
DYN_ELASTIC_SWITCH_WORKER_URLS=http://localhost:9091 \
DYN_ELASTIC_SWITCH_EXPECTED_WORKERS=1 \
DYN_ELASTIC_SWITCH_METRICS_URL=http://localhost:9090/metrics \
DYN_ELASTIC_SWITCH_POLL_INTERVAL_S=2 \
DYN_ELASTIC_SWITCH_STABLE_POLLS=3 \
DYN_ELASTIC_SWITCH_FACTOR_UP=10 \
DYN_ELASTIC_SWITCH_STRATEGY_UP=4x1 \
DYN_ELASTIC_SWITCH_COOLDOWN_S=600 \
  nohup python -m dynamo.frontend --discovery-backend file --http-port 9090 >> "$LOGS/frontend.log" 2>&1 &
echo $! > "$LOGS/frontend.pid"
FRONTEND_PID=$(cat "$LOGS/frontend.pid")
echo "frontend pid=$FRONTEND_PID  log=$LOGS/frontend.log"

# --- wait for /health (kill -0 guard first) ------------------------------------
READY=0
for i in $(seq 1 40); do
  kill -0 "$FRONTEND_PID" 2>/dev/null || { echo "FATAL: frontend died; last 20 log lines:"; tail -20 "$LOGS/frontend.log"; exit 1; }
  curl -s -m 2 -o /dev/null http://localhost:9090/health 2>/dev/null && { READY=1; echo "frontend ready after ~$((i*3))s"; break; }
  sleep 3
done
[[ $READY -eq 1 ]] || { echo "FATAL: frontend not ready within 120s"; exit 1; }

# --- controller must have started ----------------------------------------------
grep -m1 "Elastic TP/PP switch controller started" "$LOGS/frontend.log" \
  || { echo "FATAL: controller did not start — check DYN_ELASTIC_SWITCH_* env and frontend.log"; exit 1; }

# --- worker/model discovery -----------------------------------------------------
MODEL_ID=""
for i in $(seq 1 20); do
  MODEL_ID=$(curl -s -m 5 http://localhost:9090/v1/models | python -c 'import sys,json;d=json.load(sys.stdin)["data"];print(d[0]["id"] if d else "")' 2>/dev/null)
  [[ -n "$MODEL_ID" ]] && break
  sleep 3
done
[[ -n "$MODEL_ID" ]] || { echo "FATAL: no model discovered at frontend — is the backend registered?"; exit 1; }
echo "model=$MODEL_ID"
echo "$MODEL_ID" > /tmp/e2e_model_id      # step3 reads this

# --- baseline inference on 2x2 ----------------------------------------------------
RESP=$(curl -s -m 120 http://localhost:9090/v1/chat/completions -H 'Content-Type: application/json' \
  -d "{\"model\": \"$MODEL_ID\", \"messages\": [{\"role\": \"user\", \"content\": \"What is 2+3? One number.\"}], \"max_tokens\": 64}")
echo "$RESP" | head -c 300; echo
echo "$RESP" | grep -q '"chatcmpl' && echo "OK: baseline inference on 2x2 works" \
  || { echo "FATAL: baseline inference failed"; exit 1; }

echo

# send a request to warm up
echo "send a request to warmup instances"
curl -s http://127.0.0.1:9091/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/",
    "prompt": "Warmup request",
    "max_tokens": 1024,
    "temperature": 0
  }' >/dev/null

echo "STEP 2 DONE. Next: ./3-gen_worload-vllm.sh"
