#!/bin/bash
set -euo pipefail

# demo_run.sh — 演示编排器
# 读取 demo_scenario.yaml，驱动通用工具执行演示
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
# This script: benchmarks/elastic_vllm/demo/demo_run.sh
# service.sh:  components/src/dynamo/remp/tests/elastic_vllm/service.sh
: "${SERVICE_SCRIPT:=${PROJECT_DIR}/../../components/src/dynamo/remp/tests/elastic_vllm/service.sh}"

# Resolve to absolute path
SERVICE_SCRIPT="$(cd "$(dirname "${SERVICE_SCRIPT}")" && pwd)/$(basename "${SERVICE_SCRIPT}")"

# Output directory
mkdir -p "${OUTPUT_DIR}"
OUTPUT_DIR="$(cd "${OUTPUT_DIR}" && pwd)"

# Log directory (where service.sh writes logs)
# service.sh sets LOG_DIR=${WORKSPACE_DIR}/logs, so we need to know WORKSPACE_DIR
# WORKSPACE_DIR is derived from service.sh's location
SERVICE_DIR="$(dirname "${SERVICE_SCRIPT}")"
WORKSPACE_DIR="$(cd "${SERVICE_DIR}/../../../../../../.." && pwd)"
LOG_DIR="${WORKSPACE_DIR}/logs"

# ===================== Step 0 (deferred): Elastic Controller Defaults =====================
# NOTE: Defaults are applied AFTER scenario overrides (Step 2) so that the
# priority order is: command-line env vars > scenario controller_overrides > defaults.
# The `:-` assignment only takes effect if the variable is still unset/empty.

echo "=== Elastic vLLM Demo Runner ==="
echo "Scenario:  ${SCENARIO}"
echo "Output:    ${OUTPUT_DIR}"
echo "Service:   ${SERVICE_SCRIPT}"
echo "Logs:      ${LOG_DIR}"
echo ""

# ===================== Step 1: Parse Scenario YAML =====================
echo ">>> Parsing scenario file..."

# Use Python to extract scenario fields (stdlib + pyyaml)
SCENARIO_DATA=$(python3 - "$SCENARIO" <<'PYEOF'
import sys, yaml, json

with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)

# Output as JSON for bash to consume
print(json.dumps(data))
PYEOF
)

# Extract initial topology
INITIAL_TOPO=$(echo "$SCENARIO_DATA" | jq -r '.service.initial_topology // "2x2"')
case "$INITIAL_TOPO" in
    2x2)  INIT_TP=2; INIT_PP=2;;
    4x1)  INIT_TP=4; INIT_PP=1;;
    1x4)  INIT_TP=1; INIT_PP=4;;
    *)    echo "ERROR: unknown topology: $INITIAL_TOPO" >&2; exit 1;;
esac

echo "    Initial topology: ${INITIAL_TOPO} (tp=${INIT_TP}, pp=${INIT_PP})"

# ===================== Step 2: Apply Scenario Controller Overrides =====================
CONTROLLER_OVERRIDES=$(echo "$SCENARIO_DATA" | jq -r '.service.controller_overrides // empty')
if [[ -n "$CONTROLLER_OVERRIDES" && "$CONTROLLER_OVERRIDES" != "null" ]]; then
    echo "    Applying controller overrides from scenario..."
    # Iterate over keys and export each
    while IFS='=' read -r key value; do
        [[ -z "$key" ]] && continue
        # Only set if not already set in environment (command-line takes priority)
        if [[ -z "${!key:-}" ]]; then
            export "$key=$value"
            echo "      $key=$value"
        else
            echo "      $key=${!key} (kept from env, scenario override ignored)"
        fi
    done < <(echo "$CONTROLLER_OVERRIDES" | jq -r 'to_entries[] | "\(.key)=\(.value)"')
fi

# ===================== Step 2b: Apply Defaults =====================
# Now apply defaults for anything still unset. `:-` only sets if empty/unset.
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

# Control plane URL (shared across steps)
CTRL_URL="http://localhost:9091"

