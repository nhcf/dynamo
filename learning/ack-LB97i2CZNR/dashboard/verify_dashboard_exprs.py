#!/usr/bin/env python3
"""把 dashboard JSON 里所有面板表达式代入真实变量后，逐条走 query_range 验证。

Grafana 凭据从环境变量读取，不落盘：
  GRAFANA_USER=admin GRAFANA_TOKEN=*** python3 verify_dashboard_exprs.py
"""
import json
import os
import subprocess
import time

GRAFANA_AUTH = f"{os.environ['GRAFANA_USER']}:{os.environ['GRAFANA_TOKEN']}"

DS = "34afaf30-b0a7-4abb-939a-5df0ef66b219"
BASE = f"http://isg.fed.zhejianglab.org/grafana/api/datasources/proxy/uid/{DS}/api/v1/query_range"

dash = json.load(open("learning/ack-LB97i2CZNR/dashboard/dynamo-acklb-overview-dashboard.json"))
subs = {"$namespace": "nhi", "$dgd": "dynamo-trace-demo-mixed", "$window": "1h"}

now = int(time.time())
bad = 0
total = 0
for p in dash["panels"]:
    for t in p.get("targets", []):
        total += 1
        expr = t["expr"]
        for k, v in subs.items():
            expr = expr.replace(k, v)
        out = subprocess.run(
            ["curl", "-s", "-m", "25", "-G", "-u", GRAFANA_AUTH, BASE,
             "--data-urlencode", f"query={expr}",
             "--data-urlencode", f"start={now - 3600}", "--data-urlencode", f"end={now}",
             "--data-urlencode", "step=300s"],
            capture_output=True, text=True).stdout
        try:
            d = json.loads(out)
        except Exception:
            d = {"status": "BAD_JSON", "error": out[:150]}
        if d.get("status") != "success":
            bad += 1
            print(f"[FAIL] {p['title']}: {d.get('error')}")
        # 空序列只统计不判失败（演示流量停播期属预期）
print(f"\n{total} target exprs checked, {bad} syntax/API failures")
