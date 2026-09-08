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

# Logs & PID files — kept inside the recipe directory so the workspace root stays clean
LOG_DIR="${SCRIPT_DIR}/logs"
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
    --model /mnt/nanhuinfer/models/Qwen3-0.6B/
    --distributed-executor-backend mp
    --gpu-memory-utilization 0.85
    --tensor_parallel_size 4
    --pipeline_parallel_size 1
    --tp-pp-switch-prebuild-strategies 4x1,2x2,1x4
    --tp-pp-switch-kv-transfer-window-size 2
    --tp-pp-switch-kv-transfer-max-scratch-size-mb 256
    --enforce-eager
)

# Default switch subcommand payload fields, keep json key naming consistent
SWITCH_new_world_size=4
SWITCH_target_tensor_parallel_size=1
SWITCH_target_pipeline_parallel_size=4
SWITCH_target_num_blocks=null
SWITCH_request_handling="wait"
SWITCH_admission_handling="queue"

# ===================== Helper Functions =====================

usage() {
cat << EOF
Usage: $0 <COMMAND> [COMMAND_ARGS] [GLOBAL_OPTIONS]

Commands:
    sync                     Git clone/pull repos & sync source files to conda site‑packages
    dynamo                   Start Dynamo service (backend + frontend)
    vllm                     Start native vllm openai api server
    status                   Query current parallel strategy state
    switch                   Trigger parallel‑strategy switch request
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

[ switch subcommand specific arguments (json field name 1:1 mapping) ]
    --new_world_size                 default=4
    --target_tensor_parallel_size    default=1
    --target_pipeline_parallel_size  default=4
    --target_num_blocks              default=null
    --request_handling               default="wait"
    --admission_handling             default="queue"

Examples:
    # Sync source code
    $0 sync

    # Foreground start vllm
    $0 vllm

    # Background start dynamo, override model and gpu‑memory‑utilization
    $0 dynamo --background --model /mnt/nanhuinfer/models/Qwen3-1.5B --gpu-memory-utilization 0.4

    # Query parallel strategy state
    $0 status

    # Trigger switch with custom parameters
    $0 switch \\
        --new_world_size 4 \\
        --target_tensor_parallel_size 2 \\
        --target_pipeline_parallel_size 2 \\
        --request_handling wait \\
        --admission_handling queue

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

# ---------- Status subcommand ----------
cmd_status() {
    if ! command -v curl &> /dev/null; then
        echo "ERROR: curl command not found"
        exit 1
    fi

    # Try control plane first (dynamo mode), then frontend (vllm mode)
    local url="${CONTROL_URL}/engine/control/parallel_strategy_state"
    local resp
    local http_code
    http_code=$(curl -s -o /tmp/dynamo_status_resp -w "%{http_code}" -X POST "${url}" \
        -H "Content-Type: application/json" -d '{}' 2>/dev/null)
    resp=$(cat /tmp/dynamo_status_resp 2>/dev/null)
    rm -f /tmp/dynamo_status_resp

    if [[ -n "${resp}" ]] && echo "${resp}" | jq -e '.status' &>/dev/null 2>&1; then
        echo ">> [Dynamo Control Plane] ${url}"
        echo "${resp}" | jq .
        return 0
    fi

    # If control plane returns 503, backend is still initializing
    if [[ "${http_code}" == "503" ]]; then
        echo "⚠️  Control plane returned 503 — backend is still initializing. Please wait."
        echo "   Check progress: tail -f ${LOG_FILE}"
        return 1
    fi

    # Fallback: try vllm native endpoint
    url="${SERVICE_URL}/is_switching_parallel_strategy"
    resp=$(curl -s -X GET "${url}" 2>/dev/null)
    if [[ -n "${resp}" ]]; then
        echo ">> [vLLM Native] ${url}"
        echo "${resp}" | jq . 2>/dev/null || echo "${resp}"
        return 0
    fi

    echo "ERROR: No service endpoint reachable. Is the service running?"
    echo "  Tried: ${CONTROL_URL}/engine/control/parallel_strategy_state"
    echo "  Tried: ${SERVICE_URL}/is_switching_parallel_strategy"
    exit 1
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

# ---------- Switch subcommand ----------
cmd_switch() {
    if ! command -v curl &> /dev/null; then
        echo "ERROR: curl command not found"
        exit 1
    fi

    json_payload=$(cat <<JSON
{
  "new_world_size": ${SWITCH_new_world_size},
  "target_tensor_parallel_size": ${SWITCH_target_tensor_parallel_size},
  "target_pipeline_parallel_size": ${SWITCH_target_pipeline_parallel_size},
  "target_num_blocks": ${SWITCH_target_num_blocks},
  "request_handling": "${SWITCH_request_handling}",
  "admission_handling": "${SWITCH_admission_handling}"
}
JSON
)

    # Try control plane first (dynamo mode), then vllm native
    local url="${CONTROL_URL}/engine/control/switch_parallel_strategy"
    local resp
    resp=$(curl -s -X POST "${url}" \
        -H "Content-Type: application/json" \
        -d "${json_payload}" 2>/dev/null)

    if [[ -n "${resp}" ]] && echo "${resp}" | jq -e '.status' &>/dev/null 2>&1; then
        echo ">> [Dynamo Control Plane] ${url}"
        echo ">> Payload:"
        echo "${json_payload}" | jq .
        echo ">> Response:"
        echo "${resp}" | jq .
        return 0
    fi

    # Fallback: vllm native
    url="${SERVICE_URL}/switch_parallel_strategy"
    resp=$(curl -s -X POST "${url}" \
        -H "Content-Type: application/json" \
        -d "${json_payload}" 2>/dev/null)

    if [[ -n "${resp}" ]]; then
        echo ">> [vLLM Native] ${url}"
        echo ">> Payload:"
        echo "${json_payload}" | jq .
        echo ">> Response:"
        echo "${resp}" | jq . 2>/dev/null || echo "${resp}"
        return 0
    fi

    echo "ERROR: No service endpoint reachable for switch. Is the service running?"
    exit 1
}

# Parse switch subcommand arguments, argument name exactly match api json key
parse_switch_overrides() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --new_world_size)
                SWITCH_new_world_size="$2"; shift 2;;
            --target_tensor_parallel_size)
                SWITCH_target_tensor_parallel_size="$2"; shift 2;;
            --target_pipeline_parallel_size)
                SWITCH_target_pipeline_parallel_size="$2"; shift 2;;
            --target_num_blocks)
                SWITCH_target_num_blocks="$2"; shift 2;;
            --request_handling)
                SWITCH_request_handling="$2"; shift 2;;
            --admission_handling)
                SWITCH_admission_handling="$2"; shift 2;;
            *)
                echo "ERROR: unknown switch argument: $1"
                usage
                exit 1;;
        esac
    done
    cmd_switch
    exit 0
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

        status)
            cmd_status
            exit 0
            ;;
        health)
            cmd_health
            exit 0
            ;;
        switch)
            shift
            parse_switch_overrides "$@"
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
    echo "ERROR: Must specify command dynamo / vllm, or subcommand sync / status / switch / health / stop"
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
fi