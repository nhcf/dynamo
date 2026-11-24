# 5xx 取证参考

## Higress Loki

以当日日报的路由和值域为准，参考 [生成脚本](../../../../learning/daily-report/run_report.py) 的数据源、标签和接口过滤。下例按路由、状态码、响应详情和标记对账。将目标路由替换为当日范围。全天聚合用 `[24h]`，逐小时查询用 `[1h]` 和 1 小时步长，每个点对应其前一小时。检查查询完整性，遇到窗口边界差异时核对原始日志时间戳。

```logql
sum by (route_name, response_code, detail, response_flags) (
  count_over_time(
    {filename="/var/log/proxy/access.log",authority="i-nhi.zhejianglab.org",method="POST",route_name="<route>",response_code=~"5.."}
    | json p="path",detail="response_code_details"
    | p=~"^/maas/v1/(chat/completions|messages)([?].*)?$"
    [1h]
  )
)
```

| 日志证据 | 下一步 |
|---|---|
| `via_upstream` | 先确认 `upstream_cluster`，再追对应上游的错误和部署。Higress 的 `upstream_host` 可能是 LiteLLM Pod 地址。 |
| `upstream_reset_before_response_started{connection_termination}` 且 `UC` | 按 `upstream_host` 与秒级时间聚类，核对多模型是否指向同一实例。 |
| `response_timeout` 且 `UT` | 核对耗时、终态与上游处理进度。相近耗时支持存在超时边界，计时器归属需要独立证据。 |

进一步分析时查询 `duration`、`upstream_service_time`、`upstream_host`、`upstream_cluster` 及必要的请求 ID。使用生成脚本的分页思路，触及上限时按时间切片并检查覆盖。聚合失败或只取得部分日志时保留缺测说明。

## LiteLLM 调用记录

使用已授权的只读连接查询 `LiteLLM_SpendLogs`。网关路由展示名可能与 `model_group` 有差异。先核对模型组，按部署 ID、状态、错误码与错误类型汇总，再取代表性错误签名。下例参数依次为按数据库实际存储口径换算的开始和结束时间、模型组。

```sql
SELECT
    model_id,
    status,
    metadata::jsonb #>> '{error_information,error_code}' AS error_code,
    metadata::jsonb #>> '{error_information,error_class}' AS error_class,
    count(*) AS entries
FROM "LiteLLM_SpendLogs"
WHERE "startTime" >= $1 AND "startTime" < $2 AND model_group = $3
GROUP BY 1, 2, 3, 4
ORDER BY entries DESC;
```

- 按需要核对 `error_message`、`attempted_retries`、`max_retries`、`cache_hit`。缓存命中只能证明有返回，后端健康需要查看实际调用证据。
- 失败记录若缺 `api_base`，以错误正文中的目标地址、同部署历史成功调用和 Controller 记录交叉核验。在 case 中注明地址证据来自哪一侧。
- 比较网关终态与调用起止时间。若两层计数不同，先核对它们的统计单位、入口范围、跨日边界、重试回退以及流式响应头发出后的终止语义。

## 历史作业与容器

- MaaS Controller 按作业 ID 搜索服务实例、地址、路由更新和 `/v1/models` 探测事件。遇到同一地址曾登记给多个作业时，按时间校对作业和地址的对应关系。
- 依据网关上游 IP 和事件时间关联 Kubernetes Pod。用 `kubectl get pod -o json` 核对 `lastState.terminated`、退出原因和重新运行时间，用 `kubectl logs --previous --timestamps` 检查关闭、排空及探针日志。注明查到的日志覆盖，确认当前状态所见的退出记录确实对应故障时间。
- 退出码 137 可以与应用关闭日志和 Higress 503 时间线互证。归因为 OOM、探针失败或人工重启需要对应事件或其他直接证据。