# Compute actual concurrency thresholds for TUI display
# TUI needs the real concurrent-request count at which switching triggers,
# not the raw FACTOR multiplier. Threshold = FACTOR × EXPECTED_WORKERS.
UP_THRESHOLD_ACTUAL=$(awk -v fu="$DYN_ELASTIC_SWITCH_FACTOR_UP" -v ew="$DYN_ELASTIC_SWITCH_EXPECTED_WORKERS" 'BEGIN { printf "%.0f", fu * ew }')
DOWN_THRESHOLD_ACTUAL=$(awk -v fd="$DYN_ELASTIC_SWITCH_FACTOR_DOWN" -v ew="$DYN_ELASTIC_SWITCH_EXPECTED_WORKERS" 'BEGIN { printf "%.0f", fd * ew }')

# ===================== Step 3: Start Service =====================
if [[ $SKIP_START -eq 1 ]]; then
    echo ">>> Skipping service start (--skip-start)"
else
    echo ">>> Starting service..."
    echo "    DYN_ELASTIC_SWITCH_ENABLE=$DYN_ELASTIC_SWITCH_ENABLE"
    echo "    DYN_ELASTIC_SWITCH_FACTOR_UP=$DYN_ELASTIC_SWITCH_FACTOR_UP"
    echo "    DYN_ELASTIC_SWITCH_FACTOR_DOWN=$DYN_ELASTIC_SWITCH_FACTOR_DOWN"
    echo "    DYN_ELASTIC_SWITCH_STRATEGY_UP=$DYN_ELASTIC_SWITCH_STRATEGY_UP"
    echo "    DYN_ELASTIC_SWITCH_STRATEGY_DOWN=$DYN_ELASTIC_SWITCH_STRATEGY_DOWN"
    echo "    DYN_ELASTIC_SWITCH_COOLDOWN_S=$DYN_ELASTIC_SWITCH_COOLDOWN_S"
    echo ""

    # service.sh must be run from WORKSPACE_DIR
    cd "${WORKSPACE_DIR}"
    bash "${SERVICE_SCRIPT}" remp --background \
        --tensor_parallel_size "${INIT_TP}" \
        --pipeline_parallel_size "${INIT_PP}"
    cd "${PROJECT_DIR}"

    # ===================== Step 4: Verify Service =====================
    # Use `service.sh health` as the primary readiness signal.
    #   - Backend /health → 503 (initializing) / 200 (ready to serve)
    #   - Frontend /health → 200 (ready)
    # We require BOTH to report fully healthy before proceeding.
    # `service.sh health` considers 503 as "ok" (still booting), so we
    # parse its output to confirm the backend is truly healthy (not initializing).

    echo ">>> Verifying service readiness via health check..."
    local_wait=0
    SERVICES_READY=0
    while [[ $local_wait -lt 300 ]]; do
        health_output=$(cd "${WORKSPACE_DIR}" && bash "${SERVICE_SCRIPT}" health 2>&1) || true

        # Both frontend and control plane must report healthy (200),
        # not just "initializing" (503) or "not available".
        fe_ok=0; cp_ok=0
        echo "$health_output" | grep -q "Frontend.*healthy" && fe_ok=1
        echo "$health_output" | grep -q "Control plane.*healthy" && cp_ok=1

        if [[ $fe_ok -eq 1 && $cp_ok -eq 1 ]]; then
            echo "    ✅ Frontend healthy"
            echo "    ✅ Control plane healthy"
            SERVICES_READY=1
            break
        fi

        # Show status for user visibility
        if [[ $cp_ok -eq 0 ]]; then
            cp_line=$(echo "$health_output" | grep "Control plane" | head -1)
            echo "    ... ${cp_line:-waiting for control plane} (${local_wait}s)"
        fi
        if [[ $fe_ok -eq 0 ]]; then
            fe_line=$(echo "$health_output" | grep "Frontend" | head -1)
            echo "    ... ${fe_line:-waiting for frontend} (${local_wait}s)"
        fi
        sleep 5
        local_wait=$((local_wait + 5))
    done
    if [[ $SERVICES_READY -eq 0 ]]; then
        echo "    ERROR: Services not fully ready after 300s" >&2
        echo "    Check ${LOG_DIR}/backend.log and ${LOG_DIR}/frontend.log" >&2
        exit 1
    fi

    # Verify elastic controller started
    if [[ "$DYN_ELASTIC_SWITCH_ENABLE" != "0" ]]; then
        echo ">>> Verifying elastic controller..."
        local_wait=0
        while [[ $local_wait -lt 30 ]]; do
            if [[ -f "${LOG_DIR}/frontend.log" ]] && \
               grep -q "\[TP/PP\] controller started" "${LOG_DIR}/frontend.log" 2>/dev/null; then
                echo "    ✅ Elastic controller started"
                break
            fi
            sleep 2
            local_wait=$((local_wait + 2))
        done
        if [[ $local_wait -ge 30 ]]; then
            echo "    WARNING: Could not confirm elastic controller startup" >&2
        fi
    fi

    # Verify initial topology
    topo_resp=$(curl -s -m 5 -X POST "${CTRL_URL}/engine/control/parallel_strategy_state" \
        -H 'Content-Type: application/json' -d '{}' 2>/dev/null || echo '{}')
    cur_tp=$(echo "$topo_resp" | jq -r '.tensor_parallel_size // "?"' 2>/dev/null)
    cur_pp=$(echo "$topo_resp" | jq -r '.pipeline_parallel_size // "?"' 2>/dev/null)
    if [[ "$cur_tp" == "?" || "$cur_tp" == "null" || "$cur_pp" == "?" || "$cur_pp" == "null" ]]; then
        echo "    ERROR: Cannot read initial topology (TP=$cur_tp PP=$cur_pp)" >&2
        exit 1
    fi
    echo "    ✅ Current topology: TP=$cur_tp PP=$cur_pp"
