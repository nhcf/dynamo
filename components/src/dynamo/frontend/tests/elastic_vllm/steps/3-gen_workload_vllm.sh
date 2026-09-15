#!/bin/bash
# =============================================================================
# STEP 3/4 (manual run), vllm-bench variant — generate the workload with
# `vllm bench serve`: concurrency=32, isl=8192, osl=256, num_prompts=256,
# against the frontend (:9090).
#
# Flag conventions copied from 0920/bench_driver.sh (the proven-on-this-box
# workload matrix driver; its W3 config is exactly i8192 o256 c32):
#   --backend openai-chat --endpoint /v1/chat/completions --seed 123
#   NO --random-range-ratio  => default 0.0 => EXACT lengths.
#   (verified in site-packages vllm/benchmarks/datasets/utils.py:
#    get_sampling_params draws from [len*(1-ratio), len*(1+ratio)], so the
#    default 0.0 pins isl/osl; bench_driver's W3 results confirm: mean input
#    8244 = 8192 + ~52 chat-template tokens, every output exactly 256.)
#   NO --ignore-eos needed: the reproducible random-token prompts never emit
#   EOS early (W3: total_output == num_prompts*256 exactly).
#
# conc=32 > threshold 10 -> the controller MUST fire ~6s after the gauge
# ramps (3 consecutive polls x 2s).  Runs in the BACKGROUND (so you can run
# 4-check-parallelism.sh while it drives); console + JSON result land in
# /tmp/vllm_bench/.
#
# Same gauge cross-check as 3-gen_worload.sh: if
# dynamo_frontend_active_requests stays far below 32 while this runs, the
# load is not reaching the frontend and the controller will (correctly) not
# switch — check /tmp/vllm_bench/*.stdout, not the controller.
#
# Prereq: step1 + step2 done.  Model id is read from /tmp/e2e_model_id
#         (written by step2); override with M3_MODEL=...
# Overrides: VB_CONC (32) VB_ISL (8192) VB_OSL (256) VB_NUM_PROMPTS (256)
#            VB_SEED (123) VB_FE (http://localhost:9090) BENCH_PLUGIN (infinicore)
# =============================================================================
set -uo pipefail
export PATH=/opt/conda/bin:$PATH
# bench client env, same as bench_driver.sh:
#  - VLLM_PLUGINS: without it the vllm CLI aborts ("Only one platform plugin
#    can be activated, but got: ['infinicore', 'metax']").
#  - MACA_HOME: the fork's patched torch/utils/cpp_extension.py does
#    os.path.exists(MACA_HOME) unconditionally; unset => even --help dies
#    with TypeError(stat: ... NoneType).
export VLLM_PLUGINS="${BENCH_PLUGIN:-infinicore}"
export MACA_PATH=/opt/maca-3.8.0 MACA_HOME=/opt/maca-3.8.0 MACA_ROOT=/opt/maca-3.8.0

MODEL_ID="${M3_MODEL:-$(cat /tmp/e2e_model_id 2>/dev/null || true)}"
[[ -n "$MODEL_ID" ]] || { echo "FATAL: no model id — run step2 first or set M3_MODEL"; exit 1; }

CONC=${VB_CONC:-32}
ISL=${VB_ISL:-8192}
OSL=${VB_OSL:-256}
NUM_PROMPTS=${VB_NUM_PROMPTS:-256}
SEED=${VB_SEED:-123}
FE=${VB_FE:-http://localhost:9090}
RESULT_DIR=/tmp/vllm_bench
NAME="i${ISL}_o${OSL}_c${CONC}_n${NUM_PROMPTS}"
mkdir -p "$RESULT_DIR"

# health check before the round (same as bench_driver.sh)
code=$(curl -s -o /dev/null -w '%{http_code}' "$FE/health" 2>/dev/null)
[[ "$code" == "200" ]] || { echo "FATAL: frontend unhealthy (HTTP ${code:-no-response}) at $FE"; exit 1; }

echo "model=$MODEL_ID conc=$CONC isl=$ISL osl=$OSL num_prompts=$NUM_PROMPTS seed=$SEED -> $FE"

# --- start vllm bench serve in the background --------------------------------
nohup vllm bench serve --backend openai-chat --base-url "$FE" --endpoint /v1/chat/completions \
  --model "$MODEL_ID" --tokenizer "$MODEL_ID" \
  --dataset-name random --random-input-len "$ISL" --random-output-len "$OSL" \
  --num-prompts "$NUM_PROMPTS" --max-concurrency "$CONC" --request-rate inf --seed "$SEED" \
  --save-result --result-dir "$RESULT_DIR" --result-filename "${NAME}.json" --realtime-interval 1 \
  > "$RESULT_DIR/${NAME}.stdout" 2>&1 &
BENCH_PID=$!
echo "vllm bench pid=$BENCH_PID (console -> $RESULT_DIR/${NAME}.stdout, result -> $RESULT_DIR/${NAME}.json)"

# --- cross-check the gauge the controller reads (~20s in; 8k prefills ramp slower) ---
sleep 20
if ! kill -0 "$BENCH_PID" 2>/dev/null; then
  echo "FATAL: vllm bench died early; last lines:"
  tail -20 "$RESULT_DIR/${NAME}.stdout"
  exit 1
fi
GAUGE=$(curl -s -m 5 "$FE/metrics" | grep -m1 '^dynamo_frontend_active_requests')
echo "live gauge: $GAUGE"
VAL=$(echo "$GAUGE" | awk '{print $NF}')
if [[ -n "$VAL" ]] && (( $(echo "$VAL < $CONC / 2" | bc -l) )); then
  echo "WARNING: gauge ($VAL) far below conc ($CONC) — check $RESULT_DIR/${NAME}.stdout"
fi

echo
echo "Bench running: ${NUM_PROMPTS} prompts x isl${ISL}/osl${OSL} @ conc${CONC}"
echo "  (long prefills — expect many minutes; tail -f $RESULT_DIR/${NAME}.stdout)"
echo "Watch the switch NOW with: ./4-check-parallelism.sh"
