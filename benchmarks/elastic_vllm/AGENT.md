# AGENT.md — Elastic vLLM TP/PP 自动切换项目指南

> 本文档为 AI Agent 在本目录下工作时的导航与约束指南，涵盖项目架构、核心概念、开发规范和常见任务。

---

## 1. 项目概述

本项目实现 **弹性 TP/PP 并行策略自动切换**：一个嵌入在 Dynamo frontend 进程中的控制器，根据实时请求并发量自动切换 vLLM 推理引擎的并行拓扑（如 2x2 ↔ 4x1），无需人工干预。

**核心价值**：
- 高并发 prefill 重负载时自动切到 4x1（吞吐 +27~38%）
- 低并发 decode 重负载时自动切回 2x2（TPOT 优 4~9ms/token）
- 切换期间在途请求零丢失（wait + queue 模式）

**项目包含两大子系统**：
1. **弹性控制器**（`elastic_controller.py`）— 生产级控制器，嵌入 frontend 进程
2. **演示系统**（`demo/`）— TUI 可视化 + 负载生成 + 场景编排，用于展示控制器行为

---

## 2. 目录结构与文件职责

```
elastic_vllm/
├── README.md              # 演示方案完整设计文档（中文，~36KB）
├── report.md              # TP=4 vs TP=2+PP=2 压测报告（中文，~17KB）
├── AGENT.md               # 本文件 — Agent 工作指南
└── demo/
    ├── demo_load.py       # 通用闭环负载生成器（Python）
    ├── demo_tui.py        # TUI 监控面板（Textual + Rich，纯观察者）
    ├── demo_run.sh        # 演示编排器（Bash，读取场景文件驱动全流程）
    ├── demo_scenario.yaml # 默认演示场景定义
    ├── requirement.txt    # Python 依赖（textual, rich）
    └── demo_output/       # 演示输出（运行时生成）
        ├── load_stats.jsonl   # 每 5s 窗口统计 JSON 行
        ├── events.log         # 事件日志
        └── load_errors_0.log  # 负载错误日志
```

**关键外部文件**：

| 文件 | 路径 | 职责 |
|------|------|------|
| 弹性控制器 | `components/src/dynamo/frontend/elastic_controller.py` | 核心控制器逻辑 |
| 服务脚本 | `components/src/dynamo/remp/tests/elastic_vllm/service.sh` | vLLM 服务启停管理 |

---

## 3. 核心概念与术语

### 3.1 并行策略命名

| 写法 | 含义 | 说明 |
|------|------|------|
| **4x1** | TP=4, PP=1 | 4 路张量并行，无流水线；适合高并发 prefill 重 |
| **2x2** | TP=2, PP=2 | 2 路张量并行 + 2 级流水线；适合低并发 decode 重 |
| `world_size` | TP × PP | 总 GPU 数，切换时必须不变（本项目 = 4） |

⚠️ **术语红线**：绝不使用裸 "PP=2" / "PP2" 指代部署方式，必须写 "TP2PP2" 或 "2x2"。

### 3.2 控制器决策逻辑

```
并发 > FACTOR_UP × EXPECTED_WORKERS   (持续 STABLE_POLLS 次) → SWITCH UP   到 STRATEGY_UP
并发 < FACTOR_DOWN × EXPECTED_WORKERS (持续 STABLE_POLLS 次) → SWITCH DOWN 到 STRATEGY_DOWN
```

**四重防抖机制**（缺一不可，不是冗余）：
1. `campaign_active` — 切换进行中不触发新切换
2. `cooldown_s` — 切换完成后冷却期
3. `stable_polls` — 连续 N 次轮询超阈值才触发
4. `expected_workers` — 固定分母，不跟踪实时实例数

### 3.3 切换安全保证

- 切换使用 `request_handling="wait"` + `admission_handling="queue"`
- 在途请求完整执行完毕后才切换
- 新请求在 `add_request` 排队，切换完成后释放到新拓扑
- **副作用**：切换期间 `active_requests` 持续升高（排队请求仍被计数），这是设计行为，不是 bug

### 3.4 环境变量命名

所有控制器参数通过 `DYN_ELASTIC_SWITCH_*` 环境变量配置，优先级：

