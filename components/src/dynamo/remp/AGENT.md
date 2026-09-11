# AGENT.md — remp 项目导航与协作指南

## 1. 项目概述

`dynamo.remp` 是 NVIDIA Dynamo 分布式推理框架的 **vLLM 推理引擎适配层**，与 `dynamo.vllm` 和 `dynamo.sglang` 同级。它将 vLLM 引擎适配到 Dynamo 的分布式运行时，提供完整的推理服务能力，包括：

- 多种 Worker 类型（Decode / Prefill / Embedding / Classify / Encode / Realtime / Omni）
- Disaggregated Prefill/Decode 分离服务
- KV Cache 传输与 KV-aware 路由
- 弹性 TP/PP 并行策略在线切换
- 前向传播性能指标（Forward Pass Metrics）收集与自基准测试
- LoRA 运行时适配器加载/卸载
- CRIU 快照恢复模式
- 多模态编码与实时 API 支持

---

## 2. 架构概览

```
Dynamo DistributedRuntime (Rust)
    │
    ▼
dynamo.remp (Python 适配层)
    ├── main.py / __main__.py     — 入口，初始化运行时 + 启动 worker
    ├── args.py                   — CLI 配置解析与校验
    ├── backend_args.py           — vLLM 特有 Dynamo 包装参数
    ├── worker_factory.py         — WorkerFactory：创建各类 worker + 注册控制面路由
    ├── handlers.py               — BaseWorkerHandler / DecodeWorkerHandler / PrefillWorkerHandler / EmbeddingWorkerHandler
    ├── pooling_handlers.py       — ClassifyWorkerHandler（/classify + /pooling）
    │
    ├── KV 传输层
    │   ├── kv_connector_protocols.py  — KvConnectorProtocol 抽象（NIXL / Mooncake / LMCacheMP）
    │   ├── kv_hints.py                — KV 传输能力发布（P2P 路由提示）
    │   ├── cache_info.py              — KV event block size 配置
    │   └── capacity.py                — Token budget 发布到 Dynamo frontend
    │
    ├── 性能与监控
    │   ├── instrumented_scheduler.py  — InstrumentedScheduler（FPM ZMQ PUB + 自基准测试）
    │   ├── benchmark_points.py        — 自基准测试点 Pydantic schema
    │   ├── gc_policy.py              — FPM 基准测试 GC 暂停缓解
    │   ├── engine_monitor.py          — VllmEngineMonitor 健康监控
    │   ├── publisher.py              — DynamoStatLoggerPublisher 指标发布
    │   └── engine_generate.py        — vLLM Generate API 能力发布
    │
    ├── 高级功能
    │   ├── snapshot.py               — CRIU 快照恢复模式
    │   ├── lora_state.py             — LoRA 跟踪与 per-adapter asyncio.Lock
    │   ├── state_agent.py            — KV state attachment 所有者生命周期
    │   ├── headless.py               — 多节点 TP/PP 从节点模式
    │   ├── sidecar.py               — Dynamo 原生 vLLM sidecar 启动器
    │   ├── dp_topology.py            — 数据并行拓扑辅助
    │   └── embedding_worker_processes.py — 多进程共享 EngineCore 的 Embedding worker
    │
    ├── 子包
    │   ├── omni/                — 多阶段管线生成 worker（图像/音频/实时）
    │   ├── realtime/            — OpenAI Realtime API 兼容的转录 handler
    │   ├── multimodal_handlers/ — 多模态编码 worker handler
    │   └── multimodal_utils/    — 自定义编码器、嵌入缓存、请求预处理
    │
    └── tests/                  — 测试与运维脚本
        ├── service.sh           — 统一运维脚本
        ├── patches/             — 本地补丁文件
        ├── test/                — TP/PP 切换测试脚本
        └── 0920/                — 基准测试脚本
```

---

## 3. Worker 类型

