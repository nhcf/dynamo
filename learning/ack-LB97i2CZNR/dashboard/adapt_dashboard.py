#!/usr/bin/env python3
"""把官方 dynamo disagg dashboard 适配为 ack-LB · nhi 可运行版本。

适配点（对照 victoriametrics 数据源的实测 label 面）：
 1. 所有 kube_pod_status_phase join 加 cluster 过滤（该 VM 联邦多集群，
    nhi 命名空间跨集群重名会让 group_left join 右侧出现重复序列）
 2. dynamo_component="backend" -> "decode"（我们的 decode 组件暴露 decode）
 3. 删除 NVLink 面板 id=37（ack-LB 的 DCGM 未开 PROF 字段，且 4090D 无 NVLink）
 4. DCGM 面板表达式加 cluster 过滤，legend {{Hostname}} -> {{node}}
 5. Worker CPU 面板 pod=~".*worker.*" -> ".*(worker|prefill|decode).*" 并加 cluster
 6. node_cpu_seconds_total 加 cluster 过滤
 7. templating：datasource 预设 victoriametrics；新增 $cluster 变量；
    $namespace 变量限定在 $cluster 内，默认 nhi
 8. 独立 uid/title，避免覆盖官方导入件
"""
import json
import re
import sys

SRC_CM = "deploy/observability/grafana-disagg-dashboard-configmap.yaml"
DST = "learning/ack-LB97i2CZNR/dashboard/dynamo-disagg-nhi-dashboard.json"

VM_UID = "34afaf30-b0a7-4abb-939a-5df0ef66b219"
VM_NAME = "victoriametrics"
CLUSTER = "ack-LB97i2CZNR"

cm = open(SRC_CM).read()
m = re.search(r"disagg-dashboard\.json: \|-\n(.*)$", cm, re.S)
dash = json.loads(m.group(1))

# ---- 表达式级适配 ----
def fix_exprs(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "expr" and isinstance(v, str):
                v = v.replace(
                    'kube_pod_status_phase{phase="Running"}',
                    f'kube_pod_status_phase{{cluster="$cluster",phase="Running"}}',
                )
                v = v.replace('dynamo_component="backend"', 'dynamo_component="decode"')
                v = re.sub(r'\bDCGM_FI_DEV_GPU_UTIL\b(?!\{)', 'DCGM_FI_DEV_GPU_UTIL{cluster="$cluster"}', v)
                v = v.replace("DCGM_FI_DEV_MEM_COPY_UTIL", 'DCGM_FI_DEV_MEM_COPY_UTIL{cluster="$cluster"}')
                v = v.replace("DCGM_FI_DEV_FB_USED", 'DCGM_FI_DEV_FB_USED{cluster="$cluster"}')
                v = v.replace('container_cpu_usage_seconds_total{namespace="$namespace",pod=~".*worker.*",container="main"}',
                              f'container_cpu_usage_seconds_total{{cluster="$cluster",namespace="$namespace",pod=~".*(worker|prefill|decode).*",container="main"}}')
                v = v.replace('node_cpu_seconds_total{mode="idle"}', 'node_cpu_seconds_total{cluster="$cluster",mode="idle"}')
                obj[k] = v
            else:
                fix_exprs(v)
    elif isinstance(obj, list):
        for it in obj:
            fix_exprs(it)

fix_exprs(dash)

# legend {{Hostname}} -> {{node}}
for p in dash["panels"]:
    for t in p.get("targets", []):
        if t.get("legendFormat", "").startswith("GPU {{gpu}}"):
            t["legendFormat"] = "GPU {{gpu}} - {{node}}"

# 删除 NVLink 面板
before = len(dash["panels"])
dash["panels"] = [p for p in dash["panels"] if p.get("id") != 37]
print(f"panels: {before} -> {len(dash['panels'])} (removed NVLink id=37)")

# ---- templating ----
for v in dash["templating"]["list"]:
    if v["name"] == "datasource":
        v["current"] = {"text": VM_NAME, "value": VM_UID}
    if v["name"] == "namespace":
        v["query"] = f'label_values(dynamo_frontend_requests_total{{cluster="$cluster"}}, namespace)'
        v["current"] = {"selected": False, "text": "nhi", "value": "nhi"}
dash["templating"]["list"].insert(1, {
    "current": {"selected": False, "text": CLUSTER, "value": CLUSTER},
    "hide": 0,
    "label": "Cluster",
    "name": "cluster",
    "options": [],
    "query": "label_values(dynamo_frontend_requests_total, cluster)",
    "refresh": 1,
    "regex": "",
    "sort": 0,
    "type": "query",
    "datasource": {"type": "prometheus", "uid": "${datasource}"},
})

dash["title"] = "Dynamo Disaggregated Analysis (ack-LB · nhi)"
dash["uid"] = "dynamo-disagg-nhi"
dash["id"] = None
dash["version"] = 1
dash["tags"] = dash.get("tags", []) + ["ack-LB", "nhi"]

import os
os.makedirs(os.path.dirname(DST), exist_ok=True)
json.dump(dash, open(DST, "w"), indent=2, ensure_ascii=False)
print("written:", DST)

# 自检：不应再出现 backend / Hostname
s = open(DST).read()
print("post-check backend refs:", s.count('dynamo_component=\\"backend\\"'))
print("post-check Hostname refs:", s.count("Hostname"))
print("post-check bare-join refs:", s.count('kube_pod_status_phase{phase=\\"Running\\"}'))
