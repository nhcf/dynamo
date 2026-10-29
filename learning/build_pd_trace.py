#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build PD trace capability analysis; source-only, no deployment changes."""
import json
import runpy
from pathlib import Path

OUT = Path(__file__).resolve().parent
x = runpy.run_path(str(OUT / "build_tutorial.py"))
src, table, flow, note, section, page = [
    x[n] for n in ["src", "table", "flow", "note", "section", "page"]
]
x["manifest"].clear()


def S(key, path, start, end, title):
    return src("pd-" + key, path, start, end, title)


b = section(
    "pd-verdict",
    "01 / 结论：请求主链可串，跨数据面全覆盖需补齐",
    """<p><strong>当前 Dynamo 有 PD 请求主链的分布式上下文传播与部分因果关联能力，但不能把“Request Plane + KV 存储 + Events + NIXL 底层传输”整体视为已经开箱即用的一棵完整 OTel trace 树。</strong></p><p>本章固定代码 <code>2f296b592</code>，分析日期 2026-10-29。源码能力不同于线上已验证能力；未运行 PD 请求、抓取线上 Trace 或修改部署。外部引擎及 NIXL 版本可能独立变化。</p>"""
    + table(
        ["范围", "支持程度", "判断依据"],
        [
            [
                "HTTP Frontend → Runtime 请求传输 → Worker",
                "已有传播契约",
                "提取 W3C Context、注入请求 Header、接收侧 set_parent",
            ],
            [
                "Prefill → Decode handoff",
                "支持特定路径的 OTel Link",
                "Completed handoff 携带 worker_trace_link；Bootstrap 路径单独处理",
            ],
            [
                "Engine generate 与引擎内部",
                "框架与参数接口已有；内部覆盖依 backend",
                "engine.generate、Context.trace_headers、各引擎调用参数",
            ],
            [
                "KV 存储/缓存操作",
                "部分本地 instrumentation / ID / 计时；未形成所审路径的统一跨异步 trace 契约",
                "调度请求有 request_id/uuid；后台批处理和 TimingTrace 不能自动形成因果树",
            ],
            [
                "KV / FPM / 路由状态 Event Plane",
                "通用传输没有自动 W3C 传播",
                "EventEnvelope 无 Context；NATS publish 无 headers；ZMQ frames 无额外 trace metadata",
            ],
            [
                "NIXL 传输",
                "遥测与局部诊断可用；不是通用请求级 trace 保证",
                "KV transfer executor 负责 create/post/异步完成；需要明确绑定发起请求与完成回调",
            ],
        ],
    )
    + note(
        "判断“全链路”要同时满足：上下文传播、操作 span、正确开始/完成边界、异步关联与导出。能搜到同一个 request_id，或者能画出传输耗时直方图，都不足以证明这些条件。"
    ),
)
b += section(
    "pd-map",
    "02 / 把请求控制流与 KV 数据流分开",
    """<div class="diagram"><div class="lane"><b>Request Plane</b><span>客户端 W3C Context → Frontend HTTP span → Router / Dispatch → Prefill Worker → Decode Worker → SSE 返回</span></div><div class="lane"><b>Engine KV 数据面</b><span>Prefill KV cache ↔ Engine connector / NIXL ↔ Decode KV cache；具体引擎可能使用 pull、push 或异步 bootstrap</span></div><div class="lane"><b>KV 分层存储</b><span>GPU ↔ CPU ↔ SSD / 远端，由所用 KVBM、引擎 connector 或其他缓存系统负责</span></div><div class="lane"><b>Events</b><span>Worker / Router / FPM publisher → NATS 或 ZMQ → 索引/订阅消费者；不是请求 RPC</span></div></div><p>PD 分离不要求所有 KV 都经过 Dynamo KVBM。vLLM/SGLang/TRT-LLM 的 connector、Dynamo KVBM、LMCache 等是不同实现边界；不能用一个 executor 的行为代替所有引擎数据面。</p><p>Span Link 表示因果关系，不会把两个已有 Trace ID 强行合并。共享缓存、批量搬运和广播事件通常是多对多关系，不适合全部硬挂成单个请求的子 span。</p>""",
)
b += section(
    "pd-request",
    "03 / Request Plane 已有的上下文契约",
    S(
        "root",
        "lib/runtime/src/logging.rs",
        623,
        665,
        "Frontend 根/子 HTTP span：提取上游并 set_parent",
    )
    + S(
        "inject",
        "lib/runtime/src/logging.rs",
        852,
        873,
        "注入 traceparent / tracestate / 请求 ID",
    )
    + S(
        "dispatch",
        "lib/runtime/src/pipeline/network/egress/addressed_router.rs",
        1045,
        1073,
        "TCP request-plane dispatch 明确注入 Header",
    )
    + S(
        "ingress",
        "lib/runtime/src/pipeline/network/ingress/shared_tcp_endpoint.rs",
        286,
        337,
        "接收侧建立并 instrument handle_payload",
    )
    + "<p>这条链路是实际传输和处理代码中的显式行为，不只是控制台日志格式。缺少入站 Context、采样关闭、异步任务没有继承当前 span，仍可能造成独立根或不可见片段。</p>"
    + note(
        "Router 在请求路径上不代表每个排队/选择细节已有独立 span。当前 lifecycle 代码明确把更细的 router queue/selection 阶段留给后续 instrumentation；不能照旧文档示意树承诺所有 span 名称稳定存在。"
    )
    + S(
        "lifecycle", "lib/runtime/src/telemetry.rs", 122, 147, "生命周期边界只覆盖粗粒度 runtime 阶段"
    ),
    "开启 OTLP 能否自动生成每个函数的 span？",
    "不能。Exporter 只导出已创建并保留的 span，缺少埋点和上下文边界的操作仍然不可见。",
)
b += section(
    "pd-handoff",
    "04 / PD handoff：前驱信息与真正的 OTel Link",
    S(
        "autospan",
        "lib/backend-common/src/adapter.rs",
        227,
        307,
        "engine.generate：span、前驱 Link 和自身身份",
    )
    + S(
        "handoff",
        "lib/llm/src/kv_router/prefill_router/mod.rs",
        127,
        157,
        "Completed 与 Bootstrap 两类 handoff",
    )
    + S(
        "spawn",
        "lib/llm/src/kv_router/prefill_router/handoff.rs",
        50,
        61,
        "派生 Prefill future 继承当前 span",
    )
    + """<p>Completed 分支将 Prefill 的 worker_link 赋给 Decode 请求的 migration_link。尽管字段名称带 migration，这里也用于 PD handoff。EngineAdapter 解析 trace/span ID 后调用 add_link，同时写 migration_trace_id / migration_span_id 方便日志读者关联。</p><p>Bootstrap 分支写 bootstrap_info 和路由字段，不经过同一段 migration_link 赋值。它仍可依赖共同的请求上下文串联，但不应据 Completed 分支声称每种后端 handoff 必然产生同样的前驱 Link。</p>"""
    + note(
        "同一 trace 的父子传播和跨操作 Span Link 是两种能力。验收需要查 parent_span_id 与 links，不能仅凭 UI 画在同一页面上判断因果关系。"
    ),
    "migration_link 只用于 Worker 故障重试吗？",
    "不是。所读 into_decode_request 的 Completed 分支也赋值该字段，明确用于 Prefill → Decode 关联。",
)
b += section(
    "pd-engines",
    "05 / 下沉到引擎：有接口，粒度依后端",
    S(
        "context",
        "lib/bindings/python/rust/context.rs",
        432,
        462,
        "Context 优先从 engine.generate 构造引擎 Header",
    )
    + table(
        ["后端", "Dynamo 已做什么", "不能直接推出什么"],
        [
            [
                "vLLM",
                "取 context.trace_headers，再传 engine_client.generate(trace_headers=...)",
                "不能推出 model-worker 内的每次 NIXL create/post/complete 已有同请求 span",
            ],
            [
                "SGLang",
                "enable_trace 时将 external_trace_header 放进 native generate 请求",
                "不是所有传输后端都拥有同粒度的 KV span；需要对应 SGLang 版本与 connector 核对",
            ],
            [
                "TensorRT-LLM",
                "传 generate_async 的 trace_headers 与 disaggregated_params",
                "内部执行、KV fetch 和底层网络 span 依赖 TRT-LLM 实现及配置",
            ],
            [
                "自定义/统一 Backend",
                "Context SpanProxy / telemetry.start_span 提供扩展点",
                "README 的示例调用不是内置系统已经自动执行该埋点的证据",
            ],
        ],
    )
    + S(
        "vllmcontext",
        "components/src/dynamo/vllm/handlers.py",
        4138,
        4153,
        "vLLM Decode 从 Context 取 Header",
    )
    + S(
        "vllmcall",
        "components/src/dynamo/vllm/handlers.py",
        3527,
        3539,
        "传给 vLLM engine.generate",
    )
    + S(
        "sglang",
        "components/src/dynamo/sglang/request_handlers/llm/decode_handler.py",
        642,
        660,
        "SGLang trace gate",
    )
    + S(
        "trtllm",
        "components/src/dynamo/trtllm/request_handlers/handler_base.py",
        1318,
        1337,
        "TRT-LLM generate 参数",
    )
    + """<p>本地另一个 SGLang checkout 的 Mooncake sender 存在 TraceReqContext（按 bootstrap_room 建模），但它是独立版本和特定传输实现；不能据此证明当前 Dynamo 镜像中的 NIXL 路径全覆盖。本章不把该外部 checkout 当作 Dynamo 固定提交的事实。</p>""",
)
b += section(
    "pd-events",
    "06 / Event Plane 的关键断点：不是 RPC Header",
    S(
        "envelope",
        "lib/runtime/src/transports/event_plane/traits.rs",
        12,
        25,
        "EventEnvelope 的全部字段",
    )
    + S(
        "publisher",
        "lib/runtime/src/transports/event_plane/mod.rs",
        662,
        683,
        "编码 envelope：没有注入 Context",
    )
    + S(
        "nats",
        "lib/runtime/src/transports/event_plane/nats_transport.rs",
        36,
        65,
        "事件 NATS 传输只传 envelope bytes",
    )
    + S(
        "publish",
        "lib/runtime/src/distributed.rs",
        727,
        738,
        "底层使用 publish，而不是带 Context 的 headers publish",
    )
    + S(
        "zmq",
        "lib/runtime/src/transports/event_plane/zmq_transport.rs",
        280,
        301,
        "ZMQ topic / publisher / sequence / frame",
    )
    + """<p>这不是说 NATS 协议不能传 Header，也不是说用户 payload 不能自带 Trace Context；是当前通用 Event Plane 没有替业务自动做。Request Plane 的 NATS ingress 有上下文处理，不代表同样使用 NATS 的 KV/FPM 事件天然共享该逻辑。</p>"""
    + S(
        "kv",
        "lib/kv-router/src/protocols.rs",
        1314,
        1323,
        "KV cache 事件无标准 trace metadata",
    )
    + S(
        "sequence",
        "lib/kv-router/src/protocols.rs",
        1250,
        1259,
        "ActiveSequenceEvent 有 request_id，但它不是 W3C Context",
    )
    + note(
        "publisher_id / sequence / block hash 用于生产者身份、顺序与缓存索引；request_id 可帮助检索。它们不能代替 trace_id + parent span context + trace flags，消费者不会因此自动继承生产者 span。",
        True,
    ),
    "把 NATS 的 exporter 打开能修复这个断点吗？",
    "不能自动修复。还要定义携带上下文的消息契约、在生产和消费边界创建 span / Link，并处理批量和广播语义。",
)
b += section(
    "pd-storage",
    "07 / KV 存储：局部 instrumentation 不等于跨异步串联",
    S(
        "sched",
        "lib/llm/src/block_manager/connector/scheduler.rs",
        39,
        80,
        "局部 debug instrument 与调度消息",
    )
    + S(
        "request",
        "lib/llm/src/block_manager/connector/protocol.rs",
        93,
        113,
        "LeaderTransferRequest：request_id / uuid 与调度信息",
    )
    + S(
        "timing", "lib/kvbm-engine/src/offload/batch.rs", 24, 60, "TimingTrace 只是本地阶段时钟"
    )
    + """<p>现有代码并非“完全没有 trace”：调度函数有 request_id/operation_id 字段和 debug instrumentation。但将消息放到后台 scheduler/worker 后，以上序列化请求没有 W3C Context；仅保留请求 ID 不会恢复分布式父子关系。</p><p>新 offload pipeline 的 TimingTrace 记录 enqueued/policy/precondition/batched/transfer 的 Instant，适合阶段耗时分析；它不等于 OTel TraceContext，也没有自动指向一个前端请求。</p><p>缓存块可被多个请求复用，offload 批次可跨请求聚合，eviction/prefetch 还可能由后台策略触发。正确分析应分别考虑当前请求等待、共享块生产历史和后台存储动作，必要时用多条 Span Link，而不是捏造单一 parent。</p>"""
    + note(
        "本结论限定于已核对的调度消息、offload timing、transfer executor 和通用事件传输。其他第三方缓存系统或自定义插件可以自行埋点；本章没有证明它们一律不支持追踪。"
    ),
)
b += section(
    "pd-nixl",
    "08 / NIXL：传输可观测与请求 trace 的区别",
    S(
        "nixl",
        "lib/kvbm-physical/src/transfer/executor/nixl.rs",
        350,
        380,
        "创建、提交、异步完成 notification",
    )
    + S(
        "xferctx",
        "lib/kvbm-physical/src/transfer/context.rs",
        210,
        237,
        "TransferContext 是资源/完成通知上下文",
    )
    + S(
        "complete",
        "lib/kvbm-physical/src/transfer/notifications/nixl_events.rs",
        152,
        192,
        "后台完成事件按 UUID 触发",
    )
    + """<p>TransferContext 名字中的 Context 是 NIXL/CUDA/运行时资源上下文，不是 OTel Context。所读 executor 使用 create_xfer_req / post_xfer_req，pending 时返回异步完成 notification；没有在这段逻辑中自动建立请求关联的跨端传输 span。</p><p>这里是 Dynamo KVBM physical transfer 的示例路径，不代表 vLLM/SGLang/TRT-LLM 都调用这个 executor。引擎自带 PD connector 需要在引擎源码或实际 Trace 中再核对。</p>"""
    + table(
        ["信息", "能够说明", "不能自动证明"],
        [
            [
                "NIXL bytes/requests/latency telemetry",
                "传输了多少、成功失败与耗时分布",
                "哪个请求 span 是这次传输的父节点",
            ],
            ["引擎 KV transfer Histogram", "引擎暴露的传输时延统计", "某次请求的 send/recv span 链"],
            [
                "NVTX / Nsight timeline",
                "GPU/CPU/传输的性能时间线",
                "自动出现在 Tempo/Jaeger 同一分布式 Trace",
            ],
            [
                "completion UUID / transfer ID",
                "本地或协议层异步完成关联",
                "跨进程 OTel Context 已传到接收/完成处理器",
            ],
        ],
    )
    + note(
        "NIXL 有遥测和新版本的专用 tracing/NVTX 能力，不能笼统说“NIXL 没有 trace”。这里的缺口是与 Dynamo 请求 Context 自动接合的契约，不是性能观测手段不存在。"
    )
    + S(
        "nop",
        "components/src/dynamo/vllm/nixl_telemetry.py",
        18,
        40,
        "vLLM NIXL capture-only 插件不是 OTel exporter",
    ),
)
b += section(
    "pd-config",
    "09 / 仅配置即可实现的部分",
    """<p>所有参与的 Frontend、独立 Router 和 Worker 都应配置到可达 Collector。只在 Frontend 开启导出无法保证下游服务发出 span。</p><pre><code># 基础 runtime traces/logs 导出（每个参与进程）
OTEL_EXPORT_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
DYN_LOGGING_CONSOLE_FORMAT=jsonl

# 不同角色使用不同 OTEL_SERVICE_NAME
# 新基线中的可选粗粒度 lifecycle spans
DYN_LIFECYCLE_TRACE_ENABLED=1</code></pre><p>引擎内部 exporter、SGLang enable_trace / OTLP 参数以及依赖须按实际镜像版本配置。生命周期开关只是粗粒度 runtime 边界，不会自动补齐 storage/event/NIXL span。</p>"""
    + S("enabled", "lib/runtime/src/telemetry.rs", 450, 452, "生命周期开关独立存在")
    + S(
        "filters",
        "lib/runtime/src/logging.rs",
        1533,
        1543,
        "lifecycle target 的 OTel filter",
    )
    + """<p>联调时使用一致的父采样策略，确认网关保留 traceparent/tracestate；x-request-id 只能辅助关联，不会自动让独立根共享 Trace ID。正式采样比例与每 token/每 block 埋点粒度需要测量后再定。</p>""",
)
b += section(
    "pd-extension",
    "10 / 要覆盖 Storage & Events，需要补什么",
    table(
        ["边界", "建议实现", "关键语义"],
        [
            [
                "存储操作入口",
                "从当前请求捕获 SpanContext，创建 cache.lookup/load/save/offload 等实际操作 span",
                "区分当前请求 miss 等待、异步写回与共享 prefetch",
            ],
            [
                "后台任务 / 队列",
                "在消息中保存关联 Context 或显式 Link；任务运行时恢复/attach",
                "spawn/thread/task-local 不会自动跨任意消息序列化",
            ],
            [
                "通用 Event Plane",
                "定义可选、版本化 trace metadata，生产/消费两侧有 span",
                "兼容现有 envelope/codec；不能只改 NATS 而漏掉 ZMQ",
            ],
            [
                "NIXL connector",
                "将 request ↔ transfer_id ↔ remote request/notification 绑定",
                "span 结束于完成/失败/取消，不是 post 调用返回",
            ],
            ["批处理与共享块", "每批一个操作 span，links 指向来源/等待者", "避免把共享资源强归属到一个请求"],
            [
                "指标 / 日志 / trace",
                "低基数指标保留；高基数 request/transfer ID 放 span/log",
                "需要单请求点击跳转时评估 exemplar；不能从汇总指标反推 span",
            ],
        ],
    )
    + S(
        "extension",
        "components/src/dynamo/common/backend/telemetry.py",
        39,
        85,
        "现成 engine-author telemetry facade",
    )
    + S(
        "child",
        "lib/bindings/python/rust/context.rs",
        392,
        430,
        "动态子 span 在有效 parent Context 下创建",
    )
    + """<pre><code># 扩展点演示，不是当前系统自动执行的内置埋点
with telemetry.start_span(context, "kv.load", tier="cpu") as span:
    # 必须等待实际完成，而不是仅发起操作就退出 with
    await wait_for_kv_load_completion()
    span.set_attribute("bytes", transferred_bytes)

# 跨后台线程/进程时还需显式传播 Context；不能把此同步示例
# 直接套到批量 RDMA、eviction 或异步事件消费者。</code></pre>"""
    + note("这是实施建议，本次没有修改 Dynamo 协议、KV 管理器或 NIXL connector，也没有提交架构变更。"),
)
b += section(
    "pd-acceptance",
    "11 / 如何验收“串起来了”",
    table(
        ["测试场景", "必须核对的证据"],
        [
            [
                "PD cache miss",
                "同一个请求的 Frontend、Prefill、Decode Context；parent/links 与 worker identity",
            ],
            [
                "已完成 Prefill handoff",
                "前驱 worker link 出现在 Decode engine span 的 links；不要只看同名属性",
            ],
            ["Bootstrap/并行 handoff", "分别验证其上下文路径，不能借用 Completed 分支的结论"],
            ["跨端 KV transfer", "发起、远端接收/处理、完成/超时 span 与 transfer_id 的实际关联"],
            ["CPU/SSD/远端 cache miss", "命中/未命中、load起止、等待时间和数据位置，是否有真实 span"],
            ["KV stored/remove 事件", "生产与消费 Context/Link，事件去重/重放/批量后的因果关系"],
            ["共享 prefix、多并发、批量迁移", "没有串错请求；能表达一个动作影响多个请求"],
            ["失败/取消/重试", "span 完成状态正确，不出现永不结束或仅凭请求 ID 拼起来的链"],
        ],
    )
    + """<p>先用低并发受控请求与测试父 Context，分别触发 miss、hit、offload、传输失败；检查原始 OTLP 数据中的 trace_id、span_id、parent_span_id 和 links。Collector 收到数据只证明导出链路，不能证明应用的因果关系。</p><p>本次没有执行以上实验；结论是源码能力和缺口分析，不是对某个已部署镜像的端到端验证。</p>""",
)
b += section(
    "pd-evidence",
    "12 / 证据与阅读范围",
    """<ul><li><a href="pd-trace-source-manifest.json">固定提交的源码摘录清单</a>：路径、范围、文件 SHA256。</li><li><a href="https://github.com/ai-dynamo/nixl/blob/main/docs/telemetry.md">NIXL 官方 telemetry 文档</a>：遥测与 exporter；main 文档随版本变化。</li><li><a href="https://github.com/ai-dynamo/nixl/blob/main/src/plugins/telemetry/README.md">NIXL telemetry 插件接口</a>：统计事件不自动成为 OTel span。</li><li><a href="https://github.com/vllm-project/vllm/blob/main/docs/features/nixl_connector_usage.md">vLLM 官方 NixlConnector 使用与指标</a>：引擎路径独立于 Dynamo physical executor。</li><li><a href="https://docs.nvidia.com/dynamo/dev/reference/observability/environment-variables">Dynamo 官方观测配置</a>：核对所部署版本的开关。</li><li><a href="observability.html">可观测性源码专题</a> · <a href="planner.html">Planner 专题</a> · <a href="index.html">教程首页</a></li></ul><p>结论主要依赖正向代码证据：明确的 Header 注入/提取、PD 前驱 Link、通用 envelope 字段、NATS/ZMQ 发布函数、存储调度消息与 NIXL 完成路径。局部缺少字段不作为全仓库绝对否定；自定义 payload、第三方缓存与其他引擎版本可额外实现能力。</p>""",
)
nav = [
    ("pd-verdict", "01 支持结论"),
    ("pd-map", "02 两类平面"),
    ("pd-request", "03 Request Plane"),
    ("pd-handoff", "04 PD handoff / Link"),
    ("pd-engines", "05 引擎内部"),
    ("pd-events", "06 Event Plane 断点"),
    ("pd-storage", "07 KV 存储"),
    ("pd-nixl", "08 NIXL 传输"),
    ("pd-config", "09 配置支持范围"),
    ("pd-extension", "10 扩展工作"),
    ("pd-acceptance", "11 验收方法"),
    ("pd-evidence", "12 源码证据"),
]
out = page(
    "PD 全链路 Trace：支持到哪里？",
    "核对 Request Plane、KV 存储、异步事件与 NIXL 边界，区分上下文传播、因果关联、局部计时与真正的全链路覆盖。",
    b,
    nav,
)
out = out.replace(
    '<span class="pill">2026-10-28</span>', '<span class="pill">2026-10-29</span>'
).replace("首个源码专题 v0.1", "PD Trace 源码专题 v0.1")
(OUT / "pd-trace.html").write_text(out)
(OUT / "pd-trace-source-manifest.json").write_text(
    json.dumps(
        dict(
            revision=x["PIN"],
            analysis_date="2026-10-29",
            scope="Request plane trace propagation and reviewed storage/event/NIXL boundaries",
            runtime_verified=False,
            excerpts=x["manifest"],
        ),
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print("Built PD trace analysis:", len(x["manifest"]), "source excerpts, 12 sections")
