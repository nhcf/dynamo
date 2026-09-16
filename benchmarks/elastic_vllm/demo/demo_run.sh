#!/bin/bash
set -euo pipefail

# demo_run.sh — 演示编排器
# 读取 demo_scenario.yaml，驱动通用工具执行演示
# TUI 是唯一显示界面，从最开始就启动，所有输出通过 events 文件传递到 TUI
#
# 用法:
#   demo/demo_run.sh [options]
#
# 参数:
#   --scenario        场景 YAML 文件路径（默认 demo/demo_scenario.yaml）
#   --service-script  service.sh 脚本路径（默认自动推断）
#   --output-dir      输出目录（默认 demo_output）
#   --skip-start      跳过服务启动（服务已运行时使用）
#   --skip-stop       演示结束后不停止服务
#   -h|--help         显示帮助

# ===================== CLI Parsing =====================
SCENARIO=""
SERVICE_SCRIPT=""
OUTPUT_DIR="demo_output"
SKIP_START=0
SKIP_STOP=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --scenario)       SCENARIO="$2"; shift 2;;
        --service-script) SERVICE_SCRIPT="$2"; shift 2;;
        --output-dir)     OUTPUT_DIR="$2"; shift 2;;
        --skip-start)     SKIP_START=1; shift;;
        --skip-stop)      SKIP_STOP=1; shift;;
        -h|--help)
            sed -n '2,/^$/{ s/^# //; s/^#//; p }' "$0"
            exit 0;;
        *) echo "ERROR: unknown option: $1" >&2; exit 1;;
    esac
done

# ===================== Defaults =====================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Default scenario
: "${SCENARIO:=${SCRIPT_DIR}/demo_scenario.yaml}"

# Default service.sh path (relative to this script's location)
: "${SERVICE_SCRIPT:=${PROJECT_DIR}/../../components/src/dynamo/remp/tests/elastic_vllm/service.sh}"

# Resolve to absolute path
SERVICE_SCRIPT="$(cd "$(dirname "${SERVICE_SCRIPT}")" && pwd)/$(basename "${SERVICE_SCRIPT}")"

# Output directory
mkdir -p "${OUTPUT_DIR}"
OUTPUT_DIR="$(cd "${OUTPUT_DIR}" && pwd)"

# Log directory (where service.sh writes logs)
SERVICE_DIR="$(dirname "${SERVICE_SCRIPT}")"
WORKSPACE_DIR="$(cd "${SERVICE_DIR}/../../../../../../.." && pwd)"
LOG_DIR="${WORKSPACE_DIR}/logs"

# ===================== Prepare Output Files =====================
# Must be done BEFORE TUI starts so TUI can read them from the beginning

EVENTS_FILE="${OUTPUT_DIR}/events.log"
LOAD_INFO_FILE="${OUTPUT_DIR}/current_load.json"
RUNNER_LOG="${OUTPUT_DIR}/runner.log"
STATS_FILE="${OUTPUT_DIR}/load_stats.jsonl"
REPORT_FILE="${OUTPUT_DIR}/report.json"

> "$STATS_FILE"
> "$EVENTS_FILE"
> "$RUNNER_LOG"

# ===================== Event helpers =====================
# These write to EVENTS_FILE (for TUI) and/or RUNNER_LOG

emit_event() {
    # Write a timestamped event to the events file (TUI reads this)
    local msg="[$(date +%H:%M:%S)] $1"
    echo "$msg" >> "$EVENTS_FILE"
}

log_debug() {
    # Write debug info to runner log only (NOT shown in TUI)
    echo "[$(date +%H:%M:%S)] [DEBUG] $1" >> "$RUNNER_LOG"
}

write_load_info() {
    # Atomically write current_load.json for TUI to read
    local tmp_file="${LOAD_INFO_FILE}.tmp"
    local phase_name="$1" idx="$2" total="$3" status="$4"
    local input_len="${5:-0}" output_len="${6:-0}" conc="${7:-0}" tag="${8:-}" duration="${9:-0}"
    printf '{"phase_name":%s,"phase_idx":%d,"total_phases":%d,"status":%s,"input_len":%d,"output_len":%d,"conc":%d,"tag":%s,"duration":%d}\n' \
        "$(printf '%s' "$phase_name" | jq -R .)" "$idx" "$total" "$(printf '%s' "$status" | jq -R .)" \
        "$input_len" "$output_len" "$conc" "$(printf '%s' "$tag" | jq -R .)" "$duration" \
        > "$tmp_file"
    mv "$tmp_file" "$LOAD_INFO_FILE"
}

