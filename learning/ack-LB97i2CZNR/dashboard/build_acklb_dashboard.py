#!/usr/bin/env python3
"""从零生成 ack-LB · dynamo-trace-demo-mixed 专属 Grafana 看板。

底座（全部经 victoriametrics DS 代理实测）：
- DS 固定 victoriametrics（uid=34afaf30-b0a7-4abb-939a-5df0ef66b219）
- 所有查询固定 cluster="ack-LB97i2CZNR"，$namespace/$dgd 可筛
- $window 自定义变量（rate 窗口），默认 1h —— 该演示部署流量稀疏，
  短窗口会把直方图类指标稀释成全 NaN
- 面板组：概览 → Frontend 流量与延迟 → Router 决策 → Worker 组件
  → SGLang 引擎 → GPU 节点（DCGM）
"""
import json

DS_UID = "34afaf30-b0a7-4abb-939a-5df0ef66b219"
DS = {"type": "prometheus", "uid": DS_UID}
CL = 'cluster="ack-LB97i2CZNR"'
SEL = f'{CL},namespace="$namespace",dynamo_dgd="$dgd"'
W = "$window"

panels = []
_pid = [0]


def row(title, y):
    _pid[0] += 1
    panels.append({"collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
                   "id": _pid[0], "panels": [], "title": title, "type": "row"})
    return y + 1


def ts(title, desc, targets, x, y, w=8, h=8, unit=""):
    _pid[0] += 1
    panels.append({
        "datasource": DS, "type": "timeseries", "title": title, "description": desc,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "id": _pid[0],
        "fieldConfig": {"defaults": {
            "color": {"mode": "palette-classic"}, "unit": unit, "mappings": [],
            "custom": {"axisBorderShow": False, "axisCenteredZero": False,
                       "axisColorMode": "text", "axisLabel": "", "axisPlacement": "auto",
                       "barAlignment": 0, "drawStyle": "line", "fillOpacity": 5,
                       "gradientMode": "none", "lineInterpolation": "smooth",
                       "lineWidth": 1, "pointSize": 4, "showPoints": "never",
                       "spanNulls": False, "stacking": {"group": "A", "mode": "none"},
                       "thresholdsStyle": {"mode": "off"}},
            "thresholds": {"mode": "absolute", "steps": [{"color": "green"}]}},
            "overrides": []},
        "options": {"legend": {"calcs": ["lastNotNull"], "displayMode": "table",
                               "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "targets": [{"datasource": DS, "editorMode": "code", "expr": e,
                     "legendFormat": lf, "range": True, "refId": chr(65 + i)}
                    for i, (e, lf) in enumerate(targets)],
    })


def stat(title, desc, expr, x, y, w=4, h=4, unit="", threshold=None):
    _pid[0] += 1
    steps = [{"color": "green"}] + ([{"color": "red", "value": threshold[0]}] if threshold else [])
    panels.append({
        "datasource": DS, "type": "stat", "title": title, "description": desc,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "id": _pid[0],
        "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "unit": unit,
                       "mappings": [], "thresholds": {"mode": "absolute", "steps": steps}},
                        "overrides": []},
        "options": {"colorMode": "value", "graphMode": "area", "reduceOptions":
                    {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "textMode": "auto"},
        "targets": [{"datasource": DS, "editorMode": "code", "expr": expr,
                     "instant": True, "range": False, "refId": "A"}],
    })


def avg_div(metric, sel=SEL, by=None):
    """sum/count 均值表达式；by 为分组 label 列表。"""
    pre = f"sum by ({', '.join(by)}) (" if by else "sum("
    return (pre + f"rate({metric}_sum{{{sel}}}[{W}]) / "
            + f"rate({metric}_count{{{sel}}}[{W}]))")


# ============ Row 1: 概览 ============
y = row("概览 · 采集健康与关键水位", y=0)
stat("采集目标在线数", "job=dynamo-frontend / dynamo-worker 的 scrape 目标健康度（应恒为 4）",
     f'sum(up{{job=~"dynamo-.*",{SEL}}})', 0, y, 4, 4, "none")
stat("注册 Worker 数", "dynamo_component_router_worker_registered：KV router 当前登记的 worker",
     f'sum(dynamo_component_router_worker_registered{{{SEL}}})', 4, y, 4, 4, "none")
stat("Inflight 请求", "dynamo_request_plane_inflight_requests：请求面在途",
     f'sum(dynamo_request_plane_inflight_requests{{{SEL}}})', 8, y, 4, 4, "none")
stat("Router 决策 KV 命中率", "dynamo_component_router_kv_hit_rate 近样本均值",
     f'avg(dynamo_component_router_kv_hit_rate_sum{{{SEL}}} / dynamo_component_router_kv_hit_rate_count{{{SEL}}})',
     12, y, 4, 4, "percentunit")
