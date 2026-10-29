#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the offline prefill/decode disaggregation source walkthrough."""
import json
import runpy
from pathlib import Path

OUT = Path(__file__).resolve().parent
PIN = "9734418e5bc71027e6bd04a09314bf1765208c72"
base = runpy.run_path(str(OUT / "build_tutorial.py"))
base["src"].__globals__["PIN"] = PIN
base["manifest"].clear()
src, table, flow, note, section, page = [
    base[name] for name in ["src", "table", "flow", "note", "section", "page"]
]
parts, nav = [], []
P = "lib/llm/src/kv_router/prefill_router/"
SG = "components/src/dynamo/sglang/request_handlers/llm/"
VL = "components/src/dynamo/vllm/"
TR = "components/src/dynamo/trtllm/request_handlers/"


def S(key, path, start, end, title):
    return src("pd-serving-" + key, path, start, end, title)


def add(key, title, body, question=None, answer=None):
    title = f"{len(nav) + 1:02} / {title}"
    nav.append((key, title))
    parts.append(section(key, title, body, question, answer))


add(
    "pd-scope",
    "PD 分离究竟拆开了什么",
    "<p><strong>Dynamo 把一次逻辑生成请求编排到 Prefill 与 Decode 两类资源池，由引擎的交接协议搬运或复用 KV。</strong>Prefill 处理输入上下文，Decode 逐步生成后续 token；它们通常分别偏计算密集与访存密集，但实际瓶颈随模型、批量和输入输出长度变化。</p>"
    + table(
        ["层", "职责", "关键边界"],
        [
            [
                "Frontend / PrefillRouter",
                "发现角色、选择实例、编排交接与 Decode",
                "转发 token/元数据，不经 Frontend 搬运 KV 张量",
            ],
            [
                "Backend adapter",
                "把统一请求转换为 SGLang / vLLM / TensorRT-LLM 协议",
                "三种引擎交接对象与完成时点不同",
            ],
            [
                "Engine / KV connector",
                "分配 KV、协调接收、执行传输与生成",
                "NIXL 配置本身不证明实际走 RDMA / GPUDirect",
            ],
            [
                "Discovery / events / metrics",
                "发布角色与端点、缓存状态和负载、记录运行结果",
                "KV 事件、KV 数据、Trace 是三条不同路径",
            ],
        ],
    )
    + note(
        "源码固定于 "
        + PIN
        + "（workspace 1.7.0）；它是本地文档提交的代码父提交。本文是静态源码解读，未执行集群请求、性能测试或故障注入。ACK 私有 SGLang 1.4.1 的部署证据须单独核对。"
    ),
)

add(
    "pd-architecture",
    "架构：控制消息与 KV 数据各走哪条路",
    flow(
        [
            "Frontend 收到请求",
            "PrefillRouter 激活 / 路由",
            "Prefill 引擎计算 KV",
            "交接参数 / Bootstrap",
            "Decode 接收 KV 并生成",
            "流式结果与清理",
        ]
    )
    + '<div class="diagram"><div class="lane"><b>请求面</b><span>Client → Frontend → Prefill Worker；Frontend → Decode Worker → token stream → Client。</span></div><div class="lane"><b>KV 数据面</b><span>Prefill Engine / connector ↔ Decode Engine / connector。Bootstrap 是协调入口，张量不回传 Frontend。</span></div><div class="lane"><b>成员与状态</b><span>Discovery 发布 model card / role / bootstrap；KV events 更新缓存索引；Router 用状态选择实例。</span></div><div class="lane"><b>观测</b><span>Router request tracker + backend trace context + engine / connector metrics；需要分别核对采样与采集出口。</span></div></div><p>PD 的收益来自隔离、独立伸缩与批处理；成本包括 KV 移动、跨进程协调与额外排队。不能直接推导为所有请求的 TTFT 都降低。</p>',
)

