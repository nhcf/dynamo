# dynamo.remp — Dynamo vLLM 推理引擎适配层

`dynamo.remp` 是 NVIDIA Dynamo 分布式推理框架的 vLLM 适配层，与 `dynamo.vllm` 和 `dynamo.sglang` 同级。它将 vLLM 引擎适配到 Dynamo 的分布式运行时，支持多种推理模式、KV 传输协议和高级特性。

## 目录结构

```
remp/
├── __init__.py                       # 包版本管理
├── __main__.py                       # 入口：PYTHONHASHSEED + 快照恢复检查 → 调用 main()
├── main.py                           # 核心 worker() 异步函数 + 引擎初始化/模型注册/KV 事件/FPM
├── args.py                           # Config 类（DynamoRuntimeConfig + DynamoVllmConfig）+ CLI 解析
├── backend_args.py                   # DynamoVllmArgGroup / DynamoVllmConfig（vLLM 特有参数）
├── worker_factory.py                 # WorkerFactory：创建各类 worker + 注册控制面路由
├── handlers.py                       # 请求处理核心（BaseWorkerHandler / Decode / Prefill / Embedding）
├── pooling_handlers.py               # ClassifyWorkerHandler（/classify + /pooling API）
│
├── kv_connector_protocols.py         # KvConnectorProtocol 抽象（NIXL / Mooncake / LMCacheMP）
├── kv_hints.py                       # KV 传输能力发布（P2P 路由提示）
├── cache_info.py                     # KV event block size 配置
├── capacity.py                       # Token budget 发布到 Dynamo frontend
│
├── instrumented_scheduler.py         # InstrumentedScheduler（FPM ZMQ PUB + 自基准测试编排）
├── benchmark_points.py               # 自基准测试点 Pydantic schema
├── gc_policy.py                      # FPM 基准测试 GC 暂停缓解
├── engine_monitor.py                 # VllmEngineMonitor 引擎健康监控
├── publisher.py                      # DynamoStatLoggerPublisher 指标发布
├── engine_generate.py                # vLLM Generate API 能力发布
│
├── snapshot.py                       # CRIU 快照恢复模式（EngineSnapshotController）
├── lora_state.py                     # LoRA 跟踪与 per-adapter asyncio.Lock
├── state_agent.py                    # KV state attachment 所有者生命周期管理
├── headless.py                       # 多节点 TP/PP 从节点模式
├── sidecar.py                        # Dynamo 原生 vLLM sidecar 启动器
├── dp_topology.py                    # 数据并行拓扑辅助
├── embedding_worker_processes.py     # 多进程共享 EngineCore 的 Embedding worker 池
├── health_check.py                   # 多种健康检查 payload
│
├── constants.py                      # 重导出 DisaggregationMode / EmbeddingTransferMode
├── errors.py                         # vLLM 客户端错误 → Dynamo HttpError 转换
├── envs.py                           # 环境变量配置（DYN_FORWARDPASS_METRIC_PORT 等）
│
├── omni/                             # 多阶段管线生成 worker
│   ├── __init__.py
│   ├── __main__.py
│   ├── main.py                       # Omni worker 入口
│   ├── args.py                       # OmniArgGroup / OmniConfig
│   ├── base_handler.py               # BaseOmniHandler
│   ├── omni_handler.py               # OmniHandler
│   ├── audio_handler.py              # AudioGenerationHandler
│   ├── realtime_handler.py           # RealtimeOmniHandler
│   ├── stage_router.py               # OmniStageRouter
│   ├── stage_worker.py               # OmniStageWorker
│   ├── output_formatter.py           # Audio/Text 输出格式化
│   ├── realtime_utils.py             # Realtime 工具函数
│   ├── types.py                      # StageEngine / StageOutput / StageRequest 类型
│   ├── utils.py                      # 通用工具
│   └── connectors/
│       ├── __init__.py
│       └── nixl_connector.py         # DynamoOmniNixlConnector
│
├── realtime/                         # OpenAI Realtime API 兼容层
│   ├── __init__.py
│   ├── handler.py                    # RealtimeHandler / RealtimeTranscriptionHandler
│   ├── connection.py                 # RealtimeConnection / RealtimeTurn
│   ├── events.py                     # 事件定义
│   └── serving.py                    # 服务入口
│
├── multimodal_handlers/              # 多模态编码处理
│   ├── __init__.py
│   └── encode_worker_handler.py      # EncodeWorkerHandler
│
├── multimodal_utils/                 # 多模态工具集
│   ├── __init__.py
│   ├── embedding_cache.py            # EmbeddingCache
│   ├── encode_utils.py               # 编码工具函数
│   ├── hash_utils.py                 # 哈希工具
│   ├── cache_config.py               # 缓存配置
│   ├── chat_message_utils.py         # Chat 消息工具
│   ├── media_config.py               # 媒体配置
│   ├── model.py                      # 多模态模型抽象
│   ├── model_config.py               # 模型配置
│   ├── protocol.py                   # 协议定义
│   ├── request_processor.py          # VllmMultimodalRequestProcessor
│   ├── prefill_worker_utils.py       # Prefill worker 辅助
│   ├── multimodal_embedding_cache_connector.py  # 多模态嵌入缓存连接器
│   ├── custom_encoder/               # 自定义视觉编码器
│   │   ├── __init__.py
│   │   ├── async_encoder.py          # AsyncVisionEncoder
│   │   ├── batcher.py                # 编码批处理器
│   │   ├── adapter/                  # 编码器适配器
│   │   │   ├── __init__.py
│   │   │   ├── base.py              # CustomEncoderAdapter 基类
│   │   │   ├── factory.py           # 适配器工厂
│   │   │   ├── linear.py            # LinearEncoderAdapter
│   │   │   └── qwen3_vl.py         # Qwen3VLAdapter
│   │   └── backend/                  # 编码器后端
│   │       ├── __init__.py
│   │       └── base.py              # 编码器后端基类
│   └── models/                       # 模型特定工具
│       ├── __init__.py
│       ├── qwen.py                   # Qwen 模型工具
│       └── qwen_video_routing.py     # Qwen 视频路由
│
└── tests/                            # 测试与运维
    ├── service.sh                    # 统一运维脚本（sync/dynamo/vllm/stop/health）
    ├── patches/                      # 本地补丁文件（sync 后自动应用）
    ├── test/
    │   ├── test_offline_switch.py    # 离线 TP/PP 切换验证
    │   ├── test_online_switch.py     # 在线 TP/PP 切换验证
    │   ├── test_online_switch_dynamo.py  # Dynamo 模式在线切换测试
    │   └── report.md                 # 测试报告
    └── 0920/
        ├── run_bench.sh              # 基准测试脚本
        └── service_qwen3.8-27b.sh    # Qwen3.8-27B 专用脚本
```

