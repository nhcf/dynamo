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
export VLLM_PLUGINS=infinicore
export VLLM_DISTRIBUTED_EXECUTOR_BACKEND=mp
export VLLM_SERVER_DEV_MODE=1  # Enable switch_parallel_strategy API routes
export VLLM_INFINICORE_GDN_SINGLE_STAGE=1  # C550-compatible GDN kernel configuration
export MACA_PATH="${MACA_PATH:-/opt/maca-3.8.0}"
export MACA_HOME="$MACA_PATH"
export MACA_ROOT="$MACA_PATH"

# Resolve FLASH_ATTN_2_CUDA_SO dynamically to avoid the plugin's
# nonexistent Python 3.12 default.
PYTHON_BIN="${PYTHON_BIN:-/opt/conda/bin/python}"
if [[ -z "${FLASH_ATTN_2_CUDA_SO:-}" ]]; then
    FLASH_ATTN_2_CUDA_SO="$("$PYTHON_BIN" - <<'PY'
import importlib.util
from pathlib import Path
spec = importlib.util.find_spec("flash_attn_2_cuda")
if spec is None or spec.origin is None:
    raise SystemExit("flash_attn_2_cuda is not installed for the selected Python")
library = Path(spec.origin).resolve()
if not library.is_file() or library.suffix != ".so":
    raise SystemExit(f"FlashAttention shared library not found: {library}")
print(library)
PY
)"
    export FLASH_ATTN_2_CUDA_SO
fi

# Dynamo 端口规划：
#   FRONTEND_PORT  — 前端 OpenAI 兼容 API（/v1/chat/completions 等）
#   CONTROL_PORT   — 后端控制面（/engine/control/switch_parallel_strategy 等）
FRONTEND_PORT=9090
CONTROL_PORT=9091
SERVICE_URL="http://localhost:${FRONTEND_PORT}"
CONTROL_URL="http://localhost:${CONTROL_PORT}"

# Logs & PID files — kept inside the recipe directory so the workspace root stays clean
LOG_DIR="${WORKSPACE_DIR}/logs"
LOG_FILE="${LOG_DIR}/backend.log"
FRONTEND_LOG="${LOG_DIR}/frontend.log"
PID_FILE="${LOG_DIR}/backend.pid"
FRONTEND_PID_FILE="${LOG_DIR}/frontend.pid"

# sync subcommand repo configuration (paths relative to WORKSPACE_DIR)
REPO_DYNAMO="dynamo"
BRANCH_DYNAMO="ElasticVllm"
URL_DYNAMO="https://github.com/nhcf/dynamo.git"
REPO_ELASTIC_VLLM_DEMO="ElasticVllm_demo"
BRANCH_ELASTIC_VLLM_DEMO="codex/add-v0.22.0"
# GitHub token for private repos — MUST be set before running sync
if [[ -z "${ELASTIC_VLLM_GITHUB_TOKEN:-}" ]]; then
    echo "WARNING: ELASTIC_VLLM_GITHUB_TOKEN is not set. 'sync' will fail with an error."
    echo "         Export it before running: export ELASTIC_VLLM_GITHUB_TOKEN=<your-token>"
fi

# conda site‑packages env for sync copy
export CONDA_SITE="/opt/conda/lib/python3.10/site-packages"
export VLLM_SITE="${CONDA_SITE}/vllm"
export DYNAMO_SITE="${CONDA_SITE}/dynamo"
export DYNAMO_VLLM_SITE="${DYNAMO_SITE}/vllm"

# Local patch files directory (lives next to this script, not in workspace root)
LOCAL_PATCH_DIR="${SCRIPT_DIR}/patches"
DISCOVERY_STORE="/tmp/dynamo_store_kv"

# Default common launch arguments for dynamo / vllm
COMMON_ARGS=(
    --model /mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/
    --distributed-executor-backend mp
    --gpu-memory-utilization 0.85
    --tensor_parallel_size 4
    --pipeline_parallel_size 1
    --tp-pp-switch-prebuild-strategies 4x1,2x2,1x4
    --tp-pp-switch-kv-transfer-window-size 2
    --tp-pp-switch-kv-transfer-max-scratch-size-mb 256
    --enforce-eager
)


