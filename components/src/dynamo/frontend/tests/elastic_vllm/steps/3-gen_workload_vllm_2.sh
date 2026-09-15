#!/bin/bash
# bench_driver.sh <strategy_tag: tp4-ic|tp2pp2-ic> <phase: calib|final>
# 顺序执行 workload 矩阵压测（infinicore 路线）
# 结果与日志: bench_results/infinicore/<strategy>/<phase>/
set -o pipefail
export PATH=/opt/conda/bin:$PATH
# bench client 插件：默认 infinicore（与服务端一致）；冒烟失败可 BENCH_PLUGIN=metax 覆盖
export VLLM_PLUGINS="${BENCH_PLUGIN:-infinicore}"
export MACA_PATH=/opt/maca-3.8.0 MACA_HOME=/opt/maca-3.8.0 MACA_ROOT=/opt/maca-3.8.0

STRATEGY=${1:?usage: bench_driver.sh tp4-ic|tp2pp2-ic calib|final}
PHASE=${2:?usage: bench_driver.sh tp4-ic|tp2pp2-ic calib|final}
BASE=/workspace/dynamo/recipes/elastic-vllm/0920
MODEL=/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/
URL=http://localhost:9090
SEED=123
RESULT_DIR=$BASE/bench_results/infinicore/$STRATEGY/$PHASE
NUMPROMPTS_FILE=$BASE/bench_results/infinicore/num_prompts_final.txt
mkdir -p "$RESULT_DIR"
LOG=$RESULT_DIR/driver.log

log(){ echo "[$(date '+%F %T')] $*" >> "$LOG"; }

# workload 矩阵: 名称 输入长度 输出长度 并发
CONFIGS=(
"W1 2048 1024 4"
"W1 2048 1024 32"
"W1 2048 1024 64"
"W2 512 2048 4"
"W2 512 2048 32"
"W2 512 2048 64"
"W3 8192 256 4"
"W3 8192 256 32"
"W3 8192 256 64"
)

# calib 阶段跳过已完成的配置（空格分隔的 NAME 列表）
SKIP_CALIB="${SKIP_CALIB:-}"

log "===== DRIVER START strategy=$STRATEGY phase=$PHASE plugin=$VLLM_PLUGINS pid=$$ ====="

for cfg in "${CONFIGS[@]}"; do
  read -r W IN OUT C <<< "$cfg"
  NAME="${W}_i${IN}_o${OUT}_c${C}"

  if [[ $PHASE == calib ]]; then
    if [[ " $SKIP_CALIB " == *" $NAME "* ]]; then log "SKIP $NAME (already calibrated)"; continue; fi
    if [[ $C -ge 32 ]]; then NP=$C; else NP=$((2*C)); fi
  else
    NP=$(awk -v n="$NAME" '$1==n {print $2}' "$NUMPROMPTS_FILE" 2>/dev/null | head -1)
    if [[ -z $NP ]]; then log "SKIP $NAME (no entry in num_prompts_final.txt)"; continue; fi
  fi

  # 每轮前健康检查
  code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/health" 2>/dev/null)
  if [[ $code != 200 ]]; then log "ABORT: service unhealthy (HTTP $code) before $NAME"; exit 1; fi

  log "START $NAME num_prompts=$NP conc=$C"
  t0=$(date +%s)
  vllm bench serve --backend openai-chat --base-url "$URL" --endpoint /v1/chat/completions \
    --model "$MODEL" --tokenizer "$MODEL" \
    --dataset-name random --random-input-len "$IN" --random-output-len "$OUT" \
    --num-prompts "$NP" --max-concurrency "$C" --request-rate inf --seed "$SEED" \
    --save-result --result-dir "$RESULT_DIR" --result-filename "${NAME}.json" \
    > "$RESULT_DIR/${NAME}.stdout" 2>&1
  rc=$?
  t1=$(date +%s)
  if [[ $rc -eq 0 && -f "$RESULT_DIR/${NAME}.json" ]]; then
    dur=$(grep -o '"duration": [0-9.]*' "$RESULT_DIR/${NAME}.json" | head -1 | awk '{print $2}')
    log "END $NAME rc=0 wall=$((t1-t0))s bench_duration=${dur}s"
  else
    log "END $NAME rc=$rc wall=$((t1-t0))s (FAILED, see ${NAME}.stdout)"
  fi
done
log "===== PHASE $PHASE COMPLETE for $STRATEGY ====="
