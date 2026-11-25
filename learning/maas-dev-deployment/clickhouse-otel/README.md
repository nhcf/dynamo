# maas-dev：Kafka OTLP Trace 直接入库 ClickHouse

设计与只读检查日期：2026-11-24。范围为 **maas-dev / `llm-trace-dev`**。
现有 `llm_traces_otlp` 和 `llm_traces_otlp_all` 属于 Vector 链路。新 Kafka 链路使用独立的
明细表、查询表和消费组，复用闲置的 `llm_traces_kafka_queue` 和 `llm_traces_kafka_mv` 名称。
测试 ClickHouse 的两个分片已重建 `llm_traces_kafka_queue`。旧解析 MV 和错误 MV 已移除。
当前队列没有挂载 MV。独立的 `_local` 和 `_all` 目标表已在两个分片创建，新 MV 待 schema 安装并验证解码后部署。

## 1 已核对的现状

- maas-dev 的 `maas-dev/otel-collector-config`：Kafka exporter 写入 `llm-trace-dev`，
  Broker 为 `10.107.159.7:9092` 至 `10.107.159.10:9092`；编码未显式设置，默认 `otlp_proto`。
- 测试 ClickHouse HTTP 接口可访问，只读查询返回版本 `25.4.3.22`。
- `cluster-storage` 当前有 2 个分片，每分片配置 1 个副本。已有明细表使用
  ReplicatedMergeTree，集群查询入口使用 Distributed。
- 已有 `llm_traces_otlp`：snake_case 字段、JSON 字符串 `attributes`、K8s 字段、
  按天分区，按 `(cluster_id, service_name, start_time, trace_id)` 排序，TTL 360 天。
- 名为 `start_time_unix_nano` / `end_time_unix_nano` 的旧 String 列，实际样本是带 9 位
  小数的 ISO 时间字符串，通过 `parseDateTime64BestEffort` 生成物化时间列。
- 旧 `llm_traces_kafka_queue` 消费 `10.200.44.14:9092` 的 `llm-trace`，格式为
  `JSONEachRow`，不是本次 OTLP Topic。其 MV 写入固定 `kafka-test` Span；错误 MV 的
  Topic/Partition/Offset 也使用固定值。不能复用它验证真实 OTLP 解码。

以上为检查时快照；本次未调整这些资源，也未将旧表当作新数据写入目标。

## 2 表和文件

```text
llm-trace-dev：OTLP protobuf 批次
    ↓ ProtobufSingle + otel_trace_clickhouse.proto
llm_traces_kafka_queue：Kafka Engine，每个消费节点 1 个消费者
    ↓ llm_traces_kafka_mv：逐层展开 Resource / Scope / Span
llm_traces_otlp_kafka_v1_local：每个 Span 一行，本地 ReplicatedMergeTree
    ↓
llm_traces_otlp_kafka_v1_all：跨分片查询
    └─ compat：按需适配旧字段名称及 ISO 时间字符串
```

| 文件 | 用途 |
|---|---|
| [otel_trace_clickhouse.proto](otel_trace_clickhouse.proto) | 无 import 的 OTLP wire 解码投影 |
| [01-target-tables.sql](01-target-tables.sql) | 明细本地表与 Distributed 查询表 |
| [02-kafka-queue.sql](02-kafka-queue.sql) | 用旧队列表名新建 Kafka Engine，使用独立消费组 |
| [03-materialized-view.sql](03-materialized-view.sql) | 解码并逐层展开的物化视图 |
| [04-query-views.sql](04-query-views.sql) | 可选的旧字段查询适配视图 |
| [05-validation-queries.sql](05-validation-queries.sql) | 服务分布、链路、属性、重复与消费状态检查 |
| [06-consumer-collector-tables.sql](06-consumer-collector-tables.sql) | 独立消费 Collector 的写入表及 Grafana 视图 |
| [consumer-collector.yaml](consumer-collector.yaml) | maas-dev 独立 Kafka 消费 Collector |
| [grafana-otel-traces.json](grafana-otel-traces.json) | Grafana Trace 搜索、Waterfall 和 Call Map |