## 核心模块说明

### 入口与配置

| 文件 | 说明 |
|------|------|
| `__main__.py` | 入口点，设置 `PYTHONHASHSEED`，检查 CRIU 快照恢复模式，委托 `dynamo.vllm.main.main()` |
| `main.py` | 核心 `worker()` 异步函数：初始化 DistributedRuntime → 创建 AsyncLLM → 注册模型 → 设置 KV 事件/FPM/指标 → 通过 WorkerFactory 创建 handler |
| `args.py` | `Config(DynamoRuntimeConfig, DynamoVllmConfig)`：合并运行时与 vLLM 配置，含 NIXL side-channel 自动检测、TP/PP 切换校验 |
| `backend_args.py` | `DynamoVllmArgGroup` / `DynamoVllmConfig`：vLLM 特有 Dynamo 包装参数定义与校验 |

### 请求处理

| 文件 | 说明 |
|------|------|
| `handlers.py` | 核心处理逻辑（4700+ 行）：`BaseWorkerHandler` 抽象基类、`DecodeWorkerHandler`、`PrefillWorkerHandler`、`EmbeddingWorkerHandler`、`_DeferredAbort` 延迟中止守卫、`VllmEnginePauseController` 引擎暂停控制器 |
| `pooling_handlers.py` | `ClassifyWorkerHandler`（继承 `EmbeddingWorkerHandler`）：`/classify` + `/pooling` API，支持批量并发编码 |
| `worker_factory.py` | `WorkerFactory`（1876 行）：根据 `--disaggregation-mode` 创建对应 worker handler，注册所有控制面路由 |

### KV 传输与路由

