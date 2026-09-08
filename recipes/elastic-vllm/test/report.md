# Elastic-vLLM TP/PP 并行策略切换测试报告

| 项目 | 值 |
|------|-----|
| 测试日期 | 2026-09-08 |
| 测试模式 | Dynamo 模式 |
| 测试类型 | 离线测试 + 在线测试 |
| 测试结果 | ✅ 全部通过 |

---

## 1. 测试环境

| 项目 | 值 |
|------|-----|
| GPU 数量 | 4 |
| GPU 平台 | Metax MACA（cu-bridge CUDA 兼容层） |
| Python | 3.10.10 |
| PyTorch | 2.10.0+metax3.8.0.7 |
| vLLM | 0.22.0 |
| 模型 | `/mnt/nanhuinfer/models/Qwen3-0.6B/` |
| conda site-packages | `/opt/conda/lib/python3.10/site-packages` |
| GPU 显存利用率 | 0.4 |
| enforce_eager | True |
| 预构建策略 | 4x1, 2x2, 1x4 |

### 关键环境变量

| 变量 | 值 | 说明 |
|------|-----|------|
| `VLLM_PLUGINS` | metax | 避免 metax/infinicore 插件冲突 |
| `VLLM_SERVER_DEV_MODE` | 1 | 启用切换 API 路由 |
| `VLLM_DISTRIBUTED_EXECUTOR_BACKEND` | mp | 多进程执行器 |

---

## 2. 执行步骤

### 2.1 代码同步（sync）

**命令：**

```bash
cd /workspace
export ELASTIC_VLLM_GITHUB_TOKEN=<token>
./dynamo/recipes/elastic-vllm/service.sh sync
```

**执行结果：**

```
>>> /workspace/dynamo exist, run git pull
Already up to date.
>>> /workspace/ElasticVllm_demo exist, run git pull
Already up to date.

>>> copy ElasticVllm_demo/vllm to /opt/conda/lib/python3.10/site-packages/vllm
>>> copy dynamo/components/src/dynamo/vllm to /opt/conda/lib/python3.10/site-packages/dynamo/vllm

>>> sync completed!
>>> Applying local patches from /workspace/dynamo/recipes/elastic-vllm/patches/
    Applying exceptions_add_VLLMClientError.patch
        ✅ Applied successfully
>>> Patch verification OK
```

**同步内容：**

1. Git pull `dynamo`（分支 `ElasticVllm`）— Already up to date
2. Git pull `ElasticVllm_demo`（分支 `codex/add-v0.22.0`）— Already up to date
3. 复制 `ElasticVllm_demo/vllm/*` → `/opt/conda/lib/python3.10/site-packages/vllm/`
4. 复制 `dynamo/components/src/dynamo/vllm/*` → `/opt/conda/lib/python3.10/site-packages/dynamo/vllm/`
5. 应用本地补丁 `exceptions_add_VLLMClientError.patch` — 成功
6. 补丁验证（`SwitchParallelStrategyRequest`、`VLLMClientError` 导入）— 通过

**结果：✅ 同步成功**

---

### 2.2 离线测试（test_offline_switch.py）

**命令：**

```bash
cd /workspace
export VLLM_PLUGINS=metax
export VLLM_SERVER_DEV_MODE=1
export VLLM_DISTRIBUTED_EXECUTOR_BACKEND=mp

python3 dynamo/recipes/elastic-vllm/test/test_offline_switch.py \
    --gpu-memory-utilization 0.4
```

**测试流程与结果：**

| 步骤 | 操作 | 结果 |
|------|------|------|
| Step 1 | 初始化 LLM（4×1） | ✅ 引擎初始化成功，4 Worker 就绪 |
| Step 2 | Warmup 推理 | ✅ Prompt: `'warmup'` → Response: `'\nThis is a'` |
| Step 3 | 切换 4×1 → 2×2 | ✅ Switch to 2×2 completed |
| Step 4 | 2×2 配置下推理 | ✅ Prompt: `'hello'` → 正常输出 |
| Step 5 | 切换 2×2 → 1×4 | ✅ Switch to 1×4 completed |
| Step 6 | 1×4 配置下推理 | ✅ Prompt: `'what is the capital of France?'` → 正常输出 |
| Step 7 | 切换 1×4 → 4×1 | ✅ Switch to 4×1 completed |
| Step 8 | 4×1 配置下推理 | ✅ Prompt: `'goodbye!'` → 正常输出 |

**关键日志信息：**

- 引擎初始化：`Initializing a V1 LLM engine (v0.22.0)`
- GPU KV Cache：`829,312 tokens`，最大并发 `20.25x`
- 预构建通信组：`TP=4 PP=1`、`TP=2 PP=2`、`TP=1 PP=4` 均成功
- KV 迁移预检：每次切换均生成 preflight plan 并成功执行
- 权重重载：每次切换后模型权重重新加载，耗时 0.4s ~ 1.0s

**结果：✅ 离线测试全部通过**

```
================================================================================
  Summary
================================================================================
  ✅ All offline switch tests passed!
  Transitions verified:
    4×1 ──→ 2×2 ──→ 1×4 ──→ 4×1
  Inference succeeded at each configuration.
```

---

### 2.3 启动 Dynamo 服务

**命令：**

```bash
cd /workspace
./dynamo/recipes/elastic-vllm/service.sh dynamo --background \
    --gpu-memory-utilization 0.4 --enforce-eager
```