| Worker 类型 | Handler 类 | 说明 |
|-------------|-----------|------|
| Decode | `DecodeWorkerHandler` | Token-in-token-out 解码，支持 text-in-text-out 模式 |
| Prefill | `PrefillWorkerHandler` | 分离预填充，仅生成 1 token |
| Aggregated | `DecodeWorkerHandler` | 聚合模式（Prefill + Decode 合一） |
| Embedding | `EmbeddingWorkerHandler` | OpenAI /v1/embeddings 适配 |
| Classify | `ClassifyWorkerHandler` | /classify + /pooling API（继承 EmbeddingWorkerHandler） |
| Encode | `EncodeWorkerHandler`（multimodal_handlers/） | 多模态编码，支持 NIXL/local 嵌入传输 |
| Realtime | `RealtimeHandler`（realtime/） | OpenAI Realtime API 转录 |
| Omni | `OmniHandler`（omni/） | 多阶段管线生成（图像/音频/实时） |

---

## 4. 控制面路由（WorkerFactory.register_engine_routes）

| 路由 | 方法 | 说明 |
|------|------|------|
| `control/switch_parallel_strategy` | POST | 弹性 TP/PP 切换 |
| `control/parallel_strategy_state` | POST | 查询当前并行策略状态 |
| `control/scale_elastic_ep` | POST | 弹性 EP 扩缩容 |
| `control/ep_capacity` | POST | 查询弹性 EP 容量 |
| `control/sleep` | POST | 暂停引擎（GMS shadow mode） |
| `control/wake_up` | POST | 唤醒引擎 |
| `control/checkpoint` | POST | 触发 CRIU checkpoint |
| `control/profile` | POST | 性能 profiling |
| `control/load_lora` | POST | 加载 LoRA 适配器 |
| `control/unload_lora` | POST | 卸载 LoRA 适配器 |
| `control/list_loras` | POST | 列出已加载 LoRA |

---

## 5. KV 连接器协议

| 协议类 | 传输模式 | 说明 |
|--------|---------|------|
| `NixlConnectorProtocol` | Pull-based | Decode 从 prefill 响应读取 block 位置 |
| `MooncakeConnectorProtocol` | Push-based | Prefill 推送 blocks 到预分配 transfer_id |
| `LMCacheMPConnectorProtocol` | Cache-mediated | KV 通过共享缓存池按 token hash 移动 |

工厂函数 `make_kv_connector_protocol()` 根据 `KVTransferConfig` 自动创建协议实例，支持 MultiConnector/PdConnector 包装器解析。

---

## 6. 关键文件说明

### 核心入口

- `__main__.py` — 设置 PYTHONHASHSEED，检查快照恢复模式，调用 `dynamo.vllm.main.main()`
- `main.py` — 核心 `worker()` 异步函数：初始化运行时 → 创建引擎 → 注册模型 → 设置 KV 事件/指标/FPM → 创建 worker handler
- `args.py` — `Config` 类继承 `DynamoRuntimeConfig` + `DynamoVllmConfig`，解析与校验所有 CLI 参数
- `backend_args.py` — `DynamoVllmArgGroup` / `DynamoVllmConfig`：vLLM 特有的 Dynamo 包装参数

### 请求处理

- `handlers.py` — 核心处理逻辑（4700+ 行）：
  - `BaseWorkerHandler` — 抽象基类，包含 LoRA 管理、KV 发布、FPM 中继、引擎暂停/恢复、延迟中止守卫等
  - `DecodeWorkerHandler` — 解码请求处理，支持 token/text 两种模式
  - `PrefillWorkerHandler` — 分离预填充处理，集成 KV connector protocol
  - `EmbeddingWorkerHandler` — Embedding 请求处理（不继承 BaseWorkerHandler）
  - `_DeferredAbort` — 解聚 decode 模式下的延迟中止守卫
  - `VllmEnginePauseController` — 引擎暂停控制器（sleep/resume/checkpoint）

- `pooling_handlers.py` — `ClassifyWorkerHandler`：/classify + /pooling API，支持批量编码

### KV 与路由

- `kv_connector_protocols.py` — `KvConnectorProtocol` 抽象基类 + NIXL/Mooncake/LMCacheMP 实现
- `kv_hints.py` — `KvTransferHintSource`：per-rank 源元数据，发布 TRANSFER 能力与 P2P 端点
- `capacity.py` — 发布 vLLM token budget 到 Dynamo frontend（per-rank KV block 估算）
- `cache_info.py` — KV event block size 配置和查询