部署主链路只需 `.proto` 和 01–03。离线验证直接使用 02 的列定义和 03 的 SELECT，
把数据源换成 `clickhouse local` 的 protobuf 文件输入，并注入测试用 Kafka 位点。

一条 Kafka 消息可能展开出多行。Resource、Scope、Span 的序号与 Kafka 位点一起保存，
可定位消息内部的具体 Span。相邻层级分别 ARRAY JOIN，避免资源与 Span 错配。

## 3 与旧表的兼容和差异

沿用 `trace_id`、`span_id`、`parent_span_id`、`name`、`kind`、`cluster_id`、
`service_name`、`attributes`、Pod/Namespace/Node/Deployment/App/Job 字段。
ID 从 protobuf bytes 转为小写 hex；父 Span 为空时保留空字符串。

新表的原始纳秒列使用 UInt64，直接生成 DateTime64(9)，避免先格式化字符串再解析。
`duration_ms` 仍以毫秒展示；结束早于开始时为 NULL。`kind` 使用 Int32 保留未知枚举值。
`compat` 视图提供旧时间字符串表示，但新增字段、数值类型和 Nullable 耗时意味着它不是
旧表的逐字节 schema 复制。现有查询需显式切换入口并验证，不能直接覆盖旧表名。

新增 Resource/Scope 属性、Scope 名称与版本、schema URL、trace_state、flags、
OTel 状态、丢弃计数、Events、Links、Kafka 来源与摄入时间。
Span 属性保持平面 JSON 对象：字符串、布尔、整数、有限浮点数保留对应 JSON 类型。
空字符串、false、0、空 AnyValue 可以区分。

OTLP AnyValue 允许递归数组和对象。为避免 ClickHouse 有限 Tuple schema 遇到递归类型，
本 schema 将 ArrayValue / KeyValueList 消息按兼容的 length-delimited wire 类型读取为
bytes，保留其完整内容，并在 JSON 中表示为：

```json
{"_otlp_type":"array","protobuf_base64":"..."}
```

KeyValueList 使用 `_otlp_type=kvlist`，bytes 属性使用 `_otlp_type=bytes` 与 `base64`。
NaN/Infinity 使用带类型的对象保留表示。嵌套对象内部尚不支持直接用 JSON 路径检索，
需要额外解码；不能把本方案宣称为任意深度 OTLP JSON 转换器。
未知的未来 protobuf 字段不在投影中，也不会自动保留；如需完整原始 payload 归档，
需要独立的原始消息留存设计。

`cluster_id` 根据本次独立 Topic 固定为 `maas-dev`；服务名与 K8s 信息来自对应 Resource。
不覆盖 `service.name`，不根据 ClickHouse 消费连接推断业务 Pod。

本 Topic 的实际样本还包含 `smg`。明细表保留所有服务，在 `_all` 查询中使用
`WHERE service_name IN ('higress-gateway.higress-system', 'litellm')` 即可筛选。
服务名和 K8s 字段直接从 Resource 字符串属性取值，Resource JSON 单独保留供查询。
Request Trace 的 OTLP Logs 不属于此 Trace schema，应使用独立 Topic/表。

## 4 存储、重复与错误处理

- 初始采用 7 天测试 TTL，按 **ingested_at 的天** 分区和过期，避免迟到 Span 入库后立即
  被源时间 TTL 清理；这与旧表按 start_time 分区、360 天 TTL 不同。
- 保留原有服务和时间的排序方向，将 `span_id` 加入排序键，并为 `trace_id` 加 Bloom 索引。
- 这是至少一次入库，MergeTree 不自动去重。按 `(cluster_id, trace_id, span_id)` 统计唯一
  Span；如果要精确去重查询或增加聚合表，需要另行设计，不能直接把原始行数当请求数。
- `kafka_skip_broken_messages=0`、默认错误模式：解析错误会让该消费批次失败。
  通过 `system.kafka_consumers` 的异常及 Lag 配置告警。
  当前版本已有 stream 错误模式，但本草案没有部署隔离错误流；上线前必须明确坏消息
  定位、人工处置和恢复流程。
- protobuf 可解码但 ID/时间不符合要求的 Span 保留为 `is_valid=0`，不静默过滤。
  超出 Int64 纳秒范围的时间仅在展示列封顶，原始 UInt64 保留，并标记无效。
