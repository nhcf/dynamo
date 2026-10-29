#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the Planner source-reading chapter; no cluster operations."""
import json
import runpy
from pathlib import Path

OUT = Path(__file__).resolve().parent
base = runpy.run_path(str(OUT / "build_tutorial.py"))
src, table, flow, note, section, page = [
    base[n] for n in ["src", "table", "flow", "note", "section", "page"]
]
base["manifest"].clear()
P = "components/src/dynamo/planner/"


def S(key, file, start, end, title):
    return src("pl-" + key, P + file, start, end, title)


b = section(
    "pl-overview",
    "01 / Planner 的职责与本章边界",
    """<p>Planner 是有状态的容量控制器：采集流量与引擎观测，估算容量和排队压力，合并候选方案与资源约束，再经 Connector 提交副本目标。它同时是可观测数据的消费者与生产者。</p><p>本章固定在 <code>2f296b592</code>（workspace 1.7.0 开发基线），只分析本地源码，不读取集群、不执行扩缩容。线上 1.4.1 等版本需单独核对实现。重点是在线 builtin 路径；Global Planner、外部插件、batch、replay、retune 只标出边界，不声称已逐项完成分析。</p>"""
    + table(
        ["输入通道", "从哪里来", "控制用途"],
        [
            [
                "Prometheus 查询",
                "Frontend / Router 的窗口化请求与 Histogram；引擎 speculative 指标",
                "流量形状预测、容量下界、KV/accept-length 元数据",
            ],
            [
                "Runtime FPM 事件",
                "Worker/rank 调度遥测，经 Rust subscriber 跟踪",
                "即时负载、性能模型调优、空闲与完整性检查",
            ],
            [
                "部署状态 / Worker 元数据",
                "Environment / Connector、发现信息、模型部署卡",
                "Ready/Expected/Pending、副本状态、GPU形状、引擎上限",
            ],
            [
                "可选 batch 输入",
                "BatchMetricsConfig 的直接 OpenMetrics 端点",
                "batch-aware planning；与普通 Prometheus 查询不同，本文不深挖策略",
            ],
        ],
    )
    + note(
        "不要把 FPM 事件订阅、FPM 文件捕获和 Planner 自身的 /metrics 当成同一条链路：Planner 在线消费 FPM 不依赖先把 gzip JSONL 导入 Prometheus。"
    ),
)
b += section(
    "pl-map",
    "02 / 总体架构与一次 tick",
    flow(
        [
            "环境刷新<br>Connector + runtime namespace",
            "OBSERVE<br>Traffic / FPM / Workers",
            "PREDICT → PROPOSE<br>预测与 builtin 扩缩容",
            "RECONCILE → CONSTRAIN<br>合并、最小副本与预算",
            "PlannerEffects<br>Connector 提交 / 回执",
        ]
    )
    + """<div class="diagram"><div class="lane"><b>长周期</b><span>Prometheus 窗口 → TrafficObservation → 请求数/ISL/OSL predictor → 性能模型容量 → 吞吐副本下界</span></div><div class="lane"><b>短周期</b><span>FPM + inventory → 完整性与元数据检查 → 队列/迭代压力估计 → 负载副本建议</span></div><div class="lane"><b>输出</b><span>Effects / decision reasons → Connector；observed / predicted / estimated / signal health → Planner 自身指标与诊断记录</span></div></div>"""
    + S("main", "__main__.py", 33, 52, "启动完成后并行服务健康端点与 run")
    + S("factory", "core/planner_factory.py", 71, 139, "Environment 组装两个 provider")
    + S("tick", "core/base.py", 986, 1023, "完整 tick：观测、决策、执行与诊断")
    + "<p>真实在线 OBSERVE 优先走 <code>engine.observe()</code>，后者调用 EnvironmentObservePlugin；Base 的 _gather_tick_input 是兼容回退，不应把它写成唯一运行路径。</p>"
    + S(
        "observebridge",
        "plugins/orchestrator/engine_adapter.py",
        777,
        788,
        "engine.observe 委派",
    )
    + S("observe", "plugins/builtins/observe.py", 55, 80, "按 scheduled need_* 收集输入"),
    "为何 run() 存活不等于自动扩缩容正常？",
    "还需验证初始化、信号、模型、插件产出、预算约束及效果提交；健康端点注册不保证每个 tick 都产生可执行决策。",
)
b += section(
    "pl-modes",
    "03 / 模式、优化目标与观测依赖",
    """<p>拓扑 mode 与 optimization_target 是两个维度。agg 控制组合 Worker，disagg 分别控制 Prefill/Decode，prefill/decode 控制单池。optimization_target 决定如何使用观测数据。</p><div class="tabs" role="tablist" aria-label="Planner 策略"><button role="tab" id="pltab-easy" aria-controls="plpanel-easy" data-tab="easy" aria-selected="true">throughput / latency / load</button><button role="tab" id="pltab-sla" aria-controls="plpanel-sla" data-tab="sla" aria-selected="false">SLA 吞吐 + 负载</button></div><div class="tab-panel" id="plpanel-easy" role="tabpanel" aria-labelledby="pltab-easy" data-panel="easy"><b>非 SLA：配置归一化后强制 load scaling，关闭 throughput scaling。</b><p>throughput / latency 采用内置 queue / KV 阈值；load 使用显式阈值。optimization_target=throughput 不是 enable_throughput_scaling=true，两者名称相近但含义不同。</p></div><div class="tab-panel" id="plpanel-sla" role="tabpanel" aria-labelledby="pltab-sla" data-panel="sla" hidden><b>SLA：可启用吞吐、负载或两者。</b><p>吞吐路径使用 traffic + 性能模型；SLA load 路径使用 FPM + 性能模型，也可能查询 KV hit rate / accept length。两者一起使用时吞吐路径提供下界，负载路径处理较快变化。</p></div>"""
    + S("normalize", "config/planner_config.py", 1758, 1775, "非 SLA 的最终配置归一化")
    + S("defaults", "config/defaults.py", 23, 110, "默认参数：先区分原始默认与归一化后配置")
    + note(
        "当前 defaults 中吞吐窗口为 180s、load interval 为 5s；FPM freshness 在 factory 中设为 3 × load interval。不能把一个常量类中的布尔默认值单独当成最终生效模式。"
    ),
)
b += section(
    "pl-promql",
    "04 / Prometheus 数据：窗口、分母与命名空间",
    """<p>Provider 查询 TTFT、ITL、请求时长、请求数、ISL、OSL、KV hit rate 和 accept length；TTFT/ITL 的秒会乘 1000 转成毫秒，请求时长保留秒。</p>"""
    + S(
        "query",
        "environment/metrics_provider/prometheus_traffic_provider.py",
        158,
        196,
        "查询字段与单位转换",
    )
    + S("average", "monitoring/traffic_metrics.py", 164, 247, "Histogram 聚合：先 sum 再除")
    + """<pre><code># 代码使用窗口内 histogram 增量的加权均值，而不是 p95：
sum(increase(metric_sum[window])) /
sum(increase(metric_count[window]))

# Frontend 路径先按 model, dynamo_namespace 分组，再在客户端筛选；
# Router 路径在 PromQL 中按规范化 dynamo_namespace 筛选。</code></pre><p>“平均各实例平均值”会让空闲或少量请求实例获得不合理权重。当前实现按样本数合并：A 的 10 个请求均值 100ms，B 的 90 个请求均值 1000ms，整体均值为 910ms，而不是 550ms。</p><div class="sim"><h3>加权均值演算</h3><label>A 实例请求数：<b id="pl-n-value"></b><input id="pl-n" type="range" min="1" max="100" value="10"></label><label>B 实例请求数固定 90；A/B 平均耗时固定 100/1000ms。</label><div class="sim-output" id="pl-weighted" aria-live="polite"></div></div>"""
    + note(
        "TTFT/ITL 的 observed_* 是窗口均值，不能当成 p95/p99。SLA 控制器使用性能模型和配置目标，不是直接把观测 p95 与目标比较。Frontend 路径是 model 大小写不敏感匹配、namespace 精确匹配；Router 将 namespace 的连字符规范成下划线。"
    ),
    "为何不能直接把看板 p95 与 observed_ttft_ms 对比？",
    "统计对象、窗口和聚合方式不同。这里是 sum 增量 / count 增量形成的均值，且已转为 ms。",
)
b += section(
    "pl-demand",
    "05 / 请求数：started、completed 与 UNKNOWN",
    """<p>方法名 get_avg_request_count 容易误导：返回的是窗口内请求数，不是已经除以秒数的 RPS。预测和展示时再用 duration_s / throughput interval 换算。</p><p>Frontend 优先读取 requests_started_total，Router 使用 admitted/started 优先、completed 兼容回退。原因是：正在排队或生成的长请求尚未完成，单看 completed 会低估输入需求。</p>"""
    + S("demand", "monitoring/traffic_metrics.py", 298, 433, "started 优先与 completed 回退")
    + table(
        ["观测", "解释", "是否证明空闲"],
        [
            ["started 增量 0，匹配序列可用", "已接收请求在窗口内为 0", "该需求统计口径下可作为零流量"],
            ["仅 completed=0", "可能有请求尚未完成", "不能，返回 UNKNOWN/None"],
            ["started 缺失，completed>0", "兼容回退，可能低估需求", "不能据此证明 offered load 完整"],
            ["两者缺失或请求计数查询异常", "无法可靠确定需求", "返回 None；不是自动填 0"],
        ],
    )
    + S("valid", "monitoring/traffic_metrics.py", 54, 89, "idle NaN 归一与必需字段校验")
    + S(
        "traffic",
        "environment/metrics_provider/prometheus_traffic_provider.py",
        64,
        112,
        "TrafficObservation 的生成门槛",
    )
    + note(
        "实现并未对所有字段统一 None 语义：_get_average_metric 对部分缺失或异常返回 0，Router NaN 也归零，而请求计数更保守。不能把所有 observed latency=0 都解释为真实零耗时。只有确认 num_req=0 时，idle NaN 才按该 helper 归零；None 不被补成零。",
        True,
    ),
    "Prometheus 短暂不可用时会不会直接当作零需求缩容？",
    "请求计数路径降级为 None，完整 traffic 校验无法通过，builtin predict 不应得到本次有效 traffic；但各辅助指标的回退规则不同，须逐字段检查。",
)
b += section(
    "pl-io",
    "06 / 查询线程、缓存会话与按需采集",
    """<p>Prometheus 客户端是同步 Requests。_collect_metrics 使用 to_thread 执行查询，并用 lock / shield / 上一次任务等待保证被取消的查询不会与下一次查询并发复用同一 Session。</p>"""
    + S(
        "collectlock",
        "environment/metrics_provider/prometheus_traffic_provider.py",
        114,
        139,
        "同步查询的异步封装",
    )
    + S(
        "cadence",
        "plugins/orchestrator/engine_adapter.py",
        823,
        935,
        "base tick 与 due plugin 的惰性采集",
    )
    + "<p>插件 execution_interval 与 base scale_interval 分开；只有到期插件声明需要 observations.traffic，才拉取流量数据。throughput-only SLA 仍可能在 load-loop cadence 消费 FPM 调优，不等于一定做负载扩缩容。</p><p>窗口也有层次：scheduler 记录 due 消费者声明的 traffic_metrics_duration_s；轻量 KV 路径使用该值，当前完整 collect_traffic 则显式使用 config.throughput_adjustment_interval_seconds。不要把 scheduler 字段直接说成任意外部插件 full query 的生效窗口。</p>"
    + S(
        "cheap",
        "environment/metrics_provider/prometheus_traffic_provider.py",
        217,
        248,
        "SLA load-only 的轻量元数据观测",
    ),
    "轻量 TrafficObservation 中 num_req=0 是否证明没有流量？",
    "不证明。该对象用于 KV/accept-length 元数据传递，num_req/isl/osl 填的是占位 0。必须结合 use_full_traffic_metrics 和数据用途。",
)
b += section(
    "pl-fpm",
    "07 / FPM：事件通道、rank 与新鲜度",
    """<p>Worker 的调度遥测是结构化 msgpack，不是从 Prometheus Histogram 反推出来。Subscriber 绑定 runtime namespace + worker component + endpoint，并按 worker_id / dp_rank 保存近期状态。</p>"""
    + src(
        "pl-schema",
        "components/src/dynamo/common/forward_pass_metrics.py",
        153,
        196,
        "FPM schema 与 idle 判定",
    )
    + S(
        "sub",
        "environment/metrics_provider/runtime_provider.py",
        144,
        181,
        "创建自动发现的事件订阅",
    )
    + src(
        "pl-rustfresh",
        "lib/bindings/python/rust/llm/fpm.rs",
        150,
        188,
        "Rust snapshot：received_at、期望 rank 和已移除 Worker",
    )
    + S(
        "healthy",
        "environment/metrics_provider/runtime_provider.py",
        192,
        217,
        "Python provider 拒绝不完整池",
    )
    + """<p>新鲜度按本机接收时刻 received_at 与 Instant 计算，不是直接比较不同主机的 wall clock。model card 决定期望 rank 集合；未 join、过期、缺 rank、解码无效会使该角色的快照被拒绝，而不是只保留健康子集后当作全池状态。</p>"""
    + table(
        ["FPM 字段/状态", "控制含义"],
        [
            [
                "scheduled_requests.sum_prefill_tokens",
                "本迭代实际新计算的 prefill token；不能当成总 prompt 长度",
            ],
            ["queued_requests.sum_prefill_tokens", "未调度排队的 prefill 工作量"],
            ["scheduled/queued sum_decode_kv_tokens", "生成中的 KV 上下文压力及队列 KV 工作量"],
            ["counter_id / wall_time", "序列标记与调度到输出处理的迭代耗时；不是用户端到端 TTFT"],
            ["missing / stale / unjoined", "身份与样本完整性问题，不是 0 负载"],
        ],
    )
    + note(
        "FPM trace 文件是可选诊断持久化。Planner 的在线订阅、模型调优与 freshness 检查不依赖启用 DYN_FPM_TRACE。FPM 能力也须按实际 backend / topology 检查。"
    ),
    "只收到 4 个 DP rank 中的 1 个空闲 rank，能否认定整个池空闲？",
    "不能。健康快照会报告缺 rank，provider 拒绝该角色数据；新鲜子集不构成全池空闲证据。",
)
b += section(
    "pl-model",
    "08 / FPM 如何变成性能模型输入",
    """<p>调度遥测还用于性能模型调优。SLA tick 在 proposal 之前把可用 FPM 交给共享状态模型。性能能力由 PlannerEnginePerfModel 包装 AISimulate；它不仅接收观测，还绑定引擎身份、并行布局、KV 容量和最大 batch token 等限制。</p>"""
    + S("fit", "core/perf_model/ais_adapter.py", 323, 354, "筛选观测、保留请求形状并调优")
    + S(
        "capacity",
        "core/perf_model/ais_adapter.py",
        550,
        597,
        "向 AISimulate 请求 SLA 下容量",
    )
    + S("bootstrap", "monitoring/perf_metrics.py", 39, 115, "性能数据 bootstrap 候选")
    + """<p>自基准端点 → 配置中的 AIC interpolation → profile 文件是 bootstrap 候选；拿不到预部署数据时 caller 可继续 live FPM warmup。不能仅看 fetch_pre_deployment_metrics 自己抛异常就判断整个 Planner 必然启动失败。batch/KV/并行元数据缺失与观测样本不足是不同原因。</p><p>退化路径的确切表现看 decision reason：model_not_ready、insufficient_data、missing_engine_metadata。FPM wall_time 数据可用，也不代表所有容量查询都可用。</p>"""
    + note(
        "AISimulate 的容量结果是模型估算，可能受支持范围、样本与布局约束。这里没有调用真实 GPU 测得容量，也没有证明实际模型满足用户端 SLA。"
    ),
)
b += section(
    "pl-throughput",
    "09 / 吞吐路径：预测需求 → 容量 → 下界",
    """<p>BuiltinLoadPredict 维护请求数、ISL、OSL 的跨窗口历史；KV hit rate 和 speculative accept length 使用最新有效值，不是同样做时间序列预测。</p>"""
    + S("predictshape", "plugins/builtins/local_planner.py", 141, 163, "空闲时保留历史请求形状")
    + S(
        "predict",
        "plugins/builtins/local_planner.py",
        185,
        256,
        "同 tick PredictionData",
    )
    + S("tp", "core/state_machine.py", 318, 371, "预测数 / 窗口时长 = demand_rps")
    + S("replicas", "core/throughput_scaling.py", 344, 436, "按角色容量估算 replicas")
    + """<pre><code># 简化解释，真实结果仍受 eligibility、下界和最终预算约束：
demand_rps = predicted_num_req / traffic.duration_s
desired = max(ceil(demand_rps / engine_capacity_rps), endpoint_floor)

例：180 秒预测 900 请求 → 5 RPS
单引擎容量 2 RPS → ceil(5 / 2) = 3
若 GPU 预算最多允许 2 个该角色副本，最终输出可能被限制。</code></pre><p>容量非正时返回 None，表示不形成该角色的新容量建议，并保留原因；不是把容量当成 0 后随意扩到无穷。确认 demand=0 的特殊路径至少保留 1 个 publisher / endpoint floor。</p>"""
    + S(
        "propose",
        "plugins/builtins/local_planner.py",
        268,
        296,
        "必须存在同 tick traffic 与 predictions",
    ),
    "空闲窗口里的 ISL=0 会一直被预测器学进去吗？",
    "已见有效流量之后，num_req=0 的窗口推进请求数历史，但使用最近有效 ISL/OSL，避免把空闲误学成真实零长度请求。",
)
b += section(
    "pl-load",
    "10 / 负载路径：估算压力与安全缩容",
    """<p>SLA load scaling 不直接以 Prometheus observed_ttft_ms 超标作为唯一触发器。它用当前 FPM、性能模型、KV/accept-length 元数据估算队列 TTFT 和 decode ITL。非 SLA easy 路径则走 queue/KV 阈值。</p>"""
    + S("loadgates", "core/load_scaling.py", 158, 213, "FPM、Worker 数与正在伸缩的前置检查")
    + S("prefill", "core/load_scaling.py", 516, 623, "Prefill：队列与自身计算时间")
    + S("decode", "core/load_scaling.py", 625, 716, "Decode：排队 KV 与 speculative 折算")
    + S(
        "decision", "core/load_scaling.py", 1176, 1227, "ALL above SLA 与 can_scale_down"
    )
    + """<p>扩容判定看可用 estimates 是否全部大于 SLA；指标里展示的 estimated_* 常是 max(estimates)。因此“max 超标”与“ALL 超标”不是同一条件，看板中单个最大值不足以复现决策。</p><p>缩容前按 N/(N−1) 模拟幸存副本压力。Prefill 从预算中排除不会随副本数下降的新请求自身计算；Decode 先检查 KV 可容纳性，再检查合并后的 ITL 安全余量。默认 sensitivity=80%，不是无条件低于当前 SLA 就缩。</p>"""
    + note(
        "正常 FPM-driven 空闲缩容至少保留 1 个发布者，因为最后一个 publisher 消失后本路径无法靠 FPM 自行唤醒。实际配置的 endpoint floor、pending startup 与最终预算还会进一步影响结果。"
    ),
    "为何 estimated_itl_ms 超标却没有扩容？",
    "可能只有部分引擎超标；还需检查估算完整性、正在扩容、元数据、预算、插件约束和 effect 提交。max gauge 不是 ALL 判定的全部输入。",
)
b += section(
    "pl-easy",
    "10b / 非 SLA 路径：阈值口径与 ANY / ALL",
    "<p>Easy prefill 的 throughput/latency 目标按 queued_prefill_tokens / context_length 比例，load 目标按绝对排队 token 阈值。Easy decode 按 (scheduled KV + queued KV) / max_kv_tokens；配置 load 的 KV 阈值是百分数，内部除以 100。</p><p>Easy 路径扩容可由 ANY 引擎越过上阈值触发，缩容通常要 ALL 低于对应下阈值并满足 consolidation 约束；这不同于 SLA 模型路径的 ALL estimates 超时延目标。</p>"
    + S(
        "easy-prefill",
        "core/load_scaling.py",
        944,
        977,
        "Easy prefill 的 ANY 扩容与 ALL 检查",
    )
    + S(
        "easy-decode", "core/load_scaling.py", 1049, 1068, "Easy decode 的队列+运行中 KV 利用率"
    ),
)
b += section(
    "pl-execution",
    "11 / 建议到执行：约束、advisory 与回执",
    """<p>builtin PROPOSE 在同一 tick 合并。混合模式下，load proposer 会先刷新 throughput 下界，避免并发 PROPOSE 顺序让 load 读到旧 floor。之后仍要经过 RECONCILE / CONSTRAIN、角色最小值、GPU/可选功耗预算，再投影为 Effects。</p>"""
    + S(
        "mixed",
        "plugins/builtins/local_planner.py",
        308,
        368,
        "并发 proposer 与 throughput floor",
    )
    + S("submit", "core/base.py", 709, 777, "generation 校验、单个未确认提交")
    + S("advisory", "core/base.py", 844, 849, "advisory 阻止实际 scaling")
    + S("effect", "core/adapters.py", 200, 235, "Disagg：建议指标与 TargetReplica")
    + """<p>advisory 仍可输出建议，因此 predicted_num_*_replicas 不证明部署已修改；actual / ready 副本来自下一次 inventory。前一提交未确认时不堆叠新请求；结果未知时记录 hold，源码要求核对部署状态后重启 Planner 才恢复自动提交。</p>"""
    + note(
        "run() 的顶层恢复分支主要处理 GPUShapeUnavailableError；不能笼统宣称“所有采集异常都会安全跳过”。例如 KV helper 对传输错误降级 None，但畸形响应可重新抛出；错误契约需要按路径逐项分析。",
        True,
    ),
    "建议副本数增加而实际副本不变，该看哪一层？",
    "先看 advisory、执行 action、最终 budget/clamp，再看 submission pending/unknown 和 Connector/Operator 状态；不要先假设 Prometheus 没有更新。",
)
b += section(
    "pl-output",
    "12 / Planner 自身输出：四种数字不能混用",
    table(
        ["输出", "性质", "常见误读"],
        [
            ["observed_*", "Prometheus 窗口观测的再输出", "不是本 tick 新采样，不是 p95"],
            [
                "predicted_requests_per_second / sequence tokens",
                "下个窗口的负载预测",
                "不是已经发生的流量",
            ],
            [
                "estimated_ttft_ms / estimated_itl_ms",
                "模型推算的角色压力，常展示最大估计",
                "不是用户客户端实测，也不等于 ALL 判定",
            ],
            ["predicted_num_*_replicas", "计划/建议副本目标", "不证明已提交或已 Ready"],
            [
                "num_*_replicas / gpu_hours",
                "inventory 与运行时积分估计",
                "GPU hours 不是 GPU利用率，也不是跨重启持久化计费",
            ],
        ],
    )
    + S("publish", "core/base.py", 555, 605, "observed 与 per-engine gauge 发布")
    + S("diag", "core/base.py", 923, 971, "无新诊断值时保留 Gauge")
    + S("gpu", "core/base.py", 898, 920, "副本数 × 每副本 GPU × 时间积分")
    + """<p>Gauge 有两种不同生命周期：_set_if_observed(None) 不写入，旧值会继续被抓取为当前样本；per-engine FPM Vec 则先 clear 再填本批有效 worker/rank。前者可能看起来持续正常，后者可能消失。看板应联查信号健康和输入路径，不把 Gauge 平直当作数据新鲜。Planner 业务 Gauge 多数没有 model / dynamo_namespace 等标签，只有部分 per-engine Vec 带 worker_id / dp_rank；多 DGD 看板需要依赖采集侧 namespace、pod、部署身份，避免跨 Planner 直接求和。</p>"""
    + S("expose", "core/base.py", 128, 155, "独立 prometheus_client HTTP server")
    + note(
        "metric_reporting_prometheus_port / PLANNER_PROMETHEUS_PORT 默认 0 关闭专用指标服务；它不同于 Runtime DYN_SYSTEM_PORT，也不同于 metric_pulling_prometheus_endpoint。端口非零却 bind 失败时仅记录错误，仍要实际验证 exporter 可达。"
    ),
)
b += section(
    "pl-health",
    "13 / 信号健康与插件执行指标",
    S("signal", "monitoring/signal_health.py", 21, 125, "所需信号缺失、连续观测与恢复")
    + S("signalmetrics", "monitoring/planner_metrics.py", 240, 288, "健康/插件指标定义")
    + """<p>SIGNAL_DEGRADATION_OBSERVATIONS=3。连续三次“需要且不可用”的观测达到阈值，记录一次 warning 并增加 degraded_signals_total；持续缺失不会每 tick 增加同一 episode，恢复后再次三次缺失才是新 episode。这是观测次数，不是固定 15 秒。</p>"""
    + table(
        ["指标", "读法"],
        [
            [
                "dynamo_planner_signal_unavailable{component,signal}",
                "最近一次所需信号观测是否不可用；未轮到的输入不应机械视为恢复",
            ],
            ["dynamo_planner_degraded_signals_total", "连续缺失触发的退化事件数，不是坏样本总数"],
            [
                "plugin_evaluations_total{result}",
                "区分 accept、override、timeout/error 与 held-over",
            ],
            ["plugin_latency_seconds", "只记录相应成功调用的时延；失败需联看 counter"],
            [
                "tick_requires_unsatisfied_total",
                "本 tick 上游未产出必需字段，例如没有 traffic/predictions",
            ],
            [
                "tick_duration_seconds / tick_timeout_total",
                "前者主要覆盖 orchestrator 决策阶段，不包括完整采集和外部提交时长",
            ],
        ],
    )
    + S(
        "healthtest",
        "tests/unit/test_signal_health.py",
        53,
        74,
        "已有测试：一次 episode、恢复、再次退化",
    )
    + note(
        "缺 FPM 的 gauge、模型 warmup insufficient_data、真正 missing_engine_metadata 要分开。没有生产者的角色和未执行的路径有条件 gating，不应按指标名粗暴加总。"
    ),
    "为什么 counter 不随每次坏 tick 增加？",
    "它统计连续缺失达到阈值的 episode；不是每次缺失的采样计数。",
)
b += section(
    "pl-metadata",
    "14 / KV、speculation 与参数接入",
    S("kv", "monitoring/traffic_metrics.py", 463, 528, "KV 命中率：namespace 候选与 None")
    + """<p>即使 throughput_metrics_source=frontend，KV hit rate 仍读取 Router 指标。先试 Worker runtime namespace，再试 base namespace；若已有序列但窗口 NaN，则停止，不去读另一个 Router 的流量。有效 0.0 与 None 不同，缺失不覆盖 sticky 元数据。</p><p>accept length 来源与 backend 有关，随后按 speculative nextn 范围限制；冷态安全值通常是 1。它会影响 decode 每次 forward-pass 的输出折算，不等于 KV 命中率。</p>"""
    + table(
        ["配置/身份", "用途与核对点"],
        [
            ["metric_pulling_prometheus_endpoint", "PromQL HTTP 查询地址；不是 OTLP endpoint"],
            [
                "throughput_metrics_source=frontend|router",
                "选流量来源；多池部署必须限定 pool 的 runtime namespace",
            ],
            [
                "metric_pulling_prometheus_token / token_file / ca_bundle / ssl_verify",
                "接入带鉴权或 TLS 的查询服务；不在文档写入真实凭据",
            ],
            [
                "metric_pulling_prometheus_request_timeout_seconds",
                "单个同步 HTTP 请求超时，默认 10s；一批有多个查询，总时长不是一次 timeout",
            ],
            [
                "load_adjustment_interval_seconds",
                "影响 FPM max age 和短周期；不是 Prometheus scrape_interval",
            ],
            [
                "metric_reporting_prometheus_port",
                "Planner 自己的指标服务，默认关闭；保证 Pod 声明命名 metrics 端口与 Monitor 匹配",
            ],
            ["DYN_SYSTEM_PORT", "Runtime 系统状态服务；不要与上面 reporting/pulling 混淆"],
        ],
    )
    + note(
        "输入统计存在 scrape 延迟、窗口长度和查询耗时；FPM 是另一种 freshness。调小 Planner tick 不能自动提升输入采集的新鲜程度。"
    ),
)
b += section(
    "pl-debug",
    "15 / 源码驱动的排障路线与跟读练习",
    table(
        ["症状", "先看证据", "源码入口"],
        [
            [
                "没有任何决策",
                "优化目标归一化、scheduled need_*、due plugin",
                "config / engine_adapter / Observe",
            ],
            [
                "no_traffic_data",
                "started/completed、namespace/model、查询异常",
                "PrometheusAPIClient → collect_traffic → Predict",
            ],
            [
                "no_fpm_data / stale / join failure",
                "Worker事件发布、rank模型卡、年龄和完整性",
                "Rust snapshot → RuntimeFpmProvider",
            ],
            [
                "insufficient_data / model_not_ready",
                "bootstrap 来源、live tuning、引擎限制",
                "perf_metrics / ais_adapter",
            ],
            [
                "max估计超标却不扩",
                "是否全部估计超标；扩容中/预算/插件/提交",
                "load_scaling / engine adapter / _submit_effects",
            ],
            [
                "predicted 与 actual 不一致",
                "advisory、最终约束、回执及 rollout",
                "adapters → Connector → Operator",
            ],
            [
                "Gauge 一直平直",
                "本 tick 是否有新输入；signal_unavailable",
                "_set_if_observed / signal_health",
            ],
        ],
    )
    + """<p>建议跟读顺序：__main__ → planner_factory → _run_one_tick → EnvironmentObservePlugin → 两个 provider → builtin predict/propose → state_machine/mixins → effect submit → metrics/recorder。</p><h3>已有测试入口（本次未运行完整 Planner 单元/集成测试）</h3><ul><li>test_prometheus.py：test_get_avg_request_count_uses_started_requests、test_frontend_completed_zero_does_not_prove_zero_demand、test_metrics_normalize_nan_averages_for_idle_window。</li><li>test_signal_health.py：test_provider_rejects_incomplete_pools_without_reusing_samples、test_provider_rejects_partial_decoding_and_recovers、test_unevaluated_metadata_does_not_clear_an_alarm。</li><li>test_fpm_event_delivery.py：事件交付到 Planner 并驱动扩容的集成测试，需匹配 Runtime/依赖。</li></ul><h3>离线练习</h3><p>分别模拟 started=0、started缺失且completed=0、少一个DP rank、模型样本不足、advisory=true。记录 traffic/FPM 是否可用、reason、建议和执行边界；不要用同一个“没扩容”描述五种不同状态。</p>""",
)
b += section(
    "pl-references",
    "16 / 结论、范围与证据",
    """<p>Planner 将观测数据转换为控制输入：窗口流量决定需求预测，FPM 决定引擎压力与模型调优，部署状态决定哪些动作可执行。信号完整性、时间口径和 None/0 语义直接影响安全性。</p><p>这一章节是静态源码分析，不等于线上版本验收。新增 batch-aware direct OpenMetrics、外部 gRPC 插件策略、Global Planner、AISimulate 内部数学与功耗策略不在本次逐行分析范围。</p><ul><li><a href="planner-source-manifest.json">Planner 源码摘录清单：完整 commit、行号、SHA256</a></li><li><a href="https://docs.nvidia.com/dynamo/dev/knowledge-base/modular-components/planner/planner-design">官方 Planner Design（dev，滚动更新）</a></li><li><a href="https://github.com/ai-dynamo/dynamo/blob/2f296b59274867529ea8deef994e6954520024b6/docs/fern/pages/developer-guide/knowledge-base/modular-components/planner/choose-planner-mode.md">配套 Choose a Planner Mode，固定提交</a></li><li><a href="observability.html">可观测性源码专题</a> · <a href="index.html#outline">系统阅读大纲</a></li></ul>""",
)
nav = [
    ("pl-overview", "01 职责与边界"),
    ("pl-map", "02 架构与 tick"),
    ("pl-modes", "03 模式与依赖"),
    ("pl-promql", "04 PromQL 与聚合"),
    ("pl-demand", "05 需求与 UNKNOWN"),
    ("pl-io", "06 查询与 cadence"),
    ("pl-fpm", "07 FPM / rank / freshness"),
    ("pl-model", "08 性能模型"),
    ("pl-throughput", "09 吞吐与容量下界"),
    ("pl-load", "10 负载与安全缩容"),
    ("pl-easy", "10b 非 SLA 阈值"),
    ("pl-execution", "11 约束与执行"),
    ("pl-output", "12 Planner 自身指标"),
    ("pl-health", "13 信号健康"),
    ("pl-metadata", "14 接入参数"),
    ("pl-debug", "15 源码排障"),
    ("pl-references", "16 证据与范围"),
]
result = page(
    "Planner：观测数据如何驱动扩缩容",
    "从 Prometheus、FPM 和部署状态追到预测、容量、决策与执行；重点解释数据口径、缺失信号和 Planner 自身指标。",
    b,
    nav,
)
result = result.replace("首个源码专题 v0.1", "Planner 源码专题 v0.1")
extra = """<script>
function plannerWeighted(){var n=+document.getElementById('pl-n').value;document.getElementById('pl-n-value').textContent=n;document.getElementById('pl-weighted').textContent='正确加权均值 = ('+n+' × 100 + 90 × 1000) / ('+n+' + 90) = '+((n*100+90000)/(n+90)).toFixed(2)+' ms\\n错误的实例均值平均 = (100 + 1000) / 2 = 550 ms';}
document.getElementById('pl-n').addEventListener('input',plannerWeighted);plannerWeighted();
</script>"""
result = result.replace("</body>", extra + "</body>")
(OUT / "planner.html").write_text(result)
(OUT / "planner-source-manifest.json").write_text(
    json.dumps(
        dict(
            revision=base["PIN"],
            scope="Online native/builtin Planner with emphasis on observability inputs and outputs",
            inference_or_scaling_executed=False,
            excerpts=base["manifest"],
        ),
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(
    "Built Planner chapter: "
    + str(len(base["manifest"]))
    + " source excerpts, 17 sections."
)