**启动结果：**

```
===== Dynamo service started =====
  Frontend (OpenAI API): http://localhost:9090
  Control plane:         http://localhost:9091
  Backend PID:  664322
  Frontend PID: 664327
```

**健康检查：**

| 端点 | 端口 | 状态 |
|------|------|------|
| Frontend (`/health`) | 9090 | ✅ healthy |
| Control plane (`/health`) | 9091 | ✅ ready（初始化约 39s 后就绪） |

**初始并行策略状态：**

```json
{
  "status": "ok",
  "tensor_parallel_size": 4,
  "pipeline_parallel_size": 1,
  "data_parallel_size": 1,
  "world_size": 4,
  "physical_world_size": null,
  "num_gpu_blocks": 51850,
  "is_switching": false,
  "failed": false
}
```

**结果：✅ Dynamo 服务启动成功**

---

### 2.4 在线测试（test_online_switch.py）

**命令：**

```bash
cd /workspace
export VLLM_PLUGINS=metax
export VLLM_SERVER_DEV_MODE=1

python3 dynamo/recipes/elastic-vllm/test/test_online_switch.py
```

**测试流程与结果：**

| 步骤 | 操作 | 结果 |
|------|------|------|
| Step 0 | 验证初始状态（4×1） | ✅ 服务可达，TP=4, PP=1, is_switching=false |
| Step 1 | Warmup 推理 | ✅ Prompt: `'warmup'` → Response: `'nież\nOkay,'` (4 tokens) |
| Step 2 | 切换 4×1 → 2×2 | ✅ Switch 请求成功，等待切换完成 |
| Step 3 | 2×2 配置下推理 | ✅ Prompt: `'hello, who are you?'` → 正常输出 (30 tokens) |
| Step 4 | 切换 2×2 → 1×4 | ✅ Switch 请求成功，等待切换完成 |
| Step 5 | 1×4 配置下推理 | ✅ Prompt: `'what is the capital of France?'` → 正常输出 (30 tokens) |
| Step 6 | 切换 1×4 → 4×1 | ✅ Switch 请求成功，等待切换完成 |
| Step 7 | 4×1 配置下推理 | ✅ Prompt: `'goodbye!'` → 正常输出 (20 tokens) |

**切换请求响应示例（4×1 → 2×2）：**

```json
{
  "status": "ok",
  "message": "TP/PP switch completed",
  "tensor_parallel_size": 4,
  "pipeline_parallel_size": 1,
  "data_parallel_size": 1,
  "world_size": 4,
  "num_gpu_blocks": 51850,
  "is_switching": true,
  "failed": false
}
```

> **说明：** 切换请求返回时 `is_switching=true` 表示切换已触发但尚未完成，测试脚本通过轮询 `parallel_strategy_state` 接口等待 `is_switching` 变为 `false`，确认切换完成后才进行推理验证。

**结果：✅ 在线测试全部通过**

```
================================================================================
  Summary
================================================================================
  ✅ All online switch tests passed!
  Transitions verified:
    4×1 ──→ 2×2 ──→ 1×4 ──→ 4×1
  Inference succeeded at each configuration.
```

---

### 2.5 停止服务

**命令：**

```bash
cd /workspace
./dynamo/recipes/elastic-vllm/service.sh stop
```

**执行结果：**

```
>>> Stopping all service processes...
  Killing PID 664322 (backend.pid)
  Killing PID 664327 (frontend.pid)
  Found residual processes: 664788 665049 665050 665051 665052
  All service processes stopped
  Cleaning discovery store: /tmp/dynamo_store_kv
>>> Stop complete
```

**结果：✅ 服务停止成功，所有进程及残留清理完毕**

---

## 3. 测试结论

### 3.1 总体结论

| 测试项 | 结果 |
|--------|------|
| 代码同步（sync） | ✅ 通过 |
| 离线切换测试（test_offline_switch.py） | ✅ 通过 |
| 在线切换测试（test_online_switch.py） | ✅ 通过 |

**全部测试通过。** Dynamo 模式下，弹性 TP/PP 并行策略切换功能正常工作。

### 3.2 切换路径验证

```
4×1 ──→ 2×2 ──→ 1×4 ──→ 4×1
  │        │        │        │
  ✅        ✅        ✅        ✅
 推理OK   推理OK   推理OK   推理OK
```

### 3.3 观察与备注

1. **NCCL 库警告**：日志中出现 `Failed to load NCCL library from libnccl.so.2` 错误，这是 Metax MACA 平台的预期行为（使用 MCCL 替代 NCCL），不影响功能。
2. **KV Cache 迁移**：每次切换时 KV Cache 迁移（reshard）均正常执行，离线模式下可观察到 preflight plan 生成。
3. **权重重载耗时**：离线模式下每次切换后权重重载约 0.4s ~ 1.0s，属于正常范围。
4. **Dynamo 服务初始化**：Backend 从启动到就绪约需 39s（含模型加载、通信组预构建等）。
5. **GPU KV Cache**：Dynamo 模式下初始化后 GPU KV Cache 为 51,850 blocks，离线模式下为 829,312 tokens。
6. **重复参数警告**：`--enforce-eager` 和 `--gpu-memory-utilization` 在合并参数时出现重复，不影响功能（vLLM 取后者值）。
