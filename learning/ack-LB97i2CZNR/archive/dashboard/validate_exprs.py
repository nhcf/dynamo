#!/usr/bin/env python3
"""批量验证候选面板表达式，通过 Grafana victoriametrics DS 代理做 instant query。

Grafana 凭据从环境变量读取，不落盘：
  GRAFANA_USER=admin GRAFANA_TOKEN=*** python3 validate_exprs.py
"""
import json
import os
import subprocess
import sys

GRAFANA_AUTH = f"{os.environ['GRAFANA_USER']}:{os.environ['GRAFANA_TOKEN']}"

import time

DS = "34afaf30-b0a7-4abb-939a-5df0ef66b219"
BASE = f"http://isg.fed.zhejianglab.org/grafana/api/datasources/proxy/uid/{DS}/api/v1/query_range"
CL = 'cluster="ack-LB97i2CZNR",namespace="nhi"'


def gq(expr):
    now = int(time.time())
    out = subprocess.run(
        ["curl", "-s", "-m", "25", "-G", "-u", GRAFANA_AUTH, BASE,
         "--data-urlencode", f"query={expr}",
         "--data-urlencode", f"start={now - 6 * 3600}", "--data-urlencode", f"end={now}",
         "--data-urlencode", "step=300s"],
        capture_output=True, text=True).stdout
    try:
        d = json.loads(out)
    except Exception:
        return "BAD_JSON", out[:200]
    if d.get("status") != "success":
        return "ERR", d.get("error", out[:200])
    res = d["data"]["result"]
    sample = []
    for x in res[:3]:
        m = x["metric"]
        key = m.get("dynamo_component") or m.get("status") or m.get("model") or m.get("pod", "")[:24] or "series"
        vals = [float(v[1]) for v in x["values"] if v[1].lower() != "nan"]
        if vals:
            try:
                last = round(vals[-1], 4)
            except Exception:
                last = vals[-1]
            nonempty = f"{len(vals)}/61 pts"
        else:
            last, nonempty = None, "all-NaN"
        sample.append((key, nonempty, last))
    return "OK", f"{len(res)} series {sample}"


TESTS = [
    # 流量稀疏：rate 窗口拉长到 1h，query_range 覆盖 6h
    f'sum by (status) (rate(dynamo_frontend_requests_total{{{CL}}}[1h]))',
    f'sum by (model) (rate(dynamo_frontend_time_to_first_token_seconds_sum{{{CL}}}[1h]) / rate(dynamo_frontend_time_to_first_token_seconds_count{{{CL}}}[1h]))',
    f'sum by (model) (rate(dynamo_frontend_request_duration_seconds_sum{{{CL}}}[1h]) / rate(dynamo_frontend_request_duration_seconds_count{{{CL}}}[1h]))',
    f'sum by (model) (rate(dynamo_frontend_inter_token_latency_seconds_sum{{{CL}}}[1h]) / rate(dynamo_frontend_inter_token_latency_seconds_count{{{CL}}}[1h]))',
    f'sum by (model) (rate(dynamo_frontend_input_sequence_tokens_sum{{{CL}}}[1h]) / rate(dynamo_frontend_input_sequence_tokens_count{{{CL}}}[1h]))',
    f'sum by (model) (rate(dynamo_frontend_output_sequence_tokens_sum{{{CL}}}[1h]) / rate(dynamo_frontend_output_sequence_tokens_count{{{CL}}}[1h]))',
    f'sum by (model) (rate(dynamo_router_overhead_total_ms_sum{{{CL}}}[1h]) / rate(dynamo_router_overhead_total_ms_count{{{CL}}}[1h]))',
    f'sum by (model) (rate(dynamo_router_overhead_scheduling_ms_sum{{{CL}}}[1h]) / rate(dynamo_router_overhead_scheduling_ms_count{{{CL}}}[1h]))',
    f'sum by (model) (rate(dynamo_router_overhead_indexer_find_matches_ms_sum{{{CL}}}[1h]) / rate(dynamo_router_overhead_indexer_find_matches_ms_count{{{CL}}}[1h]))',
    f'sum by (dynamo_component) (rate(dynamo_component_request_duration_seconds_sum{{{CL}}}[1h]) / rate(dynamo_component_request_duration_seconds_count{{{CL}}}[1h]))',
    f'sum by (model) (histogram_quantile(0.99, sum by (le, model) (rate(dynamo_frontend_time_to_first_token_seconds_bucket{{{CL}}}[1h]))))',
    f'max by (dynamo_component) (rate(sglang_kv_transfer_speed_gb_s_sum{{{CL}}}[1h]) / rate(sglang_kv_transfer_speed_gb_s_count{{{CL}}}[1h]))',
    f'max by (dynamo_component) (rate(sglang_kv_transfer_latency_ms_sum{{{CL}}}[1h]) / rate(sglang_kv_transfer_latency_ms_count{{{CL}}}[1h]))',
    f'avg by (dynamo_component) (rate(sglang_e2e_request_latency_seconds_sum{{{CL}}}[1h]) / rate(sglang_e2e_request_latency_seconds_count{{{CL}}}[1h]))',
    # DCGM GPU
    f'DCGM_FI_DEV_GPU_UTIL{{cluster="ack-LB97i2CZNR"}}',
    f'DCGM_FI_DEV_FB_USED{{cluster="ack-LB97i2CZNR"}}',
]

fails = 0
for t in TESTS:
    st, info = gq(t)
    if st != "OK" or info.startswith("0 series") or "all-NaN" in info:
        fails += 1
    print(f"[{st}] {info}\n     {t[:130]}")
print(f"\n{len(TESTS)} tested, {fails} empty/failed")
sys.exit(0)
