#!/bin/bash
set -o pipefail

# ===================== Path Resolution =====================
# This script lives at <workspace>/dynamo/recipes/elastic-vllm/service.sh
# but is always executed from <workspace>/ (the dynamo project root's parent).
# SCRIPT_DIR  — where this script resides (for recipe‑local resources like patches)
# WORKSPACE_DIR — the workspace root (parent of the dynamo project), computed from
#                  script location so it works regardless of CWD.
#                  Script path: <WORKSPACE_DIR>/dynamo/recipes/elastic-vllm/service.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# ===================== Configuration Constants =====================
export VLLM_PLUGINS=metax
export VLLM_DISTRIBUTED_EXECUTOR_BACKEND=mp
export VLLM_SERVER_DEV_MODE=1  # Enable switch_parallel_strategy API routes

# Dynamo 端口规划：
#   FRONTEND_PORT  — 前端 OpenAI 兼容 API（/v1/chat/completions 等）
#   CONTROL_PORT   — 后端控制面（/engine/control/switch_parallel_strategy 等）
FRONTEND_PORT=9090
CONTROL_PORT=9091
SERVICE_URL="http://localhost:${FRONTEND_PORT}"
CONTROL_URL="http://localhost:${CONTROL_PORT}"
TOKENIZER="/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/"
SERVED_MODEL_NAME="Qwen3.8-27B"
GSP_SYSTEM_PROMPT_LEN=1024
GSP_QUESTION_LEN=1024
GSP_OUTPUT_LEN=1024
MAX_CONCURRENCY=32
SEED=123


# conda site‑packages env for sync copy
export CONDA_SITE="/opt/conda/lib/python3.10/site-packages"
vllm bench serve \
    --backend openai-chat \
    --base-url "$SERVICE_URL" \
    --model "$SERVED_MODEL_NAME" \
    --tokenizer "$TOKENIZER" \
    --dataset-name random \
    --random-input-len $((GSP_SYSTEM_PROMPT_LEN + GSP_QUESTION_LEN)) \
    --random-output-len $GSP_OUTPUT_LEN \
    --max-concurrency $MAX_CONCURRENCY \
    --warmup-requests 0 \
    --seed $SEED \
    $EXTRA_ARGS 