clear_load_info() {
    rm -f "$LOAD_INFO_FILE"
}

# ===================== TUI lifecycle =====================

TUI_PID=""
TUI_PGID=""
SHUTDOWN_FILE=""
STDOUT_SAVED=""

start_tui() {
    # Start TUI in a new process group so we can kill the entire tree
    SHUTDOWN_FILE="${OUTPUT_DIR}/.tui_shutdown"
    rm -f "$SHUTDOWN_FILE"

    setsid python3 "${SCRIPT_DIR}/demo_tui.py" \
        --fe-url http://localhost:9090 \
        --ctrl-url http://localhost:9091 \
        --log-file "${LOG_DIR}/frontend.log" \
        --stats-file "$STATS_FILE" \
        --events-file "$EVENTS_FILE" \
        --load-info "$LOAD_INFO_FILE" \
        --interval 2 \
        --up-threshold 0 \
        --down-threshold 0 \
        &
    TUI_PID=$!
    # The PGID is the same as PID when using setsid
    TUI_PGID=$TUI_PID

    # Give TUI a moment to start and take over the terminal
    sleep 2

    # Save stdout fd and redirect all shell output to runner log
    exec 3>&1
    STDOUT_SAVED=1
    exec > "${RUNNER_LOG}" 2>&1
}

stop_tui() {
    # 1. Signal TUI to exit gracefully via shutdown file
    if [[ -n "$SHUTDOWN_FILE" ]]; then
        touch "$SHUTDOWN_FILE"
    fi

    # 2. Wait for TUI to exit on its own (up to 5s)
    if [[ -n "${TUI_PID:-}" ]] && kill -0 "$TUI_PID" 2>/dev/null; then
        local wait_s=0
        while [[ $wait_s -lt 5 ]] && kill -0 "$TUI_PID" 2>/dev/null; do
            sleep 1
            wait_s=$((wait_s + 1))
        done
    fi

    # 3. If still alive, kill the entire process group
    if [[ -n "${TUI_PGID:-}" ]] && kill -0 "$TUI_PID" 2>/dev/null; then
        kill -- -"$TUI_PGID" 2>/dev/null || true
        sleep 1
    fi

    # 4. Force kill if still alive
    if [[ -n "${TUI_PID:-}" ]] && kill -0 "$TUI_PID" 2>/dev/null; then
        kill -9 -- -"$TUI_PGID" 2>/dev/null || true
        kill -9 "$TUI_PID" 2>/dev/null || true
        sleep 0.5
    fi

    # 5. Wait to reap zombie
    if [[ -n "${TUI_PID:-}" ]]; then
        wait "$TUI_PID" 2>/dev/null || true
    fi

    # 6. Clean up
    rm -f "$SHUTDOWN_FILE"
    TUI_PID=""
    TUI_PGID=""

    # 7. Restore terminal settings
    if [[ "$STDOUT_SAVED" == "1" ]]; then
        exec 1>&3 3>&-
        STDOUT_SAVED=""
    fi
    # Reset terminal to sane state (in case TUI left it in raw mode)
    stty sane 2>/dev/null || true
    # Clear any residual TUI output from terminal
    tput reset 2>/dev/null || true
}

cleanup_on_exit() {
    # Called by trap on EXIT/INT/TERM — ensure TUI is fully cleaned up
    stop_tui
}

# Set up traps BEFORE starting TUI
trap cleanup_on_exit EXIT
trap 'trap - EXIT; cleanup_on_exit; exit 130' INT
trap 'trap - EXIT; cleanup_on_exit; exit 143' TERM

# ===================== Start TUI FIRST =====================
# TUI starts before any other work, so users see ALL events from the beginning.

emit_event "[RUN] ═══ Elastic vLLM Demo ═══"
emit_event "[RUN] Scenario: $(basename "${SCENARIO}")"
emit_event "[RUN] Output: ${OUTPUT_DIR}"