# ===================== Helper Functions =====================

usage() {
cat << EOF
Usage: $0 <COMMAND> [COMMAND_ARGS] [GLOBAL_OPTIONS]

Commands:
    sync                     Git clone/pull repos & sync source files to conda site‑packages
    dynamo                   Start Dynamo service (backend + frontend)
    vllm                     Start native vllm openai api server
    stop                     Stop all running services (frontend + backend + residual processes)
    health                   Check service health endpoints

Global Options (valid for dynamo / vllm):
    -b, --background         Run in background mode, log output to files, store pids
    -h, --help               Show this help message

-------------------------------------------------------------------------------
[ sync subcommand ]
    No extra arguments. Perform git clone/pull, copy source code to conda
    site‑packages.

[ dynamo mode ]
    Starts two processes:
      1. dynamo.vllm   (backend worker, control plane on port ${CONTROL_PORT})
      2. dynamo.frontend (OpenAI API on port ${FRONTEND_PORT})
    Extra arguments:
      --discovery-backend      discovery backend, default=file
      --disaggregation-mode    disaggregation mode, default=agg

[ vllm mode ]
    Starts single vllm openai api server process.
    Extra arguments:
      --host                   listen host, default=0.0.0.0
      --port                   listen port, default=${FRONTEND_PORT}

[ dynamo / vllm common override arguments ]
    Any standard vllm argument can be overridden, example:
      $0 vllm --tensor_parallel_size 2 --gpu-memory-utilization 0.7
      $0 dynamo --model /mnt/nanhuinfer/models/Qwen3-1.5B

Examples:
    # Sync source code
    $0 sync

    # Foreground start vllm
    $0 vllm

    # Background start dynamo, override model and gpu‑memory‑utilization
    $0 dynamo --background --model /mnt/nanhuinfer/models/Qwen3-1.5B --gpu-memory-utilization 0.4

    # Stop all services
    $0 stop

    # Health check
    $0 health

Port layout (dynamo mode):
  ${FRONTEND_PORT}  — Frontend OpenAI API  (v1/chat/completions, v1/models, ...)
  ${CONTROL_PORT}   — Backend control plane (/engine/control/switch_parallel_strategy, ...)

Port layout (vllm mode):
  ${FRONTEND_PORT}  — vLLM OpenAI API (all endpoints including control)

Environment:
  VLLM_PLUGINS=${VLLM_PLUGINS}  (fixed, avoids metax/infinicore plugin conflict)
  ELASTIC_VLLM_GITHUB_TOKEN  (required for 'sync' with private repos)
  Execution dir (CWD): ${WORKSPACE_DIR}/
  Script dir:          ${SCRIPT_DIR}/
  Log dir:             ${LOG_DIR}/
  PID files:           ${PID_FILE}, ${FRONTEND_PID_FILE}
  Patch dir:           ${LOCAL_PATCH_DIR}/
EOF
}