```
命令行环境变量 > 场景文件 controller_overrides > demo_run.sh 默认值
```

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `DYN_ELASTIC_SWITCH_ENABLE` | 1 | 启用/禁用控制器 |
| `DYN_ELASTIC_SWITCH_FACTOR_UP` | 10 | UP 阈值倍数 |
| `DYN_ELASTIC_SWITCH_FACTOR_DOWN` | 2 | DOWN 阈值倍数 |
| `DYN_ELASTIC_SWITCH_STRATEGY_UP` | 4x1 | 向上切换目标 |
| `DYN_ELASTIC_SWITCH_STRATEGY_DOWN` | 2x2 | 向下切换目标 |
| `DYN_ELASTIC_SWITCH_COOLDOWN_S` | 30 | 冷却期（秒） |
| `DYN_ELASTIC_SWITCH_STABLE_POLLS` | 3 | 连续轮询次数 |
| `DYN_ELASTIC_SWITCH_POLL_INTERVAL_S` | 2 | 轮询间隔（秒） |
| `DYN_ELASTIC_SWITCH_EXPECTED_WORKERS` | 1 | 策略分母 |
| `DYN_ELASTIC_SWITCH_WORKER_URLS` | http://localhost:9091 | 控制面地址 |
| `DYN_ELASTIC_SWITCH_METRICS_URL` | http://localhost:9090/metrics | 指标来源 |

---

## 4. 架构分层

### 4.1 通用工具层（不绑定具体演示流程）

| 文件 | 角色 | 关键约束 |
|------|------|----------|
| `demo_load.py` | 闭环负载生成器 | 仅接收 input_len/output_len/conc/duration 参数，不含阶段逻辑 |
| `demo_tui.py` | TUI 监控面板 | **纯观察者**，绝不发起切换调用；仅轮询指标/状态/日志 |
| `service.sh` | 服务管理 | 不做改动，控制器参数通过环境变量传入 |

### 4.2 场景编排层（定义具体演示流程，可替换）

| 文件 | 角色 | 关键约束 |
|------|------|----------|
| `demo_scenario.yaml` | 演示流程定义 | 阶段序列 + 负载参数 + 预期事件 |
| `demo_run.sh` | 编排器 | 读取场景文件驱动通用工具执行 |

### 4.3 数据流

```
TUI (demo_tui.py)
  ├── 每 2s 轮询 :9090/metrics → 并发数
  ├── 每 2s 读取 stats_file → 吞吐/TTFT/TPOT
  ├── 每 2s POST :9091/.../state → TP/PP/is_switching
  └── 每 2s 读 frontend.log + events.log → 事件

demo_load.py → stdout(JSONL) → demo_run.sh 重定向到 stats_file → TUI 读取
demo_run.sh → events.log → TUI 读取
elastic_controller → frontend.log → TUI 读取
```

---

## 5. 开发规范

### 5.1 通用工具层原则

**`demo_load.py` 和 `demo_tui.py` 是通用工具**，修改时必须遵守：

- ❌ 不允许在通用工具中硬编码演示阶段逻辑（如 "Phase 1"、"Phase 2"）
- ❌ 不允许在 TUI 中发起任何控制面调用（switch_parallel_strategy）
- ✅ 所有演示特定行为通过参数或场景文件传入
- ✅ TUI 只读不改：`GET /metrics`、`POST .../state`（只读）、读文件

### 5.2 控制器代码硬依赖规则

`elastic_controller.py` 遵循 **标准库唯一** 原则：

- ❌ 不允许导入 `vllm`、`dynamo.vllm.handlers` 或任何第三方 HTTP 客户端
- ✅ 仅使用 `urllib.request`（标准库）进行 HTTP 调用
- ✅ 保持模块可无 GPU、无引擎环境下单元测试

### 5.3 场景文件设计

场景 YAML 的 `expect` 字段用于事后验证，不在运行时阻断流程：

```yaml
phases:
  - name: "描述性名称"
    load:                    # 可选，有 load 则启动负载生成器
      input_len: 8192
      output_len: 32
      conc: 32
      duration: 100
      tag: "prefill_heavy"
    wait: 90                 # 可选，有 wait 则静默等待
    expect:                  # 可选，验证预期事件（基于后台拓扑轮询数据）
      - event: "switch_up"   # switch_up | switch_down | switch_complete | no_switch
        within_s: 20         # switch_up/down 检测 is_switching=true（决策时刻）
        # switch_complete 检测拓扑匹配 AND is_switching=false（完成时刻）
        # no_switch 检测整个阶段内拓扑稳定
```