# Write initial load info: init phase (before service starts)
write_load_info "Initializing" -1 0 "waiting"

# Start TUI (sets up process group, redirect, traps)
start_tui

# ===================== Step 1: Parse Scenario YAML =====================
emit_event "[RUN] Parsing scenario file..."

SCENARIO_DATA=$(python3 - "$SCENARIO" <<'PYEOF'
import sys, yaml, json
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
print(json.dumps(data))
PYEOF
)

INITIAL_TOPO=$(echo "$SCENARIO_DATA" | jq -r '.service.initial_topology // "2x2"')
case "$INITIAL_TOPO" in
    2x2)  INIT_TP=2; INIT_PP=2;;
    4x1)  INIT_TP=4; INIT_PP=1;;
    1x4)  INIT_TP=1; INIT_PP=4;;
    *)    emit_event "[RUN] ERROR: unknown topology: $INITIAL_TOPO"; exit 1;;
esac

log_debug "Initial topology: ${INITIAL_TOPO} (tp=${INIT_TP}, pp=${INIT_PP})"

# ===================== Step 2: Apply Controller Overrides =====================
CONTROLLER_OVERRIDES=$(echo "$SCENARIO_DATA" | jq -r '.service.controller_overrides // empty')
if [[ -n "$CONTROLLER_OVERRIDES" && "$CONTROLLER_OVERRIDES" != "null" ]]; then
    log_debug "Applying controller overrides from scenario..."
    while IFS='=' read -r key value; do
        [[ -z "$key" ]] && continue
        export "$key=$value"
        log_debug "  $key=$value"
    done < <(echo "$CONTROLLER_OVERRIDES" | jq -r 'to_entries[] | "\(.key)=\(.value)"')
fi

# ===================== Step 2b: Apply Defaults =====================
: "${DYN_ELASTIC_SWITCH_ENABLE:=1}"
: "${DYN_ELASTIC_SWITCH_WORKER_URLS:=http://localhost:9091}"
: "${DYN_ELASTIC_SWITCH_EXPECTED_WORKERS:=1}"
: "${DYN_ELASTIC_SWITCH_METRICS_URL:=http://localhost:9090/metrics}"
: "${DYN_ELASTIC_SWITCH_POLL_INTERVAL_S:=2}"
: "${DYN_ELASTIC_SWITCH_STABLE_POLLS:=3}"
: "${DYN_ELASTIC_SWITCH_FACTOR_UP:=10}"
: "${DYN_ELASTIC_SWITCH_FACTOR_DOWN:=2}"
: "${DYN_ELASTIC_SWITCH_STRATEGY_UP:=4x1}"
: "${DYN_ELASTIC_SWITCH_STRATEGY_DOWN:=2x2}"
: "${DYN_ELASTIC_SWITCH_COOLDOWN_S:=30}"

export DYN_ELASTIC_SWITCH_ENABLE
export DYN_ELASTIC_SWITCH_WORKER_URLS
export DYN_ELASTIC_SWITCH_EXPECTED_WORKERS
export DYN_ELASTIC_SWITCH_METRICS_URL
export DYN_ELASTIC_SWITCH_POLL_INTERVAL_S
export DYN_ELASTIC_SWITCH_STABLE_POLLS
export DYN_ELASTIC_SWITCH_FACTOR_UP
export DYN_ELASTIC_SWITCH_FACTOR_DOWN
export DYN_ELASTIC_SWITCH_STRATEGY_UP
export DYN_ELASTIC_SWITCH_STRATEGY_DOWN
export DYN_ELASTIC_SWITCH_COOLDOWN_S

CTRL_URL="http://localhost:9091"

UP_THRESHOLD_ACTUAL=$(awk -v fu="$DYN_ELASTIC_SWITCH_FACTOR_UP" -v ew="$DYN_ELASTIC_SWITCH_EXPECTED_WORKERS" 'BEGIN { printf "%.0f", fu * ew }')
DOWN_THRESHOLD_ACTUAL=$(awk -v fd="$DYN_ELASTIC_SWITCH_FACTOR_DOWN" -v ew="$DYN_ELASTIC_SWITCH_EXPECTED_WORKERS" 'BEGIN { printf "%.0f", fd * ew }')

