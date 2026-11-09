# Mooncake Master Grafana dashboard

线上看板：[Mooncake Master · 存储池与元数据观测](http://isg.fed.zhejianglab.org/grafana/d/mooncake-master-overview?orgId=1)。

默认集群 `v5000_prod_ack01`、namespace `nanhu`、全部 Master Pod、速率窗口 `5m`；
可按集群、namespace、一个或多个 Master Pod 筛选。数据源为联邦 VictoriaMetrics，
UID `34afaf30-b0a7-4abb-939a-5df0ef66b219`，job 为 `igd_mooncake_scrape`。

## 展示内容

35 个业务面板、6 个分组行和 1 个读图说明，75 条查询：

| 分组 | 内容 | 使用场景 |
|---|---|---|
| 当前状态 | 采集健康、容量、使用率、Key、客户端、Get 命中、驱逐尝试 | 快速定位池容量压力与采集状态 |
| 存储池 | 内存/文件容量、Segment 使用率、soft pin、对象大小观测均值 | 分析存储分配不均、可驱逐空间和对象规格 |
| 元数据读写 RPC | Batch 请求、item、失败、部分成功；单条 RPC；分配失败和 staging | 区分调用量、对象量、业务失败与容量问题 |
| Get 命中 | 有效副本查询、内存/文件命中对象和对象字节率 | 判断 Store 对象复用，不等同于请求 token 命中 |
| 驱逐 | 尝试/成功轮次、驱逐对象和字节速率 | 判断缓存抖动及容量压力 |
| 诊断 | 客户端、Ping、Segment 挂载、重复采集数、HA 队列、指标覆盖数 | 关联客户端流失、容量变化、采集和版本差异 |

## 关键口径

- **采集来源**：headless Service 已停止采集，3 个 Master Pod 均由普通 Service
  继续采集。所有业务查询、指标覆盖统计和模板变量明确排除 `service=~".*-headless"`，
  防止停采历史样本仍被默认 lookback 或较长 rate 窗口读取。
- **重复抓取保护**：所有业务查询保留 `cluster,namespace,pod,endpoint`，
  gauge 用 `max by(...)`；counter 对原序列先 `rate`，然后 `max by(...)`。
  这是相同 endpoint 的展示去重；两个抓取时间略有不同，速率可能存在小幅差异，
  `max` 选择其中较大值。当前普通 Service 为每 endpoint 一条序列，保留聚合以稳定
  输出标签并防御意外重复。未删除后端历史数据。
- **不跨 Master 求和**：保持每个 Master 的独立曲线，防止同一 HA 存储池重复累计。
  当前没有可靠角色指标映射，因此不自动识别 leader 或选择一个“全局容量”。
- **采集健康**：仅对普通 Service 相同 endpoint 的 `up` 取 `min`，采集失败显示异常。
  这不是 gRPC readiness 或 HA 角色。消失的目标保留无数据，不补成 UP/0。
- **活跃抓取序列数**：诊断面板包含全部 Service，使用
  `count by(cluster,namespace,pod,endpoint)(count_over_time(up{...}[2m]))`，
  当前预期每 endpoint 为 1；大于 1 表示近期重复采集。此处 2 分钟是样本新鲜度窗口，
  与 `$window` 速率窗口独立；全部停采时留空，不补 0。
- **存储容量**：挂载 Store 池容量，不是 Master 进程 RSS；`segment` 维度单独保留。
- **命中率**：`rate(valid_get_nums_) / rate(total_get_nums_)` 是成功找到完整副本元数据的
  查询对象比例，不是 SGLang prompt token 命中率，也不证明 Client 后续读取成功。
  未使用 `mem_cache_hit_nums_ / mem_cache_nums_` 这类累计次数除当前对象数量的错误比例。
- **字节率**：命中对象字节率不等于实际传输吞吐；驱逐字节率是释放空间速率。
- **Batch 与单条路径**：请求、item、部分成功、失败分开展示。部分版本 Batch
  内部调用单条方法，二者不合计成一个总请求数。failure 也可能包含对象不存在、
  对象已存在等业务结果，不能全部解释为服务故障。
- **缺失与空闲**：缺失序列不补 0；没有请求或分母为 0 时比例留空。
  文件容量为 0 只说明该层未提供容量，不能当作采集故障。

## 2026-11-09 线上验证

headless 停采后的更新：最近 2 分钟 headless 无新样本，普通 Service 每个 endpoint
只有一条活跃序列且 `up=1`，最新样本年龄低于 90 秒。初次检查时旧 headless
最后样本约为 5 分钟前，仍出现在 VictoriaMetrics 默认回看结果中；这与停采不矛盾。
更新后的业务查询已显式排除 headless，长窗口历史曲线也只使用普通 Service。
更新部署前再次执行 442 组查询校验；原线上版本在保存前备份，版本号与备份路径
见 [部署记录](deployment-evidence.json)。诊断面板保留两路采集时期的历史轨迹。

3 个 Pod 的实时指标名字数量分别为 89、90、140（包括 `up`，histogram bucket
按指标名字统计）。`valid_get_nums_`、`total_get_nums_`、批量 item 等细分指标
当前只存在于 `mooncake-prod-mc0312p1-02-master-0`，另外两个实例对应面板无数据符合现状。
这里是实际指标差异观察，未根据 Pod 名称推断或核验镜像版本。

部署前完成 **442** 组在线查询检查：全部目标在 All/5m、All/1h 和 3 个单独 Pod/5m
下执行瞬时查询，所有时序目标另检查最近 1 小时历史曲线。
检查 API 非 partial response、Pod/集群/namespace 隔离、输出无 Service 重复维度、
数值有限且非负，以及比例不超过 1；部署后读取 Grafana API 验证面板、模板变量等与本地一致。
浏览器另通过 5 项检查（更新版 v2）：All 显示 80 GiB 容量；选择示例 Pod 后只显示 40 GiB；
该 Pod 的缺失 Get 命中指标显示「无数据」；采集健康显示 UP；
活跃抓取序列数诊断面板正常显示。

## 文件与维护

| 文件 | 用途 |
|---|---|
| [mooncake-master-dashboard.json](mooncake-master-dashboard.json) | 可导入 Grafana 的完整看板 |
| [build_dashboard.py](build_dashboard.py) | 可重复生成看板，无网络、无凭据 |
| [validate_deploy.py](validate_deploy.py) | 在线校验；`--deploy` 才保存线上，更新前备份并检查并发编辑 |
| [deployment-evidence.json](deployment-evidence.json) | 最终部署版本、目标、指标覆盖数及查询与浏览器校验摘要，无凭据 |

```bash
python3 learning/mooncake/dashboard/build_dashboard.py
# 凭据由当前 shell 环境提供，不写入文件：
# GRAFANA_USER / GRAFANA_PASSWORD，或 Bearer GRAFANA_TOKEN。
python3 learning/mooncake/dashboard/validate_deploy.py
python3 learning/mooncake/dashboard/validate_deploy.py --deploy
```

验证脚本针对上述生产集群及 namespace 做回归，并检查 headless 最近 2 分钟无样本、
每个 endpoint 只有一路活跃采集、普通 Service UP 和样本年龄；迁移环境时需要调整脚本和默认变量。
本次新建独立 UID `mooncake-master-overview`，不修改既有 Dynamo 看板或采集配置。
再次更新相同 UID 时保留线上 folder，并将原版本备份到执行用户的
`~/.local/state/dynamo/grafana-backups/`。首次新建没有旧版本备份。

指标口径依据：[官方可观测文档](https://kvcache-ai.github.io/Mooncake/getting_started/observability.html)、
[Master 指标定义源码](https://github.com/kvcache-ai/Mooncake/blob/main/mooncake-store/src/master_metric_manager.cpp)
与本次线上入库指标；上游 main 持续变化，面板以实际采集存在的指标为准。
更多 HiCache 关联解释见 [HiCache / Mooncake 指标解读](../../kvstore-hicache-metrics.html#master-comparison)。