- MV 写本地表，不经异步 Distributed 写入；写入返回、Kafka 提交与磁盘 fsync/复制确认
  不是同一个跨系统事务。进程故障、重平衡和多 MV 部分失败仍可能重复，不能承诺 exactly-once。

## 5 部署前提和顺序

1. 核对每个 ClickHouse 消费节点到全部 Broker 的连通性及 `advertised.listeners`。
   本机能访问 Kafka 不代表 ClickHouse Pod 能访问。
2. 将 `.proto` 放到每个消费者服务器配置的 `format_schema_path` 下，文件名保持
   `otel_trace_clickhouse.proto`，权限允许 ClickHouse 读取。本次没有安装该文件到服务器。
3. 使用独立消费组 `clickhouse-maas-dev-otlp-v1`。所有分片/副本使用同组；总消费者数量
   不能超过有效分区并发。当前 2 个分片、每分片 1 个消费者，共 2 个，Topic 为 24 分区。
   消费节点增多或副本拓扑变化时需重新核对并发与重复写入行为。
4. 使用 01 核对已创建的独立目标表，首次部署时用 01 创建。如果队列尚未切换，
   在旧对象所在节点保存 `SHOW CREATE TABLE`，先移除旧 `llm_traces_kafka_mv` 和
   `llm_traces_kafka_errors_mv`，再移除旧 `llm_traces_kafka_queue`。只读核对时这三个对象
   仅在一个分片存在，应在实施前重新检查。旧 `llm_traces_kafka_errors` 可继续作为历史记录表。
5. 使用 02 核对已创建的新队列定义，首次部署时用 02 创建队列。
   确认独立目标表与 Kafka 队列在两个分片状态一致，再执行 03
   启动消费。启动前应先在目标版本验证 protobuf 解码。需要旧时间字符串的
   查询再执行 04。SQL 使用
   `ON CLUSTER 'cluster-storage'`，从一个入口提交一次，避免逐节点重复执行。
   不使用 `IF NOT EXISTS` 掩盖表结构不一致；失败后先核对各节点完成状态。
   在执行 03 前明确新消费组起始策略。SQL 未修改服务端 librdkafka 的
   `auto.offset.reset`；应按当前版本支持的配置或停机位点重置选择 earliest/latest。
   不要把 Collector 的 `initial_offset` 当成 Kafka 表设置。
6. 以已知 TraceId 发起 Higress → LiteLLM 测试请求，再执行 05 检查数据与链路。
   日常查询存储表；不要直接 SELECT Kafka Engine 消费表。
7. 检查各节点 `system.kafka_consumers`、消费组 Lag、最老消息年龄、写入速率、parts/merge
   和磁盘占用。检查结果快照中 dev Topic 保留时间仅 10 分钟，必须留足恢复余量。

回退时先卸载新 MV 停止消费，保留独立目标表及新消费组位点。需要恢复旧 Kafka 对象时，
先移除同名的新队列和 MV，再用保存的 DDL 恢复旧版本。整个切换只操作 Kafka 对象及
新增的目标表，Vector 的两张表继续服务原链路。超过 Kafka 保留窗口后，历史消息可能已过期。

## 6 验证范围

已经完成：

- 使用官方 OTLP v1.5.0 protobuf 定义作为参考解码器，对照真实 Kafka LiteLLM 消息的
  2 个 Span，ID、时间、属性和层级映射一致。只读手动分配分区，关闭自动位点提交和保存。
- 13 个合成 Span：2 个服务、多个 Resource/Scope、12 个有效 Span 与 1 个无效 Span。
  覆盖 false、0、空字符串、大整数、bytes、空/递归数组与对象、NaN/Infinity、Events、Links。
- 本地 ClickHouse `25.12.4.35` 完成 ProtobufSingle 解码及临时 MergeTree 落表测试；
  13 行保留，12 行有效，无效结束时间对应 NULL 耗时，正常耗时为 123.456789 ms。
