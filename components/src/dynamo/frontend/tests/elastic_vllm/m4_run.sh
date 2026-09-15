#!/bin/bash
# =============================================================================
# M4 — traffic -> controller decision -> rolling switch, WITH cooldown check.
#
# Difference from run_e2e.sh (which boots the backend itself at 2x2):
#   M4 runs against an ALREADY-RUNNING Dynamo service booted at 4x1, manually
#   pre-switches the worker down to 2x2, then proves the controller switches
#   it back UP automatically under load — and that cooldown prevents a second
#   campaign while the load stays high.
#
# Causal chain under test:
#   manual switch 4x1 -> 2x2            (so "up" has somewhere to go)
#   restart frontend WITH controller env (threshold = FACTOR_UP x EXPECTED_WORKERS = 10)
#   closed-loop load conc=12 > 10 for STABLE_POLLS=3 polls (~6 s)
#   -> controller POSTs switch_parallel_strategy (wait+queue) 2x2 -> 4x1
#   -> state endpoint reports tp=4 pp=1; benchmark: 0 lost requests
#   -> load stays high for another 30 s: exactly ONE campaign line (cooldown).
#
# Prereq: a live Dynamo service on this box (backend :9091 control plane,
#         frontend :9090), booted under the infinicore env, currently at 4x1.
#         If nothing is running:  ../start_dynamo_infinicore.sh   (or ./run_e2e.sh)
#
# Usage:  ./m4_run.sh
# Env overrides: M4_CONC (12) M4_DURATION (240) M4_FACTOR_UP (10)
# =============================================================================
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS=/workspace/dynamo/logs
OUT="$BASE/m4_run"
mkdir -p "$LOGS" "$OUT"
exec > >(tee "$OUT/run.log") 2>&1

PYTHON_BIN=/opt/conda/bin/python
CTRL=http://localhost:9091/engine/control
FE=http://localhost:9090
CONC=${M4_CONC:-12}
DURATION=${M4_DURATION:-240}
FACTOR_UP=${M4_FACTOR_UP:-10}

PASS=1
note() { printf '\n===== %s =====\n' "$*"; }
fail() { echo "FAIL: $*"; PASS=0; }
state() { curl -s -m 10 -X POST "$CTRL/parallel_strategy_state" -H 'Content-Type: application/json' -d '{}'; }
state_field() { state | "$PYTHON_BIN" -c "import sys,json; print(json.load(sys.stdin).get('$1'))" 2>/dev/null; }

# ------------------------------------------------------- 0. prereq: service up
note "0. prereq: live Dynamo service at 4x1"
if [[ -f "$LOGS/backend.pid" ]] && ! kill -0 "$(cat "$LOGS/backend.pid")" 2>/dev/null; then
  echo "FATAL: backend pid file exists but process is dead. Start the service first:"
  echo "  $BASE/../start_dynamo_infinicore.sh   (or run ./run_e2e.sh for the full boot-at-2x2 E2E)"
  exit 1
fi
ST=$(state) || true
echo "$ST"
[[ "$ST" == *'"status"'* ]] || {
  echo "FATAL: control plane :9091 not answering. Start the service first:"
  echo "  $BASE/../start_dynamo_infinicore.sh"; exit 1; }
TP0=$(state_field tensor_parallel_size)
[[ "$TP0" == "4" ]] && echo "worker currently at 4x1 — good starting point" \
  || echo "NOTE: worker currently at tp=$TP0 (expected 4); pre-switch below will still put it at 2x2"

MODEL=$(curl -s -m 10 "$FE/v1/models" | "$PYTHON_BIN" -c 'import sys,json; d=json.load(sys.stdin)["data"]; print(d[0]["id"] if d else "")' 2>/dev/null)
[[ -n "$MODEL" ]] || { echo "FATAL: no model discovered at frontend :9090"; exit 1; }
echo "model=$MODEL"

# --------------------------------------------- 1. manual pre-switch down to 2x2
note "1. manual pre-switch 4x1 -> 2x2 (idle)"
RESP=$(curl -s -m 600 -X POST "$CTRL/switch_parallel_strategy" -H 'Content-Type: application/json' \
  -d '{"new_world_size": 4, "target_tensor_parallel_size": 2, "target_pipeline_parallel_size": 2, "request_handling": "wait", "admission_handling": "queue"}')
echo "$RESP"
echo "$RESP" | grep -q '"status": *"ok"' || fail "pre-switch to 2x2 did not report ok"
[[ "$(state_field tensor_parallel_size)" == "2" && "$(state_field pipeline_parallel_size)" == "2" ]] \
  || fail "state endpoint does not report 2x2 after pre-switch"

