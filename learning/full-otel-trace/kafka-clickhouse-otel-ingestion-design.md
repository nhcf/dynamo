# Kafka → ClickHouse 的 OTel Trace 入库设计

整理日期：2026-11-24。本文归纳前述集群检查和方案讨论，供实现、评审与压测使用。
**这是待验证的设计，尚未部署；文中的配置片段不是可以直接执行的完整建表脚本。**
上游接入位置见 [Higress 与 LiteLLM 接入 OTel Collector](maas-prod/integration/otel-trace-integration.md)，
该文集群快照日期为 2026-11-16，本文涉及 Kafka 的快照日期为 2026-11-24。

## 1 结论与数据流

可以不部署独立的 Consumer Collector，直接使用 ClickHouse Kafka Engine 消费 Kafka，
再通过物化视图拆分 OTLP 批次、写入自定义 Span 明细表。
这样解码、字段映射、错误处理和数据兼容性由 ClickHouse 侧承担。

```text
Higress / LiteLLM / Dynamo
          │ OTLP Trace
          ▼
入口 OTel Collector：补充环境、服务、Pod 等属性
          │ Kafka exporter：otlp_proto
          ▼
Kafka：llm-trace-prod
          │ 同一个消费组分配分区
          ▼
ClickHouse Kafka Engine：ProtobufSingle + 显式 protobuf schema
          │ ExportTraceServiceRequest 批次
          ▼
物化视图：ResourceSpans → ScopeSpans → Spans，逐层展开
          │ 每个 Span 一行
          ▼
自定义 MergeTree 系列表：检索、链路关联、统计
```

Kafka 中一条 OTLP 消息可能包含多个资源、多个 Scope、多个 Span，不能把 Kafka 消息数
当成请求数或 Span 数。一个业务请求也可能产生多个组件的 Span。

## 2 已检查的环境状态

以下为此前只读检查的时间点快照，实施前需要重新确认，不能作为实时状态。

| 项目 | maas-dev | maas-prod |
|---|---|---|
| 入口 Collector | `maas-dev/otel-collector` | `nhi/otel-collector`，1 个就绪实例 |
| Trace 导出 | Kafka，Topic 为 `llm-trace-dev` | 仅 `debug` 导出，`basic` 数量汇总，INFO 日志；Kafka 配置已注释 |
| Kafka 编码 | 未显式设置，v0.120.0 默认 `otlp_proto` | 恢复导出时需要显式确认编码 |
| Topic | `llm-trace-dev` | `llm-trace-prod` |
| 分区数 | 24 | 3 |
| 副本数 / ISR | 1 / 1 | 3 / 3 |
| `min.insync.replicas` | 2 | 2 |
| `cleanup.policy` | `delete` | `delete` |
| `retention.ms` | 600000，10 分钟 | 86400000，24 小时 |
| `retention.bytes` | -1 | -1 |
| 活跃消费组 | 检查时未发现 | 检查时未发现 |

“未发现活跃消费组”不等于证明没有手动分配分区的消费者。
保留时间还受磁盘容量、分段清理等影响，不应把它当成严格的逐条删除时刻。

现有 Broker 地址：`10.107.159.7:9092`、`10.107.159.8:9092`、
`10.107.159.9:9092`、`10.107.159.10:9092`。

**prod 当前 Collector 不向 Kafka 写入。** 即使创建 ClickHouse 消费表，也不能据此验证
完整链路；必须先恢复并验证入口 Collector 的 Kafka 导出。

dev 的副本数 1 与 `min.insync.replicas=2` 不匹配。当前版本 Kafka exporter 默认
`acks=1`，不能据此认为配置满足高可靠写入要求；若直接改为 `acks=all`，会因 ISR
不足导致写入失败，需要先修复 Topic 副本配置。[Kafka exporter 默认配置](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.120.0/exporter/kafkaexporter/factory.go)

另外，dev 的 `nhi/nhi-vector-agg-traces` 接收 OTLP，而不是消费 Kafka，向
`http://10.200.44.12:31168` 的 `llm_observability.llm_traces_otlp` 写入。
该表属于现有 Vector 自定义映射，不能假设与新方案或 Collector ClickHouse exporter
的表结构兼容。应使用新表完成验证，再评估查询迁移。

