#!/bin/bash
# =============================================================================
# STEP 3/4 (manual run) — generate the workload: closed-loop benchmark with
# CONC concurrent chat completions against the frontend (:9090).
#
# conc=20 > threshold 10 -> the controller MUST fire ~6s after this starts
# (3 consecutive polls x 2s).  Runs in the BACKGROUND; results land in
# /tmp/load_summary.json when it finishes (~DURATION seconds).
#
# IMPORTANT: this script also cross-checks the live concurrency gauge ~10s in.
# If the gauge reads 1 instead of ~CONC, the load is serialized and the
# controller will (correctly) never switch — that is a load-generator problem,
# not a controller problem.
#
# Prereq: step1 + step2 done. Model id is read from /tmp/e2e_model_id
#         (written by step2); override with M3_MODEL=...
# Overrides: M3_CONC (20) M3_DURATION (200) M3_MAX_TOKENS (32)
# =============================================================================
set -uo pipefail
export PATH=/opt/conda/bin:$PATH
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # the e2e/ dir with load_gen.py

MODEL_ID="${M3_MODEL:-$(cat /tmp/e2e_model_id 2>/dev/null || true)}"
[[ -n "$MODEL_ID" ]] || { echo "FATAL: no model id — run step2 first or set M3_MODEL"; exit 1; }
CONC=${M3_CONC:-20}
DURATION=${M3_DURATION:-200}
echo "model=$MODEL_ID conc=$CONC duration=${DURATION}s"

# --- start load in background -------------------------------------------------
M3_MODEL="$MODEL_ID" M3_CONC="$CONC" M3_DURATION="$DURATION" M3_MAX_TOKENS="${M3_MAX_TOKENS:-32}" \
  python3 "$BASE/load_gen.py" > /tmp/load_summary.json 2>/tmp/load_err.log &
LOAD_PID=$!
echo "load generator pid=$LOAD_PID (summary -> /tmp/load_summary.json)"

# --- cross-check the gauge the controller reads (~10s in) ----------------------
sleep 10
GAUGE=$(curl -s -m 5 http://localhost:9090/metrics | grep -m1 '^dynamo_frontend_active_requests')
echo "live gauge: $GAUGE"
VAL=$(echo "$GAUGE" | awk '{print $NF}')
if (( $(echo "$VAL < $CONC / 2" | bc -l 2>/dev/null || echo 0) )); then
  echo "WARNING: gauge ($VAL) far below conc ($CONC) — load may be serialized;"
  echo "         controller will not fire. Check engine log: grep 'Running:' /workspace/dynamo/logs/backend.log"
fi

echo
echo "Load is running for ~${DURATION}s. Watch the switch NOW with: ./4-check-parallelism.sh"
echo "(load summary appears in /tmp/load_summary.json when it finishes;"
echo " 'wait $LOAD_PID' in THIS shell blocks until then — but then you lose the live view)"