fi

# ===================== Prepare Output Files =====================
STATS_FILE="${OUTPUT_DIR}/load_stats.jsonl"
EVENTS_FILE="${OUTPUT_DIR}/events.log"
REPORT_FILE="${OUTPUT_DIR}/report.json"

> "$STATS_FILE"
> "$EVENTS_FILE"

# ===================== Step 5: Start TUI =====================
echo ">>> Starting TUI monitor..."
python3 "${SCRIPT_DIR}/demo_tui.py" \
    --fe-url http://localhost:9090 \
    --ctrl-url http://localhost:9091 \
    --log-file "${LOG_DIR}/frontend.log" \
    --stats-file "$STATS_FILE" \
    --events-file "$EVENTS_FILE" \
    --interval 2 \
    --up-threshold "$UP_THRESHOLD_ACTUAL" \
    --down-threshold "$DOWN_THRESHOLD_ACTUAL" &
TUI_PID=$!

# Give TUI a moment to start
sleep 1

# ===================== Step 6: Execute Scenario Phases =====================
NUM_PHASES=$(echo "$SCENARIO_DATA" | jq '.phases | length')
DEMO_START=$(date +%s)

echo ">>> Running ${NUM_PHASES} phases..."

# Track expectations for final report
declare -a EXPECT_RESULTS=()