stat("Frontend 总请求（累计）", "dynamo_frontend_requests_total 累计（计数器，重启清零）",
     f'sum(dynamo_frontend_requests_total{{{SEL}}})', 16, y, 4, 4, "none")
stat("Worker scrape 目标", 'job=dynamo-worker 的 scrape 目标数',
     f'count(up{{job="dynamo-worker",{SEL}}})', 20, y, 4, 4, "none")
y += 4
ts("Scrape 目标健康（up）", "up 时序，跌 0 表示 otel-collector 采不到该组件",
   [(f'up{{job=~"dynamo-.*",{SEL}}}', "{{job}} · {{dynamo_component}} {{pod}}")],
   0, y, 24, 5, "none")
y += 5

# ============ Row 2: Frontend 流量与延迟 ============
y = row("Frontend · 流量与延迟", y=y)
ts("Frontend 请求速率", "dynamo_frontend_requests_total rate，按 status 分",
   [(f'sum by (status) (rate(dynamo_frontend_requests_total{{{SEL}}}[{W}]))', "{{status}}")],
   0, y, 8, 8)
ts("Active / Queued 请求", "dynamo_frontend_active/queued/inflight_requests",
   [(f'sum(dynamo_frontend_active_requests{{{SEL}}})', "active"),
    (f'sum(dynamo_frontend_queued_requests{{{SEL}}})', "queued"),
    (f'sum(dynamo_frontend_inflight_requests{{{SEL}}})', "inflight")], 8, y, 8, 8)
ts("输出 Token 吞吐", "dynamo_frontend_output_tokens_total rate（tokens/s）",
   [(f'sum by (model) (rate(dynamo_frontend_output_tokens_total{{{SEL}}}[{W}]))', "{{model}}")],
   16, y, 8, 8, "reqps")
y += 8
ts("平均 TTFT", "time_to_first_token sum/count（秒）—— 无流量时为空属正常",
   [(avg_div("dynamo_frontend_time_to_first_token_seconds", by=["model"]), "{{model}}")],
   0, y, 8, 8, "s")
ts("平均请求时延（E2E）", "request_duration_seconds sum/count（秒）",
   [(avg_div("dynamo_frontend_request_duration_seconds", by=["model"]), "{{model}}")],
   8, y, 8, 8, "s")
ts("平均 ITL（token 间时延）", "inter_token_latency_seconds sum/count（秒）",
   [(avg_div("dynamo_frontend_inter_token_latency_seconds", by=["model"]), "{{model}}")],
   16, y, 8, 8, "s")
y += 8
_bkt = "dynamo_frontend_time_to_first_token_seconds_bucket"
ts("TTFT 分位数", f"histogram_quantile over {_bkt}",
   [(f'sum by (model) (histogram_quantile(0.50, sum by (le, model) (rate({_bkt}{{{SEL}}}[{W}]))))', "{{model}} p50"),
    (f'sum by (model) (histogram_quantile(0.90, sum by (le, model) (rate({_bkt}{{{SEL}}}[{W}]))))', "{{model}} p90"),
    (f'sum by (model) (histogram_quantile(0.99, sum by (le, model) (rate({_bkt}{{{SEL}}}[{W}]))))', "{{model}} p99")],
   0, y, 8, 8, "s")
ts("平均输入/输出长度", "input/output_sequence_tokens sum/count（tokens）",
   [(avg_div("dynamo_frontend_input_sequence_tokens"), "avg ISL"),
    (avg_div("dynamo_frontend_output_sequence_tokens"), "avg OSL")],
   8, y, 8, 8, "none")
ts("请求面时延分解", "dynamo_request_plane queue / send / roundtrip_ttft sum/count",
   [(avg_div("dynamo_request_plane_queue_seconds"), "queue"),
    (avg_div("dynamo_request_plane_send_seconds"), "send"),
    (avg_div("dynamo_request_plane_roundtrip_ttft_seconds"), "roundtrip_ttft")],
   16, y, 8, 8, "s")
y += 8

# ============ Row 3: Router 决策 ============
y = row("Router · KV 决策与开销", y=y)
ts("Router KV 命中率（随请求）", "dynamo_component_router_kv_hit_rate sum/count",
   [(avg_div("dynamo_component_router_kv_hit_rate", by=["model"]), "{{model}}")],
   0, y, 8, 8, "percentunit")
ts("Router 决策开销分解", "dynamo_router_overhead_*：scheduling / indexer / hashing / total（ms）",
   [(avg_div("dynamo_router_overhead_scheduling_ms"), "scheduling"),
    (avg_div("dynamo_router_overhead_indexer_find_matches_ms"), "indexer_find_matches"),
    (avg_div("dynamo_router_overhead_block_hashing_ms"), "block_hashing"),
    (avg_div("dynamo_router_overhead_total_ms"), "total")],
   8, y, 12, 8, "ms")
