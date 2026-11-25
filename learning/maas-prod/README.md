# LiteLLM OTel A/B 测试

Kafka 入库设计见 [Kafka → ClickHouse 的 OTel Trace 入库设计](kafka-clickhouse-otel-ingestion-design.md)，
包含 Kafka Engine 直接消费 OTLP、物化视图映射与验收方案。

完整接入说明见 [maas-prod Higress 与 LiteLLM 接入 OTel Collector](otel-trace-integration.md)，
包含当前状态、配置位置、跨组件链路关联和验证方法。

`litellm_otel.py` 用于切换 LiteLLM 内置 OTel callback。默认目标是
`maas-prod` 集群、`litellm-test` Namespace 下的 `litellm-test` Deployment，
配置来自 `litellm-test-config` ConfigMap。

## 使用方法

需要 Python 3、PyYAML、kubectl，以及目标资源的读取、patch 和 Pod exec 权限。
以下命令在仓库根目录执行；如果已复制脚本，请替换成实际路径，
例如 `/tmp/littelm_test_otel.py`。

```bash
# 查看配置与实际运行状态
python3 learning/maas-prod/litellm_otel.py status

# 预览操作，不修改集群资源
python3 learning/maas-prod/litellm_otel.py off --dry-run

# A 组：关闭 OTel
python3 learning/maas-prod/litellm_otel.py off

# B 组：开启 OTel
python3 learning/maas-prod/litellm_otel.py on
```

等待输出 `Verified runtime OTel enabled/disabled.` 后，再预热并开始压测。
两组使用相同的模型、并发、请求数据、缓存条件和预热流程，不要在压测中切换。
参数和其他目标的指定方法见 `--help`。

开启后服务名为 `litellm-test`，根请求采样率 100%，有上游时跟随上游采样决定，
通过 OTLP/gRPC 导出到 `otel-collector.nhi.svc.cluster.local:4317`。
不采集对话正文，OTel Logs 和 Metrics 关闭。

## Collector 配置

- `otel-collector-config.yaml` 是 Collector 的原始配置内容，不是 Kubernetes
  ConfigMap 清单。用于 `nhi/otel-collector-config` 的 `data.config.yaml`。
- `otel-collector-deployment.yaml` 启动一个实例，依赖已有的 ConfigMap、
  `otel-collector` ServiceAccount、K8s 元信息读取权限和 4317 Service。
- 当前仅以 INFO 级别输出每批 Trace 数量（`debug` exporter、`verbosity: basic`）。
  Kafka 配置保留在注释中，未启用；不发送 Kafka，也不输出 Span 明细。

## 安全边界

脚本会修改配置并触发重启，不能保证零风险。测试前核对输出的 `Target`，
建议用只允许操作测试 Namespace 的凭证运行。

- 当前测试 Deployment 使用 `Recreate`，切换会短暂中断服务，应在两轮压测之间执行。
- 每次请求明确选择并固定 context；`status` 和 `--dry-run` 也会改变本机 kubectl
  当前 context，但不修改集群资源。
- 保留其他 callback、业务配置和非管理范围的环境变量，不修改 Kubernetes Secret。
  状态检查在容器内使用已有 Master Key，不主动打印密钥。
- 使用 `resourceVersion` 防止覆盖并发修改，并检查部署就绪和运行时 callback 状态。
  不要多人同时操作配置。
- 首次执行可能因补充配置校验标记而重启，即使开关状态已符合要求；先查看 `--dry-run`。
- 修改 callback 会重新序列化 YAML，原注释和格式可能丢失。
- `off` 移除原生 `otel` callback 和脚本管理的 OTel 环境变量，不是恢复任意历史配置，
  也不关闭其他自定义 HTTPX 或 `sitecustomize` 插桩。
- 失败不会自动回滚，可能出现 ConfigMap 已更新而 Deployment 尚未更新的状态。
  应停止压测、排除错误，再重跑同一命令并执行 `status` 确认。
