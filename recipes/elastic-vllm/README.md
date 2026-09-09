# Elastic-vLLM: 弹性 TP/PP 并行策略切换

在 4 GPU 场景下，运行时动态切换 Tensor Parallelism (TP) 与 Pipeline Parallelism (PP) 并行策略，无需重启服务即可适配不同负载。

## 支持的并行策略

| 策略 | TP | PP | 适用场景 |
|------|----|----|----------|
| `4x1` | 4 | 1 | 高吞吐 Prefill |
| `2x2` | 2 | 2 | 均衡 Prefill/Decode |
| `1x4` | 1 | 4 | 低延迟 Decode |

## 环境要求

| 项目 | 值 |
|------|-----|
| GPU 数量 | 4 |
| Python | 3.10+ |
| PyTorch | 2.10.0+metax |
| vLLM | 0.22.0 |
| 模型路径 | `/mnt/nanhuinfer/models/Qwen3-0.6B/` |

## 快速开始

### 1. 设置环境变量

```bash
# 必需：用于同步 ElasticVllm_demo 私有仓库
export ELASTIC_VLLM_GITHUB_TOKEN=<your-github-token>
```

### 2. 同步代码

```bash
cd /workspace
./dynamo/recipes/elastic-vllm/service.sh sync
```

该命令会：
1. 从 GitHub 克隆/拉取 `ElasticVllm_demo` 和 `dynamo` 仓库
2. 将源码复制到 conda site-packages
3. 自动应用 `patches/` 目录下的本地补丁

> **注意**：`service.sh` 的执行目录应为 `/workspace`，脚本会自动检测工作目录。

### 3. 启动服务

```bash
cd /workspace

# vLLM 原生模式（后台）
./dynamo/recipes/elastic-vllm/service.sh vllm --background --gpu-memory-utilization 0.4

# Dynamo 模式（后台，含 backend + frontend）
./dynamo/recipes/elastic-vllm/service.sh dynamo --background --gpu-memory-utilization 0.4
```

> **注意**：`--enforce-eager` 已纳入默认参数（COMMON_ARGS），无需手动指定。在线切换必须启用该参数，否则切换时 Worker 会因 CUDA Graph 编译卡住导致 RPC 超时。

### 4. 健康检查

```bash
./dynamo/recipes/elastic-vllm/service.sh health
```

查询并行策略状态和触发切换需通过 HTTP API 直接调用（service.sh 未内置 status/switch 子命令）：

```bash
# 查询当前并行策略状态（Dynamo 模式）
curl -s -X POST http://localhost:9091/engine/control/parallel_strategy_state \
  -H "Content-Type: application/json" -d '{}' | jq .

# 查询是否正在切换（vLLM 原生模式）
curl -s http://localhost:9090/is_switching_parallel_strategy | jq .
```

### 5. 手动触发切换

```bash
# Dynamo 模式
curl -s -X POST http://localhost:9091/engine/control/switch_parallel_strategy \
  -H "Content-Type: application/json" \
  -d '{"new_world_size":4,"target_tensor_parallel_size":2,"target_pipeline_parallel_size":2,"request_handling":"wait","admission_handling":"queue"}' | jq .

# vLLM 原生模式
curl -s -X POST http://localhost:9090/switch_parallel_strategy \
  -H "Content-Type: application/json" \
  -d '{"new_world_size":4,"target_tensor_parallel_size":2,"target_pipeline_parallel_size":2,"request_handling":"wait","admission_handling":"queue"}' | jq .
```

### 6. 停止服务

```bash
./dynamo/recipes/elastic-vllm/service.sh stop
```

## 测试

### 离线测试 (`test/test_offline_switch.py`)

使用 `LLM` 类直接在进程内进行离线推理和切换，无需启动 HTTP 服务。

**流程**：
1. 初始化 LLM（4×1 配置）
2. Warmup 推理
3. 切换 4×1 → 2×2 → 推理验证
4. 切换 2×2 → 1×4 → 推理验证
5. 切换 1×4 → 4×1 → 推理验证

**运行命令**：

```bash
cd /workspace

export VLLM_PLUGINS=metax
export VLLM_SERVER_DEV_MODE=1

python3 dynamo/recipes/elastic-vllm/test/test_offline_switch.py \
    --gpu-memory-utilization 0.4
```

**预期输出**：

```
================================================================================
  Step 1: Initialize LLM (4×1)
================================================================================
...（引擎初始化日志）...

================================================================================
  Step 2: Warmup inference
================================================================================
  Prompt: 'warmup'  →  '...'

================================================================================
  Step 3: Switch 4×1 → 2×2
================================================================================
  ✅ Switch to 2×2 completed

================================================================================
  Step 4: Inference at 2×2
================================================================================
  Prompt: 'hello'
  Text:   '...'

================================================================================
  Step 5: Switch 2×2 → 1×4
================================================================================
  ✅ Switch to 1×4 completed

================================================================================
  Step 6: Inference at 1×4
================================================================================
  Prompt: 'what is the capital of France?'
  Text:   '...'

================================================================================
  Step 7: Switch 1×4 → 4×1 (back to original)
================================================================================
  ✅ Switch to 4×1 completed

================================================================================
  Step 8: Final inference at 4×1
================================================================================
  Prompt: 'goodbye!'
  Text:   '...'

================================================================================
  Summary
================================================================================
  ✅ All offline switch tests passed!
  Transitions verified:
    4×1 ──→ 2×2 ──→ 1×4 ──→ 4×1
  Inference succeeded at each configuration.
```

### 在线测试 (`test/test_online_switch.py`)

通过 HTTP API 与 vLLM / Dynamo 服务交互，验证在线切换功能。**需先启动服务**。