### 性能与基准测试

- `instrumented_scheduler.py` — `InstrumentedScheduler`（AsyncScheduler 子类）：
  - 通过 ZMQ PUB 发布每次前向传播的 FPM
  - 内置自基准测试编排（BenchmarkConfig/BenchmarkPoint/BenchmarkPointResult）
  - 跨 rank 同步（_BenchmarkSynchronizer）
- `benchmark_points.py` — Pydantic schema：BenchmarkPoints（版本化基准测试点清单，支持 PartitionSpec）
- `gc_policy.py` — `FpmGcWorkerExtension`：基准测试期间 gc.freeze() 定期冻结缓解 GC 暂停
- `publisher.py` — DynamoStatLoggerPublisher / NoopStatLogger / StatLoggerFactory：指标发布到 Dynamo 运行时
- `engine_generate.py` — `publish_engine_generate_capability()`：发布 vLLM Generate API 能力元数据

### 高级功能

- `snapshot.py` — `EngineSnapshotController`：CRIU 快照恢复模式准备
- `lora_state.py` — `LoRAState`：LoRA 跟踪与 per-adapter asyncio.Lock（WeakValueDictionary 锁回收）
- `state_agent.py` — `StateAgentLifecycle`：KV state attachment 所有者生命周期管理
- `headless.py` — 多节点 TP/PP 从节点模式（无引擎核心/调度器/Dynamo 端点）
- `sidecar.py` — Dynamo 原生 vLLM sidecar 启动器
- `dp_topology.py` — 数据并行拓扑辅助函数
- `embedding_worker_processes.py` — `EmbeddingWorkerProcessGroup`：多进程共享一个 EngineCore 的 Embedding worker 池
- `engine_monitor.py` — `VllmEngineMonitor`：引擎健康监控，支持 TP/PP 切换期间的宽限期
- `health_check.py` — 多种健康检查 payload（Vllm/Embedding/Prefill/Omni）

### 子包

- `omni/` — 多阶段管线生成 worker：
  - `main.py` — Omni worker 入口
  - `omni_handler.py` — OmniHandler（BaseOmniHandler 子类）
  - `audio_handler.py` — AudioGenerationHandler
  - `realtime_handler.py` — RealtimeOmniHandler
  - `stage_router.py` — OmniStageRouter
  - `stage_worker.py` — OmniStageWorker
  - `connectors/nixl_connector.py` — DynamoOmniNixlConnector
  - `args.py` — OmniArgGroup / OmniConfig

- `realtime/` — OpenAI Realtime API 兼容层：
  - `handler.py` — RealtimeHandler / RealtimeTranscriptionHandler
  - `connection.py` — RealtimeConnection / RealtimeTurn
  - `events.py` — 事件定义
  - `serving.py` — 服务入口

- `multimodal_handlers/` — 多模态编码处理：
  - `encode_worker_handler.py` — EncodeWorkerHandler（支持 NIXL/local 嵌入传输）

- `multimodal_utils/` — 多模态工具集：
  - `custom_encoder/` — 自定义视觉编码器适配器（adapter/ + backend/）
  - `models/` — Qwen 等模型特定工具
  - `embedding_cache.py` — EmbeddingCache
  - `request_processor.py` — VllmMultimodalRequestProcessor
  - `prefill_worker_utils.py` — Prefill worker 辅助
  - `multimodal_embedding_cache_connector.py` — 多模态嵌入缓存连接器

### 基础设施

- `constants.py` — 重导出 `DisaggregationMode` / `EmbeddingTransferMode`
- `errors.py` — vLLM 客户端错误 → Dynamo HttpError 转换
- `envs.py` — 环境变量配置（DYN_FORWARDPASS_METRIC_PORT 等）

---

## 7. 配置参数（DynamoVllmConfig 主要参数）

