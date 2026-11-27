# maas-prod Higress 与 LiteLLM 接入 OTel Collector

核对日期：2026-11-16。本文介绍接入位置、开启配置与验证方法。
集群状态来自当天的 ConfigMap、Deployment、Service Endpoint、Higress 运行配置和
LiteLLM callback 接口；以下开启示例尚未应用到集群。

## 1 当前状态与接入目标

| 组件 | 资源位置 | 核对结果 |
|---|---|---|
| Higress 2.2.2 | `higress-system/higress-gateway` | 7 个就绪副本；全局配置未启用 OTel；抽查实例没有 tracing provider |
| 正式 LiteLLM | `litellm-system/litellm` | 9 个就绪副本；未设置 OTel 环境变量；配置和抽查实例均无 OTel callback |
| 测试 LiteLLM | `litellm-test/litellm-test` | 1 个就绪副本；OTel 环境变量和 callback 均已关闭，运行时检查一致 |
| Collector | `nhi/otel-collector` | 1 个就绪副本；接收 OTLP/gRPC，输出 INFO 级别 Trace 数量汇总 |

测试 LiteLLM 曾在 2026-11-12 开启并通过 mock 请求验证；当前状态已变为关闭。
maas-dev 的 Higress 路由白名单不代表 maas-prod 也已启用。

开启后，HTTP 请求与 Trace 分别走以下路径：

```text
业务请求：客户端 → Higress → 正式或测试 LiteLLM → 模型服务

Trace：  Higress ── OTLP/gRPC ──┐
                                ├→ otel-collector.nhi.svc.cluster.local:4317
         LiteLLM ─ OTLP/gRPC  ──┘  → K8s 信息补充、属性处理、批处理
                                  → INFO 数量汇总
```

当前没有 Kafka、ONE 或 Vector 导出，也不通过此 Collector 采集 Logs/Metrics。
Higress 的 Promtail 访问日志和 LiteLLM 的 Prometheus 指标是独立配置。

本文命令使用以下函数：每次请求先选择 `maas-prod`，再显式固定 context。

```bash
kprod() {
  kubectl config use-context maas-prod >/dev/null &&
    kubectl --context maas-prod "$@"
}
```

## 2 Collector 配置

入口统一使用 `otel-collector.nhi.svc.cluster.local:4317`。
Service 的选择器为 `app: otel-collector`，核对时有一个 Ready Endpoint。
当前只启用 gRPC 4317，没有启用 OTLP/HTTP 4318。

| 配置 | 位置或值 |
|---|---|
| ConfigMap | `nhi/otel-collector-config` 的 `data.config.yaml` |
| 容器读取位置 | `/conf/config.yaml` |
| 镜像 | `harbor.zhejianglab.com/zjnc-tech/opentelemetry-collector-contrib:0.120.0` |
| ServiceAccount | `nhi/otel-collector` |
| ClusterRole / Binding | `otel-collector-k8sattributes`，允许读取 K8s 元信息 |
| CPU request / limit | `200m / 500m` |
| 内存 request / limit | `384Mi / 768Mi` |

关键管线如下；此片段引用的 processor 定义见[完整配置](../collector/otel-collector-config.yaml)，
不能单独替代完整文件。

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317

exporters:
  debug:
    verbosity: basic

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, k8sattributes, transform, batch]
      exporters: [debug]
  telemetry:
    logs:
      level: info