for ((phase_idx=0; phase_idx<NUM_PHASES; phase_idx++)); do
    phase_name=$(echo "$SCENARIO_DATA" | jq -r ".phases[$phase_idx].name")
    phase_load=$(echo "$SCENARIO_DATA" | jq -r ".phases[$phase_idx].load // empty")
    phase_wait=$(echo "$SCENARIO_DATA" | jq -r ".phases[$phase_idx].wait // 0")
    phase_expects=$(echo "$SCENARIO_DATA" | jq -r ".phases[$phase_idx].expect // []")

    echo ""
    echo "=== Phase $((phase_idx + 1))/${NUM_PHASES}: ${phase_name} ==="

    # Record event
    echo "[$(date +%H:%M:%S)] [LOAD] Phase started: ${phase_name}" >> "$EVENTS_FILE"

    # Snapshot current topology via control plane API
    phase_start_tp=$(curl -s -m 5 -X POST "${CTRL_URL}/engine/control/parallel_strategy_state" \
        -H 'Content-Type: application/json' -d '{}' 2>/dev/null | jq -r '.tensor_parallel_size // "?"')
    phase_start_pp=$(curl -s -m 5 -X POST "${CTRL_URL}/engine/control/parallel_strategy_state" \
        -H 'Content-Type: application/json' -d '{}' 2>/dev/null | jq -r '.pipeline_parallel_size // "?"')
    echo "    Topology at phase start: ${phase_start_tp}x${phase_start_pp}"

    # ── Launch background topology monitor ──────────────────────────
    # Polls parallel_strategy_state every 2s and records topology
    # change events with elapsed time since phase start.
    MONITOR_FILE="${OUTPUT_DIR}/topo_monitor_${phase_idx}.jsonl"
    MONITOR_MAX_S=0
    if [[ -n "$phase_expects" && "$phase_expects" != "null" && "$phase_expects" != "[]" ]]; then
        # Compute the maximum polling horizon from within_s / during_s
        num_exp=$(echo "$phase_expects" | jq 'length')
        for ((ei=0; ei<num_exp; ei++)); do
            w=$(echo "$phase_expects" | jq -r ".[$ei].within_s // .[$ei].during_s // 0")
            [[ $w -gt $MONITOR_MAX_S ]] && MONITOR_MAX_S=$w
        done
        # Also cover load duration so we capture switches during load
        if [[ -n "$phase_load" && "$phase_load" != "null" ]]; then
            ld=$(echo "$phase_load" | jq -r '.duration // 0')
            [[ $ld -gt $MONITOR_MAX_S ]] && MONITOR_MAX_S=$ld
        fi
        # Cover wait period
        [[ $phase_wait -gt $MONITOR_MAX_S ]] && MONITOR_MAX_S=$phase_wait
        MONITOR_MAX_S=$((MONITOR_MAX_S + 10))  # extra margin
    fi

    if [[ $MONITOR_MAX_S -gt 0 ]]; then
        rm -f "$MONITOR_FILE"
        (
            elapsed=0
            prev_topo=""
            while [[ $elapsed -lt $MONITOR_MAX_S ]]; do
                cur_state=$(curl -s -m 5 -X POST "${CTRL_URL}/engine/control/parallel_strategy_state" \
                    -H 'Content-Type: application/json' -d '{}' 2>/dev/null || echo '{}')
                cur_tp=$(echo "$cur_state" | jq -r '.tensor_parallel_size // "?"')
                cur_pp=$(echo "$cur_state" | jq -r '.pipeline_parallel_size // "?"')
                cur_sw=$(echo "$cur_state" | jq -r 'if .is_switching == false then "false" elif .is_switching == true then "true" else "?" end')
                cur_topo="${cur_tp}x${cur_pp}"
                # Always write a record (every 2s) for precise timing
                echo "{\"elapsed\":${elapsed},\"tp\":${cur_tp},\"pp\":${cur_pp},\"is_switching\":\"${cur_sw}\",\"topo\":\"${cur_topo}\"}" >> "$MONITOR_FILE"
                prev_topo="$cur_topo"
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
        # Extract load parameters
        l_model=$(echo "$phase_load" | jq -r '.model // ""')
        l_input_len=$(echo "$phase_load" | jq -r '.input_len // 128')
        l_output_len=$(echo "$phase_load" | jq -r '.output_len // 32')
        l_conc=$(echo "$phase_load" | jq -r '.conc // 20')
        l_duration=$(echo "$phase_load" | jq -r '.duration // 60')
        l_tag=$(echo "$phase_load" | jq -r '.tag // ""')

        # Use scenario model if specified, otherwise default
        : "${l_model:=/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/}"

        echo "    Load: input=${l_input_len}, output=${l_output_len}, conc=${l_conc}, duration=${l_duration}s, tag=${l_tag}"
        echo "[$(date +%H:%M:%S)] [LOAD] Load started: conc=${l_conc}, input=${l_input_len}, output=${l_output_len}, tag=${l_tag}" >> "$EVENTS_FILE"

        # Start load generator in background, redirect output to stats file
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

        # Wait for load to complete
        wait $LOAD_PID 2>/dev/null || true
        echo "    Load completed"
        echo "[$(date +%H:%M:%S)] [LOAD] Load completed: ${l_tag}" >> "$EVENTS_FILE"
    fi

    if [[ "$phase_wait" -gt 0 ]]; then
        echo "    Waiting ${phase_wait}s..."
        sleep "$phase_wait"
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
            # NOTE: jq `false // null` returns null (jq treats false as empty),
            # so we must use if/then/else to preserve the boolean value.
            exp_not_switching=$(echo "$phase_expects" | jq -r "if .[$ei].state.is_switching == false then \"false\" elif .[$ei].state.is_switching == true then \"true\" else \"unset\" end")

            result="SKIP"
            detail=""

            case "$exp_event" in
                switch_up|switch_down)
                    # Look through monitor data for switch initiation within within_s.
                    # A switch is considered "initiated" when either:
                    #   a) is_switching transitions from false to true, OR
                    #   b) topology actually changes (switch completes quickly)
                    # We prefer (a) because it captures the decision moment,
                    # while topology change only appears after completion.
                    if [[ -f "$MONITOR_FILE" ]]; then
                        # Strategy 1: Find first sample where is_switching becomes true
                        switch_start=$(jq -r "select(.elapsed <= ${exp_within} and .is_switching == \"true\") | .elapsed" "$MONITOR_FILE" 2>/dev/null | head -1)
                        if [[ -n "$switch_start" ]]; then
                            result="PASS"
                            detail="Switch initiated (is_switching=true) at ${switch_start}s (within ${exp_within}s)"
                        else
                            # Strategy 2: Fall back to topology change detection
                            change_line=$(jq -r "select(.elapsed <= ${exp_within} and .topo != \"${phase_start_tp}x${phase_start_pp}\") | .elapsed" "$MONITOR_FILE" 2>/dev/null | head -1)
                            if [[ -n "$change_line" ]]; then
                                change_topo=$(jq -r "select(.elapsed == ${change_line}) | .topo" "$MONITOR_FILE" 2>/dev/null | head -1)
                                result="PASS"
                                detail="Topology changed from ${phase_start_tp}x${phase_start_pp} to ${change_topo} at ${change_line}s (within ${exp_within}s)"
                            else
                                result="FAIL"
                                detail="No switch initiated within ${exp_within}s (topology still ${phase_start_tp}x${phase_start_pp}, is_switching never true)"
                            fi
                        fi
                    else
                        result="FAIL"
                        detail="Monitor data not available"
                    fi
                    ;;

                switch_complete)
                    # Find first sample where topology AND is_switching both match expected
                    if [[ -f "$MONITOR_FILE" ]]; then
                        # Build jq match condition
                        match_conds=".elapsed <= ${exp_within}"
                        [[ "$exp_tp" != "0" ]] && match_conds="${match_conds} and .tp == ${exp_tp}"
                        [[ "$exp_pp" != "0" ]] && match_conds="${match_conds} and .pp == ${exp_pp}"
                        [[ "$exp_not_switching" == "false" ]] && match_conds="${match_conds} and .is_switching == \"false\""
                        match_line=$(jq -r "select(${match_conds}) | .elapsed" "$MONITOR_FILE" 2>/dev/null | head -1)
                        if [[ -n "$match_line" ]]; then
                            match_topo=$(jq -r "select(.elapsed == ${match_line}) | .topo" "$MONITOR_FILE" 2>/dev/null | head -1)
                            match_sw=$(jq -r "select(.elapsed == ${match_line}) | .is_switching" "$MONITOR_FILE" 2>/dev/null | head -1)
                            result="PASS"
                            detail="State: ${match_topo} is_switching=${match_sw} at ${match_line}s (within ${exp_within}s)"
                        else
                            result="FAIL"
                            # Show last sample for debugging
                            last_topo=$(jq -r 'select(.elapsed <= '${exp_within}') | .topo' "$MONITOR_FILE" 2>/dev/null | tail -1)
                            last_sw=$(jq -r 'select(.elapsed <= '${exp_within}') | .is_switching' "$MONITOR_FILE" 2>/dev/null | tail -1)
                            detail="Target tp=${exp_tp} pp=${exp_pp} is_switching=${exp_not_switching} not reached within ${exp_within}s (last: ${last_topo} sw=${last_sw})"
                        fi
                    else
                        result="FAIL"
                        detail="Monitor data not available"
                    fi
                    ;;

                no_switch)
                    # Verify topology stayed stable during entire during_s period
                    if [[ -f "$MONITOR_FILE" ]]; then
                        # Count samples where topology differs from phase start within during_s
                        change_count=$(jq "select(.elapsed <= ${exp_during} and .topo != \"${phase_start_tp}x${phase_start_pp}\")" "$MONITOR_FILE" 2>/dev/null | jq -s 'length')
                        if [[ "$change_count" == "0" || -z "$change_count" ]]; then
                            result="PASS"
                            detail="Topology stable at ${phase_start_tp}x${phase_start_pp} for ${exp_during}s"
                        else
                            first_change=$(jq -r "select(.elapsed <= ${exp_during} and .topo != \"${phase_start_tp}x${phase_start_pp}\") | .elapsed" "$MONITOR_FILE" 2>/dev/null | head -1)
                            change_topo=$(jq -r "select(.elapsed == ${first_change}) | .topo" "$MONITOR_FILE" 2>/dev/null | head -1)
                            result="FAIL"
                            detail="Unexpected change from ${phase_start_tp}x${phase_start_pp} to ${change_topo} at ${first_change}s"
                        fi
                    else
                        result="FAIL"
                        detail="Monitor data not available"
                    fi
                    ;;
            esac

            EXPECT_RESULTS+=("${phase_name}|${exp_event}|${result}|${detail}")
            echo "    Expect [${exp_event}]: ${result} ${detail}"
        done
    fi