# --------------------------------------------------- 2. metric exposed?
note "2. controller signal exists on :9090/metrics"
curl -s -m 10 "$FE/metrics" | grep -m1 '^dynamo_frontend_active_requests' \
  || fail "dynamo_frontend_active_requests not exposed on :9090/metrics"

# ------------------------------------------- 3. restart frontend WITH controller
note "3. restart frontend with controller env (target 4x1, threshold $FACTOR_UP x 1)"
if [[ -f "$LOGS/frontend.pid" ]] && kill -0 "$(cat "$LOGS/frontend.pid")" 2>/dev/null; then
  kill "$(cat "$LOGS/frontend.pid")"; sleep 5
  kill -9 "$(cat "$LOGS/frontend.pid")" 2>/dev/null || true
fi
: > "$LOGS/frontend.log"
cd /workspace/dynamo
export PATH=/opt/conda/bin:$PATH
DYN_ELASTIC_SWITCH_ENABLE=1 \
DYN_ELASTIC_SWITCH_WORKER_URLS=http://localhost:9091 \
DYN_ELASTIC_SWITCH_EXPECTED_WORKERS=1 \
DYN_ELASTIC_SWITCH_METRICS_URL=http://localhost:9090/metrics \
DYN_ELASTIC_SWITCH_POLL_INTERVAL_S=2 \
DYN_ELASTIC_SWITCH_STABLE_POLLS=3 \
DYN_ELASTIC_SWITCH_FACTOR_UP="$FACTOR_UP" \
DYN_ELASTIC_SWITCH_STRATEGY_UP=4x1 \
DYN_ELASTIC_SWITCH_COOLDOWN_S=600 \
  nohup "$PYTHON_BIN" -m dynamo.frontend --discovery-backend file --http-port 9090 >> "$LOGS/frontend.log" 2>&1 &
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

# ------------------------------------------------------------- 4. drive load
note "4. benchmark: conc=$CONC (threshold $FACTOR_UP), ${DURATION}s"
export M3_MODEL="$MODEL" M3_CONC="$CONC" M3_DURATION="$DURATION" M3_MAX_TOKENS=32
"$PYTHON_BIN" "$BASE/load_gen.py" > "$OUT/load_summary.json" 2> "$OUT/load_err.log" &
LOAD_PID=$!

# --------------------------------------------- 5. watch the auto-switch happen
note "5. poll state every 3s until tp=4 (controller must switch it — no manual step)"
SWITCHED=0
T0=$(date +%s)
while (( $(date +%s) - T0 < DURATION )); do
  sleep 3
  TP=$(state_field tensor_parallel_size)
  echo "  t=$(( $(date +%s) - T0 ))s tp=$TP"
  [[ "$TP" == "4" ]] && { SWITCHED=1; echo "  >>> worker reports tp=4 — controller switched it"; break; }
done
[[ $SWITCHED -eq 1 ]] || fail "controller never switched the worker back to tp=4"

note "6. controller log evidence"
grep -E "SWITCH UP|SWITCH DOWN|switching http|now at|campaign #|cooldown" "$LOGS/frontend.log" \
  || fail "no controller campaign lines in frontend.log"

# ------------------------------------------------- 7. cooldown: exactly one campaign
note "7. cooldown check: load still high, no second campaign within 30s"
sleep 30
CAMPAIGNS=$(grep -c "campaign #" "$LOGS/frontend.log")
echo "campaign lines so far: $CAMPAIGNS"
[[ "$CAMPAIGNS" == "1" ]] && echo "  cooldown holds (exactly 1 campaign)" \
  || fail "expected exactly 1 campaign during cooldown, got $CAMPAIGNS"

# ------------------------------------------------------------- 8. wait for load
note "8. wait for benchmark, check zero lost requests"
wait $LOAD_PID; LOAD_RC=$?
cat "$OUT/load_summary.json"; echo
ERR=$("$PYTHON_BIN" -c "import json;d=json.load(open('$OUT/load_summary.json'));print(d['err'])" 2>/dev/null || echo "?")
[[ "$ERR" == "0" && $LOAD_RC -eq 0 ]] || fail "benchmark lost requests (err=$ERR rc=$LOAD_RC)"

note "9. final state"
ST=$(state); echo "$ST"
[[ "$(state_field failed)" == "False" ]] || fail "worker reports failed=true after switch"

note "VERDICT"
if [[ $PASS -eq 1 ]]; then
  echo "M4_PASS: pre-switched to 2x2 -> conc=$CONC load -> controller auto-switched to 4x1 -> cooldown held -> 0 lost requests"
else
  echo "M4_FAIL: see FAIL lines above; logs: $LOGS/{backend,frontend}.log, $OUT/"
fi
echo "(service left running at 4x1; stop with: $BASE/../service_qwen3.8-27b.sh stop)"
exit $((1 - PASS))
