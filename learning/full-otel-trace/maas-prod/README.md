# maas-prod 全链路 Trace

| 分类 | 入口 |
|---|---|
| `integration/` | [Higress 与 LiteLLM 接入 Collector](integration/otel-trace-integration.md) |
| `collector/` | [Kafka 导出配置](collector/otel-collector-config.yaml)、[部署清单](collector/otel-collector-deployment.yaml) |
| `clickhouse-kafka/` | [生产上线手册、DDL 生成与回退](clickhouse-kafka/kafka-clickhouse-production-runbook.md) |
| `grafana/` | [生产看板 JSON](grafana/grafana-otel-traces.json) |
| `litellm/` | [OTel A/B 测试与开关脚本](litellm/README.md) |