# ---------- Sync subcommand ----------
cmd_sync() {
    set -euo pipefail

    # Pre-flight: private repo requires token, fail fast instead of hanging on git clone
    if [[ -z "${ELASTIC_VLLM_GITHUB_TOKEN:-}" ]]; then
        echo "ERROR: ELASTIC_VLLM_GITHUB_TOKEN is not set."
        echo "       ElasticVllm_demo is a private repo and requires a GitHub token for cloning."
        echo "       Export it before running sync:"
        echo "         export ELASTIC_VLLM_GITHUB_TOKEN=<your-token>"
        echo "       Then re-run: ./service.sh sync"
        exit 1
    fi

    function git_clone_or_pull {
        local repo_dir="$1"
        local branch="$2"
        local repo_url="$3"
        if [ -d "${repo_dir}/.git" ]; then
            echo ">>> ${repo_dir} exist, run git pull"
            pushd "${repo_dir}" >/dev/null
            git pull
            popd >/dev/null
        else
            echo ">>> ${repo_dir} not exist, run git clone --depth 1 -b ${branch}"
            git clone --depth 1 -b "${branch}" "${repo_url}" "${repo_dir}"
        fi
    }

    local ws="${WORKSPACE_DIR}"
    local URL_ELASTIC_VLLM_DEMO="https://Limixxx:${ELASTIC_VLLM_GITHUB_TOKEN}@github.com/yhp49/ElasticVllm_demo.git"
    git_clone_or_pull "${ws}/${REPO_DYNAMO}" "${BRANCH_DYNAMO}" "${URL_DYNAMO}"
    git_clone_or_pull "${ws}/${REPO_ELASTIC_VLLM_DEMO}" "${BRANCH_ELASTIC_VLLM_DEMO}" "${URL_ELASTIC_VLLM_DEMO}"

    echo -e "\n>>> copy ${REPO_ELASTIC_VLLM_DEMO}/vllm to ${VLLM_SITE}"
    cp -rf "${ws}/${REPO_ELASTIC_VLLM_DEMO}/vllm/"* "${VLLM_SITE}/"

    echo -e "\n>>> copy ${REPO_DYNAMO}/components/src/dynamo/vllm to ${DYNAMO_VLLM_SITE}"
    cp -rf "${ws}/${REPO_DYNAMO}/components/src/dynamo/vllm/"* "${DYNAMO_VLLM_SITE}/"

    echo -e "\n>>> sync completed!"

    # Apply local patches (fixes not yet merged into upstream repos)
    apply_local_patches
}

