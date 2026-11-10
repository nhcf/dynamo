<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# NHI 模型服务 Grafana 看板

本目录保存两个线上看板的可部署模板。修改本地 JSON、校验后部署，部署后再导出；
不通过复制线上旧看板生成配置。API Key 以 `__NHI_FILTER_API_KEY__` 占位，
部署时由环境变量注入。管理员密码、Grafana Token 和真实 API Key 均不提交。

| 文件 | 线上 UID | 标题 | 快照版本 | 默认刷新 |
|---|---|---|---|---|
| [zero2x-dashboard.json](zero2x-dashboard.json) | `fg3uoxfcwc45ce` | 实验室zero2x科研模型面板 | 11 | 30 秒 |
| [research-model-service-dashboard.json](research-model-service-dashboard.json) | `sglang-model-service-waterlevel` | 实验室科研模型服务水位 | 48 | 5 分钟 |

访问地址：

- [zero2x 看板](http://isg.fed.zhejianglab.org/grafana/d/fg3uoxfcwc45ce/)
- [科研模型看板（5 分钟刷新）](http://isg.fed.zhejianglab.org/grafana/d/sglang-model-service-waterlevel/?refresh=5m)

URL 的 `refresh=30s` 会覆盖看板默认刷新间隔。科研模型看板应使用 `refresh=5m`，
避免慢查询尚未结束就再次发起查询。

## 数据源和统计范围

| 数据源 | UID | 用途 |
|---|---|---|
| `victoriametrics` | `34afaf30-b0a7-4abb-939a-5df0ef66b219` | 有效并发、水位、延迟直方图 |
| `mysql-odre` | `dfc3nh9bezi0wc` | zero2x 的服务映射和并发容量 |
| `nhi-higress-loki` | `fg3rvth6eufi8d` | 网关访问日志和 HTTP 429 统计 |

zero2x 看板仅展示 `business_id=nhi-zero2x` 的两个模型，服务和容量来自
`llm_service_business` 中各模型的 `status=active`、`engine_type=sglang` 记录。
每个模型独立合计 `concurrency_capacity`，顶部以只读卡片显示。
任一服务容量非正数时，对应模型不显示有效水位。

| 展示名称 | MySQL / 指标模型名 | 已验证的容量合计 |
|---|---|---|
| 021SFM-236B-Thinking | `021-reason-a100` | 80 |
| 021SFM-32B-Thinking | `021-toolcall-reason-32b` | 40 |

科研模型看板保留六个模型，各自使用顶部容量输入项，不包含 zero2x 专属服务。
以下路由映射用于访问统计；路由标签在查询中转换为展示名称，避免图例显示 `litellm` 名称。

| 展示名称 | Loki `route_name` |
|---|---|
| 021SFM-236B-Thinking | `litellm-service-021sfm-thinking` |
| 021SFM-236B-Instruct | `litellm-service-021sfm-instruct` |
| DeepSeek-V4-Flash-0731 | `litellm-service-deepseek-v4-flash` |
| DeepSeek-V4.1-Flash-1017 | `litellm-service-deepseek-v4-1-flash` |
| GLM-5.3 | `litellm-service-glm-5-3` |
| Kimi-K3 | `litellm-service-kimi-k3` |

## 指标定义

- 有效并发为处理中与排队请求之和；水位为有效并发除以该模型容量，再乘 100%。
  并发指标先按服务、Pod、endpoint、引擎相关标签去重，再汇总；采集不完整时不补零。
- TTFT 和 TPOT 均展示近 5 分钟 P95 / P99，按直方图 bucket 汇总后求分位数。
  TTFT 单位为秒，至少 10 个样本；TPOT 单位为毫秒，至少 20 个样本。
  样本不足显示“暂无数据”，不能解释为零延迟。
- 历史水位使用当前服务映射及容量，不能用于还原过去的容量配置。

访问统计使用 `/var/log/proxy/access.log`，域名精确为 `i-nhi.zhejianglab.org`，
路径为 `/maas/v1` 或其下属接口，不匹配 `/maas/v10` 等其他前缀。
日志没有协议字段，无法区分 HTTP 和 HTTPS。每条匹配日志按一次请求计数，
不按 `request_id` 去重。

- zero2x：仅 `021sfm-thinking` 路由、**指定 API Key** 的 429 请求，包含过去 24 小时
  总次数、路由次数和每 5 分钟数量趋势。
- 科研模型：仅上述六个路由，**排除指定 API Key**（Bearer 和纯 Key 格式）。
  总体和各模型卡片显示过去 24 小时的 429 比例，趋势显示每 15 分钟的 429 数量。
  15 分钟统计无匹配 429 时补零；比例无请求数据时不补零。

统计窗通过面板 `timeFrom=24h` 覆盖默认相对时间范围。Grafana 选择绝对时间范围时
可能忽略相对时间覆盖，因此查看“过去 24h”统计时应使用相对时间选择器。

## Loki 查询成本

最初的比例表达式重复扫描分母，叠加 24 小时范围的比例趋势及 30 秒刷新后，
出现 `too many outstanding requests`。版本 48 做了以下调整：

1. 比例改为单次扫描：将每条日志映射为 `429=1`、其他状态码 `=0`，
   通过 `avg_over_time` 按模型或域名分组求均值，再乘 100。
   这与同范围的 `429 数 / 总请求数` 等价；总体比例按请求量加权，
   不是六个模型比例的简单平均。
2. 过滤 API Key、路径后丢弃临时提取标签，避免为每个用户或路径生成指标序列。
3. 趋势改为只扫描 429 日志的数量查询，15 分钟窗口、最多约 96 个点
   （包含两端边界时返回 97 个点）。
4. 整个科研模型看板默认改为 5 分钟刷新，包括服务指标。

2026-11-10 单次验证：24 小时总体比例约 27 秒、分模型比例约 11 秒；
完整 24 小时数量趋势约 0.72 秒。这些结果受 Loki 缓存及负载影响，不能保证每次耗时。
若仍出现队列错误，先确认浏览器 URL 没有覆盖成 30 秒刷新，并避免同时打开多份自动刷新页面。
后续需要更高刷新频率时，应将请求数/429 数预聚合为指标，或由 Loki recording rules
定期计算，避免浏览器持续扫描原始日志；本次未修改共享数据源或 Loki 服务配置。

## Validate, export, and deploy

The tool uses only the Python standard library. Run it from this directory:

```bash
python3 dashboard_tool.py validate
```

Validation checks JSON, UID/title, recursively unique panel IDs, and literal API Keys.
It does not execute PromQL/LogQL or verify metric semantics.

Configure authentication through environment variables, using a Bearer Token or a
username/password. Interactive input keeps the values out of shell history:

```bash
export GRAFANA_URL=http://isg.fed.zhejianglab.org/grafana
read -rs -p 'Grafana Token: ' GRAFANA_TOKEN
export GRAFANA_TOKEN
read -rs -p 'Filter API Key: ' NHI_FILTER_API_KEY
export NHI_FILTER_API_KEY
```

For Basic authentication, leave `GRAFANA_TOKEN` unset and set `GRAFANA_USER` and
`GRAFANA_PASSWORD`. The tool does not print server error bodies or credentials.
`NHI_FILTER_API_KEY` must identify the actual user to filter: one template includes
that user, while the other excludes them.

Deploy a local template to its existing UID, preserving its folder:

```bash
python3 dashboard_tool.py deploy research-model-service-dashboard.json
python3 dashboard_tool.py deploy zero2x-dashboard.json
```

Deployment fails if the UID does not exist. It reads the latest ID/version and uses
`overwrite=false` to reject changes made concurrently after that read. A stale local
template still replaces the target's contents, so export and review online changes
before deploying. After saving, the tool reads back panels, variables, title, and refresh
configuration to check the result.

To render an import file for review, write outside the repository; the output uses mode 0600:

```bash
python3 dashboard_tool.py render research-model-service-dashboard.json /tmp/nhi-research.rendered.json
```

Rendered files contain the real filter key and must not be committed. Deployment renders
in memory and requires no temporary file. Grafana administrator passwords and tokens are
never written to dashboard JSON.

Export the latest deployed definitions, replacing the filter key with its placeholder:

```bash
python3 dashboard_tool.py export sglang-model-service-waterlevel research-model-service-dashboard.json
python3 dashboard_tool.py export fg3uoxfcwc45ce zero2x-dashboard.json
python3 dashboard_tool.py validate
git diff --check
```

Export refuses to write if it finds multiple distinct API Keys rather than merging
different user identities into one placeholder. Update the snapshot versions in the
opening table after exporting, and run the repository pre-commit checks before committing.