**流程**：
1. 验证服务可达性和初始状态（4×1）
2. Warmup 推理
3. 发送切换请求 4×1 → 2×2 → 等待切换完成 → 推理验证
4. 发送切换请求 2×2 → 1×4 → 等待切换完成 → 推理验证
5. 发送切换请求 1×4 → 4×1 → 等待切换完成 → 推理验证

**运行步骤**：

```bash
cd /workspace

# 1. 启动 vLLM 服务（后台）
./dynamo/recipes/elastic-vllm/service.sh vllm --background \
    --gpu-memory-utilization 0.4

# 2. 等待服务就绪
./dynamo/recipes/elastic-vllm/service.sh health

# 3. 运行在线测试
export VLLM_PLUGINS=metax
export VLLM_SERVER_DEV_MODE=1
python3 dynamo/recipes/elastic-vllm/test/test_online_switch.py

# 4. 测试完成后停止服务
./dynamo/recipes/elastic-vllm/service.sh stop
```

**预期输出**：

```
================================================================================
  Step 0: Verify initial state (4×1)
================================================================================
{
  "is_switching_parallel_strategy": false,
  "admission_handling": "queue",
  "retry_after": 1
}
✅ Service is reachable

================================================================================
  Step 1: Warmup inference
================================================================================
─── [Infer] warmup ───
  Prompt:   'warmup'
  Response: '...'
  Tokens:   4

================================================================================
  Step 2: Switch 4×1 → 2×2
================================================================================
  Sending switch request...
{
  "status": "switched",
  ...
}
  ✅ Switch complete (is_switching_parallel_strategy=false)

================================================================================
  Step 3: Inference at 2×2
================================================================================
─── [Infer] 2x2 ───
  Prompt:   'hello, who are you?'
  Response: '...'
  Tokens:   30

...（后续切换步骤类似）...

================================================================================
  Summary
================================================================================
  ✅ All online switch tests passed!
  Transitions verified:
    4×1 ──→ 2×2 ──→ 1×4 ──→ 4×1
  Inference succeeded at each configuration.
```

### 手动 API 调用验证

```bash
# 推理请求（vLLM 或 Dynamo 模式均可）
curl -s http://localhost:9090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"/mnt/nanhuinfer/models/Qwen3-0.6B/","messages":[{"role":"user","content":"Hi"}]}' | jq .

# 查询并行策略状态（Dynamo 模式）
curl -s -X POST http://localhost:9091/engine/control/parallel_strategy_state \
  -H "Content-Type: application/json" -d '{}' | jq .

# 触发切换（Dynamo 模式）
curl -s -X POST http://localhost:9091/engine/control/switch_parallel_strategy \
  -H "Content-Type: application/json" \
  -d '{"new_world_size":4,"target_tensor_parallel_size":2,"target_pipeline_parallel_size":2,"request_handling":"wait","admission_handling":"queue"}' | jq .

# 查询并行策略状态（vLLM 原生模式）
curl -s http://localhost:9090/is_switching_parallel_strategy | jq .

# 触发切换（vLLM 原生模式）
curl -s -X POST http://localhost:9090/switch_parallel_strategy \
  -H "Content-Type: application/json" \
  -d '{"new_world_size":4,"target_tensor_parallel_size":2,"target_pipeline_parallel_size":2,"request_handling":"wait","admission_handling":"queue"}' | jq .
```

## 切换参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `new_world_size` | int | 4 | 切换后总 GPU 数（须 = TP × PP） |
| `target_tensor_parallel_size` | int | — | 目标 TP 度 |
| `target_pipeline_parallel_size` | int | — | 目标 PP 度 |
| `target_num_blocks` | int\|null | null | 目标 KV Cache 块数（null=自动计算） |
| `request_handling` | `"idle"`\|`"wait"` | `"wait"` | `idle`：仅空闲时切换；`wait`：等待在途请求完成后切换 |
| `admission_handling` | `"queue"`\|`"reject"` | `"queue"` | 切换期间新请求的处理策略：排队等待或拒绝(503) |
| `retry_after` | int | 1 | 拒绝时建议的重试等待秒数 |

## 端口规划

### Dynamo 模式

| 端口 | 用途 | 路由示例 |
|------|------|----------|
| 9090 | 前端 OpenAI API | `/v1/chat/completions`, `/v1/models` |
| 9091 | 后端控制面 | `/engine/control/switch_parallel_strategy`, `/engine/control/parallel_strategy_state` |

### vLLM 原生模式

| 端口 | 用途 |
|------|------|
| 9090 | 所有路由（包括控制面和推理 API） |

## 文件结构

```
elastic-vllm/
├── README.md                 # 本文档
├── AGENT.md                  # 项目导航与协作指南
├── service.sh                # 统一运维脚本（sync/start/stop/switch/status/health）
├── patches/                  # 本地补丁文件（sync 后自动应用）
│   └── exceptions_add_VLLMClientError.patch
└── test/
    ├── test_offline_switch.py # 离线切换测试
    └── test_online_switch.py  # 在线切换测试
```

## 约束与注意事项

- 当前仅支持 **DP=1**（不支持 Data Parallelism > 1 时的 TP/PP 切换）
- Elastic EP 与 TP/PP 切换**互斥**：`enable_elastic_ep=True` 时不能做 TP/PP 切换
- 切换失败后引擎进入不可恢复状态，必须**重启服务**
- 在线模式切换需要 `VLLM_SERVER_DEV_MODE=1` 环境变量（已内置到 service.sh）
- 在线切换必须启用 `--enforce-eager`（已纳入 COMMON_ARGS 默认参数，无需手动指定）
- GPU 平台为 **Metax MACA**，通过 cu-bridge 提供 CUDA 兼容层