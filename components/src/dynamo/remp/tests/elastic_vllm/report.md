# Elastic-vLLM TP/PP 并行策略切换测试报告

| 项目 | 值 |
|------|-----|
| 测试日期 | 2026-09-11 |
| 测试模式 | Dynamo 模式（InfiniCore 平台插件） |
| 测试类型 | 离线测试 + 在线测试 |
| 测试模型 | Qwen3.8-27B |
| 测试结果 | ✅ 全部通过 |

---

## 1. 测试环境

### 1.1 硬件与软件

| 项目 | 值 |
|------|-----|
| GPU 数量 | 4 × MetaX C550 (63.6 GB/GPU) |
| GPU 平台 | Metax MACA（cu-bridge CUDA 兼容层） |
| Python | 3.10.10 |
| PyTorch | 2.10.0+metax3.8.0.7 |
| vLLM | 0.22.0 |
| ai-dynamo Rust 绑定 | v1.5.0.dev20260906 |
| 平台插件 | `vllm-infinicore`（InfiniCore 模式） |
| conda site-packages | `/opt/conda/lib/python3.10/site-packages` |

### 1.2 模型配置

| 项目 | 值 |
|------|-----|
| 模型路径 | `/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/` |
| 模型架构 | `Qwen3_5ForConditionalGeneration`（hybrid linear+full attention） |
| 模型大小 | ~55 GB（18-shard safetensors） |
| GPU 显存利用率 | 0.85 |
| enforce_eager | True |
| 预构建策略 | 4x1, 2x2, 1x4 |
| 在线测试模式 | Dynamo 模式 |

### 1.3 关键环境变量（InfiniCore 模式）

| 变量 | 值 | 说明 |
|------|-----|------|
| `VLLM_PLUGINS` | infinicore | 激活 InfiniCore 平台插件，替代 metax |
| `VLLM_SERVER_DEV_MODE` | 1 | 启用切换 API 路由 |
| `VLLM_DISTRIBUTED_EXECUTOR_BACKEND` | mp | 多进程执行器 |
| `VLLM_INFINICORE_GDN_SINGLE_STAGE` | 1 | C550 共享内存限制，Triton kernel `num_stages` 需为 1 |
| `MACA_PATH` / `MACA_HOME` / `MACA_ROOT` | `/opt/maca-3.8.0` | MACA 运行时路径 |
| `FLASH_ATTN_2_CUDA_SO` | 动态解析 | 通过 Python `importlib` 解析实际 `.so` 路径，避免插件硬编码的 python3.12 默认路径 |

### 1.4 `FLASH_ATTN_2_CUDA_SO` 动态解析

InfiniCore 插件的 `cpp_bridge.py` 默认查找 Python 3.12 路径下的 `flash_attn_2_cuda.so`，而当前环境使用 Python 3.10，导致 C++ bridge 编译失败。解决方案是在 `service.sh` 中动态解析并导出该路径：

```bash
PYTHON_BIN="${PYTHON_BIN:-/opt/conda/bin/python}"
if [[ -z "${FLASH_ATTN_2_CUDA_SO:-}" ]]; then
    FLASH_ATTN_2_CUDA_SO="$("$PYTHON_BIN" - <<'PY'
import importlib.util
from pathlib import Path
spec = importlib.util.find_spec("flash_attn_2_cuda")
if spec is None or spec.origin is None:
    raise SystemExit("flash_attn_2_cuda is not installed for the selected Python")
library = Path(spec.origin).resolve()
if not library.is_file() or library.suffix != ".so":
    raise SystemExit(f"FlashAttention shared library not found: {library}")
print(library)
PY
)"
    export FLASH_ATTN_2_CUDA_SO
fi
```

解析结果：`/opt/conda/lib/python3.10/site-packages/flash_attn_2_cuda.cpython-310-x86_64-linux-gnu.so`

### 1.5 代码基线