y += 8
ts("Router 选中请求速率", "dynamo_component_router_requests_total rate",
   [(f'sum by (model) (rate(dynamo_component_router_requests_total{{{SEL}}}[{W}]))', "{{model}}")],
   0, y, 8, 8)
ts("Router 排队水位", "router_queue_pending_* ：排队请求数 / ISL / cached tokens",
   [(f'sum(dynamo_frontend_router_queue_pending_requests{{{SEL}}})', "pending requests"),
    (f'sum(dynamo_frontend_router_queue_pending_isl_tokens{{{SEL}}})', "pending ISL tokens"),
    (f'sum(dynamo_frontend_router_queue_pending_cached_tokens{{{SEL}}})', "pending cached tokens")],
   8, y, 8, 8)
ts("Router worker 视图", "worker_registered 与 active decode blocks / prefill tokens",
   [(f'sum(dynamo_component_router_worker_registered{{{SEL}}})', "registered workers"),
    (f'sum(dynamo_frontend_worker_active_decode_blocks{{{SEL}}})', "active decode blocks"),
    (f'sum(dynamo_frontend_worker_active_prefill_tokens{{{SEL}}})', "active prefill tokens")],
   16, y, 8, 8)
y += 8

# ============ Row 4: Worker 组件 ============
y = row("Worker 组件 · prefill / decode / aggregated", y=y)
WSEL = f'{SEL},dynamo_component=~"prefill|decode|aggregated"'
ts("Worker 组件请求速率", "dynamo_component_requests_total rate，按 prefill/decode/aggregated",
   [(f'sum by (dynamo_component) (rate(dynamo_component_requests_total{{{WSEL}}}[{W}]))', "{{dynamo_component}} {{pod}}")],
   0, y, 8, 8)
ts("GPU KV Cache 使用率", "dynamo_component_gpu_cache_usage_percent（%）",
   [(f'avg by (dynamo_component) (dynamo_component_gpu_cache_usage_percent{{{WSEL}}})', "{{dynamo_component}} {{pod}}")],
   8, y, 8, 8, "percent")
ts("Worker inflight", "dynamo_component_inflight_requests",
   [(f'sum by (dynamo_component) (dynamo_component_inflight_requests{{{WSEL}}})', "{{dynamo_component}} {{pod}}")],
   16, y, 8, 8)
y += 8
ts("Worker 平均处理时延", "dynamo_component_request_duration_seconds sum/count（秒）",
   [(f'sum by (dynamo_component) (rate(dynamo_component_request_duration_seconds_sum{{{WSEL}}}[{W}]) / rate(dynamo_component_request_duration_seconds_count{{{WSEL}}}[{W}]))',
     "{{dynamo_component}}")], 0, y, 12, 8, "s")
ts("KV Block 池", "dynamo_component_total_blocks（各 worker 组件的 KV block 总量）",
   [(f'sum by (dynamo_component) (dynamo_component_total_blocks{{{WSEL}}})', "{{dynamo_component}} {{pod}}")],
   12, y, 12, 8)
y += 8

# ============ Row 5: SGLang 引擎 ============
y = row("SGLang 引擎内部", y=y)
ts("引擎生成吞吐", "sglang_gen_throughput（tok/s，按 worker 组件）",
   [(f'max by (dynamo_component) (sglang_gen_throughput{{{SEL}}})', "{{dynamo_component}} {{pod}}")],
   0, y, 8, 8)
ts("引擎前缀缓存命中率", "sglang_cache_hit_rate",
   [(f'max by (dynamo_component) (sglang_cache_hit_rate{{{SEL}}})', "{{dynamo_component}} {{pod}}")],
   8, y, 8, 8, "percentunit")
ts("Token 池水位", "full_token_usage（比例）与 used/available tokens",
   [(f'max by (dynamo_component) (sglang_full_token_usage{{{SEL}}})', "usage {{dynamo_component}}"),
    (f'max by (dynamo_component) (sglang_num_used_tokens{{{SEL}}})', "used {{dynamo_component}}"),
    (f'max by (dynamo_component) (sglang_kv_available_tokens{{{SEL}}})', "available {{dynamo_component}}")],
   16, y, 8, 8)
y += 8
ts("引擎请求排队 / 运行", "num_running_reqs / num_queue_reqs / retracted",
   [(f'max by (dynamo_component) (sglang_num_running_reqs{{{SEL}}})', "running {{dynamo_component}}"),
    (f'max by (dynamo_component) (sglang_num_queue_reqs{{{SEL}}})', "queued {{dynamo_component}}"),
    (f'max by (dynamo_component) (sglang_num_retracted_reqs{{{SEL}}})', "retracted {{dynamo_component}}")],
   0, y, 8, 8)
