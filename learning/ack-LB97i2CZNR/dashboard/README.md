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
| `dynamo-acklb-overview` | Dynamo · ack-LB (nhi · trace-demo-mixed) | 基于实测入库指标从零生成 | 52 panels / 7 分组行（含 Planner 行） |
| `dynamo-disagg-nhi` | Dynamo Disaggregated Analysis (ack-LB · nhi) | 官方 `deploy/observability/grafana-disagg-dashboard-configmap.yaml` 适配版 | 20 panels |

主看板访问：`http://isg.fed.zhejianglab.org/grafana/d/dynamo-acklb-overview`。
模板变量：`$namespace`、`$dgd`、`$window`（rate 窗口，默认 **1h**）。
Planner 行同时按 `job="dynamo-planner"` 和顶部 `$dgd` 定位。
选择 `dynamo-trace-demo-mixed` 时没有 Planner 数据属于预期，不能借用另一个 DGD 的数据。
演示流量停播时速率/时延类面板为空属预期（直方图 sum/count 在无新增样本时
无值），重新打流量后可把 `$window` 切回 `5m` 看细节。

## 脚本与产物

| 文件 | 用途 |
|---|---|
| `build_acklb_dashboard.py` | 从零生成主看板 JSON（改面板后重跑） |
| `verify_dashboard_exprs.py` | 将 JSON 内全部表达式代入真实变量逐条走 DS 代理 `query_range` 校验（当前 91 条）；该脚本默认 mixed/1h，仅验证 API 执行成功，不验证数据语义 |
| `validate_exprs.py` | 建板前的候选表达式摸底（瞬时值 + 稀疏流量下的窗口试探） |
| `adapt_dashboard.py` | 官方 disagg dashboard → `dynamo-disagg-nhi` 的 8 处适配（cluster 过滤、`backend`→`decode`、删 NVLink 面板等） |
| `dynamo-acklb-overview-dashboard.json` | 主看板快照（与线上一致） |
| `dynamo-disagg-nhi-dashboard.json` | 适配版看板快照 |
| `dashboard-p0-deployment-2026-11-03.json` | P0 部署版本、备份位置及校验记录 |

## P0 优化（2026-11-03，线上版本 5）

- KV Cache 使用率改为 `percentunit`：原值 `0.02` 显示为 `2%`。
- histogram 均值统一先汇总 `_sum` 和 `_count` 的速率再相除，避免多序列均值相加；
  概览 Router 命中率也改为所选窗口内按请求样本加权。无样本时保留无值。
- Planner 所有查询随 `$dgd` 过滤；DCGM 使用去重后的 Worker `up` pod 集合关联，
  仅展示所选 DGD 的 GPU。集合关联保留 `up=0` 的 pod，不以采集健康充当资源归属。
- 决策面板使用瞬时 one-hot 活跃状态和真实状态标签，显示 `unset`、`no_fpm_data`
  等原因；另外保留两张完整的 0/1 决策历史图。原始预测明确标注为非执行目标，
  数据不足时预测 0 不代表缩容建议。无活跃状态时，独立 absent 查询显示
  「无状态：未部署 Planner 或采集缺失」，不改变原始指标或数值型图表。
- 上线前完成 mixed/planner × 5m/1h 共 364 组瞬时查询校验，两个 DGD 各关联
  6 条 GPU 序列，mixed 没有混入 Planner；两张决策历史另做范围查询校验。
- 浏览器实测当前状态卡显示 `unset` / `no_fpm_data`，KV Cache 图例正确显示 `2%`。

原线上版本 3 已在部署记录中的 `initial_backup_path` 备份；Grafana 同时保留版本历史。
本地生成 JSON 的 `version` 是导入模板版本，线上保存版本以部署记录为准。

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
  （load/throughput_scaling_decision，枚举取值在对应的同名 label 中，活跃取值
  每 tick 为 1，legend 使用该状态 label）、插件健康（circuit_state、
  override_active、evaluations）、决策循环（tick_lag、tick_duration、gpu_hours）