| 文件 | 说明 |
|------|------|
| `kv_connector_protocols.py` | `KvConnectorProtocol` 抽象基类 + `NixlConnectorProtocol`（pull）、`MooncakeConnectorProtocol`（push）、`LMCacheMPConnectorProtocol`（cache-mediated）实现 |
| `kv_hints.py` | `KvTransferHintSource`：per-rank KV 传输能力元数据发布，支持 P2P 控制端点 |
| `capacity.py` | 发布 vLLM token budget 到 Dynamo frontend，含 per-rank KV block 估算 |
| `cache_info.py` | KV event block size 配置查询 |

### 性能与基准测试

| 文件 | 说明 |
|------|------|
| `instrumented_scheduler.py` | `InstrumentedScheduler`（AsyncScheduler 子类）：每次前向传播完成后通过 ZMQ PUB 发布 FPM，内置自基准测试编排与跨 rank 同步 |
| `benchmark_points.py` | Pydantic schema：`BenchmarkPoints`（版本化基准测试点清单）、`PartitionSpec`（请求间工作分布规格） |
| `gc_policy.py` | `FpmGcWorkerExtension`：基准测试期间 `gc.freeze()` 定期冻结，缓解 GC 暂停 |
| `publisher.py` | `DynamoStatLoggerPublisher` / `NoopStatLogger` / `StatLoggerFactory`：指标发布到 Dynamo 运行时 |
| `engine_generate.py` | `publish_engine_generate_capability()`：发布 vLLM Generate API 能力元数据 |
| `engine_monitor.py` | `VllmEngineMonitor`：引擎健康监控，支持 TP/PP 切换期间的宽限期 |

### 高级功能

| 文件 | 说明 |
|------|------|
| `snapshot.py` | `EngineSnapshotController`：CRIU 快照恢复模式准备 |
| `lora_state.py` | `LoRAState`：LoRA 适配器跟踪与 per-adapter asyncio.Lock（WeakValueDictionary 锁回收） |
| `state_agent.py` | `StateAgentLifecycle`：KV state attachment 所有者生命周期管理 |
| `headless.py` | 多节点 TP/PP 从节点模式（无 EngineCore/Scheduler/Dynamo 端点） |
| `sidecar.py` | Dynamo 原生 vLLM sidecar 启动器 |
| `dp_topology.py` | 数据并行拓扑辅助函数 |
| `embedding_worker_processes.py` | `EmbeddingWorkerProcessGroup`：多进程共享一个 EngineCore 的 Embedding worker 池，突破单进程 Python 瓶颈 |
| `health_check.py` | 多种健康检查 payload（Vllm / Embedding / Prefill / Omni） |

## Worker 类型

| 类型 | Handler | 模式 | 说明 |
|------|---------|------|------|
| Decode | `DecodeWorkerHandler` | `--disaggregation-mode decode` | Token-in-token-out 解码，支持 text-in-text-out 模式 |
| Prefill | `PrefillWorkerHandler` | `--disaggregation-mode prefill` | 分离预填充，仅生成 1 token，集成 KV connector protocol |
| Aggregated | `DecodeWorkerHandler` | `--disaggregation-mode agg` | 聚合模式（Prefill + Decode 合一） |
| Embedding | `EmbeddingWorkerHandler` | `--embedding-worker` | OpenAI /v1/embeddings 适配 |
| Classify | `ClassifyWorkerHandler` | `--classify-worker` | /classify + /pooling API |
| Encode | `EncodeWorkerHandler` | `--disaggregation-mode encode` | 多模态编码，支持 NIXL/local 嵌入传输 |
| Realtime | `RealtimeHandler` | `--realtime` | OpenAI Realtime API 转录 |
| Omni | `OmniHandler` | omni 入口 | 多阶段管线生成（图像/音频/实时） |

## KV 连接器协议

| 协议 | 传输模式 | 说明 |
|------|---------|------|
| `NixlConnectorProtocol` | Pull-based | Decode worker 从 prefill 响应读取 block 位置 |
| `MooncakeConnectorProtocol` | Push-based | Prefill worker 推送 blocks 到预分配 transfer_id |
| `LMCacheMPConnectorProtocol` | Cache-mediated | KV 通过共享缓存池按 token hash 移动 |

工厂函数 `make_kv_connector_protocol()` 根据 `KVTransferConfig` 自动创建协议实例。

## 弹性 TP/PP 切换

remp 支持运行时动态切换 Tensor Parallelism 与 Pipeline Parallelism 并行策略，无需重启服务。通过控制面路由 `control/switch_parallel_strategy` 触发。

详见 [AGENT.md](AGENT.md) 第 8 节。
