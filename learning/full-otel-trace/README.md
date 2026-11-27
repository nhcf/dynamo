# 全链路 OpenTelemetry Trace

| 环境 | 入口 | 配套文件 |
|---|---|---|
| `ack-LB97i2CZNR` | [Dynamo 部署与 Trace 接入](ack-LB97i2CZNR/index.html) | [采集计划](ack-LB97i2CZNR/trace/trace-collection-plan.md)、[请求面清单](ack-LB97i2CZNR/trace/request-plane-trace-checklist.html)、[跨集群 LiteLLM 验证](ack-LB97i2CZNR/validation/08-litellm-cross-cluster-integration.md) |
| `maas-dev` | [Kafka Engine 验证与 Grafana](maas-dev/README.md) | [入口 Collector](maas-dev/collector/k8s-cm-otel-collector-config.yaml)、[protobuf schema](maas-dev/clickhouse-kafka/otel_trace_clickhouse.proto)、[测试看板 JSON](maas-dev/grafana/grafana-otel-traces.json) |
| `maas-prod` | [生产环境索引](maas-prod/README.md) | [生产上线手册](maas-prod/clickhouse-kafka/kafka-clickhouse-production-runbook.md)、[LiteLLM OTel A/B](maas-prod/litellm/README.md)、[生产看板 JSON](maas-prod/grafana/grafana-otel-traces.json) |

Kafka 批次解码、位点和重复处理见 [Kafka → ClickHouse 设计](kafka-clickhouse-otel-ingestion-design.md)。
历史配置和验收证据按集群保存在对应目录。[NHI 指标看板](../nhi-dashboard/README.md)和
[maas-dev 平台审计](../maas-dev-deployment/index.html)仍从各自入口访问。
