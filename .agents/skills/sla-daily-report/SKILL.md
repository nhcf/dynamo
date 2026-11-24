---
name: sla-daily-report
description: 生成、补跑、验收或发布之江实验室模型服务的 SLA 日报。适用于从 Higress Loki 取数、调整日报统计或 HTML、维护本机定时任务，以及上传独立 HTML 到智算开放服务平台报表广场。
---

# 模型服务 SLA 日报

从仓库根目录执行。使用现有脚本和模板，当前模型路由与默认阈值以生成脚本为准。修改统计口径时同步方案、脚本和模板，涉及历史报表时按用户指定日期重新生成。

按业务日追查 5xx 并写入故障记录时，使用 [sla-5xx-case](../sla-5xx-case/SKILL.md)。

- [日报方案](../../../learning/daily-report/report.md)记录指标、模型映射和展示要求。
- [生成脚本](../../../learning/daily-report/run_report.py)查询 Higress Loki、计算 SLA 并写入独立 HTML。
- [HTML 模板](../../../learning/daily-report/report-template.html)控制页面布局和统计口径说明。
- [定时发布入口](../../../learning/daily-report/publish_daily_report.py)执行预发和正式两个阶段。
- [发布脚本](../../../learning/daily-report/zjic-report-upload/upload_report.py)签名上传并返回独立阅读链接。

## 生成和验收

默认取北京时间前一完整自然日。补跑传 `--date YYYY-MM-DD`，验收时可用 `--output-dir` 指定临时目录。

```bash
python3 learning/daily-report/run_report.py
python3 learning/daily-report/run_report.py --date 2026-11-18
```

取数窗口为北京时间当日 00:00 至次日 00:00，不含结束时刻。脚本串行查询 Loki，按路由和 Chat Completions、Messages 的 POST 接口筛选。匹配目标路由的 zero2x 请求计入。模型范围为科研看板六个模型加 `021SFM-32B-Thinking`，其 Higress 路由为 `021-guarantee-litellm-service-021sfm-32b-thinking`。

| 指标 | 一期判定口径 |
|---|---|
| 请求可用率 | 1 减去全天 5xx 条目数除以有效请求条目数。有效请求取 2xx、3xx、5xx，日报目标 ≥ 99.5%。 |
| 流式请求 TTFT 达成率 | 有有效首段计时的流式请求按外部请求 ID 去重。网关首次流式响应体回调耗时 ≤ 5 秒的请求占比目标为 ≥ 95%。 |
| 流式请求 TPOT 达成率 | 纳入无网关异常标记、HTTP 2xx、有有效首段和流关闭计时且至少输出两个 Token 的去重流式请求。使用 `(流关闭耗时 - 首段耗时) / (输出 Token 数 - 1)`。阈值为 ≤ 50 ms/token，Kimi-K3 为 ≤ 200 ms/token，阈值内占比目标为 ≥ 95%。 |

延迟指标排除缺少所需计时或 Token 数据的请求，审计明细保留缺数。无有效样本时该项暂无法判定。逐模型按未舍入比例判定，任一有效指标未达标则综合未达标，三项均达标则综合达标，其余为暂无法判定。

日报设 429 响应明细表，只展示有 429 的模型并按 429 条目数降序排列。429 比率以全部匹配网关日志条目数为分母，用于诊断。请求可用率基于全天有效请求直接计算，5 分钟窗口可用性沿用独立可用性报告。

检查 `learning/daily-report/YYYY-MM-DD/index.html` 的页内标题“实验室科研模型服务”、完整北京时间统计区间、七个模型、按匹配日志条目数降序的顺序、分子分母和综合状态。页首只展示统计区间。汇总 JSON 内联在 `report-data` 中，生成时间保留在数据内用于追溯。每个日期保留一份可离线打开的 HTML。排查取数差异时先对照同窗的 Loki 路由及状态码计数，再查看明细中的缺字段、重复 ID 和冲突 ID。分页或取数失败时应保留已有日报，排查后再补跑。

## 定时生成与发布

发布日期和时间使用北京时间。每天发布前一完整自然日的日报。

| 时间 | 阶段 | 目标 |
|---|---|---|
| 08:00 | 生成含数据洞察的日报并校验后上传 | `https://pre-zjic.zhejianglab.com`，区块 `rsb-1320473654566223872` |
| 09:30 | 核对同日期的预发结果，上传同一份含数据洞察的 HTML | `https://zjic.zhejianglab.com`，区块 `rsb-1320818356024680448` |

两套环境分别注入各自的 AK/SK。两套环境的日报均包含数据洞察，覆盖5xx 条目最多的模型及其主要状态码、TTFT 与 TPOT 各自的超阈值贡献，以及同模型同口径指标较前一日的最大百分点变化。缺少前一日数据时标明无法对比。添加洞察时验证指标和判定保持一致。预发结果不明确时先核实状态并处理异常。

版式验收检查「数据洞察」位于统计区间下方、模型指标表之前。洞察中提到的当日 SLA 未达标模型名称标红。429 响应明细使用独立表格。

本机 cron 分别以 `# dynamo-sla-daily-report-pre` 和 `# dynamo-sla-daily-report-prod` 标识，调用 `publish_daily_report.py pre` 和 `publish_daily_report.py prod`。用 `crontab -l` 和 `~/.local/state/dynamo-sla/cron.log` 核实运行。两个阶段共用 `~/.local/state/dynamo-sla/cron.lock`。维护时按标识更新并保留其他任务。

定时入口从仓库外的 `~/.config/dynamo-sla/credentials.json` 按环境读取 `ZJIC_ACCESS_KEY` 和 `ZJIC_ACCESS_SECRET`，文件权限为 `0600`。成功回执保存在 `~/.local/state/dynamo-sla/YYYY-MM-DD.{pre,prod}.json`。预发回执记录 `includesDataInsights` 和实际上传文件的 `sha256`。正式任务校验同日预发回执中的区块、洞察标记与当前 HTML 摘要，再上传同一份文件。手动执行对应阶段可传 `--date`，预发复用已生成的 HTML 时另传 `--reuse-existing`。

## 发布到报表广场

临时发布或补发按用户指定的环境、区块和业务日期执行。定时发布按上表执行。

发布前确认所选日期的 HTML 已生成。将 `ZJIC_ACCESS_KEY` 和 `ZJIC_ACCESS_SECRET` 通过运行环境或受管凭据注入。凭据不写入 skill、仓库、HTML 或日志。先用 `--dry-run` 检查文件、日期、区块和摘要，再用同一组参数去掉 `--dry-run` 执行上传。单文件 HTML 无需 `--entry-file`。

```bash
report_day=2026-11-18
python3 learning/daily-report/zjic-report-upload/upload_report.py \
  --origin https://pre-zjic.zhejianglab.com \
  --block-id rsb-1320473654566223872 \
  --report-date "$report_day" \
  --file "learning/daily-report/$report_day/index.html" \
  --dry-run
```

用返回的 `status`、`reportId`、`reportDate`、`revision` 和 `sha256` 核对上传。若返回 `outcomeUnknown: true`，先核实该日期的报表是否已保存，再决定是否重传。检查并提供返回的 `viewUrl`。阅读路由返回 HTTP 200 只说明页面入口可达，登录后的渲染需要另行验证。

报表广场的嵌入窗口由平台控制。遇到留白时检查模板是否限制了内容宽度，并用上传返回的独立阅读页验证布局。只改布局时可从原 HTML 的内联数据重新渲染，再校验指标和判定值是否一致。