| 仓库 | 分支 | Commit | 日期 | 提交信息 |
|------|------|--------|------|----------|
| [nhcf/dynamo](https://github.com/nhcf/dynamo.git) | `ElasticVllm` | `42a670a` | 2026-09-09 | Merge pull request #2 from nhcf/main |
| [yhp49/ElasticVllm_demo](https://github.com/yhp49/ElasticVllm_demo.git) | `codex/add-v0.22.0` | `11a60cf` | 2026-09-08 | fix(worker): release hybrid models before TP/PP KV allocation |

<details>
<summary>完整 Commit 信息</summary>

**dynamo** (`ElasticVllm` 分支)

```
commit 42a670aeaf8cd2c764689aa6f7312cfb74e23093
Author: Limixxx <99061087+Limixxx@users.noreply.github.com>
Date:   2026-09-09 09:28:08 +0800

    Merge pull request #2 from nhcf/main
```

**ElasticVllm_demo** (`codex/add-v0.22.0` 分支)

```
commit 11a60cf72312d157a06e6d0f35b2e345275191a3
Author: yhp49 <2622522970@qq.com>
Date:   2026-09-08 15:41:45 +0800

    fix(worker): release hybrid models before TP/PP KV allocation
```

</details>

---

## 2. 代码更新说明

### 2.1 同步流程

由于 `ELASTIC_VLLM_GITHUB_TOKEN` 未设置，`service.sh sync` 无法自动执行。采用手动同步方式：

```bash
export CONDA_SITE="/opt/conda/lib/python3.10/site-packages"
export PATCH_DIR="/workspace/dynamo/recipes/elastic-vllm/patches"

# 1. 复制 ElasticVllm_demo/vllm（弹性 vLLM 引擎核心代码）
cp -rf /workspace/ElasticVllm_demo/vllm/* "${CONDA_SITE}/vllm/"

# 2. 复制 dynamo/components/src/dynamo/vllm（Dynamo vLLM 后端适配层）
cp -rf /workspace/dynamo/components/src/dynamo/vllm/* "${CONDA_SITE}/dynamo/vllm/"

# 3. 仅复制 dynamo/common/constants.py（新增 KV_HINT_TRANSFER_CAPABILITY_KEY）
#    ⚠️ 不要全量复制 dynamo/common/*，详见 §2.3 备注 #1
cp /workspace/dynamo/components/src/dynamo/common/constants.py \
   "${CONDA_SITE}/dynamo/common/constants.py"

# 4. 应用全部本地补丁
for pf in "${PATCH_DIR}"/*.patch; do
    patch -p1 -d "${CONDA_SITE}" --force --forward < "$pf"
done
```

**验证（InfiniCore 模式）：**

```bash
export VLLM_PLUGINS=infinicore
python3 -c "from dynamo.vllm.main import main; print('✅ OK')"
python3 -c "from dynamo.frontend.main import main; print('✅ OK')"
python3 -c "from vllm.v1.engine import SwitchParallelStrategyRequest; print('✅ OK')"
```

### 2.2 补丁清单

同步过程中对 `site-packages` 中的 5 个文件进行了修补，均已整理为标准 unified diff 格式的
patch 文件，存放于 `dynamo/recipes/elastic-vllm/patches/` 目录下。

| # | 补丁文件 | 目标文件 | 分类 | 用途 |
|---|----------|----------|------|------|
| 1 | `chunk_delta_h_add_if.patch` | `vllm/model_executor/layers/fla/ops/chunk_delta_h.py` | 硬件适配 | MetaX C550 共享内存限制：`num_stages` 需为 1 |
| 2 | `exceptions_add_VLLMClientError.patch` | `vllm/exceptions.py` | 缺失依赖 | 新增 `VLLMClientError` 异常类 |
| 3 | `dynamo_vllm_main_add_response_plane_compat.patch` | `dynamo/vllm/main.py` | API 兼容 | `config.response_plane` → `getattr` 安全访问 |
| 4 | `dynamo_common_runtime_add_response_plane_param.patch` | `dynamo/common/utils/runtime.py` | API 兼容 | 为 `create_runtime()` 添加 `response_plane` 参数占位 |
| 5 | `vllm_async_llm_sync_parallel_config_after_switch.patch` | `vllm/v1/engine/async_llm.py` | Bug 修复 | 切换后同步 `vllm_config.parallel_config` |

<details>
<summary>补丁���细说明</summary>

#### ① chunk_delta_h_add_if.patch — 硬件适配

- **修改文件**：`vllm/model_executor/layers/fla/ops/chunk_delta_h.py`
- **原因**：MetaX C550 GPU 共享内存仅 64 KiB，Triton kernel 的 `num_stages` 参数必须设为 1 才能编译通过；默认值 `[2, 3, 4]` 会导致编译 OOM
- **修改内容**：
  1. 添加 `import os`
  2. 将 `for num_stages in [2, 3, 4]` 替换为条件表达式：当环境变量 `VLLM_INFINICORE_GDN_SINGLE_STAGE=1` 时使用 `[1]`，否则保持 `[2, 3, 4]`
- **激活方式**：`export VLLM_INFINICORE_GDN_SINGLE_STAGE=1`

```diff
+import os
 ...
-        for num_stages in [2, 3, 4]
+        for num_stages in (
+            [1] if os.environ.get("VLLM_INFINICORE_GDN_SINGLE_STAGE") == "1"
+            else [2, 3, 4]
+        )
```

#### ② exceptions_add_VLLMClientError.patch — 缺失依赖

- **修改文件**：`vllm/exceptions.py`
- **原因**：ElasticVllm fork 的 `vllm.v1.engine.exceptions` 模块引用了 `VLLMClientError`，但该类在基线 vLLM 0.22.0 中不存在
- **修改内容**：
  1. 新增 `VLLMClientError(Exception)` 基类
  2. 新增 `VLLMUnprocessableEntityError(VLLMClientError)` 子类
  3. 将 `VLLMNotFoundError` 的基类从 `Exception` 改为 `VLLMClientError`

```diff
-class VLLMNotFoundError(Exception):
+class VLLMClientError(Exception):
+    """vLLM-specific client error (4xx class)."""
+    pass
+
+class VLLMUnprocessableEntityError(VLLMClientError):
+    """vLLM-specific unprocessable entity error (422)."""
+    pass
+
+class VLLMNotFoundError(VLLMClientError):
     """vLLM-specific NotFoundError"""
```

#### ③ dynamo_vllm_main_add_response_plane_compat.patch — API 兼容

- **修改文件**：`dynamo/vllm/main.py`（第 229 行）
- **原因**：新版 `dynamo/components` 的 `main.py` 中 `create_runtime()` 调用传入了 `response_plane=config.response_plane`，但当前安装的 `ai-dynamo` v1.5.0.dev20260906 的 `Config` 对象没有 `response_plane` 属性，导致 `AttributeError`
- **修改内容**：将 `config.response_plane` 替换为 `getattr(config, "response_plane", "tcp")`

```diff
-        response_plane=config.response_plane,
+        response_plane=getattr(config, "response_plane", "tcp"),
```

#### ④ dynamo_common_runtime_add_response_plane_param.patch — API 兼容

- **修改文件**：`dynamo/common/utils/runtime.py`（第 50 行）
- **原因**：旧版 pip 安装的 `create_runtime()` 函数签名没有 `response_plane` 参数，而新版 `main.py` 会传入此参数，导致 `TypeError`
- **修改内容**：在 `create_runtime()` 参数列表中添加 `response_plane: str = "tcp"` 占位参数（接收但不传递给 `DistributedRuntime`，因为旧版 Rust 绑定不支持该参数）
- **⚠️ 重要**：不要全量复制 `dynamo/components/src/dynamo/common/*` 到 site-packages 来替代此补丁，因为新版 `runtime.py` 会将 `response_plane` 传递给 `DistributedRuntime()`，而旧版 Rust 绑定不接受该参数，会导致前端和后端均启动失败

```diff
     use_kv_events: Optional[bool] = None,
+    response_plane: str = "tcp",
 ) -> Tuple[DistributedRuntime, asyncio.AbstractEventLoop]:
```

#### ⑤ vllm_async_llm_sync_parallel_config_after_switch.patch — Bug 修复

- **修改文件**：`vllm/v1/engine/async_llm.py`
- **原因**：`switch_parallel_strategy()` 调用 `await self.engine_core.switch_parallel_strategy_async(request)` 后，EngineCore 在独立进程中更新了自身的 `parallel_config`，但 `AsyncLLM` 进程中的 `self.vllm_config.parallel_config` 仍持有旧值。Dynamo handler 的 `_parallel_strategy_state()` 方法从 `self.vllm_config.parallel_config` 读取 TP/PP，导致切换后查询始终返回旧值
- **修改内容**：在 `await` 成功后，添加镜像更新，确保 `AsyncLLM` 进程内的配置与 `EngineCore` 保持一致

```diff
             await self.engine_core.switch_parallel_strategy_async(request)
+            # Mirror the parallel config update that EngineCore performs so that
+            # callers (e.g. Dynamo handler _parallel_strategy_state) see the new
+            # TP/PP values without a separate round-trip.
+            self.vllm_config.parallel_config.tensor_parallel_size = (
+                request.target_tensor_parallel_size
+            )
+            self.vllm_config.parallel_config.pipeline_parallel_size = (
+                request.target_pipeline_parallel_size
+            )
+            self.vllm_config.parallel_config.world_size = request.new_world_size
+            if hasattr(self.vllm_config.parallel_config, "__post_init__"):
+                self.vllm_config.parallel_config.__post_init__()
```

- **适用条件**：仅 Dynamo 模式需要（vLLM 原生模式不经过 `AsyncLLM` 的状态查询）
- **发现过程**：Dynamo 模式的切换 API 能触发引擎内部的切换流程（KV preflight → 权重重载），但 `parallel_strategy_state` 查询始终返回旧的 TP/PP 值。经代码追踪确认根因为 `async_llm.py` 中切换后未同步配置

</details>

### 2.3 补丁应用验证

全部 5 个补丁支持正向应用与反向还原：

```bash
# 正向应用
for pf in ${PATCH_DIR}/*.patch; do
    patch -p1 -d ${CONDA_SITE} --force --forward < "$pf"
done

# 反向还原
for pf in ${PATCH_DIR}/*.patch; do
    patch -p1 -d ${CONDA_SITE} --force -R < "$pf"
done
```

### 2.4 `service.sh` 适配（InfiniCore 模式）

为支持 InfiniCore 平台插件，对 `service.sh` 做了以下修改：

1. **`VLLM_PLUGINS` 从 `metax` 改为 `infinicore`**
2. **新增环境变量**：`VLLM_INFINICORE_GDN_SINGLE_STAGE=1`、`MACA_PATH`/`MACA_HOME`/`MACA_ROOT`
3. **新增 `FLASH_ATTN_2_CUDA_SO` 动态解析**：使用 Python `importlib.util.find_spec()` 在运行时定位 `flash_attn_2_cuda` 的 `.so` 文件路径，避免 InfiniCore 插件硬编码的 Python 3.12 默认路径
4. **默认模型路径从 `Qwen3-0.6B` 改为 `Qwen3.8-27B`**

### 2.5 代码更新备注

1. **不要全量复制 `dynamo/common/*`**：新版 `common/utils/runtime.py` 会将 `response_plane` 传递给 `DistributedRuntime()`，而旧版 Rust 绑定（v1.5.0.dev20260906）不接受该参数，会导致前端和后端均启动失败。仅同步 `dynamo/common/constants.py`，并通过补丁 ④ 为 `runtime.py` 添加参数占位。
2. **`async_llm.py` 配置不同步 Bug**：这是 ElasticVllm_demo 代码中的一处遗漏——`EngineCore` 在自己的进程中更新 `parallel_config`，但 `AsyncLLM`（运行在 Dynamo handler 进程中）的 `vllm_config.parallel_config` 从未被更新。补丁 ⑤ 在 `switch_parallel_strategy_async` 完成后添加镜像同步。此 Bug 仅影响 Dynamo 模式下的状态查询，不影响实际推理和切换逻辑。
3. **补丁与上游版本耦合**：补丁 ①② 针对 ElasticVllm_demo 的 `codex/add-v0.22.0` 分支生成，补丁 ③④ 针对 dynamo 的 `ElasticVllm` 分支生成，补丁 ⑤ 针对 ElasticVllm_demo 的 `codex/add-v0.22.0` 分支生成。若上游代码变更，需重新生成补丁。
4. **InfiniCore C++ Bridge 路径问题**：`vllm-infinicore` 插件的 `cpp_bridge.py` 硬编码查找 Python 3.12 下的 `flash_attn_2_cuda.so`。当运行环境的 Python 版本不同时（当前为 3.10），必须通过 `FLASH_ATTN_2_CUDA_SO` 环境变量或 `VLLM_INFINICORE_DISABLE_CPP_BRIDGE=1` 解决。本报告采用动态路径解析方案。
5. **InfiniCore attention backend 与 metax 插件互斥**：`VLLM_PLUGINS` 只能指定一个平台插件。InfiniCore 模式下 `vllm-infinicore` 会自动 patch vLLM 原生 FlashAttention 后端以兼容 MACA 平台，无需 metax 插件参与。

---

## 3. 测试结果

### 3.1 离线测试（test_offline_switch.py）

**命令：**

```bash
cd /workspace
export VLLM_PLUGINS=infinicore
export VLLM_SERVER_DEV_MODE=1
export VLLM_DISTRIBUTED_EXECUTOR_BACKEND=mp
export VLLM_INFINICORE_GDN_SINGLE_STAGE=1
export MACA_PATH=/opt/maca-3.8.0
export MACA_HOME=$MACA_PATH
export MACA_ROOT=$MACA_PATH
export FLASH_ATTN_2_CUDA_SO=/opt/conda/lib/python3.10/site-packages/flash_attn_2_cuda.cpython-310-x86_64-linux-gnu.so

python3 dynamo/recipes/elastic-vllm/test/test_offline_switch.py \
    --model /mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/ \
    --gpu-memory-utilization 0.85
```

**测试流程与结果：**

| 步骤 | 操作 | 结果 | 详情 |
|------|------|------|------|
| Step 1 | 初始化 LLM（4×1） | ✅ | 4 Worker 就绪，权重加载 21.54s |
| Step 2 | Warmup 推理 | ✅ | Prompt: `'warmup'` → `'\n=== étym'` |
| Step 3 | 切换 4×1 → 2×2 | ✅ | Switch to 2×2 completed |
| Step 4 | 2×2 配置下推理 | ✅ | Prompt: `'hello'` → `' stansferm\n\nHello! How can I help you today?'` |
| Step 5 | 切换 2×2 → 1×4 | ✅ | Switch to 1×4 completed |
| Step 6 | 1×4 配置下推理 | ✅ | Prompt: `'what is the capital of France?'` → `'The capital of France is **Paris**.'` |
| Step 7 | 切换 1×4 → 4×1 | ✅ | Switch to 4×1 completed |
| Step 8 | 4×1 配置下推理 | ✅ | Prompt: `'goodbye!'` → 正常输出 |

**关键性能指标：**

| 指标 | 值 |
|------|-----|
| 模型架构 | `Qwen3_5ForConditionalGeneration`（hybrid linear+full attention） |
| 首次权重加载 | 21.54s（18-shard safetensors） |
| GPU KV Cache | 2,463,998 tokens |
| 可用 KV Cache 显存 | 38.01 GiB |
| 显存占用（4×1） | 13.01 GiB/GPU |
| 显存占用（2×2） | 13.0 GiB/GPU |
| 显存占用（1×4） | 12.89 GiB/GPU |
| 权重重载（4×1→2×2） | 18.67s |
| 权重重载（2×2→1×4） | 23.69s |
| 权重重载（1×4→4×1） | 19.73s |

**KV 迁移预检示例（4×1 → 2×2）：**

```
preflight plan=194759db...
  groups=[
    {'group_id': 0, 'kind': 'linear_attention', 'preservation_mode': 'reset_and_zero', 'components': 64},
    {'group_id': 1, 'kind': 'linear_attention', 'preservation_mode': 'reset_and_zero', 'components': 64},
    {'group_id': 2, 'kind': 'linear_attention', 'preservation_mode': 'reset_and_zero', 'components': 64},
    {'group_id': 3, 'kind': 'full_attention', 'preservation_mode': 'preserve_and_reshard', 'components': 32}
  ]
```

> **说明：** Qwen3.8-27B 采用 hybrid attention 架构（64 个 linear_attention 层 + 32 个 full_attention 层）。切换时 linear_attention 层执行 `reset_and_zero`，full_attention 层执行 `preserve_and_reshard`（KV Cache 重分片保留）。

**结果：✅ 离线测试全部通过**

---

### 3.2 在线测试（Dynamo 模式，InfiniCore）

**命令：**

```bash
cd /workspace
# 启动 Dynamo 服务（service.sh 已内置 InfiniCore 配置）
./dynamo/recipes/elastic-vllm/service.sh dynamo --background

# 运行在线切换测试
python3 dynamo/recipes/elastic-vllm/test/test_online_switch_dynamo.py
```

**服务端口：**

| 用途 | 地址 |
|------|------|
| OpenAI API | `http://localhost:9090/v1/chat/completions` |
| 控制 API | `http://localhost:9091/engine/control/...` |

**切换 API 调用格式（Dynamo 控制面）：**

```bash
# 查询并行策略状态
curl -X POST http://localhost:9091/engine/control/parallel_strategy_state \
  -H 'Content-Type: application/json' -d '{}'

# 发起切换请求
curl -X POST http://localhost:9091/engine/control/switch_parallel_strategy \
  -H 'Content-Type: application/json' \
  -d '{"new_world_size": 4, "target_tensor_parallel_size": 2, "target_pipeline_parallel_size": 2}'
```

> **注意：** Dynamo 模式的控制面 API 位于端口 9091 的 `/engine/control/` 路径下，请求体要求 `new_world_size`、`target_tensor_parallel_size`、`target_pipeline_parallel_size` 三个必填字段。

**测试流程与结果：**

| 步骤 | 操作 | 结果 | 详情 |
|------|------|------|------|
| Step 0 | 验证初始状态（4×1） | ✅ | TP=4, PP=1, is_switching=false, blocks=3181 |
| Step 1 | Warmup 推理（4×1） | ✅ | Prompt: `'warmup'` → 正常输出 (10 tokens) |
| Step 2 | 切换 4×1 → 2×2 | ✅ | API 返回 22.6s，状态立即更新 TP=2 PP=2 |
| Step 3 | 2×2 配置下推理 | ✅ | Prompt: `'hello, who are you?'` → 正常输出 (30 tokens) |
| Step 4 | 切换 2×2 → 1×4 | ✅ | API 返回 30.5s，状态立即更新 TP=1 PP=4 |
| Step 5 | 1×4 配置下推理 | ✅ | Prompt: `'what is the capital of France?'` → 正常输出 (27 tokens) |
| Step 6 | 切换 1×4 → 4×1 | ✅ | API 返回 22.9s，状态立即更新 TP=4 PP=1 |
| Step 7 | 4×1 配置下推理 | ✅ | Prompt: `'goodbye!'` → 正常输出 (30 tokens) |

**切换请求响应示例（4×1 → 2×2）：**

```json
{
  "status": "ok",
  "message": "TP/PP switch completed",
  "tensor_parallel_size": 2,
  "pipeline_parallel_size": 2,
  "data_parallel_size": 1,
  "world_size": 4,
  "physical_world_size": null,
  "num_gpu_blocks": 3181,
  "is_switching": false,
  "failed": false
}
```

**关键性能指标（Dynamo 在线模式，InfiniCore）：**

| 指标 | 值 |
|------|-----|
| GPU KV Cache blocks | 3,181 |
| 切换 API 耗时（4×1→2×2） | 22.6s |
| 切换 API 耗时（2×2→1×4） | 30.5s |
| 切换 API 耗时（1×4→4×1） | 22.9s |

**结果：✅ Dynamo 模式在线测试全部通过**

---

## 4. 与 metax 模式对比

| 对比项 | metax 模式 | infinicore 模式 |
|--------|-----------|----------------|
| `VLLM_PLUGINS` | `metax` | `infinicore` |
| 平台插件包 | `vllm-metax` | `vllm-infinicore` |
| C++ Bridge | 不适用 | 需设置 `FLASH_ATTN_2_CUDA_SO` 或 `VLLM_INFINICORE_DISABLE_CPP_BRIDGE=1` |
| Attention 后端 | MetaX MacaFlashAttentionBackend（插件内置） | InfiniCoreFlashAttentionBackend（patch vLLM 原生 FA 后端） |
| KV Cache 更新 | MetaX 自定义 | InfiniCore 自定义 + 原生 fallback |
| `VLLM_INFINICORE_GDN_SINGLE_STAGE` | 仅 InfiniCore 模式需设置 | 必须设为 1 |
| 离线测试 | ✅ 通过 | ✅ 通过 |
| Dynamo 在线测试 | ✅ 通过 | ✅ 通过 |
| 切换路径 | 4×1↔2×2↔1×4 | 4×1↔2×2↔1×4 |

两种平台插件模式下弹性 TP/PP 切换功能均正常工作。

---

## 5. 测试结论

### 5.1 总体结论

| 测试项 | 结果 |
|--------|:----:|
| 代码同步（手动） | ✅ |
| InfiniCore 平台插件适配 | ✅ |
| 离线切换测试（InfiniCore） | ✅ |
| 在线切换测试（Dynamo + InfiniCore） | ✅ |

**全部测试通过。** Qwen3.8-27B 在 InfiniCore 平台插件 + Dynamo 模式下的弹性 TP/PP 并行策略切换功能正常工作。

### 5.2 切换路径验证

```
4×1 ──→ 2×2 ──→ 1×4 ──→ 4×1
  │        │        │        │
  ✅        ✅        ✅        ✅
 推理OK   推理OK   推理OK   推理OK
```

### 5.3 观察与备注

1. **FA2 不可用（预期）**：MACA 平台上 `libcudart.so.13` 不可用，FA2 后端不可用。InfiniCore 插件通过 `_patch_native_flash_attention_module()` 将 FA version 强制设为 2 并禁用 fp8/sinks 检查，配合 FLASH_ATTN_2_CUDA_SO 使 C++ bridge 正常编译，不影响功能。
2. **`FLASH_ATTN_2_CUDA_SO` 路径问题**：InfiniCore 插件 `cpp_bridge.py` 硬编码 Python 3.12 路径。通过动态解析脚本在 `service.sh` 中解决，参考 `start_vllm_infinicore.sh` 方案。也可通过 `VLLM_INFINICORE_DISABLE_CPP_BRIDGE=1` 完全禁用 C++ bridge（会牺牲部分性能）。
3. **NCCL 库警告**：日志中出现 `Failed to load NCCL library from libnccl.so.2` 错误，这是 MACA 平台的预期行为（使用 MCCL 替代 NCCL），不影响功能。
4. **KV Cache 迁移**：每次切换时 KV Cache 迁移均正常执行。Qwen3.8-27B 的 hybrid attention 架构下，linear_attention 层执行 `reset_and_zero`，full_attention 层执行 `preserve_and_reshard`（KV Cache 重分片保留）。
5. **权重重载耗时**：27B 模型每次切换后权重重载 18s ~ 24s，主要由磁盘 I/O（18-shard safetensors）和模型加载决定。
6. **Dynamo 模式兼容性**：当前 `ai-dynamo` Rust 绑定（v1.5.0.dev20260906）与新版 `dynamo/vllm` Python 代码（`response_plane` 参数）不兼容，需通过补丁 ③④ 修补。全量同步 `dynamo/common` 会导致前端和后端均启动失败，建议仅同步 `dynamo/common/constants.py`。
7. **`async_llm.py` 配置不同步 Bug**：切换后 `vllm_config.parallel_config` 不同步，需补丁 ⑤ 修复，否则 Dynamo handler 无法读取到切换后的 TP/PP 值。详见 §2.2 补丁 ⑤ 说明。
8. **补丁批量应用**：所有 5 个补丁均为标准 unified diff 格式，支持正向应用与反向还原，可通过 `for pf in ${PATCH_DIR}/*.patch; do patch -p1 -d ${CONDA_SITE} --force --forward < "$pf"; done` 批量应用。补丁清单详见 §2.2。