add(
    "pd-outcomes",
    "共享编排器有三种 Prefill 结果",
    S("outcome", P + "mod.rs", 98, 157, "PrefillOutcome 与 Decode 请求构造")
    + table(
        ["结果", "如何进入 Decode", "意味着什么"],
        [
            [
                "Completed",
                "prefill_result + migration_link + prefill_worker_id",
                "获得计算结果后交接；并不等价于 KV 已被 Decode 消费",
            ],
            [
                "Bootstrap",
                "bootstrap_info + prefill_worker_id / prefill_dp_rank",
                "协调信息可用即可启动 Decode，Prefill 继续在后台运行",
            ],
            ["Terminal", "直接返回 output", "Prefill 已触发终态，无需伪造 KV 参数或继续 Decode"],
        ],
    )
    + note(
        "handoff_id 不能当成通用 NIXL transfer ID 或 Trace ID。Request ID、engine request ID、bootstrap room、handoff ID 和 tracing context 各自有语义。"
    ),
    "Bootstrap 已返回，能否认为 Prefill 已完成？",
    "不能。后台 observer 仍要持续消费 Prefill 流，核对错误和结束时间。",
)

add(
    "pd-activation",
    "自动激活：发现角色与配置收敛",
    S("binding", P + "activation.rs", 291, 332, "目标 Prefill model card 与运行配置 watcher")
    + S(
        "activation",
        P + "activation.rs",
        640,
        664,
        "Active / Pending / Untargeted 的一致性快照",
    )
    + S("pending", P + "mod.rs", 160, 184, "Pending 错误与 Bootstrap 字段解析")
    + "<p>激活依赖模型、命名空间、角色与端点发布，以及绑定构建完成；仅给进程加角色参数不足以证明路由器已经激活。Prefill card 的路由模式与 block size 可以独立于 Decode 配置，DP、TP、PP 也不能混为一谈。</p><p>Pending 在当前实现中使用 WorkerUnavailable 错误，通常对应 503，且被错误分类视为可迁移；是否实际重试仍受请求资格和预算约束，下一次也可能仍处于 Pending。仓库中部分概念文档称该错误不参与迁移，应以此固定版本实现为准。</p>",
)

add(
    "pd-entry",
    "入口状态机：先决定是否远端 Prefill",
    S("entry", P + "mod.rs", 368, 441, "清理外部 bypass 标记、query-only 与激活状态")
    + "<p>客户端提供的 x-bypass-remote-prefill 会先被移除，避免外部伪造路由器拥有的决策。query_instance_id 是咨询 Decode RoutingHost 的查询路径，不实际执行 Prefill。Untargeted 进入后续 backend 路径，需要该 backend 能承接本地 Prefill。</p>"
    + note(
        "这不是‘远端 Prefill 任意失败都自动回退聚合’。已派发后的错误会进入正常错误处理，不能在未知 KV 副作用下随意再做一次 Prefill。",
        True,
    ),
)

add(
    "pd-selection",
    "实例选择、Bootstrap room 与并行拓扑",
    S("prepare", P + "mod.rs", 743, 783, "选中 Prefill 后准备请求与 Bootstrap")
    + S("room", P + "mod.rs", 800, 831, "DP room 编码与 Decode 路由覆盖")
    + "<p>共享 Frontend 主路径先选择并派发 Prefill，再派发 Decode；直接 Prefill 路由模式则需要外部路由者显式提供 Prefill 实例 ID。引擎内部可能先由 Decode 注册接收信息，不能把引擎的数据流时序套成所有 Frontend 的固定选点顺序。条件 PD 的 Decode preview 是另一个决策分支。</p><p>room 被限制到有符号 64 位范围，并在 DP 信息可用时满足 room % dp_size = dp_rank。它编码的是 DP 分组路由，不是 TP rank。Decode 覆盖把 overlap_score_credit 设为 0、assume_kv_reuse 设为 false，且不再跟踪 Prefill token 负载；这不意味着忽略 Decode 的块占用或容量。</p><p>模型、tokenizer、KV layout/dtype、并行拓扑、LoRA 和缓存隔离信息需符合实际引擎/connector 契约；不能任意混搭后端，也不能简单声称所有组合都要求 TP 完全相同。</p>",
)

add(
    "pd-completed",
    "完成后交接：Completed 时序",
    flow(
        [
            "派发 Prefill",
            "读取结果并排空尾流",
            "构造 prefill_result",
            "附带 worker link",
            "派发 Decode",
            "继续生成",
        ]
    )
    + S("consume", P + "admission.rs", 22, 109, "首个结果分类与 Prefill 尾流观察")
    + '<p>没有 Bootstrap 的正常结果先记录 Prefill complete，再继续排空尾流；尾流错误仍会传播。Decode 通过 prefill_result 获得引擎特定参数，迁移/Trace 所需 worker link 在结果路径中传递。不能只检查第一个成功 chunk 就宣告请求成功。</p><p><a href="pd-serving-completed.mmd">Completed 时序 Mermaid 源图</a>。</p>',
)

