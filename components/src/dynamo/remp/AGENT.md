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

- `__main__.py` — 设置 PYTHONHASHSEED，调用 `dynamo.vllm.main.main()`
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
