# AGENT.md — remp 项目导航与协作指南

## 1. 项目概述

`dynamo.remp` 是 NVIDIA Dynamo 分布式推理框架的 **vLLM 推理引擎适配层**，与 `dynamo.vllm` 和 `dynamo.sglang` 同级。它将 vLLM 引擎适配到 Dynamo 的分布式运行时，**专注于 LLM 文本生成模型**，提供以下核心能力：

- 多种 Worker 类型（Decode / Prefill / Aggregated / Embedding）
- Disaggregated Prefill/Decode 分离服务
- KV Cache 传输与 KV-aware 路由（NIXL / Mooncake / LMCacheMP）
- 弹性 TP/PP 并行策略在线切换
- 前向传播性能指标（Forward Pass Metrics）收集与自基准测试
- LoRA 运行时适配器加载/卸载
- 多节点 Headless 从节点模式

---

## 2. 架构概览

### 2.1 整体架构

REMP 后端采用**适配层架构**，在 Dynamo 分布式运行时与 vLLM 推理引擎之间建立桥接：

```
客户端请求
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│  dynamo.frontend（Dynamo 前端）                                   │
│  OpenAI 兼容 API /v1/chat/completions, /v1/models 等              │
└──────────────────────┬──────────────────────────────────────────┘
                       │ Dynamo 内部通信（NATS/TCP）
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│  dynamo.remp（Dynamo 适配层 ← 本模块）                            │
│                                                                 │
│  ┌──────────── Dynamo 适配组件 ────────────┐                     │
│  │ main.py          运行时初始化 + worker 协调  │                     │
│  │ args.py          配置解析与校验             │                     │
│  │ backend_args.py  vLLM 特有 Dynamo 参数     │                     │
│  │ worker_factory   Worker 创建 + 控制面路由    │                     │
│  │ handlers.py      请求处理 + 策略切换适配     │                     │
│  │ kv_hints.py      KV 传输能力发布           │                     │
│  │ capacity.py      Token budget 发布         │                     │
│  │ publisher.py     指标转发到 Dynamo          │                     │
│  │ instrumented_    FPM 采集 + 自基准测试      │                     │
│  │   scheduler.py                               │                     │
│  │ engine_monitor   健康监控 + 切换宽限期       │                     │
│  │ lora_state.py    LoRA 管理                  │                     │
│  │ state_agent.py   KV state 生命周期          │                     │
│  │ health_check.py  健康检查 payload           │                     │
│  │ errors.py        错误转换                   │                     │
│  └────────────────────────────────────────┘                     │
│                       │                                         │
│                       │ engine_client.generate() / .encode()    │
│                       ▼                                         │
│  ┌──────── ElasticVllm 引擎组件（vLLM fork） ────────┐                     │
│  │ AsyncLLM          异步推理入口                   │                     │
│  │ EngineCore        调度 + KV Cache 管理           │                     │
│  │ MultiprocExecutor 多进程执行器                   │                     │
│  │ GPUModelRunner    模型前向执行                    │                     │
│  │ GPUWorker         Worker 进程管理                 │                     │
│  │ KVCacheReshard    KV Cache 重分片（切换时迁移）    │                     │
│  │ SwitchParallel    TP/PP 切换编排                  │                     │
│  │   StrategyRequest                            │                     │
│  └────────────────────────────────────────────┘                     │
│                       │                                         │
│                       ▼                                         │
│  ┌──────── vLLM 原生引擎组件 ────────┐                     │
│  │ VllmConfig        全局配置                      │                     │
│  │ AsyncScheduler    请求调度                       │                     │
│  │ KVCacheManager    KV Cache 分配/回收             │                     │
│  │ Model Executor    模型权重加载 + GPU 执行         │                     │
│  │ SamplingParams    采样参数                       │                     │
│  │ LoRA Request      LoRA 适配器加载                │                     │
│  │ ZmqEventPublisher KV 事件发布                    │                     │
│  │ Metrics/Stats     Prometheus 指标               │                     │
│  └─────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 组件归属

| 归属 | 组件 | 说明 |
|------|------|------|
| **Dynamo 适配层** (`dynamo.remp`) | `main.py` | 初始化 `DistributedRuntime`，创建 `AsyncLLM`，注册模型端点，设置 KV 事件/FPM/指标 |
| | `args.py` / `backend_args.py` | 解析 Dynamo 运行时参数 + vLLM 参数，含 TP/PP 切换校验 |
| | `worker_factory.py` | 根据 `--disaggregation-mode` 创建 Handler，注册控制面路由 |
| | `handlers.py` | 请求收发核心：将 Dynamo 端点请求转换为 `engine_client.generate()` 调用；TP/PP 切换控制面适配；LoRA 加载/卸载编排 |
| | `kv_hints.py` | 发布 KV 传输能力到 Dynamo 服务发现，用于 P2P 路由 |
| | `capacity.py` | 将 vLLM token budget 发布到 Dynamo frontend，用于流量调度 |
| | `publisher.py` | 将 vLLM 内部指标（`StatLoggerBase`）桥接到 Dynamo 运行时 |
| | `instrumented_scheduler.py` | 扩展 `AsyncScheduler`，采集前向传播性能指标（FPM），内置自基准测试编排 |
| | `engine_monitor.py` | 引擎健康监控，TP/PP 切换期间提供宽限期 |
| | `lora_state.py` | LoRA 适配器跟踪与 per-adapter 锁管理 |
| | `state_agent.py` | KV state attachment 所有者生命周期管理 |
| | `kv_connector_protocols.py` | KV 传输协议抽象（NIXL/Mooncake/LMCacheMP），定义 prefill→decode 数据面接口 |
| | `headless.py` / `sidecar.py` | 多节点从节点模式 / sidecar 启动器 |
| | `health_check.py` / `errors.py` / `envs.py` / `constants.py` | 基础设施 |
| **ElasticVllm 引擎**（vLLM fork） | `AsyncLLM` | 异步推理入口，封装 EngineCore 通信，提供 `generate()` / `encode()` API |
| | `EngineCore` | 核心调度循环：请求调度 → 模型执行 → 输出处理；含 `switch_parallel_strategy()` 切换编排 |
| | `MultiprocExecutor` | 多进程执行器，管理 TP/PP Worker 进程生命周期 |
| | `GPUModelRunner` | GPU 模型前向执行、权重加载/重载 |
| | `GPUWorker` | 单 GPU Worker 进程，执行模型推理 |
| | `KVCacheReshard` | KV Cache 重分片模块，TP/PP 切换时执行 KV 迁移 |
| | `SwitchParallelStrategyRequest` | 切换请求数据结构 |
| **vLLM 原生组件** | `VllmConfig` / `ParallelConfig` | 全局配置与并行策略配置 |
| | `AsyncScheduler` | 请求调度器（被 `InstrumentedScheduler` 扩展） |
| | `KVCacheManager` | KV Cache 块分配/回收/前缀缓存 |
| | `SamplingParams` | 采样参数（temperature, top_p 等） |
| | `LoRARequest` | LoRA 适配器请求 |
| | `ZmqEventPublisher` | KV 事件 ZMQ 发布 |
| | `StatLoggerBase` / `IterationStats` | Prometheus 指标采集 |

### 2.3 数据流

1. **推理请求**：客户端 → `dynamo.frontend`（OpenAI API）→ Dynamo 内部通信 → `handlers.py`（Dynamo 适配）→ `engine_client.generate()`（ElasticVllm）→ `EngineCore` → `GPUModelRunner`（vLLM 原生）
2. **TP/PP 切换**：控制面 API → `handlers.py`（适配层拦截）→ `engine_client.switch_parallel_strategy()`（ElasticVllm）→ `EngineCore` 编排 → `KVCacheReshard`（KV 迁移）+ `GPUModelRunner`（权重重载）
3. **KV 传输**：`PrefillWorkerHandler`（Dynamo 适配）→ `kv_connector_protocols.py`（协议抽象）→ NIXL/Mooncake/LMCacheMP 传输层 → `DecodeWorkerHandler`

### 2.4 模块树

```
dynamo.remp (Python 适配层)
    ├── main.py / __main__.py     — 入口，初始化运行时 + 启动 worker
    ├── args.py                   — CLI 配置解析与校验
    ├── backend_args.py           — vLLM 特有 Dynamo 包装参数
    ├── worker_factory.py         — WorkerFactory：创建各类 worker + 注册控制面路由
    ├── handlers.py               — BaseWorkerHandler / DecodeWorkerHandler / PrefillWorkerHandler / EmbeddingWorkerHandler
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
    │   ├── lora_state.py             — LoRA 跟踪与 per-adapter asyncio.Lock
    │   ├── state_agent.py            — KV state attachment 所有者生命周期
    │   ├── headless.py               — 多节点 TP/PP 从节点模式
    │   ├── sidecar.py               — Dynamo 原生 vLLM sidecar 启动器
    │   └── dp_topology.py            — 数据并行拓扑辅助
    │
    └── tests/
        └── elastic_vllm/
            ├── service.sh           — 统一运维脚本
            ├── patches/             — 合并补丁文件
            │   ├── dynamo.patch     — 针对 Dynamo 的补丁
            │   └── vllm.patch       — 针对 vLLM 的补丁
            ├── test_offline_switch.py        — 离线 TP/PP 切换验证
            ├── test_online_switch_dynamo.py  — Dynamo 模式在线 TP/PP 切换测试
            └── report.md                     — 测试报告