- 用 Null 表模拟 Kafka 输入并挂载实际 MV，核对 13 行落表数据、消息内序号和注入的 Kafka 位点。
- 测试服务器 `25.4.3.22` 用只读 SELECT 对合成 RowBinary 结构化输入执行同一映射 SQL，
  13 行结果与本地一致。这个步骤验证映射 SQL，不等于测试服务器已经完成 protobuf 解码。
- 建表与建视图 DDL 通过服务器 `EXPLAIN AST` 语法解析。
  旧格式适配的时间字符串在服务器验证保留 9 位小数；语法解析不代替实际建表与消费测试。

尚未完成：服务器端安装 schema 后的 **25.4 ProtobufSingle 解码**、Kafka Engine/MV
端到端摄入、复制持久性与故障恢复、实际 Higress payload 验证、链路压测。
真实样本检索未获得 Higress 消息，Higress 当前仅用标准 OTLP 合成数据验证映射。
业务 payload 未写入仓库，认证密码不在 SQL/文档中。

## 7 Grafana 看板

看板已部署到 [pre-isg Grafana](http://pre-isg.fed.zhejianglab.org/grafana/d/maas-dev-otel-traces/maas-dev-7c-otel-trace-explorer)，
使用现有 `ClickHouse-LLM-Traces` 数据源，查询 `llm_observability.otel_traces_maas_dev_grafana`。
如需在另一套 Grafana 部署，导入 JSON 并选择接入测试 ClickHouse 的数据源。

- **Trace 搜索**按时间范围列出最近 100 条 Trace，点击 Trace ID 可查看详情。列表耗时为时间范围内可见 Span 的时间窗口。
- **Waterfall**按 Trace ID 展示最多 1000 个有效 Span 和耗时。也可手工输入 Trace ID 查询保留期内的数据。
- **Call Map**展示最早 200 个有效 Span，按 `parent_span_id` 计算调用深度，父节点在左、子节点在右。
  同一列按开始时间排列，红色表示 OTel 错误状态。
  父节点缺失或超出显示范围时，保留子节点。详情面板按 `span_id` 去除重复摄入。

## 8 临时消费链路

maas-dev 的 `otel-kafka-clickhouse` Deployment 以一个副本运行，通过 `otel-ch-maas-dev-v1`
消费组解码 `llm-trace-dev` 的 OTLP protobuf，使用 ClickHouse exporter 写入独立的
`llm_observability.otel_traces_maas_dev` Distributed 表。查询视图供 Grafana 使用。
ClickHouse Kafka Engine 等待 schema 的方案继续保留，当前队列未挂载 MV。

首次消费从 `latest` 开始。Collector 同步写入目标分片，写入成功后标记 Kafka 位点。
写入与提交之间的失败可能导致重放。同步重试耗尽或永久错误可能阻塞分区，可检查消费组
Lag、Collector 异常和最后入库时间。目标表按入库时间保留 7 天。

清单通过 `maas-dev/otel-kafka-clickhouse-auth` Secret 读取测试库用户名和密码。部署顺序为
06 SQL、Secret、Collector 清单，最后验证数据再切 Grafana。临时停止消费可将 Deployment
副本设为 0，恢复后从已提交位点继续。

真实请求 `39a86e5d0ee962dfa00221a8618686db` 验证了 Kafka 位点推进、ClickHouse 入库和
Grafana 查询。看板返回 51 个 Span 和 50 条父子连线，LiteLLM 入口的父节点为 Higress 出站 Span。

## 9 参考

- [Kafka Engine 与虚拟列](https://clickhouse.com/docs/reference/engines/table-engines/integrations/kafka)
- [ProtobufSingle：无长度前缀的单条 protobuf](https://github.com/ClickHouse/ClickHouse/blob/master/docs/reference/formats/Protobuf/ProtobufSingle.mdx)
- [OTLP Trace v1.5.0 字段定义](https://github.com/open-telemetry/opentelemetry-proto/blob/v1.5.0/opentelemetry/proto/trace/v1/trace.proto)
- [AnyValue 与 KeyValue 定义](https://github.com/open-telemetry/opentelemetry-proto/blob/v1.5.0/opentelemetry/proto/common/v1/common.proto)
- [前期 Kafka → ClickHouse 设计讨论](../../maas-prod/kafka-clickhouse-otel-ingestion-design.md)
