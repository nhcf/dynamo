#!/usr/bin/env python3
"""Build the Mooncake Master dashboard; no credentials or network access required."""
import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "34afaf30-b0a7-4abb-939a-5df0ef66b219"}
IDENTITY = "cluster,namespace,pod,endpoint"
SELECTOR = 'job="igd_mooncake_scrape",service!~".*-headless",cluster="$cluster",namespace="$namespace",pod=~"${pod:regex}"'
LEGEND = "{{pod}}"
PANELS = []
Y = 0


def raw(metric):
    return f"{metric}{{{SELECTOR}}}"


def gauge(metric, extra=""):
    return f"max by ({IDENTITY}{extra}) ({raw(metric)})"


def rate(metric):
    # Rate each raw counter BEFORE removing duplicate Service scrape labels.
    return f"max by ({IDENTITY}) (rate({raw(metric)}[$window]))"


def ratio(numerator, denominator):
    # Suppress undefined ratios when idle or when capacity is not configured.
    return f"({numerator}) / ({denominator} > 0)"


def row(title):
    global Y
    PANELS.append(
        {
            "id": len(PANELS) + 1,
            "type": "row",
            "title": title,
            "collapsed": False,
            "panels": [],
            "gridPos": {"x": 0, "y": Y, "w": 24, "h": 1},
        }
    )
    Y += 1


def panel(
    title,
    queries,
    unit="short",
    description="",
    x=0,
    width=8,
    kind="timeseries",
    height=8,
):
    defaults = {
        "unit": unit,
        "min": 0,
        "noValue": "无数据",
        "color": {"mode": "palette-classic"},
        "thresholds": {
            "mode": "absolute",
            "steps": [{"color": "green", "value": None}],
        },
        "mappings": [],
    }
    if unit == "percentunit":
        defaults["max"] = 1
    if kind == "timeseries":
        defaults["custom"] = {
            "drawStyle": "line",
            "lineInterpolation": "linear",
            "lineWidth": 1,
            "fillOpacity": 6,
            "showPoints": "never",
            "spanNulls": False,
            "stacking": {"mode": "none", "group": "A"},
            "axisCenteredZero": False,
        }
        options = {
            "legend": {
                "showLegend": True,
                "displayMode": "table",
                "placement": "bottom",
                "calcs": ["lastNotNull", "max"],
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        }
    else:
        options = {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "value_and_name",
            "colorMode": "value",
            "graphMode": "none",
            "orientation": "auto",
        }
    p = {
        "id": len(PANELS) + 1,
        "type": kind,
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": {"x": x, "y": Y, "w": width, "h": height},
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": options,
        "targets": [
            {
                "refId": chr(65 + i),
                "expr": expr,
                "legendFormat": legend,
                "datasource": DS,
                "editorMode": "code",
                "range": kind == "timeseries",
                "instant": kind != "timeseries",
            }
            for i, (expr, legend) in enumerate(queries)
        ],
    }
    PANELS.append(p)
    return p


def charts(items):
    global Y
    for offset in range(0, len(items), 3):
        for index, item in enumerate(items[offset : offset + 3]):
            title, queries, unit, desc = item
            panel(title, queries, unit, desc, x=index * 8)
        Y += 8


def q(metric, label=None, counter=False):
    return (
        (rate if counter else gauge)(metric),
        LEGEND + (" · " + label if label else ""),
    )


def rpc(ops, suffix):
    return [
        q("master_" + op + "_" + suffix + "_total", op + " · " + suffix, True)
        for op in ops
    ]


def variable(name, label, query, current, multi=False):
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": DS,
        "definition": query,
        "query": {"query": query, "refId": name},
        "refresh": 1,
        "sort": 1,
        "multi": multi,
        "includeAll": multi,
        "allValue": ".*" if multi else None,
        "current": {
            "text": "All" if multi else current,
            "value": "$__all" if multi else current,
        },
        "options": [],
    }


