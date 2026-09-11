# dynamo.remp — Dynamo vLLM 推理引擎适配层

`dynamo.remp` 是 NVIDIA Dynamo 分布式推理框架的 vLLM 适配层，与 `dynamo.vllm` 和 `dynamo.sglang` 同级。它将 vLLM 引擎适配到 Dynamo 的分布式运行时，**专注于 LLM 文本生成模型**，支持多种推理模式和 KV 传输协议。

## 目录结构

```
remp/
├── __init__.py                       # 包版本管理
├── __main__.py                       # 入口：PYTHONHASHSEED → 调用 main()
├── main.py                           # 核心 worker() 异步函数 + 引擎初始化/模型注册/KV 事件/FPM
├── args.py                           # Config 类（DynamoRuntimeConfig + DynamoVllmConfig）+ CLI 解析
├── backend_args.py                   # DynamoVllmArgGroup / DynamoVllmConfig（vLLM 特有参数）
├── worker_factory.py                 # WorkerFactory：创建各类 worker + 注册控制面路由
├── handlers.py                       # 请求处理核心（BaseWorkerHandler / Decode / Prefill / Embedding）
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
├── lora_state.py                     # LoRA 跟踪与 per-adapter asyncio.Lock
├── state_agent.py                    # KV state attachment 所有者生命周期管理
├── headless.py                       # 多节点 TP/PP 从节点模式
├── sidecar.py                        # Dynamo 原生 vLLM sidecar 启动器
├── dp_topology.py                    # 数据并行拓扑辅助
├── health_check.py                   # 健康检查 payload（Vllm / Embedding / Prefill）
│
├── constants.py                      # 重导出 DisaggregationMode
├── errors.py                         # vLLM 客户端错误 → Dynamo HttpError 转换
├── envs.py                           # 环境变量配置（DYN_FORWARDPASS_METRIC_PORT 等）
│
└── tests/                            # 测试与运维
    └── elastic_vllm/
        ├── service.sh                # 统一运维脚本（sync/dynamo/vllm/stop/health）
        ├── patches/                  # 补丁文件
        │   ├── dynamo.patch          # 针对 dynamo 的合并补丁
        │   └── vllm.patch            # 针对 vllm 的合并补丁
        ├── test_offline_switch.py    # 离线 TP/PP 切换验证
        ├── test_online_switch_dynamo.py  # Dynamo 模式在线 TP/PP 切换测试
        └── report.md                 # 测试报告
```

## 核心模块说明

### 入口与配置

| 文件 | 说明 |
|------|------|
| `__main__.py` | 入口点，设置 `PYTHONHASHSEED`，委托 `dynamo.vllm.main.main()` |
| `main.py` | 核心 `worker()` 异步函数：校验本地模型路径 → 初始化 DistributedRuntime → 创建 AsyncLLM → 注册模型 → 设置 KV 事件/FPM/指标 → 通过 WorkerFactory 创建 handler |
| `args.py` | `Config(DynamoRuntimeConfig, DynamoVllmConfig)`：合并运行时与 vLLM 配置，含 NIXL side-channel 自动检测、TP/PP 切换校验 |
| `backend_args.py` | `DynamoVllmArgGroup` / `DynamoVllmConfig`：vLLM 特有 Dynamo 包装参数定义与校验 |

### 请求处理

| 文件 | 说明 |
|------|------|
| `handlers.py` | 核心处理逻辑：`BaseWorkerHandler` 抽象基类、`DecodeWorkerHandler`、`PrefillWorkerHandler`、`EmbeddingWorkerHandler`、`_DeferredAbort` 延迟中止守卫、`VllmEnginePauseController` 引擎暂停控制器 |
| `worker_factory.py` | `WorkerFactory`：根据 `--disaggregation-mode` 创建对应 worker handler，注册所有控制面路由 |

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
| `publisher.py` | `DynamoStatLoggerPublisher` / `StatLoggerFactory`：指标发布到 Dynamo 运行时 |
| `engine_generate.py` | `publish_engine_generate_capability()`：发布 vLLM Generate API 能力元数据 |
| `engine_monitor.py` | `VllmEngineMonitor`：引擎健康监控，支持 TP/PP 切换期间的宽限期 |

### 高级功能

| 文件 | 说明 |
|------|------|
| `lora_state.py` | `LoRAState`：LoRA 适配器跟踪与 per-adapter asyncio.Lock（WeakValueDictionary 锁回收） |
| `state_agent.py` | `StateAgentLifecycle`：KV state attachment 所有者生命周期管理 |
| `headless.py` | 多节点 TP/PP 从节点模式（无 EngineCore/Scheduler/Dynamo 端点） |
| `sidecar.py` | Dynamo 原生 vLLM sidecar 启动器 |
| `dp_topology.py` | 数据并行拓扑辅助函数 |
| `health_check.py` | 健康检查 payload（Vllm / Embedding / Prefill） |

## 模型路径要求

remp **不从 HuggingFace 或其他远程源下载模型权重**。`--model` 参数必须指向本地已有的模型路径（目录或文件）。如果指定路径不存在，worker 启动时会立即抛出 `FileNotFoundError` 并退出。多节点部署时，需确保所有节点均可访问该路径（例如通过共享文件系统）。

## Worker 类型

| 类型 | Handler | 模式 | 说明 |
|------|---------|------|------|
| Decode | `DecodeWorkerHandler` | `--disaggregation-mode decode` | Token-in-token-out 解码，支持 text-in-text-out 模式 |
| Prefill | `PrefillWorkerHandler` | `--disaggregation-mode prefill` | 分离预填充，仅生成 1 token，集成 KV connector protocol |
| Aggregated | `DecodeWorkerHandler` | `--disaggregation-mode agg` | 聚合模式（Prefill + Decode 合一） |
| Embedding | `EmbeddingWorkerHandler` | 内置 | OpenAI /v1/embeddings 适配 |

## KV 连接器协议

| 协议 | 传输模式 | 说明 |
|------|---------|------|
| `NixlConnectorProtocol` | Pull-based | Decode worker 从 prefill 响应读取 block 位置 |
| `MooncakeConnectorProtocol` | Push-based | Prefill worker 推送 blocks 到预分配 transfer_id |
| `LMCacheMPConnectorProtocol` | Cache-mediated | KV 通过共享缓存池按 token hash 移动 |

工厂函数 `make_kv_connector_protocol()` 根据 `KVTransferConfig` 自动创建协议实例。

## 弹性 TP/PP 切换

remp 支持运行时动态切换 Tensor Parallelism 与 Pipeline Parallelism 并行策略，无需重启服务。通过控制面路由 `control/switch_parallel_strategy` 触发。

详见 [AGENT.md](AGENT.md) 第 9 节。