```

- `memory_limiter`：每秒检查，限制 640 MiB，突增余量 128 MiB。
- `k8sattributes`：按连接来源关联 Pod，补充 Pod 名称/IP、Namespace、Node、
  Deployment，以及 `app.name`、`jobname` 标签。连接经过代理或 NAT 时需核对关联结果。
- `transform`：补齐 host、HTTP 和 peer 属性，调整 Higress、SGLang Router 的部分
  SpanKind 与名称。部分规则带固定默认值，接入不同接口时应检查是否符合实际语义。
- `batch`：发送阈值 1024 条，超时 5 秒。

`debug` 是 exporter 名称，不是日志级别。`verbosity: basic` 以 INFO 输出每批
记录数量，不输出 Trace ID 或 Span 属性明细；仅设置日志级别为 INFO 并不能替代
`verbosity` 配置。[Debug exporter 说明](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.120.0/exporter/debugexporter/README.md)

Kafka 参数以注释保留，原 topic 为 `llm-trace-prod`。当前 Trace 不会进入 Kafka
或下游查询库。日志汇总只能证明有数据进入出口，不能用于查询完整调用链。

本地 `otel-collector-config.yaml` 是原始 Collector 配置，不是 ConfigMap 清单。
[Deployment 清单](../collector/otel-collector-deployment.yaml)依赖已有的 Service、ServiceAccount、
ClusterRole/Binding 和 ConfigMap。Collector 配置变更后应重启并验证新实例：

```bash
kprod -n nhi rollout restart deployment/otel-collector
kprod -n nhi rollout status deployment/otel-collector --timeout=180s
```

## 3 Higress 接入配置

配置入口是 `higress-system/higress-config` 的 `data.higress`，不是
`data.mesh.defaultConfig.tracing`。当前 `data.higress` 没有启用 tracing，
也没有发现包含 tracing 补丁的 EnvoyFilter 或指向 Collector 的 McpBridge registry。

下面是应合并到 `data.higress` 内部的配置片段，不是可直接 apply 的完整 ConfigMap：

```yaml
tracing:
  enable: true
  sampling: 1
  timeout: 500
  opentelemetry:
    service: otel-collector.nhi.svc.cluster.local
    port: 4317
```

`sampling` 单位是百分比，`1` 表示 1%，`100` 表示 100%；`timeout` 单位是毫秒。
该片段控制全局采样，不能直接作为“仅采集测试路由”的开关。正式流量是否开启、
采样比例及路由范围需要单独确定；A/B 的完整采样应限定在测试请求范围内。
[Higress 全局配置说明](https://higress.ai/docs/latest/user/configmap/)

抽查 Envoy 已存在 Kubernetes 服务发现产生的集群：
`outbound|4317||otel-collector.nhi.svc.cluster.local`。
因此这里使用真实 K8s Service 名称，不复用 maas-dev 的 `otel-collector.static`。
出现服务发现 cluster 只说明目标已被发现，不代表 OTel exporter 已启用。

当前 HTTP connection manager 虽有默认采样字段，但没有
`envoy.tracers.opentelemetry` provider，bootstrap 也没有 tracing 配置，
不能据此认定已向 Collector 发送 Trace。

Higress 将 ConfigMap 转为 Envoy 配置。开启后应检查运行配置，不能只检查 ConfigMap：

```bash
kprod -n higress-system exec deployment/higress-gateway -c higress-gateway -- \
  pilot-agent request GET config_dump > /tmp/higress-maas-prod-config-dump.json

python3 - <<'PY'
import json

def inspect(value):
    if isinstance(value, dict):
        if value.get('name') == 'envoy.tracers.opentelemetry' and 'typed_config' in value:
            print(json.dumps(value, indent=2))
        for child in value.values():
            inspect(child)
    elif isinstance(value, list):
        for child in value:
            inspect(child)

with open('/tmp/higress-maas-prod-config-dump.json') as source:
    inspect(json.load(source))
PY
```

需要核对 provider、目标 cluster、超时和服务名。集群有多个网关副本，以上命令
只抽查一个；正式验收需覆盖所有副本的配置更新情况。

当前正式 LiteLLM 路由主要使用 `/maas/...`，目标为
`litellm.litellm-system.svc.cluster.local:4000`；测试路由使用 `/test/...`，目标为
`litellm-test.litellm-test.svc.cluster.local:4000`。测试路由包括
`test-litellm-service-deepseek-v4-flash-test` 和 `test-litellm-service-mock-model`。
配置路由白名单时应匹配实际生成的 Envoy 路由名，不能直接照搬 dev 名称。

## 4 LiteLLM 接入配置

正式与测试实例的配置入口不同，不要混用：

| 对象 | 正式 LiteLLM | 测试 LiteLLM |
|---|---|---|
| Namespace | `litellm-system` | `litellm-test` |
| Deployment | `litellm` | `litellm-test` |
| ConfigMap | `litellm-config` | `litellm-test-config` |
| 容器配置路径 | `/app/config/config.yaml` | `/app/config/config.yaml` |
| 建议 OTel 服务名 | `litellm` | `litellm-test` |

在现有 `litellm_settings` 中加入内置 callback：

```yaml
litellm_settings:
  callbacks: [otel]