def build():
    global Y
    PANELS.clear()
    Y = 0
    PANELS.append(
        {
            "id": 1,
            "type": "text",
            "title": "读图说明 · Master 控制面 / Store 存储池",
            "gridPos": {"x": 0, "y": 0, "w": 24, "h": 4},
            "options": {
                "mode": "markdown",
                "content": "**按 Pod 分开展示，不跨 Master 求和。** 仅展示普通 Service（排除 `.*-headless`），避免停采后的历史样本进入窗口。相同 endpoint 的意外重复序列通过 `max by(cluster,namespace,pod,endpoint)` 去重；counter 先 rate 后去重。\n\n**Get 命中是副本元数据可用率**，不是请求 token 命中率，也不是实际传输成功率；命中字节率不是网络吞吐。不同版本缺失的指标显示「无数据」，无请求时比例留空。未配置文件层时文件容量比例留空。采集 UP 仅代表 metrics 可达，不代表 gRPC ready / HA leader。HA Pod 独立展示，不推断角色。",
            },
        }
    )
    Y = 4
    row("01 · 当前状态（每个 Master 独立）")
    cards = [
        (
            "采集健康 · 普通 Service",
            [(f"min by ({IDENTITY}) ({raw('up')})", LEGEND)],
            "short",
            "仅检查普通 Service；UP 为采集可达，0 为采集失败，不是服务 readiness。",
        ),
        (
            "内存池使用率",
            [
                (
                    ratio(
                        gauge("master_allocated_bytes"),
                        gauge("master_total_capacity_bytes"),
                    ),
                    LEGEND,
                )
            ],
            "percentunit",
            "挂载存储池的分配量 / 总容量，不是 Master 进程 RSS。",
        ),
        (
            "内存池总容量",
            [q("master_total_capacity_bytes")],
            "bytes",
            "每 Pod 独立，禁止跨 HA Master 相加。",
        ),
        ("已分配内存", [q("master_allocated_bytes")], "bytes", "Store 内存池已分配字节。"),
        (
            "当前 Key 数",
            [q("master_key_count")],
            "short",
            "物理对象数，不等于请求、KV page 或 token 数。",
        ),
        ("活跃客户端", [q("master_active_clients")], "short", "注册客户端数量，不等于 Worker Pod 数量。"),
        (
            "副本元数据 Get 命中率",
            [(ratio(rate("valid_get_nums_"), rate("total_get_nums_")), LEGEND)],
            "percentunit",
            "仅支持该指标的实例展示；按查询对象计数。无查询留空。",
        ),
        (
            "驱逐尝试 / 秒",
            [q("master_attempted_evictions_total", counter=True)],
            "ops",
            "驱逐轮次尝试计数，不是被驱逐对象数。",
        ),
    ]
    for i, (title, queries, unit, desc) in enumerate(cards):
        p = panel(
            title, queries, unit, desc, x=(i % 4) * 6, width=6, kind="stat", height=5
        )
        if i == 0:
            p["fieldConfig"]["defaults"]["mappings"] = [
                {
                    "type": "value",
                    "options": {
                        "0": {"text": "采集异常", "color": "red"},
                        "1": {"text": "UP", "color": "green"},
                    },
                }
            ]
        if i % 4 == 3:
            Y += 5
    row("02 · 存储池容量 / Segment / 对象")
    charts(
        [
            (
                "内存池容量趋势",
                [
                    q("master_allocated_bytes", "已分配"),
                    q("master_total_capacity_bytes", "总容量"),
                ],
                "bytes",
                "按 Master 分开；不跨池求和。",
            ),
            (
                "内存池使用率趋势",
                [
                    (
                        ratio(
                            gauge("master_allocated_bytes"),
                            gauge("master_total_capacity_bytes"),
                        ),
                        LEGEND,
                    )
                ],
                "percentunit",
                "容量为 0 时留空。",
            ),
            (
                "文件层容量（若启用）",
                [
                    q("master_allocated_file_size_bytes", "已分配"),
                    q("master_total_file_capacity_bytes", "总容量"),
                ],
                "bytes",
                "file storage 层，由部署配置决定；0 容量通常表示未启用。",
            ),
            (
                "Segment 使用率",
                [
                    (
                        ratio(
                            gauge("segment_allocated_bytes", ",segment"),
                            gauge("segment_total_capacity_bytes", ",segment"),
                        ),
                        LEGEND + " · {{segment}}",
                    )
                ],
                "percentunit",
                "保留 segment 维度。一个 Segment 缺少 allocated 序列时留空，不填成 0。",
            ),
            (
                "Key 与软固定 Key",
                [
                    q("master_key_count", "全部"),
                    q("master_soft_pin_key_count", "soft pin"),
                ],
                "short",
                "soft-pinned 对象有活动租约，可能限制可驱逐空间。",
            ),
            (
                "对象大小 · 写入观测均值",
                [
                    (
                        ratio(
                            rate("master_value_size_bytes_sum"),
                            rate("master_value_size_bytes_count"),
                        ),
                        LEGEND,
                    )
                ],
                "bytes",
                "该直方图新增样本的平均大小，不是当前池中所有对象的平均值。无样本留空。",
            ),
        ]
    )
    row("03 · 元数据读写 RPC（批量 RPC / item / 单条调用分开）")
    batch = [
        "batch_get_replica_list",
        "batch_exist_key",
        "batch_put_start",
        "batch_put_end",
        "batch_put_revoke",
    ]
    single = ["get_replica_list", "exist_key", "put_start", "put_end", "put_revoke"]
    charts(
        [
            (
                "批量 RPC 请求 / 秒",
                rpc(batch, "requests"),
                "reqps",
                "每次 batch 调用计一次；不能直接与 items/s 相加。",
            ),
            (
                "批量对象查询 / 秒",
                rpc(batch, "items"),
                "ops",
                "每次 batch 涉及的 item 数量。不同版本可能不提供。",
            ),
            (
                "单条 RPC 调用 / 秒",
                rpc(single, "requests"),
                "reqps",
                "部分版本 Batch 实现会调用单条路径，不与 Batch 请求计数合计。",
            ),
            (
                "批量 RPC 失败 / 秒",
                rpc(batch, "failures"),
                "ops",
                "整次失败计数；部分失败另看 partial_successes / failed_items。失败含业务返回，并非全部服务故障。",
            ),
            (
                "批量失败 item 比例",
                [
                    (
                        ratio(
                            rate("master_" + op + "_failed_items_total"),
                            rate("master_" + op + "_items_total"),
                        ),
                        LEGEND + " · " + op,
                    )
                    for op in batch
                ],
                "percentunit",
                "failed items / total items；无请求留空；包含业务未命中等版本相关失败语义，不等于系统异常率。",
            ),
            (
                "批量部分成功 / 秒",
                rpc(batch, "partial_successes"),
                "ops",
                "部分 item 成功的 batch 调用数；不与整次失败混为一谈。",
            ),
            (
                "单条 RPC 失败 / 秒",
                rpc(single, "failures"),
                "ops",
                "Get 失败可能是对象不存在，不等同传输错误；Exist 返回 false 可不记为 RPC failure。",
            ),
            (
                "分配失败 / 秒（版本可选）",
                [q("master_put_start_alloc_failures_total", counter=True)],
                "ops",
                "观察内存容量、驱逐、staging 的关联；无此序列表示版本未提供，不代表 0。",
            ),
            (
                "待释放 staging 大小",
                [q("master_put_start_discarded_staging_size")],
                "bytes",
                "瞬时 gauge，不做 rate；搭配容量压力观察。",
            ),
        ]
    )
    row("04 · Get 元数据命中（非 token / 网络传输指标，版本可选）")
    charts(
        [
            (
                "Get 有效副本 / 总查询",
                [q("valid_get_nums_", "有效副本", True), q("total_get_nums_", "总查询", True)],
                "ops",
                "这两个名字无 _total 后缀，但源码定义为 counter；按 rate 使用。",
            ),
            (
                "内存层 / 文件层命中对象 / 秒",
                [
                    q("mem_cache_hit_nums_", "内存层", True),
                    q("file_cache_hit_nums_", "文件层", True),
                ],
                "ops",
                "内存层仍属于 HiCache L3 的 Store 内存池，不是 Worker GPU L1；不拿 hit 数除当前缓存对象数。",
            ),
            (
                "命中对象字节率（非网络吞吐）",
                [
                    q("mem_cache_hit_bytes_total", "内存层", True),
                    q("file_cache_hit_bytes_total", "文件层", True),
                ],
                "Bps",
                "Get 返回元数据时累计对象大小，不保证后续 Client 实际读完；不等于 RDMA/TCP 真实字节。",
            ),
        ]
    )
    row("05 · 驱逐与容量压力")
    charts(
        [
            (
                "驱逐轮次 / 秒",
                [
                    q("master_attempted_evictions_total", "尝试", True),
                    q("master_successful_evictions_total", "成功", True),
                ],
                "ops",
                "统计驱逐轮次；尝试不保证有可驱逐对象。",
            ),
            (
                "驱逐对象 / 秒",
                [q("master_evicted_key_count", counter=True)],
                "ops",
                "counter 虽无 _total 后缀，仍需先 rate。",
            ),
            (
                "驱逐字节 / 秒",
                [q("master_evicted_size_bytes", counter=True)],
                "Bps",
                "驱逐释放空间的速率，不是写入/网络吞吐。",
            ),
        ]
    )
    row("06 · 客户端 / 挂载 / 采集与 HA 诊断")
    charts(
        [
            (
                "活跃客户端趋势",
                [q("master_active_clients")],
                "short",
                "客户端下降时关联 ping 失败、Segment unmount、容量下降。",
            ),
            (
                "Ping 请求与失败 / 秒",
                [
                    q("master_ping_requests_total", "请求", True),
                    q("master_ping_failures_total", "失败", True),
                ],
                "ops",
                "Master 心跳 RPC；与客户端连接状态一起解读。",
            ),
            (
                "Segment 挂载 / 卸载 / 失败",
                rpc(["mount_segment", "unmount_segment"], "requests")
                + rpc(["mount_segment", "unmount_segment"], "failures"),
                "ops",
                "挂载请求与失败都按 counter rate 展示。",
            ),
            (
                "同 endpoint 活跃抓取序列数（2m）",
                [
                    (
                        f'count by ({IDENTITY}) (count_over_time(up{{job="igd_mooncake_scrape",cluster="$cluster",namespace="$namespace",pod=~"${{pod:regex}}"}}[2m]))',
                        LEGEND,
                    )
                ],
                "short",
                "诊断查询包含全部 Service，统计最近 2 分钟有样本的序列，正常每 endpoint 为 1；若大于 1，排查重复采集。停采历史不会因默认 lookback 继续计数。全部消失时留空，不补 0。",
            ),
            (
                "HA 待应用 oplog / 待处理 mutation",
                [
                    q("ha_oplog_pending_entries", "oplog"),
                    q("ha_pending_mutation_queue_size", "mutation"),
                ],
                "short",
                "仅原始 gauge，不据此推断 leader/standby。未启用 HA 可一直为 0。",
            ),
            (
                "每 Pod 已暴露指标族数量",
                [
                    (
                        f'count by ({IDENTITY}) (max by ({IDENTITY},__name__) ({{{SELECTOR},__name__=~"master_.+|segment_.+|valid_get_nums_|total_get_nums_|mem_cache_.+|file_cache_.+|ha_.+"}}))',
                        LEGEND,
                    )
                ],
                "short",
                "去重后的指标名字数，histogram bucket 合为一个名字；版本差异会导致数量不同。不是健康评分。",
            ),
        ]
    )
    return {
        "uid": "mooncake-master-overview",
        "title": "Mooncake Master · 存储池与元数据观测",
        "id": None,
        "version": 0,
        "schemaVersion": 39,
        "editable": True,
        "style": "dark",
        "tags": ["mooncake", "kv-cache", "master", "observability"],
        "timezone": "browser",
        "refresh": "1m",
        "time": {"from": "now-6h", "to": "now"},
        "panels": PANELS,
        "links": [
            {
                "title": "Mooncake 官方可观测文档",
                "type": "link",
                "url": "https://kvcache-ai.github.io/Mooncake/getting_started/observability.html",
                "targetBlank": True,
            }
        ],
        "templating": {
            "list": [
                variable(
                    "cluster",
                    "集群",
                    'label_values(up{job="igd_mooncake_scrape"}, cluster)',
                    "v5000_prod_ack01",
                ),
                variable(
                    "namespace",
                    "命名空间",
                    'label_values(up{job="igd_mooncake_scrape",service!~".*-headless",cluster="$cluster"}, namespace)',
                    "nanhu",
                ),
                variable(
                    "pod",
                    "Master Pod",
                    'label_values(up{job="igd_mooncake_scrape",service!~".*-headless",cluster="$cluster",namespace="$namespace"}, pod)',
                    "",
                    True,
                ),
                {
                    "name": "window",
                    "label": "速率窗口",
                    "type": "custom",
                    "query": "5m,15m,1h,6h",
                    "current": {"text": "5m", "value": "5m"},
                    "options": [
                        {"text": w, "value": w, "selected": w == "5m"}
                        for w in ["5m", "15m", "1h", "6h"]
                    ],
                },
            ]
        },
    }


if __name__ == "__main__":
    output = Path(__file__).with_name("mooncake-master-dashboard.json")
    output.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n")
    print(output)
