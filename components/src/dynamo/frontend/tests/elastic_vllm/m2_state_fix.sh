#!/bin/bash
# M2 — verify the worker state fix (F4) on the box.
#
# Acceptance (from IMPL-PLAN §9):
#   parallel_strategy_state reports the NEW tp/pp after a manual switch,
#   and the switch success payload itself reports is_switching=false.
#
# Run from the remote box. Read-only against the running service except for
# the two switches (4x1 -> 2x2 -> 4x1), which is exactly what M2 is for.
set -uo pipefail

CTRL=http://localhost:9091/engine/control
FE=http://localhost:9090

say() { printf '\n===== %s =====\n' "$*"; }

state() {
  curl -s -m 10 -X POST "$CTRL/parallel_strategy_state" \
    -H 'Content-Type: application/json' -d '{}'
}

switch() { # $1=tp $2=pp $3=world
  local t0 t1 body
  t0=$(date +%s.%N)
  body=$(curl -s -m 600 -X POST "$CTRL/switch_parallel_strategy" \
    -H 'Content-Type: application/json' \
    -d "{\"new_world_size\": $3, \"target_tensor_parallel_size\": $1, \"target_pipeline_parallel_size\": $2, \"request_handling\": \"wait\", \"admission_handling\": \"queue\"}")
  t1=$(date +%s.%N)
  echo "$body"
  echo "elapsed_s: $(echo "$t1 $t0" | awk '{printf "%.1f", $1-$2}')"
}

say "0. processes"
ps aux | grep -E 'dynamo\.(vllm|frontend)' | grep -v grep | awk '{print $2, $11, $12, $13}'

say "1. models visible at frontend"
curl -s -m 10 "$FE/v1/models" | head -c 600; echo

MODEL=$(curl -s -m 10 "$FE/v1/models" | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null)
echo "model=$MODEL"

say "2. inference before any switch"
curl -s -m 120 "$FE/v1/chat/completions" -H 'Content-Type: application/json' \
  -d "{\"model\": \"$MODEL\", \"messages\": [{\"role\": \"user\", \"content\": \"Say OK\"}], \"max_tokens\": 8}" \
  | head -c 400; echo

say "3. initial state (expect tp=4 pp=1 is_switching=false failed=false)"
state; echo

say "4. switch 4x1 -> 2x2 (expect status ok AND tp=2 pp=2 is_switching=false IN THE PAYLOAD)"
switch 2 2 4

say "5. state after switch (expect tp=2 pp=2 — the F4 fix; old code said tp=4 pp=1 here)"
state; echo

say "6. inference after switch"
curl -s -m 120 "$FE/v1/chat/completions" -H 'Content-Type: application/json' \
  -d "{\"model\": \"$MODEL\", \"messages\": [{\"role\": \"user\", \"content\": \"Say OK\"}], \"max_tokens\": 8}" \
  | head -c 400; echo

say "7. switch back 2x2 -> 4x1"
switch 4 1 4

say "8. final state (expect tp=4 pp=1)"
state; echo

say "9. negative check: pause_routing must be boolean-validated"
curl -s -m 10 -X POST "$CTRL/switch_parallel_strategy" \
  -H 'Content-Type: application/json' \
  -d '{"new_world_size": 4, "target_tensor_parallel_size": 2, "target_pipeline_parallel_size": 2, "pause_routing": "yes"}'; echo

say "M2 DONE"
