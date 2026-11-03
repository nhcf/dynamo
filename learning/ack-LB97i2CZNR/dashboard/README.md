# ack-LB Dynamo Grafana 看板

针对 ack-LB97i2CZNR 集群 `nhi` 命名空间的 `dynamo-trace-demo-mixed` DGD，
在线上 Grafana（`http://isg.fed.zhejianglab.org/grafana/`）部署了两个看板。
数据源固定为联邦 VictoriaMetrics（datasource uid
`34afaf30-b0a7-4abb-939a-5df0ef66b219`），所有查询硬编码
`cluster="ack-LB97i2CZNR"` —— 该 DS 是多集群联邦，缺 cluster 过滤时
kube-state join 会因跨集群重名 namespace 出现重复序列。

## 已部署看板

| uid | 标题 | 来源 | 面板 |
|---|---|---|---|
| `dynamo-acklb-overview` | Dynamo · ack-LB (nhi · trace-demo-mixed) | 基于实测入库指标从零生成 | 50 panels / 7 分组行（含 Planner 行） |
| `dynamo-disagg-nhi` | Dynamo Disaggregated Analysis (ack-LB · nhi) | 官方 `deploy/observability/grafana-disagg-dashboard-configmap.yaml` 适配版 | 20 panels |

主看板访问：`http://isg.fed.zhejianglab.org/grafana/d/dynamo-acklb-overview`。
模板变量：`$namespace`、`$dgd`、`$window`（rate 窗口，默认 **1h**）。
Planner 行来自独立 DGD `dynamo-trace-demo-planner`，选择器按
`job="dynamo-planner"` 定位，不随顶部 `$dgd` 切换。
演示流量停播时速率/时延类面板为空属预期（直方图 sum/count 在无新增样本时
无值），重新打流量后可把 `$window` 切回 `5m` 看细节。

## 脚本与产物

| 文件 | 用途 |
|---|---|
| `build_acklb_dashboard.py` | 从零生成主看板 JSON（改面板后重跑） |
| `verify_dashboard_exprs.py` | 上线前把 JSON 内全部表达式代入真实变量逐条走 DS 代理 `query_range` 校验（当前 87 条，0 失败） |
| `validate_exprs.py` | 建板前的候选表达式摸底（瞬时值 + 稀疏流量下的窗口试探） |
| `adapt_dashboard.py` | 官方 disagg dashboard → `dynamo-disagg-nhi` 的 8 处适配（cluster 过滤、`backend`→`decode`、删 NVLink 面板等） |
| `dynamo-acklb-overview-dashboard.json` | 主看板快照（与线上一致） |
| `dynamo-disagg-nhi-dashboard.json` | 适配版看板快照 |

## 重新部署

```bash
cd learning/ack-LB97i2CZNR/dashboard
python3 build_acklb_dashboard.py                 # 重新生成 JSON
GRAFANA_USER=admin GRAFANA_TOKEN=*** \
  python3 verify_dashboard_exprs.py              # 全量表达式校验
# 校验通过后 POST 部署（凭据一律走环境变量，不落盘、不写进任何提交）
python3 -c "import json;d=json.load(open('dynamo-acklb-overview-dashboard.json'));json.dump({'dashboard':d,'folderId':0,'overwrite':True,'message':'redeploy'},open('/tmp/dash-acklb.json','w'))"
curl -u "admin:$GRAFANA_TOKEN" -H 'Content-Type: application/json' \
  -X POST http://isg.fed.zhejianglab.org/grafana/api/dashboards/db \
  --data-binary @/tmp/dash-acklb.json
```

## 数据底座（实测枚举，2026-11-03）

- `job="dynamo-frontend"`：~120 指标 —— `dynamo_frontend_*`（请求/TTFT/ITL/
  ISL·OSL/tokenize/router 排队/worker 视图）、`dynamo_component_router_*`
  （kv_hit_rate、worker_registered、requests_total）、`dynamo_router_overhead_*`
  （scheduling/indexer/hashing/total ms）、`dynamo_request_plane_*`、`dynamo_tokio_*`
- `job="dynamo-worker"`：31 个 `dynamo_component_*` / `dynamo_work_handler_*`
  （`dynamo_component` 取值 `aggregated|decode|prefill`）+ 93 个 `sglang_*`
  （gen_throughput、cache_hit_rate、full_token_usage、kv tokens、PD 四级队列、
  kv_transfer speed/latency）
- GPU：`DCGM_FI_DEV_*{cluster="ack-LB97i2CZNR"}`（RTX 4090 D，无 PROF/NVLink
  字段，legend 用 `{{node}} GPU{{gpu}} {{pod}}`）
- `job="dynamo-planner"`（2026-11-03 新增，独立 DGD `dynamo-trace-demo-planner`，
  49 个非-bucket 指标）：`dynamo_planner_*` —— 副本水位（num/predicted_num
  prefill·decode）、观测 vs 预测（rps、ISL·OSL、ttft/itl）、SLA 目标、伸缩决策
  （load/throughput_scaling_decision，枚举取值以 `__name__` label 展开，活跃取值
  每 tick 为 1，legend 用 `{{__name__}}`）、插件健康（circuit_state、
  override_active、evaluations）、决策循环（tick_lag、tick_duration、gpu_hours）