add(
    "pd-bootstrap",
    "提前协调：Bootstrap 时序",
    S("spawn", P + "admission.rs", 164, 191, "独立 Prefill observer 与阶段 permit")
    + S(
        "outcome-dispatch",
        P + "mod.rs",
        548,
        594,
        "预先发现 Bootstrap 与流中返回 Bootstrap 两种入口",
    )
    + "<p>Bootstrap 可来自 Discovery 中已发布的端点，也可从 Prefill 首个输出提取。路由器可以在 Prefill 尚未结束时构造 Decode 请求，由引擎的接收/发送协议协调实际 KV 就绪。</p>"
    + flow(
        [
            "获得 Bootstrap",
            "后台持续观察 Prefill",
            "并行派发 Decode",
            "流式转发普通 token",
            "检查 Prefill observer 结果",
            "允许最终成功结束",
        ]
    )
    + '<p>Bootstrap 分支的 Prefill complete 在 observer 消费到流结束后记录，和普通结果路径的采样位置不同。它是编排器观察点，不是 GPU kernel 计时。</p><p><a href="pd-serving-bootstrap.mmd">Bootstrap 时序 Mermaid 源图</a>。</p>',
)

add(
    "pd-forward",
    "并行交接：最终成功必须等待什么",
    S("task", P + "handoff.rs", 26, 82, "PrefillTask 生命周期与 tracing span")
    + S("race", P + "handoff.rs", 110, 168, "并行等待 Decode、Prefill 与首项门控")
    + S("terminal-gate", P + "handoff.rs", 185, 253, "转发 token 与延迟最终成功终态")
    + "<p>PrefillTask 的 Drop 会分离后台任务，避免客户端取消直接中断尚需完成的 KV 传输。BeforeFirst 等模式可先缓存 Decode 首项，等待 Prefill 元数据。普通 token 可以提前转发，但成功终态/EOF 需要同时满足 Prefill observer 成功，防止先返回最终成功再发现 Prefill 错误。</p>",
)

add(
    "pd-terminal",
    "Prefill 自己结束，以及原始采样上限恢复",
    S("terminal-check", P + "admission.rs", 111, 162, "终态优先于交接参数校验")
    + S(
        "decode-request", P + "mod.rs", 635, 689, "Terminal 返回、staged KV 与恢复 max_tokens"
    )
    + "<p>EOS、stop 等可能在 Prefill 阶段产生终态；此时不会要求不存在的交接参数。非终态 TRT context-only 结果却必须有有效 ctx_request_id。Prefill 内部常只生成一个 token，最终 Decode 请求恢复用户原来的 max_tokens；内部一 token 的采样/结果细节由后端协议承接，不能据此推断用户一定先收到一个独立 Prefill token。</p>",
)

add(
    "pd-sg",
    "SGLang：Bootstrap 与后台结果消费",
    S("sg-sampling", "components/src/dynamo/sglang/_disagg.py", 15, 44, "PD 并行采样限制")
    + S("sg-max", SG + "prefill_handler.py", 92, 151, "Prefill 请求采样准备")
    + S(
        "sg-engine",
        SG + "prefill_handler.py",
        246,
        304,
        "engine Bootstrap 参数、首个空输出与持续消费",
    )
    + "<p>SGLang 交接包含 host / port / room。Prefill handler 首先返回 token_ids 为空的 Bootstrap chunk，随后等待后台消费 engine 输出；这个空 chunk 是控制信息，不是首 token。当前 PD 只允许 n=1：单个 room 不能直接代表多个独立采样分支的传输。</p>",
)

add(
    "pd-sg-decode",
    "SGLang Decode：必须把 Bootstrap 交给引擎",
    S(
        "sg-decode",
        SG + "decode_handler.py",
        630,
        662,
        "native Decode 对 Bootstrap 的校验与参数传递",
    )
    + S(
        "sg-standard", SG + "decode_handler.py", 779, 821, "标准 Decode 与 prefill_dp_rank"
    )
    + S(
        "sg-bypass",
        SG + "decode_handler.py",
        118,
        126,
        "当前 SGLang 不支持 conditional-disagg bypass",
    )
    + "<p>Decode-only 路径缺失 Bootstrap 会报错；prefill_dp_rank 在适用路径直接传给引擎。external_trace_header 仅在启用相应 tracing 时提供，传递 header 不能替代引擎侧启用与 exporter 配置。</p>"
    + note(
        "当前源码明确拒绝 SGLang 条件 PD bypass（HTTP 400）。不要把 vLLM / TensorRT-LLM 的本地 Prefill bypass 能力套到 ACK 的 SGLang 部署。",
        True,
    ),
)