```

---

## 3. Worker 类型

| Worker 类型 | Handler 类 | 说明 |
|-------------|-----------|------|
| Decode | `DecodeWorkerHandler` | Token-in-token-out 解码，支持 text-in-text-out 模式 |
| Prefill | `PrefillWorkerHandler` | 分离预填充，仅生成 1 token |
| Aggregated | `DecodeWorkerHandler` | 聚合模式（Prefill + Decode 合一） |
| Embedding | `EmbeddingWorkerHandler` | OpenAI /v1/embeddings 适配 |

---

## 4. 控制面路由（WorkerFactory.register_engine_routes）

| 路由 | 方法 | 说明 |
|------|------|------|
| `control/switch_parallel_strategy` | POST | 弹性 TP/PP 切换 |
| `control/parallel_strategy_state` | POST | 查询当前并行策略状态 |
| `control/scale_elastic_ep` | POST | 弹性 EP 扩缩容 |
| `control/ep_capacity` | POST | 查询弹性 EP 容量 |
| `control/sleep` | POST | 暂停引擎 |
| `control/wake_up` | POST | 唤醒引擎 |
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

- `__main__.py` — 设置 PYTHONHASHSEED，调用 `dynamo.remp.main.main()`
- `main.py` — 核心 `worker()` 异步函数：校验本地模型路径 → 初始化运行时 → 创建引擎 → 注册模型 → 设置 KV 事件/指标/FPM → 创建 worker handler
- `args.py` — `Config` 类继承 `DynamoRuntimeConfig` + `DynamoVllmConfig`，解析与校验所有 CLI 参数
- `backend_args.py` — `DynamoVllmArgGroup` / `DynamoVllmConfig`：vLLM 特有的 Dynamo 包装参数

### 请求处理

- `handlers.py` — 核心处理逻辑：
  - `BaseWorkerHandler` — 抽象基类，包含 LoRA 管理、KV 发布、FPM 中继、引擎暂停/恢复、延迟中止守卫等
  - `DecodeWorkerHandler` — 解码请求处理，支持 token/text 两种模式
  - `PrefillWorkerHandler` — 分离预填充处理，集成 KV connector protocol
  - `EmbeddingWorkerHandler` — Embedding 请求处理（不继承 BaseWorkerHandler）
  - `_DeferredAbort` — 解聚 decode 模式下的延迟中止守卫
  - `VllmEnginePauseController` — 引擎暂停控制器（sleep/resume）
  - `_snapshot()` / `_snapshot_timed_out_result` — Elastic EP 容量快照（与 CRIU 无关）

- `worker_factory.py` — `WorkerFactory`：根据 `--disaggregation-mode` 创建对应 worker handler，注册所有控制面路由

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
- `publisher.py` — DynamoStatLoggerPublisher / StatLoggerFactory：指标发布到 Dynamo 运行时
- `engine_generate.py` — `publish_engine_generate_capability()`：发布 vLLM Generate API 能力元数据

### 高级功能

- `lora_state.py` — `LoRAState`：LoRA 跟踪与 per-adapter asyncio.Lock（WeakValueDictionary 锁回收）
- `state_agent.py` — `StateAgentLifecycle`：KV state attachment 所有者生命周期管理
- `headless.py` — 多节点 TP/PP 从节点模式（无引擎核心/调度器/Dynamo 端点）
- `sidecar.py` — Dynamo 原生 vLLM sidecar 启动器
- `dp_topology.py` — 数据并行拓扑辅助函数
- `engine_monitor.py` — `VllmEngineMonitor`：引擎健康监控，支持 TP/PP 切换期间的宽限期
- `health_check.py` — 健康检查 payload（Vllm / Embedding / Prefill）

### 基础设施

- `constants.py` — 重导出 `DisaggregationMode`
- `errors.py` — vLLM 客户端错误 → Dynamo HttpError 转换
- `envs.py` — 环境变量配置（DYN_FORWARDPASS_METRIC_PORT 等）

### 补丁文件

- `tests/elastic_vllm/patches/dynamo.patch` — 针对 Dynamo 的合并补丁（含 `dynamo/common/runtime.py` 和 `dynamo/vllm/main.py` 的修改）
- `tests/elastic_vllm/patches/vllm.patch` — 针对 vLLM 的合并补丁（含 `vllm/exceptions.py`、`vllm/inputs/preprocess_templates/default/chunk_delta_h.py` 和 `vllm/engine/async_llm.py` 的修改）

---

## 7. 模型路径要求

remp **不从 HuggingFace 或其他远程源下载模型权重**。`--model` 参数必须指向本地已有的模型路径（目录或文件）。如果指定路径不存在，worker 启动时会立即抛出 `FileNotFoundError` 并退出。多节点部署时，需确保所有节点均可访问该路径（例如通过共享文件系统）。

---

## 8. 配置参数（DynamoVllmConfig 主要参数）

| 参数 | 类型 | 说明 |
|------|------|------|
| `--disaggregation-mode` | `agg\|prefill\|decode` | 分离服务模式 |
| `--use-vllm-tokenizer` | bool | 启用 text-in-text-out 模式 |
| `--enable-rl` | bool | 启用 RL 请求面 |
| `--embedding-worker` | bool | 启用 Embedding worker |
| `--headless` | bool | 无头从节点模式 |
| `--benchmark-mode` | `prefill\|decode\|agg` | 自基准测试模式 |

---

## 9. 弹性 TP/PP 并行策略切换

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

## 10. 测试与运维

### tests/ 目录结构

```
tests/elastic_vllm/
├── service.sh                    — 统一运维脚本（sync/dynamo/vllm/stop/health）
├── patches/                      — 合并补丁文件
│   ├── dynamo.patch              — 针对 Dynamo 的补丁
│   └── vllm.patch                — 针对 vLLM 的补丁
├── test_offline_switch.py        — 离线 TP/PP 切换验证
├── test_online_switch_dynamo.py  — Dynamo 模式在线 TP/PP 切换测试
└── report.md                     — 测试报告
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

## 11. 运行环境

| 项目 | 值 |
|------|-----|
| Python | 3.10+ |
| PyTorch | 2.10.0+metax |
| vLLM | 0.22.0 |
| GPU 平台 | Metax MACA（通过 cu-bridge 兼容 CUDA API） |

---

## 12. 开发注意事项

- `handlers.py` 是最大的文件（4700+ 行），包含核心请求处理逻辑，修改时需注意 `_DeferredAbort` 在 NIXL KV 传输窗口期间的安全约束
- `worker_factory.py` 包含 worker 创建和所有控制面路由注册逻辑
- `instrumented_scheduler.py` 扩展了 vLLM 的 `AsyncScheduler`，同时支持 sync 和 async 引擎模式
- KV 连接器协议通过 `KvConnectorProtocol` 抽象隔离，新增连接器需继承基类并在 `make_kv_connector_protocol()` 中注册
- LoRA 状态管理通过 `LoRAState` 统一，per-adapter asyncio.Lock 使用 `WeakValueDictionary` 实现锁回收
- `_snapshot()` 方法是 Elastic EP 容量快照，与 CRIU 快照无关，不可删除
- 模型权重不支持远程下载，`--model` 必须指向本地路径，否则启动时报 `FileNotFoundError`