emit_event "[RUN] Controller: UP≥${UP_THRESHOLD_ACTUAL} DOWN≤${DOWN_THRESHOLD_ACTUAL} cool=${DYN_ELASTIC_SWITCH_COOLDOWN_S}s"
emit_event "[RUN] Initial topology: ${INITIAL_TOPO} (TP=${INIT_TP}, PP=${INIT_PP})"

# Update TUI threshold chart lines via a control file
echo "{\"up_threshold\":${UP_THRESHOLD_ACTUAL},\"down_threshold\":${DOWN_THRESHOLD_ACTUAL}}" \
    > "${OUTPUT_DIR}/thresholds.json"

# ===================== Step 3: Start Service =====================
if [[ $SKIP_START -eq 1 ]]; then
    emit_event "[RUN] Skipping service start (--skip-start)"
else
    emit_event "[RUN] Starting service (topology=${INITIAL_TOPO})..."
    write_load_info "Starting service" -1 0 "waiting"
    log_debug "DYN_ELASTIC_SWITCH_ENABLE=$DYN_ELASTIC_SWITCH_ENABLE"
    log_debug "DYN_ELASTIC_SWITCH_FACTOR_UP=$DYN_ELASTIC_SWITCH_FACTOR_UP"
    log_debug "DYN_ELASTIC_SWITCH_FACTOR_DOWN=$DYN_ELASTIC_SWITCH_FACTOR_DOWN"
    log_debug "DYN_ELASTIC_SWITCH_STRATEGY_UP=$DYN_ELASTIC_SWITCH_STRATEGY_UP"
    log_debug "DYN_ELASTIC_SWITCH_STRATEGY_DOWN=$DYN_ELASTIC_SWITCH_STRATEGY_DOWN"
    log_debug "DYN_ELASTIC_SWITCH_COOLDOWN_S=$DYN_ELASTIC_SWITCH_COOLDOWN_S"

    cd "${WORKSPACE_DIR}"
    bash "${SERVICE_SCRIPT}" remp --background \
        --tensor_parallel_size "${INIT_TP}" \
        --pipeline_parallel_size "${INIT_PP}" \
        >> "${RUNNER_LOG}" 2>&1
    cd "${PROJECT_DIR}"

    emit_event "[RUN] Service script launched, waiting for readiness..."

    # ===================== Step 4: Verify Service =====================
    write_load_info "Verifying service" -1 0 "waiting"
    local_wait=0
    SERVICES_READY=0
    while [[ $local_wait -lt 300 ]]; do
        health_output=$(cd "${WORKSPACE_DIR}" && bash "${SERVICE_SCRIPT}" health 2>&1) || true

        fe_ok=0; cp_ok=0
        echo "$health_output" | grep -q "Frontend.*healthy" && fe_ok=1
        echo "$health_output" | grep -q "Control plane.*healthy" && cp_ok=1

        if [[ $fe_ok -eq 1 && $cp_ok -eq 1 ]]; then
            emit_event "[RUN] ✓ Service ready (FE+CP healthy, ${local_wait}s)"
            SERVICES_READY=1
            break
        fi

        # Emit periodic progress events so TUI shows something is happening
        if (( local_wait % 15 == 0 )); then
            emit_event "[RUN] Waiting for service... (${local_wait}s)"
        fi

        sleep 5
        local_wait=$((local_wait + 5))
    done
    if [[ $SERVICES_READY -eq 0 ]]; then
        emit_event "[RUN] ✗ ERROR: Services not ready after 300s"
        stop_tui
        echo "ERROR: Services not ready after 300s" >&2
        exit 1
    fi

    # Verify elastic controller started
    if [[ "$DYN_ELASTIC_SWITCH_ENABLE" != "0" ]]; then
        emit_event "[RUN] Verifying elastic controller..."
        local_wait=0
        while [[ $local_wait -lt 30 ]]; do
            if [[ -f "${LOG_DIR}/frontend.log" ]] && \
               grep -q "\[TP/PP\] controller started" "${LOG_DIR}/frontend.log" 2>/dev/null; then
                emit_event "[RUN] ✓ Elastic controller started"
                break
            fi
            sleep 2
            local_wait=$((local_wait + 2))
        done
        if [[ $local_wait -ge 30 ]]; then
            emit_event "[RUN] ⚠ WARNING: Could not confirm elastic controller startup"
        fi
    fi

    # Verify initial topology
    topo_resp=$(curl -s -m 5 -X POST "${CTRL_URL}/engine/control/parallel_strategy_state" \
        -H 'Content-Type: application/json' -d '{}' 2>/dev/null || echo '{}')
    cur_tp=$(echo "$topo_resp" | jq -r '.tensor_parallel_size // "?"' 2>/dev/null)
    cur_pp=$(echo "$topo_resp" | jq -r '.pipeline_parallel_size // "?"' 2>/dev/null)
    if [[ "$cur_tp" == "?" || "$cur_tp" == "null" || "$cur_pp" == "?" || "$cur_pp" == "null" ]]; then
        emit_event "[RUN] ✗ ERROR: Cannot read initial topology"
        stop_tui
        echo "ERROR: Cannot read initial topology" >&2
        exit 1
    fi
    emit_event "[RUN] ✓ Current topology: TP=$cur_tp PP=$cur_pp"