⚠️ **expect 验证不再使用日志 grep**，而是通过后台拓扑监控进程每 2s 轮询 `parallel_strategy_state` API，记录 JSONL 数据到 `topo_monitor_{phase_idx}.jsonl`，阶段结束后从监控数据评估预期。

### 5.4 代码风格

- Bash 脚本使用 `set -euo pipefail`
- Python 使用 `argparse` 做 CLI，`type: int` 显式标注类型
- 日志前缀 `[TP/PP]` 用于控制器，`[LOAD]` 用于负载事件
- JSON 输出一行一条（JSONL），`flush=True` 确保实时

---

## 6. 常见任务指南

### 6.1 添加新的负载阶段

1. 编辑 `demo_scenario.yaml`，在 `phases` 列表末尾追加新阶段
2. 设置 `load` 或 `wait` 参数
3. 按需添加 `expect` 验证项
4. 运行 `demo/demo_run.sh --scenario demo/demo_scenario.yaml` 验证

### 6.2 调整控制器阈值

优先级从高到低：

```bash
# 方式1：命令行环境变量（最高优先级）
DYN_ELASTIC_SWITCH_FACTOR_UP=5 DYN_ELASTIC_SWITCH_COOLDOWN_S=15 demo/demo_run.sh

# 方式2：场景文件 controller_overrides（中等优先级）
# 在 demo_scenario.yaml 的 service.controller_overrides 中设置

# 方式3：修改 demo_run.sh 默认值（最低优先级，不推荐）
```

### 6.3 添加新的 TUI 指标面板

1. 在 `demo_tui.py` 中创建新的 `ChartWidget` 或 `Static` 子类
2. 在 `State` 中添加对应的 `deque` 历史字段
3. 在 `update_history()` 中追加数据
4. 在 `ElasticMonitorApp.compose()` 中添加新 widget
5. 确保新面板是纯观察者，不发起任何写操作

### 6.4 修改负载生成器

`demo_load.py` 使用 `http.client.HTTPConnection` 直接发送 streaming completions 请求：

- 闭环模式：CONC 个线程，每线程循环 发送→等完整响应→发送下一个
- 每 `--window` 秒（默认 5s）输出一行窗口统计 JSON
- 结束时输出 `"final": true` 的汇总行
- 合成 prompt 使用重复填充文本：`chars_needed = input_len × 4`

### 6.5 运行完整演示

```bash
cd dynamo/benchmarks/elastic_vllm

# 一键启动
demo/demo_run.sh --scenario demo/demo_scenario.yaml

# 查看报告
cat demo_output/report.json | jq .
```

前置条件：4 GPU 空闲，模型可用，`curl`/`jq`/`pyyaml` 已安装。

---

## 7. 压测数据关键结论

以下结论来自 `report.md`，修改控制器逻辑时必须参考：

| 负载特征 | 最优拓扑 | 性能差距 |
|----------|----------|----------|
| prefill 重（8192 in / 256 out, C≥32） | **4x1** | 吞吐 +27~33% |
| 均衡型（2048/1024, C≥32） | **4x1** | 吞吐 +9~13%, TTFT +80% |
| 解码重（128 in / 4096+ out, C≤96） | **2x2** | 吞吐 +4~9%, TPOT -4~8ms |
| 超高并发（C≥128） | **4x1** | 吞吐 +24%+ |

**工程建议**：默认 TP=4；仅纯长文本生成（短输入长输出）且并发 ≤96、对 TTFT 不敏感时，TP2PP2 有 4~9% 优势。

---

## 8. 已知问题与注意事项

### 8.1 切换期间的 active_requests 虚高

切换期间排队请求仍被 `dynamo_frontend_active_requests` 计数，导致并发指标虚高。**这是设计行为**，控制器靠 `campaign_active` + `cooldown_s` 抑制防抖，不要"修复"该指标。

### 8.2 冷却期对演示节奏的影响

负载停止后需等待 `COOLDOWN_S`（默认 30s）结束，控制器才会检测低并发并 SWITCH DOWN。场景设计中 Gap 阶段的 `wait` 时间需 ≥ 冷却期 + 决策时间 + 切换时间。