## 3 编码选择

| 方案 | Collector → Kafka | ClickHouse 输入 | 实施条件 |
|---|---|---|---|
| 主方案 | `otlp_proto` | `ProtobufSingle` | 验证实际 ClickHouse 版本、嵌套字段及 AnyValue 映射 |
| 备选方案 | `otlp_json` | `JSONAsString`，SQL 提取并展开 | 先验证 Collector 版本和稳定性，使用独立 Topic |
| 另一种架构 | `otlp_proto` | Consumer Collector 解码后写入 ClickHouse | 多维护一个组件，减少 ClickHouse 侧的 OTLP 解码工作 |

### 3.1 为什么是 ProtobufSingle

Kafka exporter 的 OTLP protobuf 消息是单个未附带消息长度前缀的 protobuf 请求体。
ClickHouse `ProtobufSingle` 对应这种消息格式，不能直接照搬带长度分隔的
`Protobuf` 流式输入设置。[ProtobufSingle 定义](https://github.com/ClickHouse/ClickHouse/blob/master/docs/reference/formats/Protobuf/ProtobufSingle.mdx)

需要在每个实际执行消费的 ClickHouse 服务端部署 schema，解决相关 import，
并确认 schema 路径、访问权限和版本一致。`otel_trace.proto` / `ExportTraceServiceRequest`
只是本文拟定的文件名与消息名，并不表示环境中已经存在该文件。

OTLP 包含多层 repeated 字段；`AnyValue` 还包含 oneof、数组和递归的键值列表。
ClickHouse 支持 protobuf 嵌套与 repeated 字段，不代表任意 OTLP 属性都能自动无损映射。
必须用实际版本和样本验证可用的 schema、字段类型及复杂属性处理策略。
若无法保留业务必需字段，应调整方案，不能静默丢弃。
[ClickHouse Protobuf 格式](https://clickhouse.com/docs/reference/formats/Protobuf/Protobuf)

### 3.2 JSON 备选方案的限制

JSON 便于 SQL 提取字段，也便于保留复杂属性；Kafka Engine 可先通过 `JSONAsString`
读取整条 JSON，再由物化视图解析。
[JSONAsString](https://clickhouse.com/docs/reference/formats/JSON/JSONAsString)

但当前讨论的 Collector v0.120.0 将 `otlp_json` 标记为实验性并提示不用于生产。
因此本文不将它作为该版本下可以直接上线的默认方案。
如后续升级并采用 JSON，应创建新版本 Topic，禁止把 protobuf 和 JSON 混写到同一 Topic。
[v0.120.0 编码检查](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.120.0/exporter/kafkaexporter/factory.go)

OTLP JSON 使用 `resourceSpans`、`scopeSpans` 等字段；Trace/Span ID 为十六进制字符串，
纳秒时间戳等 64 位整数通常以字符串表示。不要经由 Float64 转换纳秒时间戳，否则可能
丢失精度；也不能对 JSON ID 再做一次 bytes→hex 转换。
[OTLP JSON 规范](https://opentelemetry.io/docs/specs/otlp/)

## 4 Kafka Engine 的配置边界

以下仅展示拟使用的设置，不是完整 DDL。实际实施需要先获取 ClickHouse 版本，
完成 protobuf schema 和 Kafka 表列定义，再验证这些设置。

```sql
-- Kafka Engine SETTINGS 草案
kafka_broker_list = '10.107.159.7:9092,10.107.159.8:9092,10.107.159.9:9092,10.107.159.10:9092',
kafka_topic_list = 'llm-trace-prod',
kafka_group_name = 'ch-llm-trace-prod-v1',
kafka_format = 'ProtobufSingle',
kafka_schema = 'otel_trace:ExportTraceServiceRequest',
kafka_skip_broken_messages = 0,
kafka_commit_every_batch = 0,
kafka_commit_on_select = 0
```

- Kafka Engine 配合物化视图驱动后台消费；日常查询目标明细表，不直接 SELECT Kafka 表。
- 承担同一条入库流水线的实例使用同一消费组。每个实例设置不同消费组会重复读取全部数据。
- prod 当前有 3 个分区，整个消费组有效并发最多 3 个分区消费者。初期可从总计 1～2 个开始，
  再根据消费延迟和 ClickHouse 负载调整；不能只看单机的 `kafka_num_consumers`。
- 新消费组的起始位点需要通过实际版本支持的 Kafka 客户端配置或停机后的位点重置明确设置。
  不要把 Collector 的 `initial_offset` 当成 Kafka Engine 表设置。
- 即使从 earliest 开始，也只能读到尚未过期的数据。

Kafka Engine 的设置、虚拟列和消费行为参见
[官方文档](https://clickhouse.com/docs/reference/engines/table-engines/integrations/kafka)。

## 5 自定义 Span 表与物化视图

建议先新建独立表，例如 `llm_observability.llm_traces_spans_v1`。
以下是逻辑字段契约，物理列类型和复杂字段存储形式留待 PoC 确认。

| 字段 | 含义与处理要求 |
|---|---|
| `ClusterName` | 明确来源集群，不通过消费连接猜测 |
| `Timestamp` | Span 开始时间，保留纳秒精度 |
| `TraceId` / `SpanId` / `ParentSpanId` | 标准化为小写十六进制，空父 Span 单独处理 |
| `ServiceName` | 从当前 Resource 的 `service.name` 提取 |
| `SpanName` / `SpanKind` | 操作名称与类型 |
| `Duration` | `end_time_unix_nano - start_time_unix_nano`，约定单位为纳秒 |
| `StatusCode` / `StatusMessage` | OTel 状态；不要与 HTTP 状态码混为一列 |
| `ResourceAttributes` / `SpanAttributes` | 保留类型，明确数组、键值列表及缺失值策略 |
| `Events` / `Links` | 保留事件、链接及其关联属性 |
| `KafkaTopic` / `KafkaPartition` / `KafkaOffset` | 定位原始批次，用于排错和重放审计 |

展开必须沿着父子层级进行：先 ResourceSpans，再其内部 ScopeSpans，最后其内部 Spans。
每行继承对应资源和 Scope 信息；不能独立展开多组数组后意外产生笛卡尔积。
protobuf ID 是 bytes：TraceId 为 16 字节、SpanId 为 8 字节，转换后分别为 32、16 个
十六进制字符。应检查无效长度，不用截断或补零掩盖异常。

集群标识应由入口 Collector 注入，或通过消费 Topic 到环境的固定映射补充。
Consumer 连接的 Pod/IP 不是原始业务 Pod；不能在消费端依靠连接信息重建源 Pod 属性。

物理设计初稿可按天分区、保留 7 天，并以链路检索和服务时间查询作为排序键设计依据。
这只是容量评估的起点，**不是现有表的已核实配置**；最终需根据写入量、磁盘预算和查询
模式决定，并明确迟到数据、错误时间戳与 TTL 的处理规则。

Kafka Engine 解码后的结构化批次不是原始字节归档。如果需要原始消息留存，应另行设计
RawBLOB 或对象存储归档路径；不能认为保留展开后的列就具备原始 payload 重放能力。

## 6 位点、可靠性与重复数据

该方案按 **至少一次** 交付设计。写入失败、消费重平衡、进程重启和提交位点前崩溃都可能
导致重新消费。物化视图写入成功与 Kafka 位点提交不是跨系统的原子事务。

初期保留 `kafka_skip_broken_messages=0`，避免跳过坏消息导致静默丢失。
保留按块处理的提交方式，不通过 SELECT 提交位点。出现格式错误时应报警、定位
Topic/分区/Offset，按明确的隔离或重放流程处理。较新版本的错误流或死信能力需要
按实际版本验证，不能假定当前集群已经支持。

建议写入本地 MergeTree 或明确配置复制语义的 ReplicatedMergeTree。
如果物化视图只写入异步 Distributed 队列，需要额外评估“入口已返回成功，但远端尚未
落表”的窗口。插入流水线返回成功也不自动等价于数据已 fsync 到介质；掉电持久性和
跨副本确认需要单独配置、验证，并测量成本。多个物化视图之间也不具备整体原子性。

去重逻辑使用 `(ClusterName, TraceId, SpanId)` 作为 Span 身份。
`(Topic, Partition, Offset)` 只能标识 Kafka 消息，不能标识其中的每个 Span。

如选择 ReplacingMergeTree，排序键和分区必须让同一 Span 的重放落入同一个去重范围，
同时包含足以区分不同 Span 的身份字段。它只在合并时去重，合并前查询仍可能看到重复，
需要按查询范围使用 FINAL 或专门去重视图；直接 `count()` 原始行不能保证精确计数。
[ReplacingMergeTree 行为](https://clickhouse.com/docs/concepts/features/operations/update/replacing-merge-tree)

## 7 Request Trace 与消费 Collector 备选

Dynamo Request Trace 若通过 OTLP Logs 输出，属于 Logs 信号；本文链路处理的是 Traces。
应为 Request Trace 使用独立 Topic、消费表与目标日志表，并按需过滤日志 Scope。
不要将 Logs、Traces 或普通业务日志混入同一 OTLP Topic。

如果 ClickHouse 的 protobuf 映射无法满足实际数据，仍可改用 Consumer Collector：
Kafka receiver 解码 `otlp_proto`，ClickHouse exporter 负责写表。
但使用该方案时也要验证字段契约，不能直接写入现有 Vector 自定义表。

Consumer Collector 的“处理后标记消息”不必然意味着 ClickHouse 已落表：
batch processor 或 exporter 的异步队列可能提前返回。
如需要尽量让确认关联到下游插入结果，应评估同步流水线、关闭异步发送队列，以及
ClickHouse 异步插入的等待设置；同时设计重试、背压和停机行为。该备选同样不能直接
宣称端到端 exactly-once。
[Kafka receiver v0.120.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.120.0/receiver/kafkareceiver/README.md)

## 8 实施与验收步骤

1. 重新检查 Collector 镜像版本、导出配置、Kafka Topic、副本、保留时间，以及 ClickHouse
   版本、拓扑、schema 目录和现有查询依赖。prod 恢复 Kafka 导出应作为独立变更实施。
2. 使用独立测试 Topic、消费组和目标表，从实际 payload 制作脱敏样本，完成 protobuf
   schema、Kafka 表列定义、展开 SQL 和最终 DDL。此时才具备可评审的部署脚本。
3. 用参考 OTLP 解码结果对照入库结果：资源数、Scope 数、Span 数、ID、纳秒时间戳、
   父子关系、状态、不同类型属性、Events 和 Links；覆盖多资源与多 Span 的单条消息。
4. 发送带已知 TraceId 的测试请求，核对 Higress、LiteLLM、Dynamo 的上下游关联。
   对照源 Span 数，不能仅凭 Kafka 消息数或明细行数判断完整性。
5. 注入写入失败、消费重启、分区重平衡、坏消息，确认位点行为、重复范围、报警和恢复
   步骤；确认是否存在跳过、提前确认或超出保留期导致的丢失。
6. 分档增加负载，观察 Kafka 分区 Lag、最老未消费消息年龄、写入延迟、CH CPU/内存、
   磁盘、parts 数量、合并压力和查询延迟。消费最老消息年龄必须留足 Topic 保留时间余量。
7. 确认数据正确、性能满足要求后，再按窗口切换正式消费与查询；保留旧链路的恢复条件，
   避免两条链路同时向同一明细表写入造成无法辨别的重复。

回退时暂停新消费流水线并保留消费组位点、Topic 和目标表，恢复旧查询入口。
是否还能重放取决于 Kafka 保留窗口，不能把暂停消费视为无限期缓存。

当前仍缺少实际 ClickHouse 版本、完整建表定义、protobuf 映射验证和故障测试结果。
因此，本文可以用于方案评审，不能作为已经验证可上线的生产部署手册。