add(
    "pd-vllm",
    "vLLM：由 KV Connector 决定交接契约",
    S(
        "connector-contract",
        VL + "kv_connector_protocols.py",
        4,
        64,
        "NIXL / Mooncake / LMCacheMP 与 protocol adapter",
    )
    + S("vl-prefill", VL + "handlers.py", 4584, 4624, "Prefill 单 token 与 connector 参数")
    + S(
        "vl-output", VL + "handlers.py", 4670, 4727, "Prefill 输出中的 disaggregated_params"
    )
    + "<p>NixlConnectorProtocol 为 Prefill 构建 do_remote_decode=true 等参数，Decode 使用 Prefill 响应的 kv_transfer_params。这里的 NIXL pull、Mooncake push、LMCacheMP 共享缓存是 adapter 声明的不同契约，不能推广为所有 connector 都有同样的每请求参数或传输方向。</p>",
)

add(
    "pd-vllm-decode",
    "vLLM Decode：恢复交接参数与本地执行分支",
    S(
        "vl-decode",
        VL + "handlers.py",
        3978,
        4026,
        "prefill_result、kv_transfer_params 与条件聚合分支",
    )
    + S("vl-params", VL + "handlers.py", 4536, 4582, "健康探测与交接参数构建")
    + "<p>Decode 从 prefill_result.disaggregated_params 提取 kv_transfer_params；健康探测可跳过真实交接，因此探测成功不能证明 PD 数据面正常。路由器授权的 bypass 会使 Decode worker 在该请求上执行聚合模式。多模态/外部 encoder 的限制需要按代码分支检查，不能仅以文本 PD 成功推断所有协议组合都支持。</p>",
)

add(
    "pd-trt",
    "TensorRT-LLM：context_only → generation_only",
    S("trt-prefill", TR + "handlers.py", 146, 218, "PrefillHandler 的单结果与取消处理")
    + S(
        "trt-decode",
        TR + "handler_base.py",
        597,
        647,
        "Decode 反序列化上下文并切换 generation_only",
    )
    + S(
        "trt-context",
        TR + "handler_base.py",
        815,
        849,
        "context_only、disagg_request_id 与上下文交接",
    )
    + S(
        "trt-bypass",
        TR + "handler_base.py",
        1098,
        1120,
        "conditional bypass 使用 context_and_generation",
    )
    + "<p>TRT 使用 codec 编码/恢复引擎上下文参数和 request ID。聚合模式为 context_and_generation；拆分模式中 Prefill 负责 context_only，Decode 恢复后使用 generation_only。已处理的多模态 embedding handles 需要清理，避免重复导入。该协议不能机械替换成 SGLang 的 room 或 vLLM 的 kv_transfer_params。</p>",
)

add(
    "pd-conditional",
    "条件 PD：评估 Decode 本地 Prefill 是否更划算",
    S(
        "conditional-preview",
        P + "conditional_bypass.rs",
        31,
        75,
        "只在符合条件的 KV Decode 上 preview",
    )
    + S("policy", "lib/kv-router/src/conditional_disagg.rs", 149, 167, "ISL 绝对与比例双阈值")
    + S(
        "conditional-admit",
        P + "conditional_bypass.rs",
        100,
        147,
        "Decode busy gate 与保留原 preview 选择",
    )
    + "<p>策略使用净新增 token、缓存覆盖与可用负载信号决定是否跳过远端 Prefill。ISL Bounding 同时检查有效 ISL 小于绝对阈值以及有效 ISL / max(prompt_tokens,1) 小于比例阈值（均为严格小于）。显式指定 Prefill 实例会阻止该 bypass。</p><p>Decode busy gate 启用而所需容量信息未知时不会贸然放行；成功分支提交的是同一 preview 路由计划，避免决策看中一个实例却派发另一个。需要相应缓存事件与后端本地执行能力，不能仅改一个阈值就认为功能可用。</p>",
)