# ---------- Apply local .patch files to conda site‑packages ----------
apply_local_patches() {
    if [[ ! -d "${LOCAL_PATCH_DIR}" ]] || [[ -z "$(ls -A "${LOCAL_PATCH_DIR}"/*.patch 2>/dev/null)" ]]; then
        echo ">>> No local patches found in ${LOCAL_PATCH_DIR}/, skip"
        return 0
    fi

    echo ">>> Applying local patches from ${LOCAL_PATCH_DIR}/"
    for pf in "${LOCAL_PATCH_DIR}"/*.patch; do
        echo "    Applying $(basename "${pf}")"
        # Strip level 1 and apply inside site‑packages; --force rejects if already applied
        if patch -p1 -d "${CONDA_SITE}" --force --quiet --reverse --dry-run < "${pf}" &>/dev/null; then
            echo "        Already applied, skip"
        elif patch -p1 -d "${CONDA_SITE}" --force --quiet --forward < "${pf}"; then
            echo "        ✅ Applied successfully"
        else
            echo "        ❌ FAILED — manual intervention required"
            return 1
        fi
    done

    # Verify critical imports
    python -c "from vllm.exceptions import VLLMClientError, VLLMNotFoundError, VLLMUnprocessableEntityError" 2>/dev/null
    if [[ $? -eq 0 ]]; then
        echo ">>> Patch verification OK"
    else
        echo "WARNING: Patch verification failed — some imports may not work"
    fi
}

# ---------- Kill all service‑related processes ----------
kill_all_services() {
    echo ">>> Stopping all service processes..."

    # 1. Stop via PID files (graceful then force)
    for pf in "${PID_FILE}" "${FRONTEND_PID_FILE}"; do
        if [[ -f "${pf}" ]]; then
            local pid
            pid=$(cat "${pf}")
            if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
                echo "  Killing PID ${pid} ($(basename "${pf}"))"
                kill "${pid}" 2>/dev/null
                sleep 2
                kill -9 "${pid}" 2>/dev/null
            fi
            rm -f "${pf}"
        fi
    done

    # 2. Find and kill any remaining vllm/dynamo/VLLM processes
    local remaining
    remaining=$(ps aux | grep -E "(dynamo\.vllm|dynamo\.frontend|vllm\.entrypoints\.openai\.api_server|VLLM::Worker|VLLM::EngineCore)" | grep -v grep | awk '{print $2}')
    if [[ -n "${remaining}" ]]; then
        echo "  Found residual processes: ${remaining}"
        echo "${remaining}" | xargs kill -9 2>/dev/null
        sleep 3
    fi

    # 3. Verify all gone
    remaining=$(ps aux | grep -E "(dynamo\.vllm|dynamo\.frontend|vllm\.entrypoints\.openai\.api_server|VLLM::Worker|VLLM::EngineCore)" | grep -v grep | awk '{print $2}')
    if [[ -z "${remaining}" ]]; then
        echo "  All service processes stopped"
    else
        echo "  WARNING: Some processes could not be killed: ${remaining}"
    fi

    # 4. Clean discovery store to avoid stale state on restart
    if [[ -d "${DISCOVERY_STORE}" ]]; then
        echo "  Cleaning discovery store: ${DISCOVERY_STORE}"
        rm -rf "${DISCOVERY_STORE}"
    fi

    echo ">>> Stop complete"
}

# ---------- Stop subcommand ----------
cmd_stop() {
    kill_all_services
}

# ---------- Health subcommand ----------
cmd_health() {
    if ! command -v curl &> /dev/null; then
        echo "ERROR: curl command not found"
        exit 1
    fi

    local ok=0

    # Check frontend
    local fe_resp
    fe_resp=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${FRONTEND_PORT}/health" 2>/dev/null)
    if [[ "${fe_resp}" == "200" ]]; then
        echo "✅ Frontend (port ${FRONTEND_PORT}): healthy"
        ok=1
    else
        echo "❌ Frontend (port ${FRONTEND_PORT}): not responding (HTTP ${fe_resp})"
    fi

    # Check control plane (dynamo mode only)
    # /health returns HTTP 503 when notready, 200 when ready
    local ctrl_http_code
    ctrl_http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${CONTROL_PORT}/health" 2>/dev/null)
    if [[ "${ctrl_http_code}" == "200" ]]; then
        echo "✅ Control plane (port ${CONTROL_PORT}): healthy"
        ok=1
    elif [[ "${ctrl_http_code}" == "503" ]]; then
        echo "⏳ Control plane (port ${CONTROL_PORT}): initializing (503 notready)"
        ok=1
    else
        echo "⏭️  Control plane (port ${CONTROL_PORT}): not available (normal for vllm mode)"
    fi

    if [[ ${ok} -eq 0 ]]; then
        echo ""
        echo "No healthy endpoints. Run '$0 dynamo --background' or '$0 vllm --background' to start."
        exit 1
    fi
}

# ===================== Main Argument Parsing =====================
BACKGROUND=0
MODE=""
USER_OVERRIDE_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        stop|--stop)
            cmd_stop
            exit 0
            ;;
        -b|--background)
            BACKGROUND=1
            shift
            ;;
        sync)
            cmd_sync
            exit 0
            ;;

        health)
            cmd_health
            exit 0
            ;;
        dynamo|vllm)
            MODE="$1"
            shift
            ;;
        --*)
            USER_OVERRIDE_ARGS+=("$1")
            # Check if next arg is a value (not another flag)
            if [[ $# -gt 1 && "$2" != --* ]]; then
                USER_OVERRIDE_ARGS+=("$2")
                shift 2
            else
                shift
            fi
            ;;
        *)
            echo "ERROR: unknown argument: $1"
            usage
            exit 1
            ;;
    esac
done

# Validate start mode
if [[ "${MODE}" != "dynamo" && "${MODE}" != "vllm" ]]; then
    echo "ERROR: Must specify command dynamo / vllm, or subcommand sync / health / stop"
    usage
    exit 1
fi

# Prevent duplicate background instance
if [[ ${BACKGROUND} -eq 1 ]]; then
    # Ensure log directory exists
    mkdir -p "${LOG_DIR}"
    for pf in "${PID_FILE}" "${FRONTEND_PID_FILE}"; do
        if [[ -f "${pf}" ]]; then
            EXIST_PID=$(cat "${pf}")
            if kill -0 "${EXIST_PID}" 2>/dev/null; then
                echo "WARNING: Service already running pid=${EXIST_PID} ($(basename "${pf}")), run '$0 stop' first"
                exit 1
            else
                echo "Stale pid file detected, remove ${pf}"
                rm -f "${pf}"
            fi
        fi
    done
fi

# Merge arguments: base args first, user override args append later (vllm takes latter value)
FINAL_ARGS=(
    "${COMMON_ARGS[@]}"
    "${USER_OVERRIDE_ARGS[@]}"
)

# ===================== Start Services =====================

if [[ "${MODE}" == "dynamo" ]]; then
    # ---- Dynamo mode: backend + frontend ----
    echo "===== Starting Dynamo service ====="

    # Clean discovery store to avoid stale state
    if [[ -d "${DISCOVERY_STORE}" ]]; then
        echo ">>> Cleaning discovery store: ${DISCOVERY_STORE}"
        rm -rf "${DISCOVERY_STORE}"
    fi

    # --- Start backend (dynamo.vllm) ---
    echo ">>> Starting backend (dynamo.vllm) — control plane on port ${CONTROL_PORT}"
    echo "    Final merged arguments: ${FINAL_ARGS[*]}"
    export DYN_SYSTEM_PORT="${CONTROL_PORT}"

    CMD_BACKEND=(
        python -m dynamo.vllm
        --discovery-backend file
        --disaggregation-mode agg
        "${FINAL_ARGS[@]}"
    )

    if [[ ${BACKGROUND} -eq 1 ]]; then
        > "${LOG_FILE}"
        nohup "${CMD_BACKEND[@]}" >> "${LOG_FILE}" 2>&1 &
        BACKEND_PID=$!
        echo "${BACKEND_PID}" > "${PID_FILE}"
        echo "    Backend PID: ${BACKEND_PID}, log: ${LOG_FILE}"

        # Wait for backend to become ready
        echo ">>> Waiting for backend to initialize..."
        local_wait=0
        while [[ ${local_wait} -lt 180 ]]; do
            # Check control-plane route directly (health may report notready before routes registered)
            ctrl_resp=$(curl -s -X POST "http://localhost:${CONTROL_PORT}/engine/control/parallel_strategy_state" \
                -H "Content-Type: application/json" -d '{}' 2>/dev/null)
            if echo "${ctrl_resp}" | jq -e '.status' &>/dev/null; then
                echo "    ✅ Backend is ready (port ${CONTROL_PORT})"
                break
            fi
            # Also check /health as fallback
            if curl -s -o /dev/null -w '%%{http_code}' "http://localhost:${CONTROL_PORT}/health" 2>/dev/null | grep -q '200'; then
                echo "    ✅ Backend health check passed (port ${CONTROL_PORT})"
                break
            fi
            sleep 5
            local_wait=$((local_wait + 5))
            # Check if process died
            if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
                echo "    ❌ Backend process exited unexpectedly. Check ${LOG_FILE}"
                tail -20 "${LOG_FILE}"
                exit 1
            fi
            echo "    ... waiting (${local_wait}s)"
        done

        if [[ ${local_wait} -ge 180 ]]; then
            echo "    ⚠️  Backend did not become ready within 180s. Check ${LOG_FILE}"
        fi
    else
        echo "    (foreground mode — backend will run in this terminal)"
        exec "${CMD_BACKEND[@]}"
    fi

    # --- Start frontend (dynamo.frontend) ---
    echo ">>> Starting frontend (dynamo.frontend) — OpenAI API on port ${FRONTEND_PORT}"
    CMD_FRONTEND=(
        python -m dynamo.frontend
        --discovery-backend file
        --http-port "${FRONTEND_PORT}"
    )

    if [[ ${BACKGROUND} -eq 1 ]]; then
        > "${FRONTEND_LOG}"
        nohup "${CMD_FRONTEND[@]}" >> "${FRONTEND_LOG}" 2>&1 &
        FRONTEND_PID_VAL=$!
        echo "${FRONTEND_PID_VAL}" > "${FRONTEND_PID_FILE}"
        echo "    Frontend PID: ${FRONTEND_PID_VAL}, log: ${FRONTEND_LOG}"

        # Wait for frontend to become ready
        echo ">>> Waiting for frontend to initialize..."
        local_wait=0
        while [[ ${local_wait} -lt 60 ]]; do
            if curl -s -o /dev/null "http://localhost:${FRONTEND_PORT}/health" 2>/dev/null; then
                echo "    ✅ Frontend is ready (port ${FRONTEND_PORT})"
                break
            fi
            sleep 3
            local_wait=$((local_wait + 3))
            if ! kill -0 "${FRONTEND_PID_VAL}" 2>/dev/null; then
                echo "    ❌ Frontend process exited unexpectedly. Check ${FRONTEND_LOG}"
                tail -20 "${FRONTEND_LOG}"
                exit 1
            fi
        done

        if [[ ${local_wait} -ge 60 ]]; then
            echo "    ⚠️  Frontend did not become ready within 60s. Check ${FRONTEND_LOG}"
        fi

        echo ""
        echo "===== Dynamo service started ====="
        echo "  Frontend (OpenAI API): http://localhost:${FRONTEND_PORT}"
        echo "  Control plane:         http://localhost:${CONTROL_PORT}"
        echo "  Log dir:      ${LOG_DIR}/"
        echo "  Backend PID:  ${BACKEND_PID}"
        echo "  Frontend PID: ${FRONTEND_PID_VAL}"
        echo ""
        echo "Quick test:"
        echo "  curl http://localhost:${FRONTEND_PORT}/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"/mnt/nanhuinfer/models/Qwen3-0.6B/\",\"messages\":[{\"role\":\"user\",\"content\":\"Hi\"}]}'"
        echo "  curl -X POST http://localhost:${CONTROL_PORT}/engine/control/parallel_strategy_state -H 'Content-Type: application/json' -d '{}'"
    else
        exec "${CMD_FRONTEND[@]}"
    fi

else
    # ---- vLLM native mode ----
    echo "===== Starting vLLM OpenAI API server on port ${FRONTEND_PORT} ====="
    echo "    Final merged arguments: ${FINAL_ARGS[*]}"

    CMD=(
        python -m vllm.entrypoints.openai.api_server
        --host 0.0.0.0
        --port "${FRONTEND_PORT}"
        "${FINAL_ARGS[@]}"
    )

    if [[ ${BACKGROUND} -eq 1 ]]; then
        > "${LOG_FILE}"
        nohup "${CMD[@]}" >> "${LOG_FILE}" 2>&1 &
        PID=$!
        echo "${PID}" > "${PID_FILE}"
        echo "    Service PID: ${PID}, log: ${LOG_DIR}/backend.log"

        # Wait for service to become ready
        echo ">>> Waiting for vLLM to initialize..."
        local_wait=0
        while [[ ${local_wait} -lt 180 ]]; do
            if curl -s -o /dev/null "http://localhost:${FRONTEND_PORT}/health" 2>/dev/null; then
                echo "    ✅ vLLM is ready (port ${FRONTEND_PORT})"
                break
            fi
            sleep 5
            local_wait=$((local_wait + 5))
            if ! kill -0 "${PID}" 2>/dev/null; then
                echo "    ❌ vLLM process exited unexpectedly. Check ${LOG_FILE}"
                tail -20 "${LOG_FILE}"
                exit 1
            fi
            echo "    ... waiting (${local_wait}s)"
        done

        if [[ ${local_wait} -ge 180 ]]; then
            echo "    ⚠️  vLLM did not become ready within 180s. Check ${LOG_FILE}"
        fi

        echo ""
        echo "===== vLLM service started ====="
        echo "  OpenAI API: http://localhost:${FRONTEND_PORT}"
        echo "  Log dir:  ${LOG_DIR}/"
        echo "  PID:  ${PID}"
    else
        echo "    (foreground mode)"
        exec "${CMD[@]}"
    fi