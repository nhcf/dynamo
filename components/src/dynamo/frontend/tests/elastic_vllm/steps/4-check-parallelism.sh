#!/bin/bash
# =============================================================================
# STEP 4/4 (manual run) — check whether the parallelism actually switched.
#
# Run this WHILE the step3 load is running (within ~10s of starting it).
# Decision latency ~6s (3 polls x 2s), switch duration ~27s, so:
#   t≈6s    frontend.log shows "SWITCH UP -> 4x1: concurrency 20 > 10 for 3 polls"
#   t≈7-35s state endpoint shows is_switching=true
#   t≈35s   state endpoint shows tp=4 pp=1, is_switching=false, failed=false
#
# This script polls the state endpoint every 2s until tp=4 (max ~DURATION),
# then prints the controller log evidence and the benchmark result.
#
# Prereq: step3 load running (or just finished).
# Overrides: C4_TIMEOUT (260) — seconds to keep polling.
# =============================================================================
set -uo pipefail
export PATH=/opt/conda/bin:$PATH
LOGS=/workspace/dynamo/logs
CTRL=http://localhost:9091/engine/control
TIMEOUT=${C4_TIMEOUT:-260}

state() { curl -s -m 10 -X POST "$CTRL/parallel_strategy_state" -H 'Content-Type: application/json' -d '{}'; }
field() { state | python -c "import sys,json; print(json.load(sys.stdin).get('$1'))" 2>/dev/null; }

echo ">>> polling state every 2s (expect is_switching=true, then tp=4 pp=1)"
SWITCHED=0; SEEN_SWITCHING=0; T0=$(date +%s)
while (( $(date +%s) - T0 < TIMEOUT )); do
  ST=$(state)
  TP=$(echo "$ST" | python -c 'import sys,json;print(json.load(sys.stdin).get("tensor_parallel_size"))' 2>/dev/null)
  PP=$(echo "$ST" | python -c 'import sys,json;print(json.load(sys.stdin).get("pipeline_parallel_size"))' 2>/dev/null)
  SW=$(echo "$ST" | python -c 'import sys,json;print(json.load(sys.stdin).get("is_switching"))' 2>/dev/null)
  echo "  t=$(( $(date +%s) - T0 ))s tp=$TP pp=$PP is_switching=$SW"
  [[ "$SW" == "True" ]] && SEEN_SWITCHING=1
  if [[ "$TP" == "4" && "$PP" == "1" ]]; then
    SWITCHED=1; echo "  >>> SWITCHED: state endpoint reports tp=4 pp=1"; break
  fi
  sleep 2
done

echo
echo ">>> controller decision + campaign lines (frontend.log):"
grep -E "SWITCH UP|SWITCH DOWN|switching http|now at|campaign #|cooldown" "$LOGS/frontend.log" || echo "  (none — controller never fired; check the gauge in step3)"

echo
echo ">>> final state:"
state; echo

if [[ $SWITCHED -eq 1 ]]; then
  [[ $SEEN_SWITCHING -eq 1 ]] && echo "(observed is_switching=true mid-flight)" \
    || echo "(NOTE: never observed is_switching=true — poll too coarse or switch too fast)"
  echo "SWITCH CHECK: PASS"
else
  echo "SWITCH CHECK: FAIL — worker never reached tp=4 pp=1 within ${TIMEOUT}s"
fi

echo
echo ">>> after the benchmark finishes (~200s from step3 start), verify zero lost requests:"
echo "    cat /tmp/load_summary.json    # 'err' must be 0, 'ok' == 'total'"
if [[ -s /tmp/load_summary.json ]]; then
  echo "    (summary already present:)"; cat /tmp/load_summary.json; echo
fi

echo
echo ">>> optional cooldown check: keep load high 30s more, then"
echo "    grep -c 'campaign #' $LOGS/frontend.log    # must stay 1"
echo
echo "Done. Stop the service with: /workspace/dynamo/recipes/elastic-vllm/0920/service_qwen3.8-27b.sh stop"
[[ $SWITCHED -eq 1 ]] || exit 1