add(
    "pd-cancel",
    "取消与失败：独立 Prefill 生命周期的理由",
    S("independent", P + "mod.rs", 711, 719, "保留 ID / metadata，使用独立取消上下文")
    + S("reject", P + "handoff.rs", 259, 276, "仅明确容量拒绝会取消 Prefill")
    + S(
        "cleanup",
        "lib/llm/src/kv_router/routing_host/cancellation.rs",
        18,
        43,
        "取消后的 staged KV cleanup 预算",
    )
    + '<p>取消不是把两个任务一同 abort。Decode 明确 CapacityExhausted 表示接收侧未准入，可以取消 Prefill；网络错误具有歧义，不能假定远端没有创建接收状态。客户端已退出时，携带 staged KV 的 Decode 仍可能获准进入清理路径。</p><p>独立取消上下文仍保留逻辑请求 ID 和 metadata，并传播首响应 guard。当前 cleanup 有共享 120 秒预算；这不是对 GPU 回收时延的保证。更完整的终态、迁移与清理责任见<a href="reliability.html">可靠性与流式生命周期</a>。</p>',
)

add(
    "pd-timing",
    "观测源码：Prefill、首 token 与完成时点",
    S(
        "timing-start",
        "lib/llm/src/protocols/common/timing.rs",
        258,
        270,
        "Prefill start / first token / Decode first token",
    )
    + S(
        "timing-duration",
        "lib/llm/src/protocols/common/timing.rs",
        315,
        326,
        "排队与 Prefill 时长的计算",
    )
    + S(
        "guard-start",
        "lib/llm/src/kv_router/routing_host/request_guard.rs",
        315,
        355,
        "按阶段记录 start，按非空 token 记录 first token",
    )
    + table(
        ["观测", "计算 / 触发", "解读边界"],
        [
            [
                "prefill_wait_time_ms",
                "prefill_start − request_received",
                "路由器观察的请求到 Prefill 起点时间；不是纯引擎排队",
            ],
            [
                "prefill_time_ms",
                "first_token_time − prefill_start",
                "包含相关调度与通信；不是 CUDA kernel 时间",
            ],
            [
                "first_token / decode_first_token",
                "observe_tokens 看见 new_tokens > 0",
                "Bootstrap 空 chunk 不触发；OnceLock 首次写入生效",
            ],
            [
                "prefill_complete",
                "结果/流观察器的不同位置",
                "Completed 与 Bootstrap 采样边界不同，不能当统一引擎完成信号",
            ],
        ],
    )
    + "<p>不能把毫秒字段直接代入以秒为单位的 Prometheus 查询；缺失时间点返回 None，并不是实际耗时为零。</p>",
)

add(
    "pd-transfer-metric",
    "KV transfer estimated latency 到底测到了什么",
    S(
        "transfer-timing",
        "lib/llm/src/protocols/common/timing.rs",
        532,
        547,
        "Prefill complete 与 Decode first token 的差值",
    )
    + S(
        "transfer-hist",
        "lib/llm/src/kv_router/metrics.rs",
        1146,
        1153,
        "KV transfer estimate 的 Histogram 与 buckets",
    )
    + S(
        "record",
        "lib/llm/src/kv_router/routing_host/request_guard.rs",
        378,
        405,
        "请求生命周期结束时一次性记录",
    )
    + "<p>指标为 <code>router_kv_transfer_estimated_latency_seconds</code>，计算 <code>decode_first_token.saturating_duration_since(prefill_complete)</code>。它可能包含路由派发、网络、接收等待、传输和首步 Decode。源码将其描述为传输延迟上界估计，但这不是对 NIXL 传输区间的直接打点。</p>"
    + note(
        "Bootstrap 重叠执行或 observer 调度可能使观察到的 Prefill complete 晚于 Decode first token，此时饱和差值为 0；缺任一时间点则不记录。不能把所有样本解释为严格的物理传输上界，更不能用零值证明没有传输。",
        True,
    )
    + '<p>guard 对已派发请求执行一次 metrics 记录，不能默认这些样本全部来自成功请求。分位数还受 buckets、实例/模型标签和聚合方法影响。要隔离实际 KV 数据面耗时，结合 engine / connector 传输统计与设备网络证据，见<a href="storage-events.html">Storage &amp; Events</a>。</p>',
)