fi

# ===================== Step 5: Ready — update TUI chart thresholds =====================
# Now that service is up, re-launch TUI with correct thresholds by writing a signal file
# The TUI will pick up the actual thresholds from thresholds.json on next poll
emit_event "[RUN] ✓ Demo ready, starting phases..."
clear_load_info

# ===================== Step 6: Execute Scenario Phases =====================
NUM_PHASES=$(echo "$SCENARIO_DATA" | jq '.phases | length')
DEMO_START=$(date +%s)

log_debug "Running ${NUM_PHASES} phases..."

# Track expectations for final report
declare -a EXPECT_RESULTS=()

for ((phase_idx=0; phase_idx<NUM_PHASES; phase_idx++)); do
    phase_name=$(echo "$SCENARIO_DATA" | jq -r ".phases[$phase_idx].name")
    phase_load=$(echo "$SCENARIO_DATA" | jq -r ".phases[$phase_idx].load // empty")
    phase_wait=$(echo "$SCENARIO_DATA" | jq -r ".phases[$phase_idx].wait // 0")
    phase_expects=$(echo "$SCENARIO_DATA" | jq -r ".phases[$phase_idx].expect // []")

    # Emit phase start event
    emit_event "[LOAD] ═══ Phase $((phase_idx + 1))/${NUM_PHASES}: ${phase_name} ═══"

    # Write current_load.json for TUI
    if [[ -n "$phase_load" && "$phase_load" != "null" ]]; then
        l_model=$(echo "$phase_load" | jq -r '.model // ""')
        l_input_len=$(echo "$phase_load" | jq -r '.input_len // 128')
        l_output_len=$(echo "$phase_load" | jq -r '.output_len // 32')
        l_conc=$(echo "$phase_load" | jq -r '.conc // 20')
        l_duration=$(echo "$phase_load" | jq -r '.duration // 60')
        l_tag=$(echo "$phase_load" | jq -r '.tag // ""')
        : "${l_model:=/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/}"

        write_load_info "$phase_name" "$phase_idx" "$NUM_PHASES" "loading" \
            "$l_input_len" "$l_output_len" "$l_conc" "$l_tag" "$l_duration"
        emit_event "[LOAD] Load: input=${l_input_len} output=${l_output_len} C=${l_conc} dur=${l_duration}s tag=${l_tag}"
    else
        write_load_info "$phase_name" "$phase_idx" "$NUM_PHASES" "waiting"
    fi

    # Snapshot current topology via control plane API
    phase_start_tp=$(curl -s -m 5 -X POST "${CTRL_URL}/engine/control/parallel_strategy_state" \
        -H 'Content-Type: application/json' -d '{}' 2>/dev/null | jq -r '.tensor_parallel_size // "?"')
    phase_start_pp=$(curl -s -m 5 -X POST "${CTRL_URL}/engine/control/parallel_strategy_state" \
        -H 'Content-Type: application/json' -d '{}' 2>/dev/null | jq -r '.pipeline_parallel_size // "?"')
    log_debug "Topology at phase start: ${phase_start_tp}x${phase_start_pp}"

    # ── Launch background topology monitor ──────────────────────────
    MONITOR_FILE="${OUTPUT_DIR}/topo_monitor_${phase_idx}.jsonl"
    MONITOR_MAX_S=0
    if [[ -n "$phase_expects" && "$phase_expects" != "null" && "$phase_expects" != "[]" ]]; then
        num_exp=$(echo "$phase_expects" | jq 'length')
        for ((ei=0; ei<num_exp; ei++)); do
            w=$(echo "$phase_expects" | jq -r ".[$ei].within_s // .[$ei].during_s // 0")
            [[ $w -gt $MONITOR_MAX_S ]] && MONITOR_MAX_S=$w
        done
        if [[ -n "$phase_load" && "$phase_load" != "null" ]]; then
            ld=$(echo "$phase_load" | jq -r '.duration // 0')
            [[ $ld -gt $MONITOR_MAX_S ]] && MONITOR_MAX_S=$ld
        fi
        [[ $phase_wait -gt $MONITOR_MAX_S ]] && MONITOR_MAX_S=$phase_wait
        MONITOR_MAX_S=$((MONITOR_MAX_S + 10))
    fi

    if [[ $MONITOR_MAX_S -gt 0 ]]; then
        rm -f "$MONITOR_FILE"
        (
            elapsed=0
            while [[ $elapsed -lt $MONITOR_MAX_S ]]; do
                cur_state=$(curl -s -m 5 -X POST "${CTRL_URL}/engine/control/parallel_strategy_state" \
                    -H 'Content-Type: application/json' -d '{}' 2>/dev/null || echo '{}')
                cur_tp=$(echo "$cur_state" | jq -r '.tensor_parallel_size // "?"')
                cur_pp=$(echo "$cur_state" | jq -r '.pipeline_parallel_size // "?"')
                cur_sw=$(echo "$cur_state" | jq -r 'if .is_switching == false then "false" elif .is_switching == true then "true" else "?" end')
                cur_topo="${cur_tp}x${cur_pp}"
                echo "{\"elapsed\":${elapsed},\"tp\":${cur_tp},\"pp\":${cur_pp},\"is_switching\":\"${cur_sw}\",\"topo\":\"${cur_topo}\"}" >> "$MONITOR_FILE"
                sleep 2
                elapsed=$((elapsed + 2))
            done
        ) &
        MONITOR_PID=$!
    else
        MONITOR_PID=
    fi

    # ── Run phase load ──────────────────────────────────────────────
    if [[ -n "$phase_load" && "$phase_load" != "null" ]]; then
        log_debug "Starting load: input=${l_input_len}, output=${l_output_len}, conc=${l_conc}, duration=${l_duration}s, tag=${l_tag}"

        python3 "${SCRIPT_DIR}/demo_load.py" \
            --model "$l_model" \
            --fe-url http://localhost:9090 \
            --conc "$l_conc" \
            --duration "$l_duration" \
            --input-len "$l_input_len" \
            --output-len "$l_output_len" \
            --window 5 \
            --tag "$l_tag" \
            >> "$STATS_FILE" 2>"${OUTPUT_DIR}/load_errors_${phase_idx}.log" &
        LOAD_PID=$!

        wait $LOAD_PID 2>/dev/null || true
        emit_event "[LOAD] ✓ Load completed: ${l_tag}"
    fi

    if [[ "$phase_wait" -gt 0 ]]; then
        log_debug "Waiting ${phase_wait}s..."
        # Emit periodic progress events during wait
        wait_elapsed=0
        while [[ $wait_elapsed -lt $phase_wait ]]; do
            chunk=$(( phase_wait - wait_elapsed < 15 ? phase_wait - wait_elapsed : 15 ))
            sleep "$chunk"
            wait_elapsed=$((wait_elapsed + chunk))
            if [[ $wait_elapsed -lt $phase_wait ]]; then
                emit_event "[LOAD] Waiting... ${wait_elapsed}s/${phase_wait}s"
            fi
        done
        emit_event "[LOAD] ✓ Wait completed (${phase_wait}s)"
    fi

    # ── Stop background monitor ─────────────────────────────────────
    if [[ -n "$MONITOR_PID" ]]; then
        kill "$MONITOR_PID" 2>/dev/null || true
        wait "$MONITOR_PID" 2>/dev/null || true
    fi

    # ── Check expectations from monitor data ────────────────────────
    if [[ -n "$phase_expects" && "$phase_expects" != "null" && "$phase_expects" != "[]" ]]; then
        num_expects=$(echo "$phase_expects" | jq 'length')
        for ((ei=0; ei<num_expects; ei++)); do
            exp_event=$(echo "$phase_expects" | jq -r ".[$ei].event")
            exp_within=$(echo "$phase_expects" | jq -r ".[$ei].within_s // 0")
            exp_during=$(echo "$phase_expects" | jq -r ".[$ei].during_s // 0")
            exp_tp=$(echo "$phase_expects" | jq -r ".[$ei].state.tp // 0")
            exp_pp=$(echo "$phase_expects" | jq -r ".[$ei].state.pp // 0")
            exp_not_switching=$(echo "$phase_expects" | jq -r "if .[$ei].state.is_switching == false then \"false\" elif .[$ei].state.is_switching == true then \"true\" else \"unset\" end")

            result="SKIP"
            detail=""

            case "$exp_event" in
                switch_up|switch_down)
                    if [[ -f "$MONITOR_FILE" ]]; then
                        switch_start=$(jq -r "select(.elapsed <= ${exp_within} and .is_switching == \"true\") | .elapsed" "$MONITOR_FILE" 2>/dev/null | head -1)
                        if [[ -n "$switch_start" ]]; then
                            result="PASS"
                            detail="Switch initiated at ${switch_start}s (within ${exp_within}s)"
                        else
                            change_line=$(jq -r "select(.elapsed <= ${exp_within} and .topo != \"${phase_start_tp}x${phase_start_pp}\") | .elapsed" "$MONITOR_FILE" 2>/dev/null | head -1)
                            if [[ -n "$change_line" ]]; then
                                change_topo=$(jq -r "select(.elapsed == ${change_line}) | .topo" "$MONITOR_FILE" 2>/dev/null | head -1)
                                result="PASS"
                                detail="Topology changed to ${change_topo} at ${change_line}s (within ${exp_within}s)"
                            else
                                result="FAIL"
                                detail="No switch within ${exp_within}s (still ${phase_start_tp}x${phase_start_pp})"
                            fi
                        fi
                    else
                        result="FAIL"
                        detail="Monitor data not available"
                    fi
                    ;;

                switch_complete)
                    if [[ -f "$MONITOR_FILE" ]]; then
                        match_conds=".elapsed <= ${exp_within}"
                        [[ "$exp_tp" != "0" ]] && match_conds="${match_conds} and .tp == ${exp_tp}"
                        [[ "$exp_pp" != "0" ]] && match_conds="${match_conds} and .pp == ${exp_pp}"
                        [[ "$exp_not_switching" == "false" ]] && match_conds="${match_conds} and .is_switching == \"false\""
                        match_line=$(jq -r "select(${match_conds}) | .elapsed" "$MONITOR_FILE" 2>/dev/null | head -1)
                        if [[ -n "$match_line" ]]; then
                            match_topo=$(jq -r "select(.elapsed == ${match_line}) | .topo" "$MONITOR_FILE" 2>/dev/null | head -1)
                            result="PASS"
                            detail="State ${match_topo} at ${match_line}s (within ${exp_within}s)"
                        else
                            last_topo=$(jq -r 'select(.elapsed <= '${exp_within}') | .topo' "$MONITOR_FILE" 2>/dev/null | tail -1)
                            result="FAIL"
                            detail="Target tp=${exp_tp} pp=${exp_pp} not reached (last: ${last_topo})"
                        fi
                    else
                        result="FAIL"
                        detail="Monitor data not available"
                    fi
                    ;;

                no_switch)
                    if [[ -f "$MONITOR_FILE" ]]; then
                        change_count=$(jq "select(.elapsed <= ${exp_during} and .topo != \"${phase_start_tp}x${phase_start_pp}\")" "$MONITOR_FILE" 2>/dev/null | jq -s 'length')
                        if [[ "$change_count" == "0" || -z "$change_count" ]]; then
                            result="PASS"
                            detail="Topology stable at ${phase_start_tp}x${phase_start_pp} for ${exp_during}s"
                        else
                            first_change=$(jq -r "select(.elapsed <= ${exp_during} and .topo != \"${phase_start_tp}x${phase_start_pp}\") | .elapsed" "$MONITOR_FILE" 2>/dev/null | head -1)
                            result="FAIL"
                            detail="Unexpected change at ${first_change}s"
                        fi
                    else
                        result="FAIL"
                        detail="Monitor data not available"
                    fi
                    ;;
            esac

            EXPECT_RESULTS+=("${phase_name}|${exp_event}|${result}|${detail}")
            emit_event "[EXPECT] ${result} ${exp_event}: ${detail}"
        done
    fi

    # Update load info to indicate phase done
    write_load_info "$phase_name" "$phase_idx" "$NUM_PHASES" "done"