### 8.3 初始拓扑必须是 2x2

控制器需要有"向上"切换的空间。若初始已是 4x1，高并发不会触发 SWITCH UP（已在最高拓扑）。

### 8.4 demo_output 中的错误数据

`demo_output/load_stats.jsonl` 中如果所有请求均为 `err`，说明演示环境不可用（模型/服务未运行）。这不是代码 bug，是运行环境问题。

### 8.5 TP2PP2 领先上限约 9%

长输出场景下 TP2PP2 的领先收敛到纯 TPOT 之比 ≈ 9%，输出再加长也无法突破 20% 阈值。这是该负载族的数学极限。

### 8.6 service.sh 不做改动

所有控制器参数通过环境变量传递给 frontend 子进程。service.sh 本身不需要任何修改。

### 8.7 parallel_strategy_state 的 is_switching 语义

API `engine/control/parallel_strategy_state` 返回的 `is_switching` 字段：
- **`true`** 时表示切换**决策已做出**，切换正在进行中，但 `tp`/`pp` 字段仍显示**旧拓扑**
- **`false`** 时且拓扑已变，表示切换**已完成**

因此：
- `switch_up`/`switch_down` 预期应检测 `is_switching=true`（决策时刻），而非拓扑变化（完成时刻）
- `switch_complete` 预期应检测拓扑匹配 AND `is_switching=false`
- 拓扑字段在切换完成前不会更新，这是设计行为

### 8.8 /health 是唯一的引擎就绪信号

`service.sh remp --background` 在控制路由注册后即返回，但模型仍在加载。`parallel_strategy_state` 在模型加载完成前就返回有效 `.status`，**不可作为就绪信号**。唯一可靠的"引擎就绪"指示是 `/health` 返回 HTTP 200。

`service.sh health` 同时检查 Frontend (9090) 和 Control plane (9091)，两者都必须报告 healthy 才表示服务完全就绪。

### 8.9 python3 heredoc 与 stdin 管道冲突

在 bash 中，`printf ... | python3 - <<'PYEOF'` 会导致 heredoc 抢占 stdin，使 printf 的管道数据丢失。应改用 `python3 -c '...'` 内联方式避免此问题。

---

## 9. 测试策略

### 9.1 控制器单元测试

`elastic_controller.py` 可在无 GPU、无引擎环境下测试：
- `ControllerConfig.from_env()` 可注入 `env` 字典
- `sum_metric()` 可用 Prometheus text fixture 测试
- `_poll_once()` 返回 `Decision` 对象可直接断言
- `_run_campaign()` 可 mock `_post_json` / `_get_json`

### 9.2 演示系统集成测试

通过 `demo_run.sh --skip-start`（服务已运行时）快速验证编排逻辑：
- 场景文件解析是否正确
- 负载生成器参数传递是否正确
- expect 验证逻辑正确匹配拓扑监控数据
- `topo_monitor_*.jsonl` 数据完整，能准确记录 `elapsed`、`topo`、`is_switching` 字段
- jq `false // null` 陷阱：jq 中 `false` 被视为空值，`false // null` 返回 `null` 而非 `false`，需用 `if/then/else` 替代 `//` 运算符

### 9.3 TUI 测试

TUI 基于 Textual 框架，可通过 `textual pilot` 模式进行无头测试：
- 验证数据轮询不抛异常
- 验证切换事件正确出现在 event_log 中
- 验证 widget 渲染不依赖外部状态

---

## 10. 修改检查清单

在修改本目录下任何文件后，确认以下事项：

- [ ] 通用工具（`demo_load.py`、`demo_tui.py`）是否引入了演示特定逻辑？不应引入。
- [ ] TUI 是否保持了纯观察者角色？不应发起写操作。
- [ ] 控制器代码是否只使用了标准库？不应引入第三方依赖。
- [ ] 环境变量优先级是否保持：命令行 > 场景文件 > 默认值？
- [ ] 场景文件格式是否向后兼容？（新字段应有默认值）
- [ ] `demo_run.sh` 的 `set -euo pipefail` 是否仍生效？
- [ ] 切换期间的 active_requests 虚高行为是否被正确处理？（不应"修复"）
- [ ] demo_output 目录内容是否被 .gitignore？（运行时生成，不应入库）