add(
    "pd-trace",
    "Trace：请求串联与数据面覆盖是两项检查",
    S(
        "worker-attribution",
        "lib/llm/src/protocols/common/timing.rs",
        441,
        469,
        "Prefill / Decode worker 与 DP rank 归因",
    )
    + S("decision-log", P + "conditional_bypass.rs", 170, 198, "条件路由 debug 日志的诊断字段")
    + '<p>从 Frontend request span 沿 Runtime metadata、backend context 到 engine tracing，逐段检查 parent / link / request ID。Completed 路径的 worker_link 与 Bootstrap 路径不保证具备完全相同的前驱关系。共享 Request ID、同一 Trace ID、span link 和引擎内部传输 span 是不同证据。</p><p>条件路由日志包含 request_id、worker_id、dp_rank、prompt/net_new/overlap/cached_tokens、负载与 gate 结果；需要对应 debug 日志等级。不要臆造一个未定义的条件路由 Prometheus counter。详细开关与既有覆盖缺口见<a href="pd-trace.html">PD 全链路 Trace 专题</a>。</p>',
)

add(
    "pd-configuration",
    "部署接入：逐层准备与验证",
    table(
        ["工作", "检查项", "完成证据"],
        [
            [
                "协议兼容",
                "model/tokenizer、backend/engine/connector 版本、KV dtype/layout、TP/PP/DP、LoRA、并行采样和多模态能力",
                "配置与引擎支持矩阵一致，真实请求可完成",
            ],
            [
                "角色与发现",
                "两类 worker 发布相同目标模型、正确角色和端点，绑定为 Active",
                "实际选中的 Prefill / Decode worker ID 可归因",
            ],
            ["请求面连通", "Frontend 到两类 worker 的请求端点；直接路由时实例 pin", "非健康探测请求完成且流式终态正确"],
            [
                "交接与数据面",
                "Bootstrap / connector 监听与地址可达、NIXL/UCX/网卡/设备可见性",
                "传输实际发生，KV 与 token 结果正确",
            ],
            ["缓存与容量状态", "KV events 采集和订阅、块大小/容量配置", "路由状态更新且不混同监控抓取"],
            [
                "观测出口",
                "Frontend/backend tracing、engine tracing、metrics 端口与 Collector/Prometheus",
                "同一真实请求可定位两阶段，指标有样本",
            ],
            ["故障语义", "Prefill/Decode 拒绝、晚到错误、取消和清理", "按授权实验，验证终态与资源恢复；本文未执行"],
        ],
    )
    + "<p>示例入口：<code>examples/backends/sglang/launch/disagg.sh</code> 与 <code>examples/backends/vllm/launch/disagg.sh</code>；这些脚本提供当前版本参数参考，不应直接覆盖已有部署。工作负载可先验证文本单请求，再验证实际并发、长上下文与扩缩容。</p>",
)

add(
    "pd-ack",
    "结合现有 ACK 记录：如何确认真的走 PD",
    '<p>用户保存的<a href="ack-LB97i2CZNR/dynamo-test-deployment.md">部署快照</a>描述 <code>nhss-job/dynamo-qwen38-27b-mixed-demo</code>：Frontend、Prefill、Decode、aggregated 各 1，私有 SGLang runtime 1.4.1，TP=4，Qwen3.8-27B。它是混合拓扑的历史证据，不能只看见两种角色的 Pod 就断言流量均走 PD。</p>'
    + table(
        ["已有记录", "在 PD 分析中的作用", "需要补充的运行证据"],
        [
            ["请求 TCP / 事件 ZMQ", "Request Plane 与缓存状态分别连通", "实际 endpoint、订阅与选中实例"],
            ["Bootstrap 12345", "SGLang 的 KV 协调入口", "room、Prefill/Decode 角色与数据面成功"],
            ["worker metrics 9090 / NIXL 19090", "业务/引擎与传输观测出口", "真实暴露指标、采集状态、请求前后变化"],
            ["engine KV events 5557", "缓存状态发布", "接收方状态更新；它不是 KV 张量传输端口"],
            ["聚合与 igd-* 可见", "存在其他候选端点", "真实请求 worker 归因，排除聚合路径与健康探测假交接"],
        ],
    )
    + '<p>上述端口均来自已保存快照，未重新连接集群验证；当前代码的自动激活、条件 PD 和指标细节不能直接承诺在该私有 1.4.1 镜像中完全相同。继续沿用<a href="ack-LB97i2CZNR/request-plane-trace-checklist.html">ACK Request Plane Trace 工作清单</a>，补充真实 PD 请求证据后再作性能结论。</p>',
)

