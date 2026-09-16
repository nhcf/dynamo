# Elastic vLLM TP/PP 自动切换演示方案

> 目标：通过 TUI 可视化展示弹性控制器根据流量自动切换 2x2 与 4x1 拓扑的过程，以及对推理性能指标的影响

## 1. 背景与依据

根据 [report.md](file:///d:/agents/dynamo/benchmarks/elastic_vllm/report.md) 的压测数据：

| 负载特征 | 最优拓扑 | 差距 |
|---|---|---|
| prefill 重（8192 in / 256 out, C≥32） | **4x1** | +27~33% 吞吐 |
| 均衡型（2048/1024, C≥32） | **4x1** | +9~13% 吞吐, TTFT +80% |
| 解码重（128 in / 4096+ out, C≤96） | **2x2** | +4~9% 吞吐, TPOT -4~8ms |

根据 [elastic_controller.py](file:///d:/agents/dynamo/components/src/dynamo/frontend/elastic_controller.py) 的控制器逻辑：

```
并发 > FACTOR_UP × EXPECTED_WORKERS  (持续 STABLE_POLLS 次) → SWITCH UP   到 STRATEGY_UP
并发 < FACTOR_DOWN × EXPECTED_WORKERS (持续 STABLE_POLLS 次) → SWITCH DOWN 到 STRATEGY_DOWN
```

**演示核心信息**：控制器根据并发流量自动切换拓扑——高并发时切到 4x1（吞吐 +27%），低并发时切回 2x2（decode 负载 TPOT -7%），无需任何人工干预。

## 2. 架构设计

### 文件分层

```
通用工具层（可复用，不绑定具体演示流程）
├── demo/demo_load.py     闭环负载生成器：接收 input_len/output_len/conc/duration，每 5s 输出窗口 JSON
├── demo/demo_tui.py      TUI 监控面板：轮询指标/状态/日志，绘制曲线，纯观察者
└── service.sh            服务管理：remp 子命令（不改动，控制器参数通过环境变量传入）

场景编排层（定义具体演示流程，可替换）
├── demo/demo_scenario.yaml  演示流程定义：阶段、负载特征参数、预期事件、时间安排
└── demo/demo_run.sh         编排器：设默认值 → 读场景 → 启服务 → 启 TUI → 按场景驱动负载 → 报告
```

**核心原则**：`demo_load.py` 和 `demo_tui.py` 是通用工具，不包含任何演示特定的阶段逻辑；`demo_scenario.yaml` 定义"做什么、何时做、期望什么"；`demo_run.sh` 读取场景文件并驱动通用工具执行。

### 数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│                        demo_tui.py (终端 TUI)                        │
│  ┌──────────┬──────────┬──────────┬──────────┬─────────────────┐    │
│  │拓扑状态   │并发曲线  │吞吐曲线  │延迟曲线  │ 事件日志         │    │
│  │TP/PP     │active   │tok/s     │TPOT      │ controller/load │    │
│  └──────────┴──────────┴──────────┴──────────┴─────────────────┘    │
└──┬────────────────┬────────────────────────┬────────────────────────┘
   │ 每 2s 轮询      │ 每 5s 读取 stdout       │ tail -f frontend.log
   ▼                  ▼                         ▼
:9090/metrics       demo_load.py stdout       [CONTROLLER] SWITCH UP...
dynamo.frontend     (JSON, 每 5s 一行)         grep SWITCH/campaign
                        ▲
                        │ demo_run.sh 按场景驱动
                        │
              ┌─────────────────────┐
              │  demo_scenario.yaml  │   ← 可替换的演示流程定义
              │                     │
              │  phases:            │
              │    - name: phase1   │
              │      input_len: 8192│
              │      output_len: 32 │
              │      conc: 32       │
              │      duration: 100  │
              │      expect:        │
              │        switch_up    │
              │    - name: gap      │
              │      wait: 60       │
              │      expect:        │
              │        switch_down  │
              │    - name: phase2   │
              │      input_len: 128 │
              │      output_len:4096│
              │      conc: 8        │
              │      duration: 60   │
              └─────────────────────┘
```

**关键：TUI 是纯观察者**——只读取指标和状态，不发起任何切换调用。所有切换由 [elastic_controller.py](file:///d:/agents/dynamo/components/src/dynamo/frontend/elastic_controller.py) 中嵌入在 frontend 进程内的控制器自动完成。

## 3. service.sh 与弹性控制器

service.sh 本身不做改动。弹性控制器通过 `DYN_ELASTIC_SWITCH_*` 环境变量配置，由 frontend 进程中的 `ControllerConfig.from_env()` 读取。

**demo_run.sh 在调用 service.sh 之前设置默认环境变量**，实现"remp 子命令默认启用控制器"的效果。用户有两种方式覆盖：

| 优先级 | 方式 | 示例 |
|---|---|---|
| 1 (最高) | 启动前设置环境变量 | `DYN_ELASTIC_SWITCH_COOLDOWN_S=10 demo/demo_run.sh` |
| 2 | demo_scenario.yaml 中 `controller_overrides` | `DYN_ELASTIC_SWITCH_FACTOR_UP: 5` |
| 3 (最低) | demo_run.sh 内置默认值 | 见下表 |

demo_run.sh 内置默认值（`:` 语法，仅当变量未设置时生效）：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DYN_ELASTIC_SWITCH_ENABLE` | 1 | 启用控制器 |
| `DYN_ELASTIC_SWITCH_WORKER_URLS` | `http://localhost:9091` | 后端控制面地址 |
| `DYN_ELASTIC_SWITCH_EXPECTED_WORKERS` | 1 | 策略除数（不跟踪实例数） |
| `DYN_ELASTIC_SWITCH_METRICS_URL` | `http://localhost:9090/metrics` | 并发指标来源 |
| `DYN_ELASTIC_SWITCH_POLL_INTERVAL_S` | 2 | 轮询间隔（秒） |
| `DYN_ELASTIC_SWITCH_STABLE_POLLS` | 3 | 触发所需连续轮询次数（~6s） |
| `DYN_ELASTIC_SWITCH_FACTOR_UP` | 10 | 并发 > 此值 × EXPECTED_WORKERS → SWITCH UP |
| `DYN_ELASTIC_SWITCH_FACTOR_DOWN` | 2 | 并发 < 此值 × EXPECTED_WORKERS → SWITCH DOWN |
| `DYN_ELASTIC_SWITCH_STRATEGY_UP` | 4x1 | 向上切换目标 |
| `DYN_ELASTIC_SWITCH_STRATEGY_DOWN` | 2x2 | 向下切换目标 |
| `DYN_ELASTIC_SWITCH_COOLDOWN_S` | 30 | 切换后冷却期（秒） |

**覆盖示例**：

```bash
# 方式1: 环境变量覆盖（最高优先级，覆盖一切）
DYN_ELASTIC_SWITCH_COOLDOWN_S=10 demo/demo_run.sh --scenario demo/demo_scenario.yaml
DYN_ELASTIC_SWITCH_FACTOR_UP=5 DYN_ELASTIC_SWITCH_FACTOR_DOWN=1 demo/demo_run.sh

# 方式2: 场景文件中覆盖（中等优先级）
# 在 demo_scenario.yaml 的 controller_overrides 中设置

# 禁用控制器
DYN_ELASTIC_SWITCH_ENABLE=0 demo/demo_run.sh
```

## 4. 通用工具

### demo/demo_load.py — 闭环负载生成器

```bash
#!/bin/bash
# 通用闭环负载生成器（纯 bash + curl + python），每 5s 输出一行 JSON 到 stdout。
#
# 与 load_gen.py 的区别：load_gen.py 只在结束时输出汇总；本脚本每 5s 输出窗口统计，
# 供 TUI 实时绘制曲线。
#
# 用法:
#   demo/demo_load.py --model <model_id> [options]
#
# 参数:
#   --model        模型 ID（必填）
#   --fe-url       frontend 地址（默认 http://localhost:9090）
#   --conc         并发线程数（默认 20）
#   --duration     总运行秒数（默认 200）
#   --input-len    输入 token 长度（生成对应长度的 prompt，默认 128）
#   --output-len   每请求最大输出 token 数（默认 32）
#   --window       统计窗口秒数（默认 5）
#   --tag          场景标签，写入输出 JSON（可选，如 "prefill_heavy"）
#
# 输出格式（每 --window 秒一行）:
#   {"t": 40.0, "tag": "prefill", "conc": 32, "thr_out": 144.0,
#    "ttft_mean": 5398, "ttft_p99": 38288, "tpot_mean": 198,
#    "ok": 192, "err": 0}
#
# 设计要点:
#   - 根据 input_len 生成合成 prompt（重复填充文本至目标 token 数 ≈ input_len × 4 chars）
#   - 网络调用在子进程中执行 → 真实并发 = CONC（控制器看到的 active_requests ≈ CONC）
#   - 闭环模式: 发请求 → 等完整响应 → 发下一个 → 在途数恒定
#   - 每 5s 统计窗口内已完成请求的吞吐/延迟分布
#   - 结束时输出最终汇总行（"final": true）
```

### demo/demo_tui.py — TUI 监控面板

```bash
#!/usr/bin/env python3
# 通用 TUI 监控面板（Python + curses + sparkline），纯观察者——不发起任何切换调用。
#
# 用法:
#   python3 demo/demo_tui.py [options]
#
# 参数:
#   --fe-url        frontend 地址（默认 http://localhost:9090）
#   --ctrl-url      控制面地址（默认 http://localhost:9091）
#   --log-file      frontend.log 路径（默认 logs/frontend.log）
#   --interval      轮询间隔秒数（默认 2）
#   --history       曲线历史长度（默认 120 个数据点）
#   --up-threshold  UP 阈值线（并发曲线上的虚线，默认 10）
#   --down-threshold DOWN 阈值线（默认 2）
#
# 数据源:
#   1. GET  :9090/metrics  → dynamo_frontend_active_requests（并发数）
#   2. POST :9091/engine/control/parallel_strategy_state → TP/PP/is_switching/failed
#   3. stdin JSON 行 → 负载生成器的 5s 窗口统计（吞吐/TTFT/TPOT）
#   4. tail -f frontend.log → grep "SWITCH UP|SWITCH DOWN|campaign #"（控制器事件）
#
# 面板:
#   - 拓扑状态: TP/PP, is_switching, failed, campaign 计数
#   - 指标快照: 并发/吞吐/TTFT/TPOT/成功率
#   - 并发曲线: active_requests 时间序列 + UP/DOWN 阈值线
#   - 吞吐曲线: tok/s 时间序列
#   - TPOT 曲线: ms 时间序列
#   - 事件日志: 控制器决策 + 负载启停 + 异常
#
# 切换标记:
#   - 控制器决策时（SWITCH UP/DOWN 出现在 frontend.log）→ 画垂直虚线
#   - 切换期间（is_switching=true）→ 曲线段黄色高亮
#   - campaign 完成 → 标注编号和耗时
```

### TUI 面板 UI 设计

终端要求：最小 120×36 字符，建议 140×40+。面板每 2s 刷新一次（`--interval`），使用 `tput` 定位光标全屏重绘。

#### 整体布局

```
┌─ Elastic TP/PP Switch Monitor ──────────────────────────────────────────────────────────────┐
│                                                                                             │
│ ┌─ Topology ─────────────┐  ┌─ Metrics Snapshot ──────────────────────────────────────────┐ │
│ │ TP=2  PP=2   [2x2]    │  │  Active    Throughput    TTFT(p99)    TPOT(mean)    OK%     │ │
│ │ Switching: NO          │  │   32       144.2 t/s     38.3 s        198 ms      100%    │ │
│ │ Failed: NO             │  │  ▲ +8       ▲ +12.1      ▼ -1.2s       ▼ -22ms            │ │
│ │ Campaigns: 2           │  │                                                          │ │
│ └─────────────────────────┘  └──────────────────────────────────────────────────────────┘ │
│                                                                                             │
│ ┌─ Concurrency (active_requests) ──────────────────────────────────────────────────────────┐ │
│ │ 40 ┤                                                                                   │ │
│ │ 35 ┤      ╭──╮            ▽ UP=10                                                      │ │
│ │ 30 ┤    ╭─╯  ╰─╮                      ╭──╮                                            │ │
│ │ 25 ┤   ╭╯      ╰╮                    ╭╯  ╰─╮                                          │ │
│ │ 20 ┤  ╭╯        ╰╮                  ╭╯      ╰╮                                        │ │
│ │ 15 ┤ ╭╯          ╰─╮               ╭╯        ╰╮                                       │ │
│ │ 10 ┤╭╯ ┄┄┄┄┄┄┄┄┄┄┄╰╮┄┄┄┄┄┄┄┄┄┄┄┄╭╯ ┄┄┄┄┄┄┄┄╰─┄┄┄  △ DOWN=2                        │ │
│ │  5 ┤╯               ╰───┄┄┄┄┄┄┄┄┄┄╯               ╰───┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄              │ │
│ │  0 ┤────────────────────╎──────────────╎────────────────────╎───────────────► t        │ │
│ │                      SWITCH UP    SWITCH DOWN                                            │ │
│ └──────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                             │
│ ┌─ Throughput (tok/s) ─────────────────────────────────────────────────────────────────────┐ │
│ │ 160┤                                                                                   │ │
│ │ 140┤              ╭──────╮                                                             │ │
│ │ 120┤          ╭───╯      ╰───╮                                                         │ │
│ │ 100┤  ╭──╮ ╭─╯              ╰───╮ ╭──╮                                                │ │
│ │  80┤ ╭╯  ╰─╯                    ╰─╯  ╰──╮                                             │ │
│ │  60┤─╯                                  ╰──────────────────────                         │ │
│ │   0┤────────────────────╎──────────────╎────────────────────╎───────────────► t         │ │
│ └──────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                             │
│ ┌─ TPOT mean (ms) ─────────────────────────────────────────────────────────────────────────┐ │
│ │ 300┤  ╭──╮                                                                              │ │
│ │ 250┤ ╭╯  ╰╮                          ╭───╮                                             │ │
│ │ 200┤╭╯    ╰─╮    ╭──────╮          ╭─╯   ╰─╮                                           │ │
│ │ 150┤╯       ╰───╯      ╰─╮      ╭─╯       ╰───╮                                        │ │
│ │ 100┤                     ╰──╮ ╭─╯            ╰───────────────────                        │ │
│ │  50┤                       ╰─╯                                                     │     │
│ │   0┤────────────────────╎──────────────╎────────────────────╎───────────────► t         │ │
│ └──────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                             │
│ ┌─ Event Log ──────────────────────────────────────────────────────────────────────────────┐ │
│ │ 00:12 [CONTROLLER] SWITCH UP -> 4x1: concurrency 32 > 10 for 3 polls                  │ │
│ │ 00:38 [CONTROLLER] Campaign #1 complete: 4x1, took 26.1s                               │ │
│ │ 01:42 [CONTROLLER] SWITCH DOWN -> 2x2: concurrency 0 < 2 for 3 polls                  │ │
│ │ 02:08 [CONTROLLER] Campaign #2 complete: 2x2, took 25.8s                               │ │
│ │ 02:15 [LOAD] Phase 2 started: decode_heavy, conc=8, input=128, output=4096             │ │
│ └──────────────────────────────────────────────────────────────────────────────────────────┘ │
│ Tick: 125  |  Phase: Phase 2: decode heavy  |  Elapsed: 02:35  |  q=quit                   │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

#### 各区域详细设计

**1. Topology（拓扑状态栏）**

```
┌─ Topology ─────────────┐
│ TP=2  PP=2   [2x2]    │    ← 当前 TP/PP 值 + 策略名
│ Switching: NO          │    ← is_switching 状态：YES=黄色闪烁，NO=绿色
│ Failed: NO             │    ← failed 标志：YES=红色，NO=绿色
│ Campaigns: 2           │    ← 已完成 campaign 总计数
└─────────────────────────┘
```

- 正常运行：策略名绿色 `[2x2]`
- 切换中：策略名黄色闪烁 `[2x2 → 4x1]`，显示进度（如 `26%`，根据 campaign 阶段推断）
- 切换失败：`[FAILED]` 红色

**2. Metrics Snapshot（指标快照栏）**

```
┌─ Metrics Snapshot ──────────────────────────────────────────────────┐
│  Active    Throughput    TTFT(p99)    TPOT(mean)    OK%            │
│   32       144.2 t/s     38.3 s        198 ms      100%           │
│  ▲ +8       ▲ +12.1      ▼ -1.2s       ▼ -22ms                    │
└────────────────────────────────────────────────────────────────────┘
```

- 第二行：当前窗口数值
- 第三行：与上一个窗口的差值，`▲` 绿色表示改善，`▼` 红色表示恶化
- `Active` 来自 `:9090/metrics` 的 `dynamo_frontend_active_requests`
- `Throughput`/`TTFT`/`TPOT`/`OK%` 来自 demo_load.py 的 stdin JSON

**3. Concurrency 曲线（并发时间序列）**

- Y 轴：0 ~ 当前最大值（自动缩放，最小上限 40）
- X 轴：最近 `--history` 个数据点（默认 120 个 × 2s = 240s 时间窗）
- `▽` 标记 UP 阈值线（`--up-threshold`，默认 10），虚线 `┄┄┄` 绘制
- `△` 标记 DOWN 阈值线（`--down-threshold`，默认 2），虚线绘制
- 切换事件标记：垂直 `╎` 虚线穿过曲线区域，底部标注 `SWITCH UP` / `SWITCH DOWN`
- 切换期间数据点：黄色 `█` 替代正常绿色 `╭╮` 曲线

**4. Throughput 曲线（吞吐时间序列）**

- Y 轴：0 ~ 自动缩放
- 数据来源：demo_load.py 的 `thr_out` 字段
- 切换事件垂直标记同上

**5. TPOT 曲线（解码延迟时间序列）**

- Y 轴：0 ~ 自动缩放
- 数据来源：demo_load.py 的 `tpot_mean` 字段
- 切换事件垂直标记同上

**6. Event Log（事件日志）**

```
┌─ Event Log ──────────────────────────────────────────────────────────────────────────────┐
│ 00:12 [CONTROLLER] SWITCH UP -> 4x1: concurrency 32 > 10 for 3 polls                  │
│ 00:38 [CONTROLLER] Campaign #1 complete: 4x1, took 26.1s                               │ │
│ 01:42 [CONTROLLER] SWITCH DOWN -> 2x2: concurrency 0 < 2 for 3 polls                  │ │
│ 02:08 [CONTROLLER] Campaign #2 complete: 2x2, took 25.8s                               │ │
│ 02:15 [LOAD] Phase 2 started: decode_heavy, conc=8, input=128, output=4096             │ │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

- 最多保留 5 行，新事件从顶部插入，旧事件挤出
- 事件来源：
  - `[CONTROLLER]`：从 `frontend.log` grep `SWITCH UP|SWITCH DOWN|[TP/PP] campaign.*finished`
  - `[LOAD]`：demo_run.sh 在启停负载时写入 `demo_output/events.log`
  - `[ERROR]`：请求失败率 > 5% 时告警
- 时间戳为演示开始后的相对时间 `MM:SS`
- `[CONTROLLER]` 行黄色高亮，`[LOAD]` 行青色，`[ERROR]` 行红色

**7. 状态栏（底部）**

```
Tick: 125  |  Phase: Phase 2: decode heavy  |  Elapsed: 02:35  |  q=quit
```

- `Tick`：当前轮询计数
- `Phase`：当前执行的场景阶段名（通过 events.fifo 接收）
- `Elapsed`：演示开始后的经过时间
- `q=quit`：按 `q` 退出 TUI

#### 曲线绘制算法

使用 sparkline 思路，将 `--history` 个数据点映射到终端宽度（扣除 Y 轴标签和边框后的可用列数）：

1. 将数据点数组下采样到可用列数（简单取每 `ceil(N/cols)` 个点的平均值）
2. Y 轴线性映射到 6 行高度（0~5），每行对应一个字符高度
3. 相邻点用 Unicode box-drawing 字符 `╭╮╰╯│` 连接，形成连续曲线
4. 阈值线：计算阈值对应的行号，在该行绘制 `┄┄┄` 虚线 + `▽`/`△` 标记
5. 切换事件：在对应列位置绘制 `╎` 垂直虚线贯穿所有行

#### 颜色方案（tput setaf）

| 元素 | 颜色 | tput 编号 |
|---|---|---|
| 正常曲线 | 绿色 | 2 |
| 切换期间曲线 | 黄色 | 3 |
| 切换事件标记 `╎` | 黄色 | 3 |
| UP 阈值线 `▽` | 红色 | 1 |
| DOWN 阈值线 `△` | 蓝色 | 4 |
| 指标改善 `▲` | 绿色 | 2 |
| 指标恶化 `▼` | 红色 | 1 |
| 正常拓扑状态 | 绿色 | 2 |
| 切换中拓扑状态 | 黄色闪烁 | 3 + blink |
| 失败状态 | 红色 | 1 |
| 边框 | 默认/白色 | 7 |
| 标题文字 | 青色 | 6 |

#### 刷新与退出

- 主循环：`while true; do poll_all; redraw; sleep $INTERVAL; done`
- 退出：检测 `q` 键（`read -t 0.01 -n 1 -s`），或 `SIGTERM`/`SIGINT`
- 退出时执行 `tput reset` 恢复终端状态

## 5. 场景文件

### demo/demo_scenario.yaml — 演示流程定义

```yaml
# 弹性 TP/PP 自动切换演示场景
# 定义阶段序列，每个阶段包含负载特征参数和预期控制器事件

# 服务启动配置
service:
  initial_topology: "2x2"           # 初始拓扑（让控制器有"向上"空间）
  # 可覆盖默认控制器参数（demo_run.sh 将 export 为环境变量）:
  # controller_overrides:
  #   DYN_ELASTIC_SWITCH_FACTOR_UP: 10
  #   DYN_ELASTIC_SWITCH_FACTOR_DOWN: 2
  #   DYN_ELASTIC_SWITCH_COOLDOWN_S: 30

# 演示阶段
phases:
  # Phase 1: 高并发 prefill 重负载 → 控制器自动 SWITCH UP
  - name: "Phase 1: prefill 重 (C=32 > 阈值 10)"
    load:
      input_len: 8192             # 输入 token 长度（prefill 重）
      output_len: 32              # 输出 token 长度（max_tokens）
      conc: 32                    # 并发线程数
      duration: 100               # 总运行秒数
      tag: "prefill_heavy"
    expect:
      - event: "switch_up"
        within_s: 15               # 负载启动后 15s 内应出现 SWITCH UP
        log_pattern: "SWITCH UP -> 4x1: concurrency 32 > 10 for 3 polls"
      - event: "switch_complete"
        within_s: 45               # 切换在 ~26s 完成
        state: { tp: 4, pp: 1, is_switching: false }

  # Gap: 停止负载，等待控制器 SWITCH DOWN
  - name: "Gap: 等待 SWITCH DOWN"
    wait: 90                       # 等待冷却 30s + 决策 6s + 切换 26s ≈ 62s，留余量
    expect:
      - event: "switch_down"
        within_s: 70
        log_pattern: "SWITCH DOWN -> 2x2: concurrency 0 < 2 for 3 polls"
      - event: "switch_complete"
        within_s: 100
        state: { tp: 2, pp: 2, is_switching: false }

  # Phase 2: 低并发 decode 重负载 → 不触发 SWITCH UP，展示 2x2 优势
  - name: "Phase 2: decode 重 (C=8 < 阈值 10, 不触发 UP)"
    load:
      input_len: 128              # 短输入
      output_len: 4096            # 长输出（decode 重）
      conc: 8                     # 并发 < 阈值 10，不触发 UP
      duration: 60
      tag: "decode_heavy"
    expect:
      - event: "no_switch"         # 不应该触发任何切换
        during_s: 60
```

### 场景文件设计说明

- **负载特征参数**：`input_len`（输入 token 长度）、`output_len`（输出 token 长度）、`conc`（并发）直接描述负载特征，`demo_load.py` 根据 `input_len` 自动生成对应长度的合成 prompt，根据 `output_len` 设置 `max_tokens`
- **可替换**：换一个 `demo_scenario_v2.yaml` 就能跑完全不同的演示（如更多阶段、不同负载参数）
- `expect` 字段用于 `demo_run.sh` 在结束时验证预期是否满足，生成 PASS/FAIL 报告
- `wait` 阶段不发负载，只等待控制器自动反应

## 6. 演示时间轴

```
初始拓扑: 2x2    控制器: UP阈值=10, DOWN阈值=2, 冷却=30s

时间轴 ──────────────────────────────────────────────────────────────────────►

Phase 1: prefill 重负载 (C=32 > 10)        Gap: 无负载 (并发 0 < 2)         Phase 2: decode 负载 (C=8 < 10)
  约 100s                                    约 90s                            约 60s

  ┌── 2x2 基线 ──┬── 控制器 SWITCH UP ──┬── 4x1 稳态 ──┬── 停止负载 ──┬── 冷却 + SWITCH DOWN ──┬── 2x2 decode ──┐
  │   ~10s       │   ~6s 决策 + 26s 切换   │   ~30s       │              │   30s冷却 + 6s + 26s    │   ~60s         │
  │  吞吐低      │  is_switching=true   │  吞吐高      │  并发→0      │  is_switching=true     │  TPOT 优势     │
  │  ~104 tok/s  │  停放请求仍计数        │  ~144 tok/s  │              │                        │  ~94ms         │
  └──────────────┴───────────────────────┴──────────────┴──────────────┴────────────────────────┴────────────────┘

                  ▲ [CONTROLLER]                                    ▲ [CONTROLLER]                    (无切换)
                  SWITCH UP -> 4x1                                  SWITCH DOWN -> 2x2
                  concurrency 32 > 10                              concurrency 0 < 2
```

## 7. demo/demo_run.sh 编排逻辑

```bash
#!/bin/bash
# 读取 demo_scenario.yaml，驱动通用工具执行演示

# 0. 设置弹性控制器默认环境变量（: 语法，仅当变量未设置时生效）
#    : "${DYN_ELASTIC_SWITCH_ENABLE:=1}"
#    : "${DYN_ELASTIC_SWITCH_WORKER_URLS:=http://localhost:9091}"
#    : "${DYN_ELASTIC_SWITCH_EXPECTED_WORKERS:=1}"
#    : "${DYN_ELASTIC_SWITCH_METRICS_URL:=http://localhost:9090/metrics}"
#    : "${DYN_ELASTIC_SWITCH_POLL_INTERVAL_S:=2}"
#    : "${DYN_ELASTIC_SWITCH_STABLE_POLLS:=3}"
#    : "${DYN_ELASTIC_SWITCH_FACTOR_UP:=10}"
#    : "${DYN_ELASTIC_SWITCH_FACTOR_DOWN:=2}"
#    : "${DYN_ELASTIC_SWITCH_STRATEGY_UP:=4x1}"
#    : "${DYN_ELASTIC_SWITCH_STRATEGY_DOWN:=2x2}"
#    : "${DYN_ELASTIC_SWITCH_COOLDOWN_S:=30}"
#    export DYN_ELASTIC_SWITCH_ENABLE DYN_ELASTIC_SWITCH_WORKER_URLS ...

# 1. 读取场景文件，提取服务配置
#    initial_topology=2x2, controller_overrides=[...]

# 2. 将场景文件中的 controller_overrides export 为环境变量
#    （场景文件覆盖默认值，但低于命令行环境变量）

# 3. 用 service.sh 启动服务（环境变量自动传递给子进程）
#    ./service.sh remp --background --tensor_parallel_size 2 --pipeline_parallel_size 2

# 4. 验证服务就绪 + 控制器启动
#    curl :9090/health
#    grep "\[TP/PP\] controller started" logs/frontend.log

# 5. 启动 TUI（前台终端显示）
#    python3 demo/demo_tui.py --up-threshold 10 --down-threshold 2 &

# 6. 遍历场景 phases:
#    for phase in phases:
#      if phase.load:
#        启动 demo/demo_load.py（后台），传入 input_len/output_len/conc/duration
#        → 并发升至 conc，控制器自动决策
#      if phase.wait:
#        sleep phase.wait
#        → 并发降至 0，控制器自动 SWITCH DOWN
#      收集 demo_load.py 的 stdout JSON 行（TUI 通过 stdin 读取）
#      验证 expect 事件（检查 frontend.log grep + 状态端点）

# 7. 生成总结报告
#    输出 demo_output/report.json

# 8. 停止 TUI
```

## 8. 预期指标对比表（演示结束后输出）

### Phase 1: prefill 重 (C=32) — 控制器自动 2x2→4x1

| 指标 | 2x2（切换前） | 4x1（切换后） | 差距 |
|---|---|---|---|
| 输出吞吐 (tok/s) | ~104 | ~144 | +38% |
| TTFT mean (ms) | ~7911 | ~5398 | -32% |
| TPOT mean (ms) | ~273 | ~198 | -27% |

### Phase 2: decode 重 (C=8) — 控制器自动 4x1→2x2 后

| 指标 | 4x1（切换前基线） | 2x2（切换后） | 差距 |
|---|---|---|---|
| TPOT mean (ms) | ~101 | ~94 | -7% |
| 输出吞吐 (tok/s) | ~158 | ~170 | +8% |
| TTFT mean (ms) | ~536 | ~710 | +32%（2x2 劣势，但 C=8 绝对值小） |

**结论**：弹性控制器根据并发流量自动选择最优拓扑——高并发时切到 4x1（吞吐 +38%），低并发时切回 2x2（decode TPOT -7%），全程零人工干预。

## 9. 前置条件

- `demo_run.sh` 在启动服务前设置 `DYN_ELASTIC_SWITCH_*` 默认环境变量，实现默认启用弹性控制器
- `curl`、`jq` 已安装
- `pyyaml` 已安装（读取场景文件）：`pip install pyyaml`
- 4 GPU 空闲，模型 Qwen3.8-27B 位于 `/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/`
- conda Python 在 `/opt/conda/bin/python`（service.sh 默认路径）

## 10. 快速启动

```bash
cd benchmarks/elastic_vllm

# 一键启动演示
# （自动完成：启服务带控制器 → 启 TUI → 按场景驱动负载 → 验证预期 → 报告）
demo/demo_run.sh --scenario demo/demo_scenario.yaml

# 演示结束后查看报告
cat demo_output/report.json | jq .
```

### 手动分步执行

```bash
# 1. 设置控制器环境变量并启动服务（backend 2x2 + frontend 带控制器）
export DYN_ELASTIC_SWITCH_ENABLE=1
export DYN_ELASTIC_SWITCH_WORKER_URLS=http://localhost:9091
export DYN_ELASTIC_SWITCH_EXPECTED_WORKERS=1
export DYN_ELASTIC_SWITCH_METRICS_URL=http://localhost:9090/metrics
export DYN_ELASTIC_SWITCH_FACTOR_UP=10
export DYN_ELASTIC_SWITCH_FACTOR_DOWN=2
export DYN_ELASTIC_SWITCH_STRATEGY_UP=4x1
export DYN_ELASTIC_SWITCH_STRATEGY_DOWN=2x2
export DYN_ELASTIC_SWITCH_COOLDOWN_S=30
export DYN_ELASTIC_SWITCH_STABLE_POLLS=3
export DYN_ELASTIC_SWITCH_POLL_INTERVAL_S=2
./service.sh remp --background --tensor_parallel_size 2 --pipeline_parallel_size 2

# 或一行式覆盖参数
DYN_ELASTIC_SWITCH_FACTOR_UP=5 DYN_ELASTIC_SWITCH_COOLDOWN_S=15 \
  ./service.sh remp --background --tensor_parallel_size 2 --pipeline_parallel_size 2

# 2. 确认控制器启动
grep "\[TP/PP\] controller started" logs/frontend.log

# 3. 确认初始拓扑 2x2
curl -s -X POST http://localhost:9091/engine/control/parallel_strategy_state \
  -H 'Content-Type: application/json' -d '{}' | jq .

# 4. 启动 TUI 监控（前台终端）
python3 demo/demo_tui.py --up-threshold 10 --down-threshold 2

# 5. Phase 1: prefill 重负载 (C=32 > 阈值 10 → 控制器自动切 4x1)
demo/demo_load.py --model /mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/ \
  --conc 32 --duration 100 --input-len 8192 --output-len 32 --tag prefill_heavy

# 6. 等待 ~90s（冷却 30s + 决策 6s + 切换 26s），控制器自动切回 2x2
#    TUI 实时显示 SWITCH DOWN 事件

# 7. Phase 2: decode 重负载 (C=8 < 阈值 10, 不触发 UP)
demo/demo_load.py --model /mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/ \
  --conc 8 --duration 60 --input-len 128 --output-len 4096 --tag decode_heavy
```

## 11. 自定义演示场景

替换场景文件即可运行不同的演示流程：

```yaml
# demo_scenario_v2.yaml — 更激进的阈值 + 更多阶段
service:
  initial_topology: "2x2"
  controller_overrides:
    DYN_ELASTIC_SWITCH_FACTOR_UP: 5
    DYN_ELASTIC_SWITCH_COOLDOWN_S: 15

phases:
  - name: "burst 1"
    load: { input_len: 2048, output_len: 32, conc: 20, duration: 60, tag: "burst1" }
    expect:
      - { event: "switch_up", within_s: 15 }

  - name: "idle 1"
    wait: 50
    expect:
      - { event: "switch_down", within_s: 50 }

  - name: "burst 2"
    load: { input_len: 2048, output_len: 32, conc: 20, duration: 60, tag: "burst2" }
    expect:
      - { event: "switch_up", within_s: 15 }
```

```bash
demo/demo_run.sh --scenario demo/demo_scenario_v2.yaml
```

## 12. 注意事项

- 切换期间（~26s）在途请求被停放，`active_requests` 保持高位——这是设计行为，控制器靠冷却期 + campaign_active 抑制防抖，不要"修复"该指标
- Phase 1 停止负载后需等待冷却期（默认 30s）结束，控制器才会检测到低并发并 SWITCH DOWN
- `DYN_ELASTIC_SWITCH_COOLDOWN_S` 默认 30s（demo_run.sh 内置，演示节奏紧凑）；生产环境应设更大值
- 初始拓扑必须为 2x2，这样控制器才有"向上"切换的空间（4x1）；若初始已是 4x1，高并发不会触发 SWITCH UP
- `demo_load.py` 闭环模式保证在途数 = CONC，控制器看到的 `active_requests` ≈ CONC
- 演示数据为预期值（基于 report.md），实际值受运行时状态影响可能有 ±5% 波动
- 弹性控制器默认启用（demo_run.sh 设置 `DYN_ELASTIC_SWITCH_ENABLE=1`）；通过 `DYN_ELASTIC_SWITCH_ENABLE=0` 可禁用
- 控制器参数覆盖优先级：命令行环境变量 > 场景文件 controller_overrides > demo_run.sh 默认值
- service.sh 不做改动，所有控制器参数通过环境变量传递给 frontend 子进程