done

# Clear load info after all phases
clear_load_info

# ===================== Step 7: Generate Report =====================
emit_event "[RUN] Generating report..."

DEMO_END=$(date +%s)
DEMO_DURATION=$((DEMO_END - DEMO_START))

# Build expectation results JSON
expect_json=$(printf '%s\n' "${EXPECT_RESULTS[@]}" | python3 -c '
import sys, json
results = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    parts = line.split("|", 3)
    if len(parts) == 4:
        results.append({
            "phase": parts[0],
            "event": parts[1],
            "result": parts[2],
            "detail": parts[3]
        })
print(json.dumps(results, indent=2))
')

# Collect final topology
final_topo=$(curl -s -m 5 -X POST "http://localhost:9091/engine/control/parallel_strategy_state" \
    -H 'Content-Type: application/json' -d '{}' 2>/dev/null || echo '{}')
final_tp=$(echo "$final_topo" | jq -r '.tensor_parallel_size // "?"' 2>/dev/null)
final_pp=$(echo "$final_topo" | jq -r '.pipeline_parallel_size // "?"' 2>/dev/null)

# Build report
cat > "$REPORT_FILE" <<EOF
{
  "scenario": "$(basename "${SCENARIO}")",
  "duration_s": ${DEMO_DURATION},
  "initial_topology": "${INITIAL_TOPO}",
  "final_topology": "${final_tp}x${final_pp}",
  "controller_config": {
    "factor_up": ${DYN_ELASTIC_SWITCH_FACTOR_UP},
    "factor_down": ${DYN_ELASTIC_SWITCH_FACTOR_DOWN},
    "cooldown_s": ${DYN_ELASTIC_SWITCH_COOLDOWN_S},
    "strategy_up": "${DYN_ELASTIC_SWITCH_STRATEGY_UP}",
    "strategy_down": "${DYN_ELASTIC_SWITCH_STRATEGY_DOWN}",
    "stable_polls": ${DYN_ELASTIC_SWITCH_STABLE_POLLS}
  },
  "expectations": ${expect_json},
  "stats_file": "${STATS_FILE}",
  "events_file": "${EVENTS_FILE}"
}
EOF

# Summary event
PASS_COUNT=$(echo "$expect_json" | jq '[.[] | select(.result=="PASS")] | length' 2>/dev/null || echo 0)
FAIL_COUNT=$(echo "$expect_json" | jq '[.[] | select(.result=="FAIL")] | length' 2>/dev/null || echo 0)
emit_event "[RUN] ═══ Demo Complete: ${PASS_COUNT} PASS, ${FAIL_COUNT} FAIL (${DEMO_DURATION}s) ═══"

# ── Keep TUI alive briefly to show final results ──────────────────
sleep 5

# ── Stop TUI and restore terminal ─────────────────────────────────
stop_tui

# Print minimal final summary to terminal
echo ""
echo "=== Elastic vLLM Demo Complete ==="
echo "  Duration: ${DEMO_DURATION}s"
echo "  Initial: ${INITIAL_TOPO}  Final: ${final_tp}x${final_pp}"
echo "  Expectations: ${PASS_COUNT} PASS, ${FAIL_COUNT} FAIL"
echo "  Report: ${REPORT_FILE}"
echo "  Full log: ${RUNNER_LOG}"
echo ""

# Optionally stop service
if [[ $SKIP_STOP -eq 0 ]]; then
    echo "Stopping service..."
    cd "${WORKSPACE_DIR}"
    bash "${SERVICE_SCRIPT}" stop >> "${RUNNER_LOG}" 2>&1 || true
    cd "${PROJECT_DIR}"
    echo "Service stopped."
fi

echo "Done."
