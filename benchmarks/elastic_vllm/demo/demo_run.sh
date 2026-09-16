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
    echo ">>> Verifying service..."
    local_wait=0
    while [[ $local_wait -lt 60 ]]; do
        if curl -s -o /dev/null "http://localhost:9090/health" 2>/dev/null; then
            echo "    Frontend healthy"
            break
        fi
        sleep 3
        local_wait=$((local_wait + 3))
    done
    if [[ $local_wait -ge 60 ]]; then
        echo "    ERROR: Frontend not healthy after 60s" >&2
        exit 1
    fi

    # Verify controller started
    if [[ "$DYN_ELASTIC_SWITCH_ENABLE" != "0" ]]; then
        local_wait=0
        while [[ $local_wait -lt 30 ]]; do
            if [[ -f "${LOG_DIR}/frontend.log" ]] && \
               grep -q "\[TP/PP\] controller started" "${LOG_DIR}/frontend.log" 2>/dev/null; then
                echo "    Elastic controller started"
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
    topo_resp=$(curl -s -m 5 -X POST "http://localhost:9091/engine/control/parallel_strategy_state" \
        -H 'Content-Type: application/json' -d '{}' 2>/dev/null || echo '{}')
    cur_tp=$(echo "$topo_resp" | jq -r '.tensor_parallel_size // "?"' 2>/dev/null)
    cur_pp=$(echo "$topo_resp" | jq -r '.pipeline_parallel_size // "?"' 2>/dev/null)
    echo "    Current topology: TP=$cur_tp PP=$cur_pp"
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

    # Snapshot log position for scoped expectation checks
    phase_log_pos=0
    if [[ -f "${LOG_DIR}/frontend.log" ]]; then
        phase_log_pos=$(wc -c < "${LOG_DIR}/frontend.log")
    fi

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

    # Check expectations
    if [[ -n "$phase_expects" && "$phase_expects" != "null" && "$phase_expects" != "[]" ]]; then
        num_expects=$(echo "$phase_expects" | jq 'length')
        for ((ei=0; ei<num_expects; ei++)); do
            exp_event=$(echo "$phase_expects" | jq -r ".[$ei].event")
            exp_pattern=$(echo "$phase_expects" | jq -r ".[$ei].log_pattern // empty")
            exp_within=$(echo "$phase_expects" | jq -r ".[$ei].within_s // 0")

            result="SKIP"
            detail=""

            case "$exp_event" in
                switch_up|switch_down)
                    dir=$(echo "$exp_event" | sed 's/switch_/SWITCH /' | tr '[:lower:]' '[:upper:]')
                    if [[ -n "$exp_pattern" ]] && [[ -f "${LOG_DIR}/frontend.log" ]]; then
                        # Only check log content added during this phase
                        phase_log_tail=$(tail -c +"$((phase_log_pos + 1))" "${LOG_DIR}/frontend.log" 2>/dev/null || echo "")
                        if echo "$phase_log_tail" | grep -q "$exp_pattern"; then
                            result="PASS"
                            detail="Found: $exp_pattern"
                        else
                            result="FAIL"
                            detail="Not found: $exp_pattern"
                        fi
                    fi
                    ;;
                switch_complete)
                    if curl -s -m 5 -X POST "http://localhost:9091/engine/control/parallel_strategy_state" \
                        -H 'Content-Type: application/json' -d '{}' 2>/dev/null | \
                        jq -e '.is_switching == false' >/dev/null 2>&1; then
                        result="PASS"
                        detail="is_switching=false"
                    else
                        result="FAIL"
                        detail="is_switching=true or unreachable"
                    fi
                    ;;
                no_switch)
                    # Check that no SWITCH UP/DOWN appeared during this phase
                    exp_during=$(echo "$phase_expects" | jq -r ".[$ei].during_s // 0")
                    if [[ -f "${LOG_DIR}/frontend.log" ]]; then
                        phase_log_tail=$(tail -c +"$((phase_log_pos + 1))" "${LOG_DIR}/frontend.log" 2>/dev/null || echo "")
                        if echo "$phase_log_tail" | grep -q "SWITCH"; then
                            result="FAIL"
                            detail="Unexpected switch found in phase log"
                        else
                            result="PASS"
                            detail="No switch detected"
                        fi
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
expect_json=$(printf '%s\n' "${EXPECT_RESULTS[@]}" | python3 - <<'PYEOF'
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
PYEOF
)

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