```

这是合并片段。若已有其他 callback，需要保留；正式实例当前使用的 Prometheus
callback 不应被覆盖。只配置环境变量而不加载 `otel` callback，不能完成该接入。
[LiteLLM OTel 接入说明](https://docs.litellm.ai/docs/observability/opentelemetry_integration)

Deployment 的 `litellm` 容器使用以下环境变量；正式实例将服务名改为 `litellm`：

```yaml
env:
  - name: OTEL_EXPORTER
    value: otlp_grpc
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: http://otel-collector.nhi.svc.cluster.local:4317
  - name: OTEL_EXPORTER_OTLP_PROTOCOL
    value: grpc
  - name: OTEL_SERVICE_NAME
    value: litellm-test
  - name: OTEL_TRACES_EXPORTER
    value: otlp
  - name: OTEL_TRACES_SAMPLER
    value: parentbased_always_on
  - name: OTEL_LOGS_EXPORTER
    value: none
  - name: OTEL_METRICS_EXPORTER
    value: none
  - name: OTEL_PROPAGATORS
    value: tracecontext,baggage
  - name: OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT
    value: NO_CONTENT
```

无有效父上下文时，根请求采样率为 100%；有父上下文时跟随其采样决定。
`NO_CONTENT` 关闭 OTel 对话正文采集，不等同于清除所有请求元信息，
也不改变其他日志和计费系统的采集策略。

测试实例可使用已实现的脚本，不必手工 patch：

```bash
python3 learning/full-otel-trace/maas-prod/litellm/litellm_otel.py status
python3 learning/full-otel-trace/maas-prod/litellm/litellm_otel.py on --dry-run
python3 learning/full-otel-trace/maas-prod/litellm/litellm_otel.py on
python3 learning/full-otel-trace/maas-prod/litellm/litellm_otel.py off
```

脚本修改 callback 与环境变量，等待重启就绪并检查运行时状态。测试 Deployment
使用 `Recreate`，会短暂中断服务；在压测轮次之间执行。它默认不修改正式 LiteLLM。
详细边界见[测试操作说明](../litellm/README.md)。

正式实例也需要重启才能可靠加载配置；本次文档整理未执行任何开启或重启动作。

## 5 跨组件链路关联

LiteLLM 内置 OTel callback 可以提取入站 W3C `traceparent`，将入口 Span 关联到
上游 Trace。Higress 是否创建和传播了正确父上下文，必须通过实际请求验证，
不能只看两个组件都能连接 Collector。

maas-prod 当前两个 LiteLLM Deployment 都没有挂载 dev 使用的
`/opt/otel/sitecustomize.py`。因此，启用上述原生 callback 不等于已启用 dev 的
自定义 HTTPX Client Span 和出站上下文注入，也不能承诺模型后端已与网关串联。
若要覆盖 LiteLLM 到模型服务，应另行验证 HTTP 客户端插桩、请求头传播、父子 Span
关系及 SSE 流式响应的收尾行为。

Collector 的 `transform` 补充属性，但不会替代缺失的上下文传播，也不会自动
将两个不同 Trace ID 合并为一条调用链。

## 6 验证与 A/B 测试

先检查入口服务与实例就绪：

```bash
kprod -n nhi get deployment,pods,service -l app=otel-collector
kprod -n nhi get endpointslices -l kubernetes.io/service-name=otel-collector
python3 learning/full-otel-trace/maas-prod/litellm/litellm_otel.py status
```

测试 LiteLLM 开启并就绪后，可在一个终端转发端口：

```bash
kprod -n litellm-test port-forward service/litellm-test 14000:4000
```

在另一个终端使用有效测试 Key（预先通过环境变量 `TEST_API_KEY` 提供），发送
mock 请求；以下请求绕过 Higress，只验证 LiteLLM 到 Collector：

```bash
TEST_TRACE_ID=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
curl -sS http://127.0.0.1:14000/v1/chat/completions \
  -H "Authorization: Bearer ${TEST_API_KEY}" \
  -H 'Content-Type: application/json' \
  -H "traceparent: 00-${TEST_TRACE_ID}-8f1de9dd231e5486-01" \
  -d '{"model":"mock-model","messages":[{"role":"user","content":"OTel smoke test"}],"max_tokens":16}'