| 参数 | 类型 | 说明 |
|------|------|------|
| `--disaggregation-mode` | `agg\|prefill\|decode\|encode` | 分离服务模式 |
| `--use-vllm-tokenizer` | bool | 启用 text-in-text-out 模式 |
| `--route-to-encoder` | bool | 前端路由到编码器 |
| `--enable-multimodal` | bool | 启用多模态支持 |
| `--enable-rl` | bool | 启用 RL 请求面 |
| `--embedding-worker` | bool | 启用 Embedding worker |
| `--embedding-worker-processes` | int | Embedding worker 进程数 |
| `--classify-worker` | bool | 启用 Classify worker |
| `--realtime` | bool | 启用 Realtime 转录 |
| `--headless` | bool | 无头从节点模式 |
| `--gms-shadow-mode` | bool | GPU Memory Service 影子/待机模式 |
| `--benchmark-mode` | `prefill\|decode\|agg` | 自基准测试模式 |
| `--custom-encoder-class` | str | 自定义视觉编码器类路径 |

---

## 8. 弹性 TP/PP 并行策略切换

### 支持的并行策略（4 GPU 示例）

| 策略 | TP | PP | 适用场景 |
|------|----|----|----------|
| `4x1` | 4 | 1 | 高吞吐 Prefill |
| `2x2` | 2 | 2 | 均衡 Prefill/Decode |
| `1x4` | 1 | 4 | 低延迟 Decode |

### 切换参数

```json
{
  "new_world_size": 4,
  "target_tensor_parallel_size": 2,
  "target_pipeline_parallel_size": 2,
  "target_num_blocks": null,
  "request_handling": "wait",
  "admission_handling": "queue",
  "retry_after": 1
}
```

### 切换限制

- 当前仅支持 DP=1
- Elastic EP 与 TP/PP 切换互斥
- 切换失败后引擎不可恢复，必须重启
- 在线切换需要 `VLLM_SERVER_DEV_MODE=1`
- 在线切换必须启用 `--enforce-eager`

---

## 9. 测试与运维

### tests/ 目录结构

```
tests/
├── service.sh           — 统一运维脚本（sync/dynamo/vllm/stop/health）
├── patches/             — 本地补丁文件（sync 后自动应用）
├── test/
│   ├── test_offline_switch.py        — 离线 TP/PP 切换验证
│   ├── test_online_switch.py         — 在线 TP/PP 切换验证（HTTP API）
│   ├── test_online_switch_dynamo.py  — Dynamo 模式在线 TP/PP 切换测试
│   └── report.md                     — 测试报告
└── 0920/
    ├── run_bench.sh                  — 基准测试脚本
    └── service_qwen3.8-27b.sh        — Qwen3.8-27B 专用运维脚本
```

### service.sh 主要命令

| 命令 | 说明 |
|------|------|
| `sync` | 克隆/拉取上游仓库，复制到 site-packages，应用 patches |
| `dynamo` | 启动 Dynamo 模式（backend + frontend） |
| `vllm` | 启动 vLLM 原生模式 |
| `stop` | 停止所有服务 |
| `health` | 健康检查 |

---

## 10. 运行环境

| 项目 | 值 |
|------|-----|
| Python | 3.10+ |
| PyTorch | 2.10.0+metax |
| vLLM | 0.22.0 |
| GPU 平台 | Metax MACA（通过 cu-bridge 兼容 CUDA API） |

---

## 11. 开发注意事项

- `handlers.py` 是最大的文件（4700+ 行），包含核心请求处理逻辑，修改时需注意 `_DeferredAbort` 在 NIXL KV 传输窗口期间的安全约束
- `worker_factory.py`（1876 行）包含 worker 创建和所有控制面路由注册逻辑
- `instrumented_scheduler.py` 扩展了 vLLM 的 `AsyncScheduler`，同时支持 sync 和 async 引擎模式
- KV 连接器协议通过 `KvConnectorProtocol` 抽象隔离，新增连接器需继承基类并在 `make_kv_connector_protocol()` 中注册
- LoRA 状态管理通过 `LoRAState` 统一，per-adapter asyncio.Lock 使用 `WeakValueDictionary` 实现锁回收
- Embedding worker 支持多进程共享 EngineCore（`EmbeddingWorkerProcessGroup`），突破单进程 Python 瓶颈