ts("PD 分离队列", "bootstrap / inflight / transfer 队列长度（disagg 关键水位）",
   [(f'max by (dynamo_component) (sglang_num_prefill_bootstrap_queue_reqs{{{SEL}}})', "bootstrap {{dynamo_component}}"),
    (f'max by (dynamo_component) (sglang_num_prefill_inflight_queue_reqs{{{SEL}}})', "inflight {{dynamo_component}}"),
    (f'max by (dynamo_component) (sglang_num_decode_transfer_queue_reqs{{{SEL}}})', "decode_transfer {{dynamo_component}}"),
    (f'max by (dynamo_component) (sglang_num_decode_prealloc_queue_reqs{{{SEL}}})', "decode_prealloc {{dynamo_component}}")],
   8, y, 8, 8)
ts("KV 传输性能（引擎侧）", "sglang_kv_transfer_*：speed GB/s 与 latency ms 均值",
   [(f'max by (dynamo_component) (rate(sglang_kv_transfer_speed_gb_s_sum{{{SEL}}}[{W}]) / rate(sglang_kv_transfer_speed_gb_s_count{{{SEL}}}[{W}]))', "GB/s {{dynamo_component}}"),
    (f'max by (dynamo_component) (rate(sglang_kv_transfer_latency_ms_sum{{{SEL}}}[{W}]) / rate(sglang_kv_transfer_latency_ms_count{{{SEL}}}[{W}]))', "ms {{dynamo_component}}")],
   16, y, 8, 8)
y += 8

# ============ Row 6: GPU 节点 ============
y = row("GPU 节点 · DCGM", y=y)
GSEL = f'{CL},namespace="$namespace"'
ts("GPU 利用率", "DCGM_FI_DEV_GPU_UTIL（%），namespace 内 dynamo pod 所在节点 GPU",
   [(f'DCGM_FI_DEV_GPU_UTIL{{{GSEL}}}', "{{node}} GPU{{gpu}} {{pod}}")], 0, y, 8, 8, "percent")
ts("GPU 显存占用", "DCGM_FI_DEV_FB_USED（MiB）",
   [(f'DCGM_FI_DEV_FB_USED{{{GSEL}}}', "{{node}} GPU{{gpu}} {{pod}}")], 8, y, 8, 8, "mbytes")
ts("GPU 显存带宽利用", "DCGM_FI_DEV_MEM_COPY_UTIL（%）",
   [(f'DCGM_FI_DEV_MEM_COPY_UTIL{{{GSEL}}}', "{{node}} GPU{{gpu}} {{pod}}")], 16, y, 8, 8, "percent")
y += 8

dashboard = {
    "uid": "dynamo-acklb-overview",
    "title": "Dynamo · ack-LB (nhi · trace-demo-mixed)",
    "tags": ["dynamo", "ack-LB", "generated"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,
    "refresh": "1m",
    "time": {"from": "now-6h", "to": "now"},
    "schemaVersion": 39,
    "version": 1,
    "panels": panels,
    "templating": {"list": [
        {"name": "namespace", "type": "query", "label": "Namespace",
         "datasource": DS, "query": f'label_values({CL}, namespace)',
         "current": {"text": "nhi", "value": "nhi"}, "refresh": 1, "hide": 0,
         "includeAll": False, "multi": False},
        {"name": "dgd", "type": "query", "label": "DGD",
         "datasource": DS, "query": f'label_values(dynamo_frontend_requests_total{{{CL},namespace="$namespace"}}, dynamo_dgd)',
         "current": {"text": "dynamo-trace-demo-mixed", "value": "dynamo-trace-demo-mixed"},
         "refresh": 1, "hide": 0, "includeAll": False, "multi": False},
        {"name": "window", "type": "custom", "label": "Rate window",
         "query": "5m,30m,1h,6h,24h", "current": {"text": "1h", "value": "1h"},
         "hide": 0, "options": [{"selected": False, "text": t, "value": t} for t in ["5m", "30m", "1h", "6h", "24h"]]},
    ]},
    "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                              "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                              "name": "Annotations & Alerts", "type": "dashboard"}]},
}

out = "learning/ack-LB97i2CZNR/dashboard/dynamo-acklb-overview-dashboard.json"
with open(out, "w") as f:
    json.dump(dashboard, f, indent=2, ensure_ascii=False)
print(f"written {out}: {len([p for p in panels if p['type'] != 'row'])} panels + "
      f"{len([p for p in panels if p['type'] == 'row'])} rows")