```

LiteLLM 和 Collector 都有批处理，等待导出后查看 Collector 日志：

```bash
kprod -n nhi logs deployment/otel-collector --since=2m --tail=30
```

预期看到 INFO `Traces` 汇总，包含 `resource spans` 和 `spans` 数量。汇总不会
显示上面的 Trace ID；要验证同 Trace ID 和父子关系，需要短时启用明细或使用
可查询的 Trace 后端，验证结束后恢复当前 basic 出口。
不要把共享 Collector 的汇总条数直接归因于某一个请求。

完整链路验证需要另外通过真实 Higress 测试域名和 `/test/v1/chat/completions`
发请求，并确认命中了预期路由；直接访问 LiteLLM 不能证明 Higress 接入成功。

仅比较 LiteLLM 原生 OTel 开销时，A 组 `off`、B 组 `on`，两组保持相同模型、
负载、缓存、预热和资源条件；Collector 保持同一份 basic 配置。
让 Higress 的配置在两组之间保持一致，避免同时改变网关采样导致混入额外变量。
INFO 汇总仍有少量日志开销，这组测试代表“原生 LiteLLM OTel 到 Collector 汇总出口”，
不代表自定义 HTTPX 插桩、Kafka 导出或整个生产链路的性能。

## 7 与 maas-dev 的配置对比

以下对比于 2026-11-16 重新读取两个集群配置，并检查 dev Higress 的路由运行配置
和 dev LiteLLM 的活动 callback。prod 状态见第 1 节。

### Higress

| 项目 | maas-dev | maas-prod |
|---|---|---|
| 全局 tracing | `enable: true` | 未配置启用 |
| 全局采样率 | 100% | 没有生效的 OTel provider，不能只看默认采样字段 |
| gRPC 超时 | 500 ms | 未配置 OTel 超时 |
| tracing 服务名 | `otel-collector.static:4317` | 尚未配置；现有 K8s 目标为 `otel-collector.nhi.svc.cluster.local:4317` |
| 路由限制 | `higress-tracing-litellm-routes-only` EnvoyFilter | 未发现 tracing EnvoyFilter |
| Collector 出口 | maas-dev Namespace 中的 Collector | nhi Namespace 中的 Collector |

dev 全局开关为开启，但最终是否采集由路由补丁决定：

- 先将所有路由的 `client_sampling`、`random_sampling`、`overall_sampling` 设为 0。
- 对 `litellm-service-gpt-4o-mini` 和 `litellm-service-trace-test` 精确匹配，设为 100。
- 运行配置中前者对应 1 条正则规则，后者对应精确路径和前缀共 2 条规则，
  共 3 条规则开启，其余 254 条关闭。数量随路由变化，不能当作固定 Ingress 数量。
- 该白名单是两个精确名称，不是 `litellm-service-*` 动态通配；新增路由需更新补丁。

dev 的 `default` McpBridge 把 `otel-collector.static` 映射到
`172.21.3.84:4317`。另有 `otel-collector-bridge` DNS registry，指向
`otel-collector.maas-dev.svc.cluster.local`；当前 tracing 配置引用的是前者。
prod 已通过 K8s 服务发现获得 Collector cluster，无需复制 dev 的 ClusterIP。

### LiteLLM

| 项目 | dev `litellm-system/litellm` | prod `litellm-system/litellm` | prod `litellm-test/litellm-test` |
|---|---|---|---|
| 就绪副本 | 1 | 9 | 1 |
| 镜像 tag | `v1.85.0-e610b07` | `v1.85.0-dc363b68` | `v1.85.0-e610b07` |
| OTel callback | 已配置且运行时已加载 | 未配置 | 当前已关闭 |
| OTel 环境变量 | 已配置，导出至 dev Collector | 无 | 当前无；可通过开关脚本添加 |
| 自定义 HTTPX 插桩 | 有 `sitecustomize.py` | 无 | 无 |
| 其他观测 callback | Langfuse、Prometheus | Prometheus | 当前为空 |
| CPU request / limit | `220m / 2` | `2 / 2` | `2 / 2` |
| 内存 request / limit | `1Gi / 4Gi` | `6Gi / 6Gi` | `6Gi / 6Gi` |
| 重启策略 | RollingUpdate | RollingUpdate | Recreate |

dev 的主要配置为：

```yaml
litellm_settings:
  callbacks: [otel]
  success_callback: [langfuse, otel, prometheus]
  failure_callback: [langfuse, otel]