add(
    "pd-method",
    "阅读与实战顺序：从一条请求走到性能结论",
    flow(
        [
            "确认版本 / 激活 / worker 角色",
            "识别 Completed 或 Bootstrap",
            "跟踪元数据与真实 KV 交接",
            "核对 token / 终态 / cleanup",
            "关联 Trace 与阶段指标",
            "对照 workload 评估收益",
        ]
    )
    + "<p>TTFT 的关键路径大致包含 Prefill 排队与计算、交接/重叠协调、Decode 排队与首步生成；存在重叠时不能把所有阶段 wall time 直接相加。ITL 是否改善，需要看 Decode 批处理、KV 压力与传输竞争；同时记录两池负载、输入输出长度和实际网络吞吐。</p>"
    + table(
        ["源码回归场景", "应验证的语义"],
        [
            ["激活 Pending / 解绑重绑", "不在缺 Bootstrap 时误发 Decode，错误分类与预算正确"],
            ["Bootstrap 后晚到 Prefill 错误", "持续观察，不能先输出最终成功"],
            ["Decode 容量拒绝 / 模糊传输错误", "仅明确拒绝取消 Prefill，其余保留清理所需生命周期"],
            ["EOS / context_only 缺失参数", "终态可直接返回，非终态缺上下文应报错"],
            ["条件 PD 与显式 pin / SGLang n>1", "尊重决策边界与后端能力"],
            ["阶段时间缺失 / 反序", "不记录缺失估计，饱和为零且不误读为无传输"],
        ],
    )
    + '<p>本次验证范围为文档生成、固定版本源码摘录、离线链接与浏览器交互；没有运行上述推理回归场景。相邻专题：<a href="reliability.html">可靠性</a> · <a href="storage-events.html">Storage &amp; Events</a> · <a href="planner.html">Planner</a>。</p>',
)

add(
    "pd-references",
    "同版本官方文档与源码分工",
    "<p>以下官方仓库文档均固定在本章代码提交，避免把网站 latest 的变化误当成本地版本行为。架构页解释拆分动机，Router 页解释激活与编排，后端页解释引擎协议，条件 PD 页标明能力边界；有差异时回到本章逐行源码。</p>"
    + table(
        ["官方文档", "核对重点"],
        [
            [
                f'<a href="https://github.com/ai-dynamo/dynamo/blob/{PIN}/docs/fern/pages/developer-guide/knowledge-base/concepts/system-architecture/disaggregated-serving.md">Disaggregated Serving 架构</a>',
                "Prefill / Decode 拆分与 KV 数据流",
            ],
            [
                f'<a href="https://github.com/ai-dynamo/dynamo/blob/{PIN}/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/disaggregated-serving.md">Router disaggregated serving</a>',
                "自动激活、角色绑定；Pending 错误分类以当前实现为准",
            ],
            [
                f'<a href="https://github.com/ai-dynamo/dynamo/blob/{PIN}/docs/fern/pages/developer-guide/knowledge-base/modular-components/backends/sglang/disaggregation.md">SGLang disaggregation</a>',
                "引擎 Bootstrap 协调与共享 Frontend 选点顺序需分开理解",
            ],
            [
                f'<a href="https://github.com/ai-dynamo/dynamo/blob/{PIN}/docs/fern/pages/developer-guide/advanced-customizations/conditional-disaggregation.md">Conditional Disaggregation</a>',
                "实验功能；KV events、策略、后端支持（SGLang 尚不支持）",
            ],
        ],
    ),
)

result = page(
    "Prefill / Decode 分离：架构、协议与观测源码",
    "从共享 PrefillRouter 状态机，走到三种引擎交接、并行生命周期和阶段指标。",
    "".join(parts),
    nav,
)
result = result.replace(
    '<span class="pill">2026-10-28</span>', '<span class="pill">2026-10-29</span>'
)
result = result.replace("首个源码专题 v0.1", "Prefill / Decode 分离 v1.0")
(OUT / "pd-serving.html").write_text(result)
(OUT / "pd-serving-source-manifest.json").write_text(
    json.dumps(
        {
            "revision": PIN,
            "analysis_date": "2026-10-29",
            "scope": "PrefillRouter activation, Completed/Bootstrap handoff, engine protocols, conditional disaggregation, cancellation and observability",
            "runtime_verified": False,
            "excerpts": base["manifest"],
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(f"Built PD chapter: {len(base['manifest'])} excerpts, {len(nav)} sections.")