done

# ===================== Step 7: Generate Report =====================
echo ""
echo ">>> Generating report..."

DEMO_END=$(date +%s)
DEMO_DURATION=$((DEMO_END - DEMO_START))

# Build expectation results JSON
# NOTE: Cannot use `printf | python3 - <<'HEREDOC'` because the heredoc
# steals python's stdin, so the piped data is lost. Use `-c` instead.
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

echo "    Report: ${REPORT_FILE}"
echo ""

# Print summary
PASS_COUNT=$(echo "$expect_json" | jq '[.[] | select(.result=="PASS")] | length' 2>/dev/null || echo 0)
FAIL_COUNT=$(echo "$expect_json" | jq '[.[] | select(.result=="FAIL")] | length' 2>/dev/null || echo 0)
echo "=== Demo Complete ==="
echo "  Duration: ${DEMO_DURATION}s"
echo "  Final topology: ${final_tp}x${final_pp}"
echo "  Expectations: ${PASS_COUNT} PASS, ${FAIL_COUNT} FAIL"
echo "  Report: ${REPORT_FILE}"

# ===================== Step 8: Stop TUI =====================
if [[ -n "${TUI_PID:-}" ]] && kill -0 "$TUI_PID" 2>/dev/null; then
    echo ">>> Stopping TUI..."
    kill "$TUI_PID" 2>/dev/null || true
    wait "$TUI_PID" 2>/dev/null || true
fi

# Optionally stop service
if [[ $SKIP_STOP -eq 0 ]]; then
    echo ">>> Stopping service..."
    cd "${WORKSPACE_DIR}"
    bash "${SERVICE_SCRIPT}" stop 2>/dev/null || true
    cd "${PROJECT_DIR}"
fi

echo "Done."