```

Deployment 同时设置 `PYTHONPATH=/opt/otel`，挂载 `/opt/otel/sitecustomize.py`。
该文件会包装同步和异步 `httpx.Client.send`：

- 将 LiteLLM 入口 Span 关联到自定义上下文，并补充 HTTP Server 属性。
- 对有有效父 Span 的 HTTPX 调用创建 HTTP Client Span，注入 `traceparent`。
- 记录响应状态及异常，流式响应尝试在关闭连接时结束 Span。
- 不只覆盖模型请求，也可能覆盖本地 Prisma HTTP 通信，增加内部 Span 数量。

因此，dev 的采集量和开销包含原生 callback 与额外 HTTPX 包装；仅在 prod 执行
脚本 `on` 不会复制这层自定义插桩。dev 同时声明了健康检查排除和日志关联环境变量，
但自定义 HTTPX 包装没有直接读取这些开关，不能仅凭环境变量认定相关功能均生效。

两套配置均以不采集 OTel 对话正文为目标：dev 设置 `NO_CONTENT`；prod 测试实例
当前关闭 OTel，脚本开启时设置相同值。dev 未显式设置 `OTEL_TRACES_SAMPLER`，
prod 开关脚本显式设置 `parentbased_always_on`。

### Collector 出口及性能对比口径

| 项目 | maas-dev | maas-prod |
|---|---|---|
| 活动 exporter | 仅 Kafka | 仅 `debug/basic` |
| Kafka topic | `llm-trace-dev` | `llm-trace-prod` 仅保留在注释中，未启用 |
| INFO Trace 汇总 | 无 debug 出口 | 有每批数量汇总 |

不能直接把 dev 与 prod 的吞吐或延迟差异归因为 OTel：镜像、并发拓扑、资源请求、
Langfuse/Prometheus callback、自定义 HTTPX 插桩和 Collector 出口都不完全相同。
正式与测试路由范围也不同，dev 白名单不能原样用于 prod 测试路由。

当前 A/B 的合适口径是在 prod 同一个 `litellm-test` Deployment 上，仅切换原生
OTel callback 和脚本管理的环境变量，保持 Collector、缓存、负载及资源条件不变。
如果目标改为评估“dev 全套观测配置”的开销，则需要单独纳入 HTTPX 插桩和其他
callback，并注明已扩大实验范围。

## 8 维护位置

- [Collector 原始配置](../collector/otel-collector-config.yaml)
- [Collector 单实例 Deployment](../collector/otel-collector-deployment.yaml)
- [LiteLLM 开关脚本](../litellm/litellm_otel.py)
- [测试操作与安全说明](../litellm/README.md)

本文记录的启用状态会随测试开关变化。使用前以集群配置、运行时 callback 和
Envoy 实际配置为准，不能将历史接入成功记录视为当前仍在开启。
