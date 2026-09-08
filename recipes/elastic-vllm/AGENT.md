# AGENT.md — 项目导航与协作指南

## 1. 项目概述

本项目是一个 **弹性推理服务系统**，核心能力是在线运行时动态切换 Tensor Parallelism (TP) 与 Pipeline Parallelism (PP) 的并行策略，无需重启服务即可适配不同负载。项目由两大上游仓库的定制 Fork 与若干胶水脚本组成：

| 组件 | 来源 | 本地分支 | 作用 |
|------|------|----------|------|
| `ElasticVllm_demo/` | [yhp49/ElasticVllm_demo](https://github.com/yhp49/ElasticVllm_demo) | `codex/add-v0.22.0` | 基于 vLLM 0.22.0 的弹性 TP/PP 切换引擎（补丁 Fork） |
| `dynamo/` | [nhcf/dynamo](https://github.com/nhcf/dynamo) | `ElasticVllm` | NVIDIA Dynamo 分布式推理编排框架（定制 Fork） |
| `service.sh` | — | — | 统一运维脚本：同步代码、启动服务、查询状态、触发切换 |
| `test/` | — | — | 测试脚本目录 |
| `patches/` | — | — | 本地补丁文件（sync 后自动应用） |

> **⛔ 不可修改约束**：`ElasticVllm_demo/` 和 `dynamo/` 目录内容为只读上游代码，禁止直接修改。如需变更，应通过 `service.sh sync` 从 Git 仓库拉取更新，或向对应上游提交 PR。

---

## 2. 核心特性：弹性 TP/PP 并行策略切换

### 2.1 功能说明

在 4 GPU 场景下，系统可在运行时切换以下并行策略：

| 策略 | TP | PP | 适用场景 |
|------|----|----|----------|
| `4x1` | 4 | 1 | 高吞吐 Prefill |
| `2x2` | 2 | 2 | 均衡 Prefill/Decode |
| `1x4` | 1 | 4 | 低延迟 Decode |
| `2x1` | 2 | 1 | 小规模 TP |

切换时系统自动完成：
1. **请求排空**：等待在途请求完成（`request_handling=wait`）或要求引擎空闲（`request_handling=idle`）
2. **KV Cache 迁移**：将旧拓扑的 KV Cache 重分片到新拓扑（`kv_cache_reshard.py`，约 3600 行核心逻辑）
3. **模型权重重载**：从磁盘或共享内存加载新拓扑的权重
4. **分布式状态重建**：重建 TP/PP 进程组与通信后端

### 2.2 切换参数

```json
{
  "new_world_size": 4,
  "target_tensor_parallel_size": 2,
  "target_pipeline_parallel_size": 2,
  "target_num_blocks": null,
  "request_handling": "wait",      // "idle" | "wait"
  "admission_handling": "queue",   // "queue" | "reject"
  "retry_after": 1
}
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `new_world_size` | int | 切换后总 GPU 数（须 = TP × PP） |
| `target_tensor_parallel_size` | int | 目标 TP 度 |
| `target_pipeline_parallel_size` | int | 目标 PP 度 |
| `target_num_blocks` | int\|null | 目标 KV Cache 块数（null=自动计算） |
| `request_handling` | `"idle"`\|`"wait"` | `idle`：仅空闲时切换；`wait`：等待在途请求完成后切换 |
| `admission_handling` | `"queue"`\|`"reject"` | 切换期间新请求的处理策略：排队等待或拒绝(503) |
| `retry_after` | int | 拒绝时建议的重试等待秒数 |

### 2.3 启动配置参数

```bash
--tp-pp-switch-prebuild-strategies 4x1,2x2,1x4   # 预构建策略列表
--tp-pp-switch-kv-transfer-window-size 2            # KV 迁移滑动窗口大小
--tp-pp-switch-kv-transfer-max-scratch-size-mb 256  # KV 迁移暂存内存上限
--tp-pp-switch-weight-load-mode disk                # 权重加载模式: disk | node_memory
--tp-pp-switch-weight-cache-dir /dev/shm            # node_memory 模式缓存目录
```

---

## 3. API 接口

### 3.1 vLLM 原生 HTTP API（需 `VLLM_SERVER_DEV_MODE=1`）

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/switch_parallel_strategy` | 触发 TP/PP 切换 |
| `GET` | `/is_switching_parallel_strategy` | 查询是否正在切换 |
| `POST` | `/scale_elastic_ep` | 弹性专家并行扩缩容 |
| `POST` | `/is_scaling_elastic_ep` | 查询弹性 EP 扩缩状态 |

### 3.2 Dynamo 控制面 API（经由 Rust Runtime）

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/engine/control/switch_parallel_strategy` | Dynamo 适配层触发 TP/PP 切换 |
| `POST` | `/engine/control/parallel_strategy_state` | 查询当前并行策略状态 |
| `POST` | `/engine/control/scale_elastic_ep` | 弹性 EP 扩缩容 |
| `POST` | `/engine/control/ep_capacity` | 查询弹性 EP 容量 |

### 3.3 Python 离线 API

```python
from vllm import LLM, SamplingParams
from vllm.v1.engine import SwitchParallelStrategyRequest

llm = LLM(model="...", tensor_parallel_size=4, pipeline_parallel_size=1,
           tp_pp_switch_prebuild_strategies=["4x1", "2x2", "1x4"])

llm.llm_engine.switch_parallel_strategy(
    SwitchParallelStrategyRequest(
        new_world_size=4,
        target_tensor_parallel_size=2,
        target_pipeline_parallel_size=2,
    )
)
```

---

## 4. 代码结构与关键文件

```
ll/
├── AGENT.md                   # 本文档
├── service.sh                 # 统一运维脚本
├── patches/                   # 本地补丁文件（sync 后自动应用）
├── logs/                      # 日志 + PID 文件目录
├── test/                      # 测试脚本
│   ├── test_offline_switch.py  # 离线切换演示
│   └── test_online_switch.py   # 在线切换验证

workspace/                       # 工作目录，包含所有代码
├── ElasticVllm_demo/          # ⛔ 只读 — vLLM 弹性 Fork
│   └── vllm/
│       ├── v1/
│       │   ├── engine/
│       │   │   ├── __init__.py          # SwitchParallelStrategyRequest 定义
│       │   │   ├── core.py              # EngineCore.switch_parallel_strategy（核心切换编排）
│       │   │   ├── async_llm.py         # AsyncLLM 切换 + 请求准入控制
│       │   │   ├── llm_engine.py        # 离线 LLMEngine 切换入口
│       │   │   ├── core_client.py       # 切换相关 RPC 客户端
│       │   │   └── exceptions.py        # ParallelStrategySwitchInProgressError 等
│       │   ├── executor/
│       │   │   ├── multiproc_executor.py  # 多进程执行器切换逻辑
│       │   │   ├── ray_executor.py        # Ray 执行器切换逻辑
│       │   │   └── abstract.py           # 执行器抽象接口
│       │   ├── worker/
│       │   │   ├── gpu_model_runner.py    # GPU 模型运行器（KV Cache 迁移核心，7854 行）
│       │   │   ├── gpu_worker.py          # GPU Worker 切换编排
│       │   │   └── kv_cache_reshard.py   # KV Cache 重分片算法（3629 行）
│       │   └── core/
│       │       ├── sched/scheduler.py     # 调度器：block remap、prefix cache 重置
│       │       ├── block_pool.py          # Block Pool resize
│       │       └── kv_cache_manager.py    # KV Cache Manager resize
│       ├── distributed/
│       │   └── parallel_state.py          # 分布式并行状态：进程组重建、P2P warmup
│       ├── config/
│       │   └── parallel.py                # ParallelConfig：切换相关配置字段
│       ├── engine/
│       │   ├── arg_utils.py               # CLI 参数解析
│       │   └── protocol.py               # EngineClient 切换协议接口
│       ├── entrypoints/
│       │   ├── openai/api_server.py       # OpenAI API Server（注册中间件）
│       │   └── serve/
│       │       ├── __init__.py            # 路由注册入口
│       │       ├── tp_pp_switch/          # TP/PP 切换 HTTP API
│       │       │   ├── api_router.py      # POST /switch_parallel_strategy
│       │       │   └── middleware.py      # 切换期间请求准入中间件
│       │       └── elastic_ep/            # 弹性 EP HTTP API
│       │           ├── api_router.py      # POST /scale_elastic_ep
│       │           └── middleware.py      # 扩缩容期间 503 中间件
│       └── model_executor/model_loader/
│           └── node_memory_loader.py      # node_memory 权重加载
│
└── dynamo/                    # ⛔ 只读 — NVIDIA Dynamo 编排框架 Fork
    ├── lib/                   # Rust 工作空间（20+ crates）
    │   ├── runtime/           # 核心运行时，含 engine_routes.rs（/engine/* 路由注册）
    │   ├── llm/               # LLM 抽象层
    │   ├── kv-router/         # KV 感知路由
    │   ├── kvbm-*/            # KV Block Manager（GPU/CPU/SSD 多级缓存）
    │   └── bindings/python/   # PyO3/maturin 绑定
    ├── components/src/dynamo/
    │   ├── vllm/              # vLLM 后端适配
    │   │   ├── main.py        # 启动入口 + TP/PP 切换能力校验
    │   │   ├── handlers.py    # BaseWorkerHandler：Dynamo 控制面适配器
    │   │   │                  #   ├─ switch_parallel_strategy()  → 校验+调用引擎
    │   │   │                  #   ├─ scale_elastic_ep()          → 弹性 EP 扩缩
    │   │   │                  #   └─ get_parallel_strategy_state() → 状态查询
    │   │   ├── worker_factory.py  # 控制路由注册（control/switch_parallel_strategy 等）
    │   │   ├── args.py           # 配置校验与参数转发
    │   │   └── headless.py       # 无头模式适配
    │   ├── frontend/          # HTTP/gRPC 前端
    │   ├── planner/           # SLA 驱动自动扩缩
    │   ├── router/            # 请求路由
    │   ├── sglang/            # SGLang 后端
    │   └── trtllm/            # TensorRT-LLM 后端
    ├── deploy/                # Kubernetes Operator & Helm Charts
    └── container/             # Docker 构建
```

---

## 5. 运维操作（service.sh）

### 5.1 代码同步

```bash
service.sh sync
```

执行流程：
1. Git clone/pull `ElasticVllm_demo`（分支 `codex/add-v0.22.0`）
2. Git clone/pull `dynamo`（分支 `ElasticVllm`）
3. 复制 `ElasticVllm_demo/vllm/*` → `/opt/conda/lib/python3.10/site-packages/vllm/`
4. 复制 `dynamo/components/src/dynamo/vllm/*` → `/opt/conda/lib/python3.10/site-packages/dynamo/vllm/`
5. 自动应用 `patches/` 目录下的本地补丁文件（修复尚未合入上游的问题）

> 本地补丁存放在 `patches/` 目录（`.patch` 格式），sync 后自动应用到 conda site-packages。待对应 PR 合入上游后可删除补丁文件。

### 5.2 启动服务

```bash
# 前台启动 vLLM 原生模式
service.sh vllm

# 前台启动 Dynamo 模式（自动启动 backend + frontend）
service.sh dynamo

# 后台启动 Dynamo 模式（日志写入 logs/，PID 写入 logs/）
service.sh dynamo --background --gpu-memory-utilization 0.4

# 后台启动 vLLM 模式
service.sh vllm --background --gpu-memory-utilization 0.4

# 覆盖默认参数
service.sh vllm --tensor_parallel_size 2 --gpu-memory-utilization 0.7
service.sh dynamo --model /mnt/nanhuinfer/models/Qwen3-1.5B
```

**Dynamo 模式端口规划：**

| 端口 | 用途 | 路由示例 |
|------|------|----------|
| 9090 | 前端 OpenAI API | `/v1/chat/completions`, `/v1/models` |
| 9091 | 后端控制面 | `/engine/control/switch_parallel_strategy`, `/engine/control/parallel_strategy_state` |

**vLLM 模式端口：** 9090（所有路由共用）

> ⚠️ 关键环境变量：`VLLM_PLUGINS=metax`（已内置到 service.sh），避免 metax/infinicore 插件冲突。`VLLM_SERVER_DEV_MODE=1`（已内置），启用 `/switch_parallel_strategy` 和 `/is_switching_parallel_strategy` API 路由。Dynamo 模式启动时还会自动清理 `/tmp/dynamo_store_kv`（discovery store），防止重启时状态残留。
>
> ⚠️ 在线切换必须加 `--enforce-eager` 参数启动服务，否则切换时 Worker 会因 CUDA Graph 编译卡住导致 RPC 超时。

### 5.3 状态查询与切换

```bash
# 健康检查（检查前端 + 控制面）
service.sh health

# 查询当前并行策略
service.sh status
# → POST http://localhost:9091/engine/control/parallel_strategy_state (dynamo)
# → POST http://localhost:9090/is_switching_parallel_strategy (vllm)

# 触发切换
service.sh switch \
    --new_world_size 4 \
    --target_tensor_parallel_size 2 \
    --target_pipeline_parallel_size 2 \
    --request_handling wait \
    --admission_handling queue
# → POST http://localhost:9091/engine/control/switch_parallel_strategy (dynamo)
# → POST http://localhost:9090/switch_parallel_strategy (vllm)
```

### 5.4 停止服务

```bash
service.sh stop
```

停止时会依次：
1. 通过 PID 文件终止前端和后端进程
2. 扫描并强制清理所有残留的 VLLM::Worker / VLLM::EngineCore / dynamo 进程
3. 清理 `/tmp/dynamo_store_kv` 防止重启状态残留

### 5.5 默认配置

| 参数 | 默认值 |
|------|--------|
| 前端端口 | 9090 |
| 控制面端口 | 9091 (Dynamo) |
| 模型 | `/mnt/nanhuinfer/models/Qwen3-0.6B/` |
| GPU 利用率 | 0.85 |
| TP | 4 |
| PP | 1 |
| 预构建策略 | `4x1,2x2,1x4` |
| 分布式后端 | `mp`（多进程） |
| Dynamo discovery | `file` |
| Dynamo 模式 | `agg`（聚合） |

---

## 6. 运行环境

| 项目 | 值 |
|------|-----|
| Python | 3.10.10 |
| PyTorch | 2.10.0+metax3.8.0.7 |
| vLLM（基线） | 0.22.0 |
| ai-dynamo | 1.5.0.dev20260906 |
| GPU 数量 | 4（TQ_GPU_NUM=4） |
| GPU 平台 | Metax MACA（通过 cu-bridge 兼容 CUDA API） |
| conda site-packages | `/opt/conda/lib/python3.10/site-packages` |
| 模型路径 | `/mnt/nanhuinfer/models/`（含 Qwen、DeepSeek 等系列） |

---

## 7. 架构与数据流

### 7.1 切换流程（在线模式）

```
用户请求 (HTTP POST)
    │
    ▼
┌──────────────────────────────────────┐
│  API 层                              │
│  vLLM: /switch_parallel_strategy     │
│  Dynamo: /engine/control/switch_*    │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  AsyncLLM (准入控制)                 │
│  - 加锁防止并发切换                   │
│  - 设置 admission_handling           │
│  - 中间件拦截新请求(queue/reject)     │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  EngineCore (编排)                   │
│  1. 校验请求参数                      │
│  2. 排空在途请求                      │
│  3. 构建 target config               │
│  4. Preflight 校验                   │
│  5. 条件性重置 prefix cache           │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  ModelExecutor (执行)                │
│  1. 广播切换请求到 Workers            │
│  2. Worker 端：重建进程组             │
│  3. Worker 端：迁移 KV Cache         │
│  4. Worker 端：重载模型权重           │
│  5. Worker 端：重初始化 CUDA Graphs   │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Scheduler (更新)                    │
│  1. 安装新 KV Cache Config           │
│  2. Block Pool resize                │
│  3. 更新并行配置                      │
│  4. 重配 Batch Queue & Step Fn       │
└──────────────────────────────────────┘
```

### 7.2 Dynamo 控制面适配层

```
Dynamo Runtime (Rust)
    │ register_engine_route("control/switch_parallel_strategy", handler)
    ▼
BaseWorkerHandler.switch_parallel_strategy(body)
    │ 1. 参数校验（类型、约束、互斥）
    │ 2. 获取 _engine_reconfig_lock
    │ 3. 构建 SwitchParallelStrategyRequest
    │ 4. 调用 AsyncLLM.switch_parallel_strategy()
    │ 5. 异常处理：失败时标记 worker 不可用，触发 shutdown
    ▼
AsyncLLM.switch_parallel_strategy(request)
    │ (同 7.1 流程)
    ▼
返回 {"status": "ok", "tp": ..., "pp": ...}
```

---

## 8. 约束与注意事项

### 8.1 不可修改的目录

- **`ElasticVllm_demo/`** — 只读，禁止任何修改
- **`dynamo/`** — 只读，禁止任何修改
- 如需修改这两个目录中的代码，应向对应的上游仓库提交 PR，再通过 `service.sh sync` 同步

### 8.2 可修改的文件

- `service.sh` — 运维脚本
- `test/test_offline_switch.py` — 离线切换演示脚本
- `test/test_online_switch.py` — 在线切换验证脚本
- 项目根目录下的新建文件（如 `AGENT.md`、配置文件、测试脚本等）

### 8.3 切换限制

- 当前仅支持 **DP=1**（不支持 Data Parallelism > 1 时的 TP/PP 切换）
- `decode_context_parallel_size` 和 `prefill_context_parallel_size` 须为 1
- Elastic EP 与 TP/PP 切换**互斥**：`enable_elastic_ep=True` 时不能做 TP/PP 切换
- 切换失败后引擎进入不可恢复状态，必须**重启服务**
- 在线模式切换需要 `VLLM_SERVER_DEV_MODE=1` 环境变量

### 8.4 环境特殊说明

- GPU 平台为 **Metax MACA**，通过 `/opt/maca/tools/cu-bridge` 提供 CUDA 兼容层
- `CUDA_PATH` 指向 MACA 的 cu-bridge，不是 NVIDIA CUDA
- PyTorch 版本为 `2.10.0+metax3.8.0.7`，非标准 NVIDIA PyTorch
- 如遇 GPU/NVIDIA 相关命令不可用，需使用 MACA 对应工具

---

## 9. 常用开发与调试命令

### 9.1 代码同步与验证

```bash
# 同步上游代码到运行环��（自动应用 patches/ 目录补丁）
service.sh sync

# 验证 vllm 版本
pip show vllm

# 验证切换能力已注入
python -c "from vllm.v1.engine import SwitchParallelStrategyRequest; print('OK')"

# 验证 Dynamo 兼容类（由 patches/exceptions_add_VLLMClientError.patch 提供）
python -c "from vllm.exceptions import VLLMClientError; print('OK')"

# 补丁文件列表
ls patches/*.patch
```

### 9.2 服务启停

```bash
# 后台启动 Dynamo（注意：Dynamo 模式切换需要 --enforce-eager）
service.sh dynamo --background --gpu-memory-utilization 0.4 --enforce-eager
tail -f logs/backend.log

# 后台启动 vLLM
service.sh vllm --background --gpu-memory-utilization 0.4 --enforce-eager
tail -f logs/backend.log

# 健康检查
service.sh health

# 查询状态
service.sh status

# 停止
service.sh stop
```

### 9.3 在线切换验证

```bash
# 运行完整在线切换测试（需先启动服务）
python3 test/test_online_switch.py
```

测试流程（参照 test/test_offline_switch.py）：
1. 验证初始状态 4×1
2. Warmup 推理
3. 切换 4×1 → 2×2 → 推理
4. 切换 2×2 → 1×4 → 推理
5. 切换 1×4 → 4×1 → 推理

### 9.3 手动 API 调用

```bash
# 查询并行策略状态（Dynamo 模式）
curl -s -X POST http://localhost:9091/engine/control/parallel_strategy_state \
  -H "Content-Type: application/json" -d '{}' | jq .

# 触发切换（Dynamo 模式）
curl -s -X POST http://localhost:9091/engine/control/switch_parallel_strategy \
  -H "Content-Type: application/json" \
  -d '{
    "new_world_size": 4,
    "target_tensor_parallel_size": 2,
    "target_pipeline_parallel_size": 2,
    "request_handling": "wait",
    "admission_handling": "queue"
  }' | jq .

# 推理请求
curl -s http://localhost:9090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"/mnt/nanhuinfer/models/Qwen3-0.6B/","messages":[{"role":"user","content":"Hi"}]}' | jq .
```

---

## 10. 相关文档索引

| 文档 | 位置 |
|------|------|
| ElasticVllm_demo Agent 指南 | `ElasticVllm_demo/AGENTS.md` |
| Dynamo Agent 指南 | `dynamo/AGENTS.md` |
| Dynamo 测试指南 | `dynamo/tests/AGENTS.md` |
| Dynamo Router 测试指南 | `dynamo/tests/router/AGENTS.md` |
| Dynamo 文档站指南 | `dynamo/docs/fern/AGENTS.md` |
| vLLM README | `ElasticVllm_demo/README.md` |
| Dynamo README | `dynamo/README.md` |