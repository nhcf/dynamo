#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the remaining system-map chapters from pinned source evidence."""
import json
import runpy
from pathlib import Path

OUT = Path(__file__).resolve().parent
PIN = "9734418e5bc71027e6bd04a09314bf1765208c72"
base = runpy.run_path(str(OUT / "build_tutorial.py"))
base["src"].__globals__["PIN"] = PIN
src, table, flow, note, section, page = [
    base[n] for n in ["src", "table", "flow", "note", "section", "page"]
]
reports = []
DIAGRAMS = {
    "frontend": [
        ("HTTP / 模型", "Request → lifecycle / ready gate → canonical model → validate"),
        ("请求向下", "processor → token / MM → Router → backend / engine"),
        ("输出向上", "token stream → incremental parser / detokenizer → protocol / SSE"),
        ("观测与反馈", "RequestMetrics → registry / Trace summary；同源 frame → Pool Relay"),
    ],
    "runtime": [
        ("控制身份", "DistributedRuntime → namespace → component → endpoint / instance"),
        ("注册", "handler / metrics / health → request server → Discovery"),
        (
            "调用",
            "Context → control + payload → transport → receiver span → response stream",
        ),
        (
            "终止",
            "Stop / Kill → consumer reaction；endpoint withdraw / drain → unregister",
        ),
    ],
    "router": [
        (
            "状态输入",
            "KV events → prefix index；lifecycle → ActiveSequences → PromptRegistry",
        ),
        ("策略", "eligible candidates → filters → finite scorer costs → picker row"),
        ("准入", "policy class / DRR / queue → final select → capacity booking"),
        ("实际派发", "RoutingHost / RoutePlan → backend；response / Drop → cleanup"),
    ],
    "backends": [
        (
            "既有 Python",
            "main / worker_factory → Runtime endpoint → handler → engine API",
        ),
        ("共享 SDK", "Python BaseEngine / Rust LLMEngine → Worker → EngineAdapter"),
        (
            "Sidecar",
            "Dynamo planes → native adapter → engine gRPC / specific HTTP path",
        ),
        (
            "观测",
            "foreign registry + ComponentSnapshot + Worker lifecycle → exporter / router signals",
        ),
    ],
    "kv-cache": [
        ("逻辑所有权", "Mutable → Complete → registered Immutable；Weak does not pin"),
        ("层级协调", "G2 / G3 / remote search → Ready or session → hold / stage / onboard"),
        (
            "实际传输",
            "source guards + destination slots → TransferManager → completion → register",
        ),
        (
            "独立共享分配",
            "GMS server allocation → client mapping；layout / content publication differ",
        ),
    ],
    "global-routing": [
        ("现有选池", "Python Global Router grid / priority → local Router → worker"),
        ("跨域事实", "Discovery + KV / scheduler / Frontend frames → Pool Relay → WAN RPC"),
        (
            "视图工具",
            "global discovery client / PoolViews；consumer wiring and merge policy are separate",
        ),
        (
            "容量控制",
            "local Planner intent → Global Planner budget → CapacityManager → DGD",
        ),
    ],
    "validation": [
        ("语义验证", "unit / state tests → conformance → fake RPC / native protocol tests"),
        (
            "模拟",
            "workload + engine model + routing config → offline or live replay → report / telemetry",
        ),
        (
            "真实测量",
            "frozen workload + real deployment → AIPerf artifacts + engine / device evidence",
        ),
        (
            "决策",
            "classify success / error / pending → comparable SLO / goodput → explain limitations",
        ),
    ],
    "system-map": [
        ("请求", "Client → Frontend / pipeline → Router → backend → engine → stream"),
        (
            "KV 数据",
            "engine / logical ownership → connector / layout → physical transfer / storage",
        ),
        (
            "状态",
            "Discovery / KV / load events → indexes / projections / Pool Relay facts",
        ),
        (
            "控制与观测",
            "metrics / Trace → diagnosis or Planner → desired capacity → Operator / ready workers",
        ),
    ],
}


def chapter(slug, title, subtitle):
    global current, parts, nav
    current = (slug, title, subtitle)
    parts, nav = [], []
    base["manifest"].clear()
    add(
        "scope",
        "阅读范围与证据版本",
        p(subtitle)
        + note(
            "源码固定于 "
            + PIN
            + "（workspace 1.7.0），与 PD / 可靠性等新专题使用同一代码基线。本文分析实现和配置边界，未运行推理、性能实验或集群修改。历史部署与旧版章节须按版本对照。"
        ),
    )


def p(text):
    return "<p>" + text + "</p>"


def X(path, start, end, title):
    return src(
        current[0] + "-" + str(len(base["manifest"]) + 1), path, start, end, title
    )


def add(key, title, body, question=None, answer=None):
    key = current[0] + "-" + key
    title = f"{len(nav) + 1:02} / {title}"
    nav.append((key, title))
    parts.append(section(key, title, body, question, answer))


def docs(rows):
    return table(
        ["同版本官方仓库文档", "阅读用途"],
        [
            [
                f'<a href="https://github.com/ai-dynamo/dynamo/blob/{PIN}/{path}">{title}</a>',
                purpose,
            ]
            for path, title, purpose in rows
        ],
    )


def finish():
    slug, title, subtitle = current
    lanes = DIAGRAMS[slug]
    diagram_name = slug + "-architecture.mmd"
    diagram = "%% SPDX-License-Identifier: Apache-2.0\n%% Generated by build_remaining.py; do not edit.\nflowchart TB\n"
    html_lanes = []
    for i, (label, meaning) in enumerate(lanes):
        diagram += f'    subgraph S{i}["{label}"]\n        direction LR\n'
        nodes = meaning.split(" → ")
        for j, node in enumerate(nodes):
            diagram += f'        L{i}_{j}["{node}"]\n'
            if j:
                diagram += f"        L{i}_{j - 1} --> L{i}_{j}\n"
        diagram += "    end\n"
        html_lanes.append(
            f'<div class="lane"><b>{label}</b><span>{meaning}</span></div>'
        )
    (OUT / diagram_name).write_text(diagram)
    add(
        "diagram",
        "架构分层图与交叉阅读",
        '<div class="diagram">'
        + "".join(html_lanes)
        + "</div>"
        + p(
            f'<a href="{diagram_name}">Mermaid 图源</a>按职责分泳道；泳道之间不暗示已经实现自动接线。离线 HTML 无外部渲染依赖。<a href="system-map.html">系统地图</a> / <a href="index.html">全部章节</a>。'
        ),
    )
    result = page(title, subtitle, "".join(parts), nav)
    result = result.replace(
        '<span class="pill">2026-10-28</span>', '<span class="pill">2026-10-29</span>'
    )
    result = result.replace("首个源码专题 v0.1", "系统源码专题 v1.0")
    (OUT / (slug + ".html")).write_text(result)
    (OUT / (slug + "-source-manifest.json")).write_text(
        json.dumps(
            {
                "revision": PIN,
                "analysis_date": "2026-10-29",
                "scope": subtitle,
                "runtime_verified": False,
                "excerpts": base["manifest"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    reports.append(
        {
            "page": slug + ".html",
            "sections": len(nav),
            "excerpts": len(base["manifest"]),
        }
    )


# Module 02: frontend and protocol pipeline.
FE = "lib/llm/src/http/service/"
PRE = "lib/llm/src/preprocessor.rs"
chapter(
    "frontend",
    "Frontend 与协议流水线",
    "从 Python 启动、模型准入到 Rust HTTP、预处理、生成和流式投影，逐层核对协议语义与观测时点。",
)
add(
    "architecture",
    "Frontend 是模型与协议流水线的宿主",
    flow(
        [
            "CLI / FrontendConfig",
            "DistributedRuntime / PyO3",
            "ModelManager / Discovery",
            "HTTP 协议与语义校验",
            "模板 / token / Router / backend",
            "解析 / SSE / 生命周期指标",
        ]
    )
    + p(
        "Python 负责组装配置和扩展工厂，Rust 承担默认 HTTP 服务与大量 token 流水线。选择 Python chat processor 时，预处理/后处理的语义 owner 会变化，不能把默认 Rust 路径当作所有部署唯一实现。Frontend 可以内嵌 Router，但引擎计算与 KV 张量不因此搬进 Frontend。"
    ),
)
add(
    "startup",
    "启动与配置：显式跨越语言边界",
    X(
        "components/src/dynamo/frontend/main.py",
        370,
        418,
        "解析配置、传输 TLS 与 DistributedRuntime",
    )
    + X(
        "components/src/dynamo/frontend/main.py",
        435,
        481,
        "传给绑定的显式参数与 chat processor 工厂",
    )
    + p(
        "FrontendConfig 是前端配置的来源。传输 TLS 设置必须在 Runtime 初始化前可见，而前端业务参数通过绑定参数/结构显式传递。Frontend 不使用 worker 的 DYN_SYSTEM_PORT；HTTP API/metrics 与 worker system server 应分别确认。"
    ),
)
add(
    "models",
    "模型发现不是看见一个 endpoint 就立即使用",
    X("lib/llm/src/discovery/model_manager.rs", 1686, 1732, "按端点类型注册模型")
    + p(
        "ModelManager 为 chat、completions、embedding 等维护引擎入口。WorkerSet 以 namespace/component/endpoint/model/worker type 区分，当前准入要求边界规范化与 tokenizer overrides 后的 MDC checksum 相等。首个有效配置保留为 incumbent，冲突 worker 不得污染已有流水线；旧成员全部消失后才允许后继 cohort。"
    )
    + note(
        "这是 frontend-local 的接纳视图，不是集群一致选举。进程 ready、模型 serving ready、Discovery 可见与 Relay 视图就绪可有不同结果；详见控制面专题。"
    ),
)
add(
    "http",
    "HTTP 入口：解析、生命周期与 readiness",
    X(FE + "openai.rs", 2486, 2535, "chat handler 的 request lifecycle 与前置 ready gate")
    + X(FE + "service_v2.rs", 542, 576, "State 持有模型、Metrics 与 API 开关")
    + p(
        "body/JSON 解析失败在 handler 生命周期收口；模型名可来自请求模板，所以 ready gate 需要先解析目标。typed handler 内还有请求级 guard。不要把 metrics guard 之前的所有 HTTP 错误都假设成相同业务请求样本。"
    ),
)
add(
    "identity",
    "模型别名、标签基数和请求指标归属",
    X(FE + "openai.rs", 3520, 3555, "canonical 模型名、metric_model 与 inflight guard")
    + p(
        "别名先映射为 canonical served name，路由、response.model 与标签尽量保持一致；未知模型走 metric_model_for 的基数保护，不把任意外部字符串持续变成新指标序列。请求 ID 适合 Trace / 日志关联，不适合做高基数 Prometheus 标签。"
    ),
)
add(
    "validation",
    "字段准入、值校验与 owner 必须分开",
    X(
        FE + "openai.rs",
        3550,
        3615,
        "unsupported / required / generic validation 与 engine 查找",
    )
    + table(
        ["问题", "源码分析方法", "避免误解"],
        [
            [
                "是否属于公共 API",
                "先查 endpoint 的 source contract",
                "内部 extra_args 不是公开 wire 协议",
            ],
            ["此 backend 接受吗", "追 unsupported policy 与 adapter 能力", "反序列化成功不表示实现语义"],
            ["值是否有效", "范围、互斥、n/stream/budget 等校验", "不把 0 / false / omitted 当相同值"],
            [
                "由谁实施",
                "Dynamo-handled / backend-handled / jointly handled",
                "typed 字段不代表前端执行全部功能",
            ],
        ],
    )
    + p(
        "例如 max token 省略时可能保留 omission，让 backend 使用 generation defaults；提前填入一个默认值可能改变契约。相同字段在 Chat、Responses、原生 Generate 上需要分别验证。"
    ),
)
add(
    "preprocess",
    "预处理：模板、tokenization、多模态和路由信息",
    X(PRE, 3255, 3300, "模板与 tokenization 打点、MM 数据与 builder")
    + X(PRE, 3300, 3351, "extra_args、token budget 与省略上限")
    + p(
        "模板渲染、token 收集和多模态处理不是一个步骤。图像占位 token 长度不一定等于引擎展开后的长度；预算要在可获得精确信息的边界计算，必要时交给 backend。nvext.router 等字段进入 PreprocessedRequest 后才成为路由输入。"
    ),
)
add(
    "preprocess-observe",
    "预处理观测：测的是哪个区间",
    p(
        "preprocess_request_with_options 中 StageGuard 标记 STAGE_PREPROCESS；TEMPLATE_SECONDS 包围模板处理，TOKENIZE_SECONDS 包围 gather_tokens；STAGE_DURATION_SECONDS 的 preprocess 观察点在 token/MM 收集之后、部分 builder/budget 收尾之前。不能拿这些区间简单相加等同 HTTP TTFT，也不能把其中异步媒体加载时间归为 tokenizer CPU 成本。"
    )
    + p(
        "dynamo_nvtx_range 标记模板/tokenization 等范围，适合结合 CPU/GPU profiler；NVTX 范围不是自动导出的 OpenTelemetry span。开启日志、Trace、NVTX 或 Prometheus 各有独立路径。"
    )
    + X(PRE, 5886, 5923, "后处理 detokenization 累积计数在 guard Drop 收口"),
)
add(
    "postprocess",
    "后处理：token 流不等于 SSE 帧",
    X(PRE, 5925, 5964, "后处理流持有 generator、context 与取消状态")
    + X(FE + "openai.rs", 3720, 3763, "空 chunk 丢弃前仍记录生成 token")
    + p(
        "增量 detokenizer、reasoning/tool parser 与 response generator 可能暂存内容。多字节 token 组装可产生空 chunk，模型已生成 token 而客户端尚无可渲染文字；因此首 token、首内容、首 SSE 字节是不同观测。不要用 SSE 帧数估算 token 数。"
    ),
)
add(
    "sse",
    "流式投影：错误与扩展事件",
    X(FE + "openai.rs", 3764, 3800, "tool/reasoning side-channel 与 SSE error 传播")
    + p(
        "HTTP 响应头发出前可返回结构化状态码；进入 SSE 后使用流内语义错误与终态。side-channel 事件、role 去重、usage 和标准 chunk 都有独立投影逻辑。客户端断连、流 EOF 和成功终态不能互相替代；完整边界见可靠性章节。"
    ),
)
add(
    "live-metrics",
    "活跃负载：从 tokenization 更新到首 token 状态迁移",
    X(FE + "metrics.rs", 800, 845, "RequestMetrics 跨 Context 共享与输入 token 增量")
    + X(FE + "metrics.rs", 847, 891, "首输出 token 转换状态与一次性 finish")
    + table(
        ["信号", "更新者 / 语义", "诊断用途"],
        [
            [
                "awaiting_first_token_requests",
                "RequestMetrics 首个非零 output token 前",
                "准入后等待积压，区别生成中请求",
            ],
            [
                "inflight_input_tokens{state}",
                "已知 prompt token 按 awaiting/generating 移动",
                "同时看请求数与输入规模，未知长度不能当零工作量",
            ],
            ["input_tokens_total", "tokenization/annotation 增量去重更新", "用 rate 看流入输入规模"],
            ["output_tokens_total", "新生成 token 增量，可本地缓冲后 flush", "吞吐计数不对应每个 SSE 帧"],
        ],
    ),
)
add(
    "end-metrics",
    "结束时的 Histogram 与 span 摘要",
    X(FE + "metrics.rs", 2674, 2725, "collector Drop：OSL、detokenize 与当前 span 字段")
    + p(
        "OSL 只有产生过输出 token 才观察，避免失败/取消的零值污染均值；请求失败仍通过状态/错误指标记录。detokenize 汇总按实际次数计算，Trace 中 input/output、TTFT/ITL、worker attribution 只有观察到时才填。请以当时生命周期 span 是否仍附着和 exporter 采样结果判断可见性。"
    ),
)
add(
    "relay",
    "观测数据也是控制输入：FrontendLoad 发布",
    X("lib/llm/src/frontend_load.rs", 36, 45, "FrontendLoadFrame 数据形态")
    + X(
        "lib/llm/src/frontend_load.rs",
        130,
        175,
        "周期读取同一 Metrics 与 committed model views",
    )
    + p(
        "发布器从同一 metrics 对象读取快照，避免另做一套请求计数。它还结合模型 committed views 与 readiness，经 Event Plane 发布给 Pool Relay；下游的采样频率、重连和 stale 判断决定全局视图新鲜度。Prometheus 能抓到与 Relay 收到并不保证同一时刻或同一就绪视图。"
    ),
)
add(
    "practice",
    "接入与排查顺序",
    table(
        ["步骤", "验证证据"],
        [
            [
                "确认 processor 与版本",
                "Rust 默认 / vLLM / SGLang factory、frontend/worker 支持窗口",
            ],
            [
                "检查模型准入",
                "canonical model、MDC 冲突日志、committed workers 与 serving readiness",
            ],
            [
                "检查字段映射",
                "endpoint → typed request → PreprocessedRequest → adapter → response",
            ],
            [
                "比较时延区间",
                "HTTP queue / preprocess / Router / backend / postprocess / SSE；不可混用单位",
            ],
            ["验证边界", "stream 与 nonstream、工具与 reasoning、多模态、取消和错误分开用例"],
        ],
    )
    + docs(
        [
            (
                "docs/fern/pages/developer-guide/knowledge-base/modular-components/frontend/protocol-field-handling.md",
                "Protocol Field Handling",
                "字段契约、语义 owner 与完整生命周期",
            ),
            (
                "docs/fern/pages/reference/components/frontend-configuration.mdx",
                "Frontend configuration",
                "端点、processor 与 CLI 配置",
            ),
        ]
    )
    + p(
        '继续阅读：<a href="runtime.html">Runtime</a> · <a href="router.html">KV Router</a> · <a href="backends.html">引擎适配</a> · <a href="reliability.html">可靠性</a>。本章未执行接口回归或负载测试。'
    ),
)
finish()

# Module 03: distributed runtime and communications.
RT = "lib/runtime/src/"
chapter(
    "runtime",
    "Runtime 与通信：身份、发现、传输和生命周期",
    "拆解 Runtime / DistributedRuntime、endpoint 注册、请求与响应传输、Context、Python 绑定及观测传播。",
)
add(
    "architecture",
    "本地执行器与分布式能力分层",
    X(RT + "runtime.rs", 72, 130, "本地 Runtime 的执行器、取消与后台能力")
    + X(
        RT + "distributed.rs",
        139,
        188,
        "DistributedRuntime 聚合 discovery、transport 与 metrics",
    )
    + p(
        "Runtime 提供本地执行环境、后台执行器、任务和关闭；DistributedRuntime 增加成员发现、网络服务、组件注册、metrics、system health 与引擎控制路由。Python asyncio、Tokio 与推理引擎各有并发模型，不能用同一个线程池模型解释所有等待。"
    ),
)
add(
    "identity",
    "namespace / component / endpoint / instance",
    X(RT + "component.rs", 105, 137, "Instance 描述可路由服务与 codec 能力")
    + X(RT + "component.rs", 364, 379, "Endpoint 持有组件、标签与自身 registry")
    + table(
        ["标识", "含义", "边界"],
        [
            ["namespace", "逻辑部署/命名空间作用域", "不自动等于 Kubernetes namespace"],
            ["component", "逻辑组件分组", "可以有多个进程实例"],
            ["endpoint", "可调用的命名服务入口", "不必等于 HTTP path"],
            ["instance_id", "一次 worker 注册身份", "不是 Pod 名、DP rank 或 Trace ID"],
            ["transport / codec", "地址与 wire 能力", "能发现不代表网络可达、模型可准入"],
        ],
    ),
)
add(
    "planes",
    "四种职责：Discovery、Request、Response、Event",
    X(RT + "distributed.rs", 1053, 1117, "显式 overrides、backend 选择与 NATS 依赖")
    + table(
        ["平面", "当前实现选择", "传递内容"],
        [
            [
                "Discovery",
                "Kubernetes / etcd / file / mem",
                "endpoint、model、event source 等注册记录",
            ],
            ["Request", "TCP（默认） / NATS", "请求 payload、控制与 response connection 元数据"],
            ["Response", "TCP / QUIC / Velo", "响应 stream 与控制交互，须按双方配置兼容"],
            ["Event", "ZMQ / NATS", "topic 的状态事件，不是 KV 张量数据"],
        ],
    )
    + p(
        "仅存在 NATS_SERVER 不会强制进程建立 NATS 连接；只有选中的请求面/事件面需要它才连接。Discovery 与事件面默认值有解析关系，不能把任一个环境变量当所有平面的统一开关。"
    ),
)
add(
    "codec",
    "传输模式与 payload 编码是两个配置",
    X(RT + "distributed.rs", 1170, 1200, "RequestPlaneMode 默认 TCP")
    + X(RT + "pipeline/network.rs", 58, 90, "进程级 ResponsePlaneMode")
    + X(RT + "pipeline/network.rs", 117, 153, "serde legacy JSON 与当前 msgpack 配置默认")
    + p(
        "当前配置默认 payload codec 为 msgpack，而旧 wire 缺失字段的 serde 默认仍为 JSON；两种 default 面向不同边界。Instance 的缺失 codec 表示 legacy JSON-only 能力，选路/发送需按能力兼容。响应模式独立选择并在进程内缓存，不是每请求随意混用。"
    ),
)
add(
    "startup",
    "初始化：依赖可用后才监听",
    X(RT + "distributed.rs", 215, 266, "初始化取消与 discovery / NATS 依赖")
    + p(
        "构建 DistributedRuntime 可能连接外部服务；new_with_probe_policy 在初始化期间监听 shutdown，尚未完成时没有 HTTP listener。启动失败、readiness 失败和请求失败应分别诊断，不能在无监听时只调查应用路由。"
    ),
)
add(
    "endpoint-start",
    "endpoint 注册：metrics、handler、health、server、discovery",
    X(RT + "component/endpoint.rs", 134, 166, "bind_endpoint、metrics 与请求服务准备")
    + X(RT + "component/endpoint.rs", 167, 218, "canary 约束、local registry 与 server 注册")
    + p(
        "handler 要绑定 endpoint 并添加 metrics，随后准备请求服务和健康检查。canary 需要 local engine，缺失时启动失败；网络 handler 与进程内健康检查入口属于两扇门。把 endpoint 发布到发现面只是启动链中的一步，不能替代 engine-ready 或 model-serving-ready。"
    ),
)
add(
    "discovery",
    "统一 Discovery trait 与业务准入边界",
    X(RT + "discovery/mod.rs", 1567, 1613, "连接探测、身份与模型注册冲突检查")
    + p(
        "Discovery 抽象提供注册/列举/监听/撤销；runtime 层的 model 名冲突检查与 LLM 层 WorkerSet checksum 准入是两道不同保护。注册检查不等价于集群共识；业务侧还要过滤被接纳 worker，处理增删、重连和延迟构建结果。"
    ),
)
add(
    "envelope",
    "请求信封：控制与业务 payload 分离",
    X(
        RT + "pipeline/network/egress/addressed_router.rs",
        414,
        470,
        "RequestControlMessage 与 two-part envelope",
    )
    + p(
        "控制头包含 request ID、请求/响应类型、codec、connection info 和 metadata；payload 用 codec 单独编码。metadata 能跨进程，Context registry 中任意 Arc 对象不能因此自动序列化。ManyIn 的额外输入流连接和 ManyOut 响应流也需要区分。"
    ),
)
add(
    "context",
    "Context 保存身份、元数据和本地共享状态",
    X(
        RT + "pipeline/context.rs",
        14,
        64,
        "Context 与 with_id_and_metadata 的独立 controller",
    )
    + X(RT + "pipeline/context.rs", 440, 489, "Stop/Kill 向 linked children 传播")
    + p(
        "map/rejoin 保留共享请求上下文，而 with_id_and_metadata 新建 controller；同 Request ID 不代表共享取消控制器。Stop/Kill 发出状态信号，由下游任务和 adapter 响应；不能承诺 signal 发出时引擎立即停止。PD 为完成 staged KV 清理会刻意使用独立 Prefill context。"
    ),
)
add(
    "tcp-observe",
    "TCP 入口：收到请求后的网络估计与 server span",
    X(
        RT + "pipeline/network/ingress/shared_tcp_endpoint.rs",
        286,
        337,
        "网络 transit 估计、handle_payload span 与 in-flight 收口",
    )
    + X(RT + "logging.rs", 765, 813, "traceparent 提取与设置 OpenTelemetry parent")
    + p(
        "WORK_HANDLER_NETWORK_TRANSIT_SECONDS 用跨机器 SystemTime 时间戳差估计，饱和减法只能避免负值，不能解决时钟偏移；还包含实际取样边界之间的等待。不能用它当纯网卡线速延迟。收到 headers 后显式提取 parent 并 instrument handler，串联依赖两端传播、采样和 exporter。"
    ),
)
add(
    "route-trace",
    "重试 attempt 的 Trace 与 worker 归因",
    X(
        RT + "pipeline/network/egress/route_span.rs",
        20,
        81,
        "RouteTraceContext 的 attempt、from-worker 与 selected-worker",
    )
    + p(
        "每次路由尝试记录 attempt、迁移原因、此前 worker 和已完成 token，选路后才补 selected_worker_id。一次用户请求可能对应多次 worker 尝试；HTTP requests、Router attempts、engine requests 的计数分母不同。日志有 ID 不自动意味着所有 span 是同一 Trace。"
    ),
)
add(
    "python",
    "Python 绑定：generator、push egress 与 trace headers",
    X(
        "lib/bindings/python/rust/lib.rs",
        1710,
        1765,
        "serve_endpoint 按 response_sender 签名选择 push / pull",
    )
    + X(
        "lib/bindings/python/rust/context.rs",
        447,
        462,
        "Context.trace_headers 暴露 W3C 与关联 ID",
    )
    + p(
        "push 模式减少逐项从 Tokio 拉 Python __anext__ 的交互；它仍需要同一个 handler 的 pull local engine 供进程内调用/canary 使用。模式由 response_sender 能力声明而非普通 context 参数判定。引擎调用必须显式消费 trace_headers，并启用其自身 tracing 才能继续串联。"
    ),
)
add(
    "registry",
    "Metrics 层级如何进入一次 scrape",
    X(RT + "metrics.rs", 694, 731, "MetricsHierarchy、父级与 connection ID")
    + X(RT + "metrics.rs", 875, 910, "子 registry 注册与递归去重")
    + p(
        "DRT → namespace → component → endpoint 构成 registry 层级；统一抓取递归汇总子 registry，并按底层 registry 指针去重。connection_id 可自动注入 worker_id 标签。创建成功、实际更新、纳入汇总与端口可抓取必须逐段检查；foreign engine 的文本指标还需要桥接。"
    ),
)
add(
    "shutdown",
    "撤销发现、停止请求服务与生命周期所有权",
    X(RT + "component/endpoint.rs", 270, 313, "取消后的发现撤销与 endpoint unregister")
    + p(
        "关闭先阻止新流量，再等待已在途请求与后台清理。撤销发现有传播延迟；对端旧连接仍可能在途。Stop、Task Drop、endpoint unregister 与 engine.cleanup 有不同 owner，SIGKILL 不会执行这些正常路径。完整 drain/cleanup 预算见<a href='reliability.html'>可靠性专题</a>。"
    ),
)
add(
    "practice",
    "接入工作与诊断顺序",
    table(
        ["问题", "优先证据"],
        [
            ["初始化卡住", "选中的 discovery / NATS 依赖、TLS、地址、初始化日志"],
            ["能发现但调用失败", "admitted worker、instance transport、codec、请求与响应面双向可达"],
            [
                "Trace 中间断开",
                "headers → metadata → receiver parent → Python headers → engine exporter",
            ],
            ["指标未出现", "创建点、更新点、registry 层级、foreign bridge、抓取端口"],
            ["断连后占用未回收", "context signal、在途流、PD staged KV、清理预算与进程退出"],
        ],
    )
    + p(
        '事件封装与数据面边界见<a href="storage-events.html">Storage &amp; Events</a>，不要把 Event Plane 当可靠消息队列或自动完整 Trace。本文未做网络回归或真实连通性测试。'
    ),
)
finish()

# Module 04: cache-aware selection and scheduling.
KR = "lib/kv-router/src/"
BP = "lib/router-plugins/builtin/src/default/"
chapter(
    "router", "KV-aware Router 与调度", "沿缓存索引、候选资格、默认插件评分、队列准入、生命周期负载与观测归因解释一次 Worker 选择。"
)
add(
    "architecture",
    "选择器、调度器与派发宿主不是一层",
    flow(
        [
            "发现并接纳 workers",
            "hash / prefix lookup",
            "过滤候选与计算成本",
            "policy class / queue / admission",
            "容量预留与 RoutePlan",
            "派发 / token / cleanup",
        ]
    )
    + table(
        ["模块", "职责"],
        [
            ["lib/kv-router", "可独立复用的索引、选择服务、调度与序列状态"],
            ["lib/router-plugins", "通过公开策略 API 实现默认与可选 filter/scorer/picker"],
            ["lib/llm KvRouter", "将 token、LoRA、cache namespace、业务路由约束送入 selection"],
            ["RoutingHost", "管理派发、上下文、生命周期、预留/释放与路由结果记录"],
        ],
    )
    + p(
        "standalone selection/indexer/slot-tracker 不依赖 Dynamo Runtime。Router 可以内嵌 Frontend 或以服务形式使用，不能把一个函数返回 worker 等同于已经派发 engine 请求。"
    ),
)
add(
    "request",
    "一次查找包含哪些路由输入",
    X("lib/llm/src/kv_router.rs", 1304, 1365, "PromptView、约束与不同 admission 模式")
    + p(
        "token_ids、block_mm_infos、LoRA、cache_namespace、session、pin/allowed workers、topology、priority class 与 expected_output_tokens 共同构成输入。文本相同不保证可共享 KV：hash 域、模型、adapter、MM 与隔离参数都要一致。查询预览、准入查询和有 lease 的真实请求不是同一种副作用。"
    ),
)
add(
    "index",
    "缓存索引保存可复用前缀，不保存 KV 张量",
    X(KR + "indexer/traits.rs", 90, 134, "查找、事件应用和 worker/rank reset barrier")
    + p(
        "引擎 KV events 更新索引，prefix lookup 得到 per-worker 可复用块/层级信息。索引结构可选并发 radix、分片或近似方案，不能把所有实现都假设成相同精度/一致性。worker 移除或 rank 重置需要让旧状态不再可读；事件迟到与丢失可导致估计偏差。数据转移仍由 engine/connector 完成，见 Storage &amp; Events。"
    ),
)
add(
    "selection",
    "选择服务把查询与预留分开",
    X(KR + "services/selection/core/run.rs", 71, 110, "策略 class 与只读选择入口")
    + X(KR + "services/selection/core/run.rs", 119, 154, "选择并预留的入口")
    + X(
        "lib/llm/src/kv_router/routing_host/kv_selection.rs",
        109,
        162,
        "结果映射、booking 与 queue rejection",
    )
    + p(
        "host 控制 worker 资格、admission、accounting 与返回结果合法性，插件只在可见候选上运行。NoEndpoints、全部被过滤、容量不足、队列拒绝有不同故障含义，不应笼统称为 KV miss。"
    ),
)
add(
    "plugin",
    "公开策略 API：Filter → Scorers → Picker",
    X(KR + "plugins/worker_selection.rs", 45, 98, "输入组声明、有限成本与 picker 行号")
    + p(
        "Filter 决定保留候选；多个 Scorer 为每一行提供有限且越低越好的成本贡献，host 汇总并验证；Picker 返回候选行号，不能任意注入一个 worker。每个插件只看到自己声明的 WorkerInputs，保持取样一致并避免不需要的计算。策略应无容量预留或队列写入副作用。"
    )
    + note(
        "scheduling/selector/reference.rs 是测试/基准对照实现；本章默认成本取自 builtin 插件，不把 reference 当生产唯一主路径。"
    ),
)
add(
    "cache-credit",
    "默认成本：本地、Host、Disk 与 shared credit",
    X(BP + "scorer.rs", 109, 139, "一次选择准备请求公共项与 load floor")
    + X(BP + "scorer.rs", 141, 195, "分层缓存 credit、可 fetch 前缀和衰减")
    + p(
        "device overlap、host/disk overlap 与 shared/fetchable 前缀分别加权。shared fetch credit 只统计超出本地各层已有前缀的部分，不能直接把所有 cache hits 相加。开启负载相关衰减后，相对最轻 Prefill 负载越高，device overlap 的 credit 可降低；这是成本启发式，不是直接预测毫秒。"
    ),
)
add(
    "load-cost",
    "默认成本：Prefill 与 Decode 的角色差异",
    X(BP + "scorer.rs", 195, 237, "角色化成本、taint 乘子与输入组按需请求")
    + p(
        "一般路径将 Prefill token 工作量换算成块，减 credit 后钳到非负，再叠加 Decode 块成本与可选 active-request 项。decode-only 且不跟踪 Prefill 时使用另一条减法路径。preferred taint multiplier 最后作用于成本。阈值和权重改变的是路由偏好，不能单独推导为真实 worker TTFT。"
    ),
)
add(
    "picker",
    "最低成本与温度采样：相同输入也未必同实例",
    X(BP + "picker.rs", 82, 125, "零温随机破平与 softmax 分支")
    + X(BP + "picker.rs", 126, 162, "确定性 replay 规范排序与 seeded RNG")
    + p(
        "temperature=0 的生产路径对同成本候选随机破平，并非固定取最小 worker ID；非零温使用 softmax。确定性 replay 才做 canonical worker/rank 排序并使用共享 seed。比较新策略前要区分生产随机性和 replay 的可重复性。"
    ),
)
add(
    "queue",
    "调度：class 间 DRR，class 内优先级与排序",
    X(
        KR + "scheduling/policy_queue.rs",
        779,
        828,
        "按 class deficit / quantum 选择 dispatchable 项",
    )
    + X(KR + "scheduling/policy.rs", 86, 114, "WSPT 以有效 Prefill token 为成本估计")
    + p(
        "一个 SchedulerQueueActor 拥有 PolicyQueue，后者拥有各 PolicyClassQueue。class 间使用 deficit round robin；class 内可 FCFS、LCFS、WSPT，并结合 strict priority 与 deadline。WSPT 的权重是 1+max(priority_jump,0)，成本是 max(有效新增 Prefill token,1)，并不按原始 ISL 一刀切排序。"
    )
    + p(
        "deadline 在同一 strict-priority 层内先按 due 时间处理，过期请求由 actor timer 拒绝；不是等轮到队头才发现超时。队列中的 pinned worker/rank lane 与任意 worker lane 也分别维护可派发 head。"
    ),
)
add(
    "admission",
    "最终选点后，才预留与回复",
    X(KR + "scheduling/queue.rs", 1270, 1315, "无 worker 与 never-queues class 的直接路径")
    + X(
        KR + "scheduling/queue.rs",
        1810,
        1873,
        "最终 selection → load hint → book_and_respond",
    )
    + p(
        "常规请求在同一 actor turn 进入策略队列并 drain，防止新到请求绕过其他 class 的可派发积压。只有零发现窗口、从不持有排队项的 class 等走特殊直达路径。必须在一次次准入之间看见上次预留后的容量，否则同时选择会过量承诺。"
    ),
)
add(
    "preview",
    "preview 不是免费保留的最终位置",
    X(
        "lib/llm/src/kv_router/routing_host/kv.rs",
        239,
        297,
        "由 preview 继续准入并保留原 worker 的 RoutePlan",
    )
    + p(
        "条件 PD 会先看 Decode preview，再对同一计划准入；函数核对 request ID、继承 cleanup budget 并把 planned_worker 固定为选择约束。RoutePlan 的 cleanup 接管 booking/lease，不能复制一个选择结果就丢弃所有权或重新挑另一实例。"
    ),
)
add(
    "sequence",
    "本地权威写模型与可延迟的读投影",
    X(KR + "sequences/single.rs", 199, 230, "active sequence 添加与重复 add 保护")
    + X(KR + "sequences/single.rs", 262, 281, "Prefill 完成的幂等阶段迁移")
    + X(KR + "sequences/prompt_registry.rs", 304, 345, "PromptRegistry 派生 worker load")
    + p(
        "本地 owning frontend 的 ActiveSequences 是写模型；PromptRegistry 提供可延迟的派生读模型，避免热路径全局锁。peer replica lifecycle 是 best-effort advisory，不保证严格全序；Free 先于 Add 会短暂遗留 stale state，expiry 负责最终收敛。输出块变化是高频本地状态，不能假定每个块都跨 frontend 广播。"
    ),
)
add(
    "overhead",
    "观测源码：路由耗时来自明确的区间差",
    X(
        "lib/llm/src/kv_router/metrics.rs",
        970,
        996,
        "hash、seq hash、indexer、shared query 与 scheduling 的计时",
    )
    + X("lib/llm/src/kv_router.rs", 1480, 1515, "仅 admitted selections 记录此处路由观测")
    + p(
        "这些 Histogram 在 observe 时转为毫秒。seq_hash 用累计差减去 block_hash，scheduling 用 total 减去 find_matches；缺失 shared query 不会记录该项。它们是实现边界的耗时，不能直接等于排队时长或 engine 执行时间；advisory preview 也不应被当作真实派发样本。"
    ),
)
add(
    "attribution",
    "观测源码：估计命中、选中命中和真实复用分开",
    X(
        "lib/llm/src/kv_router/metrics.rs",
        1250,
        1278,
        "放弃最大 overlap 与 best/selected/reused token 系列",
    )
    + X("lib/llm/src/kv_router/metrics.rs", 654, 673, "worker/rank/phase 的路由器负载 gauges")
    + table(
        ["信号", "能解释什么", "不能解释什么"],
        [
            ["best eligible / selected prefix", "可行候选中估计缓存机会与最终选择", "不是引擎实际重用量"],
            ["worker reused tokens", "backend 回报的真实复用（是否回报依能力）", "无回报的零值不能证无复用"],
            ["non-max-overlap selections", "选择为负载或约束牺牲缓存的频度", "不能直接判为错误路由"],
            [
                "active_prefill_tokens / decode_blocks",
                "router 记账与 worker/rank 维度",
                "不是 nvidia-smi 的显存量或引擎队列原样镜像",
            ],
        ],
    ),
)
add(
    "practice",
    "排障与扩展的最短路径",
    table(
        ["症状", "顺序检查"],
        [
            [
                "没有实例",
                "Discovery → WorkerSet admission → 资格/LoRA/topology/pin → endpoint",
            ],
            [
                "命中高却不选",
                "tier credit → load cost → busy/capacity → policy score → picker",
            ],
            ["队列迟迟不动", "class 配额/排序/deadline → pinned lane → 容量释放/actor update"],
            ["负载偏差", "本地 lifecycle → replica 延迟/丢失 → PromptRegistry → expiry"],
            ["扩展策略", "只请求所需输入、保持无副作用，验证 finite costs / picker row 与代表性基准"],
        ],
    )
    + docs(
        [
            (
                "docs/fern/pages/developer-guide/knowledge-base/modular-components/router/custom-worker-selection.mdx",
                "Custom Worker Selection",
                "公开 filter/scorer/picker API",
            ),
            (
                "lib/kv-router/src/sequences/README.md",
                "Active sequence ownership",
                "写模型与派生读边界",
            ),
        ]
    )
    + p(
        '本章未运行路由回归或性能基准。进一步阅读<a href="pd-serving.html">PD</a>、<a href="kv-cache.html">KV 内存管理</a>和<a href="validation.html">验证与回放</a>。'
    ),
)
finish()

# Module 05: engine adapters and native sidecars.
BC = "lib/backend-common/src/"
PYB = "components/src/dynamo/common/backend/"
chapter(
    "backends",
    "引擎适配与 Rust Sidecar",
    "区分既有 Python backend、共享 Backend SDK 与原生引擎 Sidecar；深入流式契约、生命周期和指标桥接。",
)
add(
    "architecture",
    "三种形态，复用职责而非强行等同实现",
    table(
        ["形态", "请求路径", "主要 owner"],
        [
            [
                "既有 Python 后端",
                "Runtime Python endpoint → request handler → engine Python API",
                "各 backend 的 main / worker_factory / init_llm / handlers",
            ],
            [
                "common/backend SDK",
                "Python BaseEngine → PyO3 → Rust Worker / EngineAdapter",
                "Worker 负责统一生命周期，Engine 负责引擎行为",
            ],
            [
                "Rust Sidecar",
                "Dynamo planes → Rust Worker / adapter → native gRPC（特定路径也可 HTTP）→ engine",
                "Sidecar 转换契约，引擎独立启动/运行",
            ],
        ],
    )
    + p(
        "common/backend 是与旧入口并行的路径，不能称整个 Python 适配已统一迁移。Sidecar 在该版本文档中标为 Experimental；支持矩阵和 native proto 能力须逐后端/版本核对。Dynamo 在引擎之上编排，不接管其 CUDA kernel、attention 或内部调度器。"
    ),
)
add(
    "traits",
    "LLMEngine 与 RawEngine 的模态边界",
    X(BC + "engine.rs", 196, 250, "LLMEngine start 与 token stream 契约")
    + X(BC + "engine.rs", 439, 487, "RawEngine 使用 JSON 请求/响应而非 token pipeline")
    + p(
        "LLMEngine 接收 PreprocessedRequest，返回 LLMEngineOutput；RawEngine 接收/返回 JSON，对图像/音视频生成不强塞 tokenizer、detokenizer 与 KV 语义。WorkerConfig.model_input 与 endpoint type 要匹配所选路径。新模态扩展不能仅靠把 JSON 塞进 token_ids。"
    ),
)
add(
    "lifecycle",
    "Worker 持有完整生命周期",
    flow(
        [
            "构造 / from_args",
            "engine.start",
            "metadata / metrics / 注册",
            "generate / abort",
            "停止新请求 / grace / Prefill drain",
            "cleanup",
        ]
    )
    + X(PYB + "worker.py", 220, 260, "Python Worker 映射配置到 Rust Runtime/Worker")
    + p(
        "from_args 是工厂，构造不等于 engine ready。start 成功后应能生成，Worker 才注册模型。shutdown 在 start 调用前、调用中、成功后有不同清理路径：partial-start cleanup 必须 null-safe；构造阶段自己持有的资源仍需 Drop/析构负责。"
    ),
)
add(
    "terminal",
    "流式输出与错误契约",
    X(BC + "engine.rs", 218, 250, "terminal、typed error 与 usage")
    + p(
        "正常 chunk 无 finish_reason，最后一项是带 finish_reason 的 Ok 或 typed Err，终态后不能再有数据。Err 保留 DynamoError/BackendError 分类，比把所有错误拼成普通字符串更可诊断。debug validator 会抓终态后继续输出，但不强制每次提前结束都存在 terminal；conformance kit 才覆盖缺失终态场景。"
    ),
)
add(
    "cancel",
    "取消监控与 RAII 各负责什么",
    X(BC + "engine.rs", 252, 304, "abort、Prefill is_quiescent 与 cleanup 语义")
    + X(
        BC + "adapter.rs", 350, 411, "Stop/Kill monitor、首 token barrier 与 abort timeout"
    )
    + p(
        "框架监听 Stop/Kill 并调用 abort；静默 stream Drop 不保证触发 abort。每请求资源释放要放在 stream 内的 RAII/finally，abort 用于通知外部调度器提前停止。Decode 可等待首 token 的安全点再 abort，以免接收侧还未导入 staged KV 时破坏传输；详见可靠性章节。"
    ),
)
add(
    "trace",
    "engine.generate span：链接与字段按观察时点填写",
    X(BC + "adapter.rs", 227, 285, "引擎 span、role 与 predecessor OTel Link")
    + p(
        "span 起点记录 model、input tokens、disagg role；首个非空输出填 TTFT，终态汇总 output、finish、cancel 和 ITL。migration_link 形成真实 OTel Link，同时保留 trace/span ID 字段供日志读取。Link 与 parent-child 有不同语义，不能只看 Trace 树缩进判断 PD 已断链；原生 Sidecar 向 engine 的 tracing 还需单独核对。"
    ),
)
add(
    "python-protocol",
    "既有三种 Python backend 的职责",
    table(
        ["后端", "适配核心", "需逐路径核对"],
        [
            [
                "SGLang",
                "Engine.async_generate、Bootstrap、native / standard 请求分支",
                "external_trace_header、n=1、DP rank、abort / cache-hit trailing metadata",
            ],
            [
                "vLLM",
                "prompt / sampling 构建、engine.generate 与 KV connector protocol",
                "kv_transfer_params、多模态、generation defaults、typed errors",
            ],
            [
                "TensorRT-LLM",
                "LLM generate_async、disaggregated codec 与 push egress 能力",
                "context_only / generation_only、ctx_request_id、first-token 通知",
            ],
        ],
    )
    + p(
        '协议源码已在<a href="pd-serving.html">PD 分离</a>与<a href="reliability.html">可靠性</a>中展开。本章聚焦它们与 SDK/Sidecar 的边界，不把某个 Python handler 的能力直接承诺给 native gRPC 路径。'
    ),
)
add(
    "sidecar-vllm",
    "vLLM Sidecar：native proto 能力影响字段接受",
    X("lib/sidecar/vllm/src/engine.rs", 835, 885, "多模态声明、reasoning 与 proto 版本校验")
    + X("lib/sidecar/vllm/src/engine.rs", 1014, 1027, "原生 Abort 请求")
    + p(
        "Sidecar 读取 native model/proto 元数据，转换 PreprocessedRequest 并对版本不足的 reasoning/control 字段定向拒绝。不能假设 engine Python API 接受的所有扩展也能通过旧 gRPC proto。TP 跨节点通常仍是一个 engine 的并行执行；不是每个 TP rank 都独立接一个 Router 请求。"
    ),
)
add(
    "sidecar-sg",
    "SGLang Sidecar：默认 gRPC 与特定 native HTTP 分支",
    X(
        "lib/sidecar/sglang/src/engine.rs",
        348,
        383,
        "native 请求路由、HTTP ready 与 gRPC client",
    )
    + p(
        "标准 token 路径使用 native gRPC；特定原生 Generate 请求根据能力走 incremental HTTP。没有发现 ready HTTP endpoint 时明确报错，不能从默认 gRPC 可用推断所有原生协议都能接入。Bootstrap/DP/多节点元数据仍需与引擎版本配套。"
    ),
)
add(
    "trt-load",
    "TensorRT-LLM Sidecar：负载采集也有 freshness",
    X(
        "lib/sidecar/trtllm/src/engine.rs",
        699,
        750,
        "setup_metrics 创建 per-rank bindings、GetLoad 轮询与 stale deadline",
    )
    + p(
        "这里的 Sidecar 通过 native GetLoad 轮询产生 snapshot，并在超过 staleness 限制时标记 load_failed。框架统一 snapshot 发布是事件驱动 API，不意味着所有 backend 的原始数据都来自引擎主动推送。采集失败与旧值保留必须结合 readiness/监督逻辑诊断。"
    ),
)
add(
    "metrics-contract",
    "观测契约：foreign 指标、component snapshot、lifecycle 三条线",
    X(BC + "engine.rs", 313, 338, "setup_metrics 的两种输出")
    + X(BC + "worker.rs", 710, 750, "engine start 计时与独立 lifecycle gauges")
    + X(BC + "worker.rs", 753, 795, "根据 MetricsBindings 组装 publisher")
    + table(
        ["出口", "产生方式", "边界"],
        [
            ["engine 原生指标", "expfmt / typed callbacks bridge", "保留 vendor 名称；需要避免重复采集"],
            [
                "dynamo_component_*",
                "声明 DP ranks 并 push ComponentSnapshot",
                "框架不会凭空推断引擎内部队列",
            ],
            [
                "router kv_used_blocks signal",
                "SnapshotPublisher 交给 worker metrics publisher",
                "是路由状态信号，不能用 scrape 替代",
            ],
            [
                "lifecycle gauges",
                "Worker 记录 model load / drain / cleanup",
                "不依赖 engine 是否 opt in component metrics",
            ],
        ],
    ),
)
add(
    "bridge",
    "Python registry 桥接：文本与 typed 两个回调",
    X(PYB + "metrics.py", 63, 101, "同一 engine registry 注册 expfmt 与 typed callback")
    + X(PYB + "metrics.py", 104, 153, "global REGISTRY 的 MultiProcessCollector 冲突处理")
    + p(
        "labels 在 scrape/转换时注入。仅注册文本回调不等于 OTLP 也有同样数据，因此共享 helper 同时提供 typed callback。多进程 registry 发生 collector 冲突时，第二个 registry 仅抓特定 multiproc-only prefix，避免一次 scrape 重复暴露 engine 系列。PROMETHEUS_MULTIPROC_DIR 的生命周期还要覆盖 engine 后续写入。"
    ),
)
add(
    "snapshot",
    "SnapshotPublisher：自然生产者更新与缺失值",
    X(PYB + "publisher.py", 39, 60, "ComponentSnapshot 字段与可选 cache-hit rate")
    + X(BC + "snapshot_publisher.rs", 43, 80, "更新 component gauges 并发布路由信号")
    + X(BC + "metrics.rs", 235, 258, "实际 gauge 更新与 cache-hit 可选值")
    + p(
        "producer 可以是 engine iteration、stat logger 或 Sidecar GetLoad 循环。kv_cache_hit_rate=None 表示未得到数据/不适用，不应显示成测量零命中；旧 gauge 的可见性还要考虑上次写入。snapshot rank、声明 ranks 与 publisher lookup 必须一致；metrics 写入与网络信号发布也不能当成跨系统事务。"
    ),
)
add(
    "integration",
    "接入一个引擎需要完成的工作",
    table(
        ["工作", "具体结果"],
        [
            ["选定入口", "既有 adapter / common SDK / native Sidecar，固定双方版本与 proto"],
            ["描述模型", "ModelInput、endpoint、role、block size、DP/TP/PP 与 tokenizer/config"],
            ["实现契约", "并发 generate、terminal/error、取消、partial-start cleanup 与幂等释放"],
            [
                "连接观测",
                "engine span/header、foreign registry、per-rank snapshots（同时检查 enable_kv_routing 对 publishers 的门控）、lifecycle 与采集出口",
            ],
            ["验证能力", "文本/多模态、stream/nonstream、PD、LoRA/control/update 按支持矩阵验证"],
            ["运行验证", "conformance + CPU fake gRPC + 授权 GPU smoke；不以探针替代真实请求"],
        ],
    )
    + docs(
        [
            (
                "lib/backend-common/README.md",
                "Backend SDK",
                "共享 Worker 与 Rust engine trait",
            ),
            (
                "components/src/dynamo/common/backend/README.md",
                "Python Backend SDK",
                "BaseEngine / LLMEngine / RawEngine",
            ),
            ("lib/sidecar/README.md", "Sidecar overview", "原生协议接入和实验状态"),
        ]
    )
    + p('本文未启动引擎或运行 conformance，参见<a href="validation.html">验证与回放</a>。'),
)
finish()

# Module 07: KV block and GPU memory ownership.
KL = "lib/kvbm-logical/src/"
KE = "lib/kvbm-engine/src/"
GP = "lib/gpu_memory_service/"
chapter(
    "kv-cache",
    "KV 缓存与内存管理",
    "从逻辑块状态、分层驻留、onboard/offload 和物理传输，追到 KVBM 指标与 GPU Memory Service 的发布边界。",
)
add(
    "architecture",
    "四类状态与五层实现",
    table(
        ["层", "核心对象", "它不代表什么"],
        [
            [
                "Router prefix index",
                "hash → worker / tier 前缀估计",
                "不持有实际 tensor 或防止引擎 eviction",
            ],
            [
                "kvbm-logical",
                "Mutable / Complete / Immutable / Weak、registry/pools",
                "不负责实际 CUDA/RDMA 字节拷贝",
            ],
            [
                "kvbm-engine",
                "leader / worker / distributed session / offload policy",
                "元数据 ready 不等于所有接收 GPU 已计算完",
            ],
            [
                "kvbm-physical / kernels",
                "layout、transfer manager、事件、NIXL/CUDA/I/O",
                "配置了 transport 不证明硬件路径已生效",
            ],
            [
                "GPU Memory Service",
                "独立分配所有者、共享 handle、layout/session",
                "不等于自动 KV 策略或跨 GPU 统一缓存",
            ],
        ],
    )
    + flow(["G1 GPU HBM", "G2 pinned DRAM", "G3 NVMe / disk", "G4 object / external"])
    + p(
        "分层链表达存储能力而非所有部署都启用每层；object、collectives、硬件及 backend integration 都有 feature/config 条件。PD 引擎 KV connector 与 KVBM 分层复用可能同时存在，但不是同一个协议。"
    ),
)
add(
    "typestate",
    "逻辑块：写入、封存、注册与弱引用",
    X(KL + "blocks/mutable.rs", 72, 119, "Mutable → Complete 与 Drop 归还")
    + X(KL + "blocks/immutable.rs", 117, 136, "最后强引用释放后的 store 状态迁移")
    + X(KL + "blocks/immutable.rs", 271, 277, "Weak 升级：强引用与池内复活")
    + p(
        "MutableBlock 从 reset 取得可写槽，stage/complete 检查 block size 并封存 sequence hash；注册后由 Immutable handle 保护。Weak 不阻止 eviction，upgrade 可能失败，也可从 inactive 槽复活。Clone/Drop 表示句柄与驻留保护，不代表复制 tensor 数据；inflight_immutable 因克隆增长也不等于物理块数。"
    ),
)
add(
    "allocation",
    "池分配与 eviction：容量以所有权约束",
    X(KL + "manager/mod.rs", 56, 74, "从 reset / inactive 原子批量分配")
    + X(KL + "manager/mod.rs", 93, 124, "注册批次完成后更新 publication watermark")
    + p(
        "allocate_blocks(count) 要求可取得整个批次，不足返回 None。active 强引用保护的块不能随意牺牲；inactive 才按具体 backend 策略回收。reset/inactive/active、可写/staged/registered 和外部句柄数不是同一组统计，分析容量时要避免重复计数。注册 counter 在批次状态和 presence marker 更新完成后才递增，还可作为 offline settlement 的发布水位。"
    ),
)
add(
    "lookup",
    "本地 prefix match：遇到第一个洞就停止",
    X(KL + "manager/mod.rs", 126, 168, "一次 store lock 查连续前缀，释放锁后频率 touch")
    + p(
        "match_blocks 沿 sequence hash 左到右查 active/inactive，遇到 miss 停止；scan/match_scattered 是不同接口。返回命中句柄同时保护实际块，后续 Drop 再释放。TinyLFU 或 LRU 等策略影响淘汰，但不改变 hash 域兼容条件；性能结论需要具体池策略和 workload。"
    ),
)
add(
    "leader",
    "Leader 判断本地 Ready 还是异步 Session",
    X(KE + "leader/instance.rs", 1117, 1180, "G2/G3 查询与 Ready / AsyncSession 分支")
    + p(
        "本地 G2 连续前缀可直接 Ready；有 G3 staging 或远端/object 搜索时创建异步 session，保持源块活性并协调目标。这里还留有 explicit timing tracing 的 TODO，不能据函数名宣称已导出完整查找阶段 Trace。Ready 是该接口的返回语义，不意味着客户端已收到生成 token。"
    ),
)
add(
    "session",
    "Onboarding：搜索、持有、staging 与数据搬入",
    X(
        KE + "leader/session/messages.rs",
        15,
        65,
        "CreateSession、G2/G3Results、HoldBlocks 与 staged blocks",
    )
    + p(
        "多个 responder 汇报可用块，initiator 聚合并决定保留哪些来源；G3 命中需先 staging 到适合传输的层再通知 BlocksReady。session 用 RAII 保持块可用，必须处理会话结束、remote failure 与超时。跨层/跨实例 partial 命中不能未经 first-hole 聚合规则直接解释为可复用完整前缀。"
    ),
)
add(
    "physical",
    "PhysicalWorker 与 TransferManager 执行字节传输",
    X(
        "lib/kvbm-physical/src/manager/mod.rs",
        227,
        270,
        "按 layout handle 查找源/目的，释放锁后准备 transfer",
    )
    + p(
        "逻辑 block ID 需结合 manager/layout 身份才能定位物理槽；相同数字不代表同一块。TransferManager 解出 layer range、NIXL notification、bounce buffer、CUDA stream 与布局选项，实际完成通过 notification 等待。布局/方向/设备能力不匹配必须在实际传输分支处理，不能只依赖元数据命中。"
    ),
)
add(
    "offload",
    "Offload 的执行链与提交边界",
    X(KE + "offload/engine.rs", 120, 147, "带 precondition 的 G1→G2 与后续 offload 入口")
    + X(
        KE + "offload/pipeline.rs",
        1170,
        1227,
        "upgrade_batch 实际处理 weak 来源和 transfer state",
    )
    + X(
        KE + "offload/pipeline.rs",
        1410,
        1465,
        "prepare slot、weak→strong 与 transfer slot",
    )
    + p(
        "当前代码沿 policy / batching / precondition / prepare / transfer 的具体 channel 组装执行；理解取消时必须追到升级与取 strong handle 的位置。不能把弱引用阶段已取消的意图推广为物理传输可随时撤销；已经取得强引用并提交的工作需按完成/失败路径释放资源。"
    ),
)
add(
    "precondition",
    "源码与设计文档不一致时如何记录",
    X(KE + "offload/pipeline.rs", 1247, 1309, "当前 PreconditionAwaiter 实现与超时日志")
    + note(
        "docs/offload.md 描述的 container 取消 / P6 select 不能无条件套用当前实现。本版本此处生成独立等待任务，使用 timeout(Duration::from_secs(300), awaiter)，可见分支没有所描述的 container cancellation select；超时日志却写 after 30s。应以实际 300 秒代码值排查该路径，并单独检查后续 state/handle 取消。",
        True,
    )
    + p("这是静态实现/文档差异记录，本文没有修代码或注入取消故障。不要用旧设计图承诺立即取消、特定并发上限或逐容器隔离。"),
)
add(
    "completion",
    "完成后注册目的块，才发布可复用状态",
    X(KE + "offload/pipeline.rs", 1520, 1588, "分配目的块并提交实际 transfer")
    + X(KE + "offload/pipeline.rs", 1588, 1650, "等待完成、stage/register 与批次日志")
    + p(
        "提交返回 completion notification 后标记 in-flight；等待成功后 stage/register 目的块，再进入链式 offload 或释放。skip_transfers 是测试路径，不能当作真实吞吐。失败要传播到相关 transfer state，不能在注册完成前对外宣称该目的层已有可用 KV。"
    ),
)
add(
    "metric-source",
    "可观测源码：原子写入、scrape 汇总与标签",
    X(KL + "metrics/pool_metrics.rs", 5, 45, "BlockPoolMetrics 的 counter 与 gauge")
    + X(
        KL + "metrics/collector.rs",
        180,
        222,
        "scrape-time 快照、pool / external labels 与 MetricFamily",
    )
    + p(
        "热路径按事件更新 Relaxed 原子，MetricsAggregator 在 collect 时读 snapshot 并生成 Prometheus proto，附加 pool 与外部标签。多原子读取不是事务一致快照；counter/gauge 的单位是操作、出现次数或句柄/池槽，需按 help 核对，不能全部换算成同一种显存块数。collector 还要被接入实际 registry 与 exporter 才能从部署中查询。"
    ),
)
add(
    "metric-meaning",
    "KVBM 指标怎么解释",
    X(KL + "metrics/collector.rs", 16, 80, "命中、淘汰、注册与罕见分支 counter 定义")
    + X(KL + "metrics/stats.rs", 99, 153, "StatsCollector 按时间窗口计算速率与命中率")
    + table(
        ["信号", "计算 / 判断", "限制"],
        [
            [
                "match returned / requested",
                "对相同 pool 的 counter rate 求比",
                "计算 hash occurrence 的命中，不是用户请求命中率",
            ],
            [
                "scan returned / requested",
                "扫描匹配的 distinct hash 与输入 occurrence",
                "不直接与 match ratio 当同口径比较",
            ],
            ["allocation / eviction rate", "相邻样本 delta / dt", "重启、采样间隔与启停要考虑"],
            [
                "reset / inactive / inflight",
                "池槽与外部句柄状态",
                "inflight handle 数不能当独占物理显存块数",
            ],
            [
                "rollback / eager transition / no-op",
                "罕见并发/不变量分支诊断",
                "持续升高要结合具体分支；不全是正常 miss",
            ],
        ],
    )
    + p(
        "StatsCollector 无请求 delta 的窗口当前返回 0 hit-rate；它不是一次测量的真实零命中。批次日志中的 policy/precondition/xfer/registration/total 是毫秒观察区间，xfer 还依赖具体传输实现，不是自动 NIXL 内部 span。"
    ),
)
add(
    "gms",
    "GMS 管理共享分配，而非 KV 淘汰策略",
    X(GP + "server/allocations.py", 89, 131, "CUDA VMM 分配、OOM 重试、连接与预算")
    + X(GP + "server/allocations.py", 131, 171, "重试可见日志与 allocation 身份")
    + p(
        "独立 server 持有 GPU 物理分配，client 通过 Unix socket/共享 FD 在自己地址空间导入映射；writer/reader 和 session 控制访问。每个 server 对应其设备，不是所有 GPU 的统一地址空间。它可承载权重或缓存布局，但不自动实现 KVBM 的 prefix 查找、分层策略或 PD 协议。client crash 与 GPU/driver/server failure 是不同故障域，不能把进程外持有升级为全面持久性保证。"
    ),
)
add(
    "gms-publication",
    "GMS 的两种 commit：内容发布与布局发布",
    X(
        GP + "client/memory_manager.py",
        333,
        383,
        "GPU synchronize、unmap、commit 与 commit_layout",
    )
    + p(
        "commit 先同步 GPU 写、撤销 writer 映射再发布内容并结束 session；commit_layout 只发布可重接的布局，保留写入映射并收窄为 RW_DATA，阻止后续 allocate/free 改变结构。这两种原子边界不同，不能把 layout committed 当 tensor 内容永远不会再变，也不能绕过客户端同步直接发布。"
    ),
)
add(
    "gms-observe",
    "GMS / failover 观测：状态、等待与真实切换",
    X(
        GP + "failover_lock/failover_metrics.py",
        27,
        78,
        "独立 registry、状态 gauge、时间戳与 transition",
    )
    + X(
        GP + "failover_lock/failover_metrics.py",
        176,
        205,
        "实际 switch attempt/success 与文本/typed 桥接",
    )
    + p(
        "OOM 日志含对齐尺寸、tag、已等待秒数与可用内存；它与 KV eviction counter 不是同一现象。failover metrics 在 engine system metrics 暴露，真实竞争锁时才记 attempt/success，并持久化状态计数；success/attempt 之差包含尚未完成尝试，不能直接断言均为失败。它监测的是该锁接管流程，不证明请求无损或 KV 全部恢复。"
    ),
)
add(
    "practice",
    "接入和验证清单",
    table(
        ["工作", "必需证据"],
        [
            ["先确认集成", "backend 是否使用 KVBM/GMS、feature、启用层级与缓存域"],
            ["核对容量", "block size、layout、物理大小、各 pool 与活跃强引用"],
            ["验证复用", "prefix 连续性、目标块注册、engine 实际 cache-hit 与输出正确性"],
            ["验证传输", "precondition → submit → notification → registration 与真实数据校验"],
            [
                "连接观测",
                "logical collector、pipeline logs、engine/connector telemetry、GMS 独立指标出口",
            ],
            ["验证异常", "不足容量、eviction race、取消、远端失败与 partial publication，分别测语义"],
        ],
    )
    + docs(
        [
            (
                "lib/kvbm-logical/docs/advancements.md",
                "Logical block design",
                "type state 与 pools",
            ),
            ("lib/kvbm-engine/docs/offload.md", "Offload design", "设计意图；必须对照当前实现差异"),
            ("lib/gpu_memory_service/README.md", "GPU Memory Service", "共享分配与客户端 API"),
        ]
    )
    + p(
        '未运行 GPU / RDMA / disk / object 测试，数据面遥测详见<a href="storage-events.html">Storage &amp; Events</a>。'
    ),
)
finish()

# Module 11: global hierarchy and pool views.
GR = "components/src/dynamo/global_router/"
RE = "lib/llm/src/pool_relay/"
chapter(
    "global-routing",
    "多集群与全局路由：实现现状与视图边界",
    "分别分析 Python 分层 Global Router、Pool Relay 的跨域事实、Rust 视图工具和 Global Planner 容量仲裁，避免把独立模块视为已连通系统。",
)
add(
    "architecture",
    "先分清四条独立链路",
    table(
        ["链路", "当前实现", "边界"],
        [
            [
                "请求分层路由",
                "Python GlobalRouterHandler → pool namespace local router",
                "当前策略按预先配置 grid 和 priority，不是自动读取 Relay 实时负载",
            ],
            [
                "跨域事实导出",
                "Pool Relay → catalog / filter / readiness / load gRPC",
                "发布事实，不执行跨 DC 路由策略或自动合并独立池",
            ],
            [
                "全局视图工具",
                "global-discovery-client / global-view-receiver",
                "提供 client / reconciliation API，不代表已接入所有请求面",
            ],
            [
                "容量协调",
                "Python Global Planner → CapacityManager → DGD connector",
                "仲裁伸缩，与每请求选池分开",
            ],
        ],
    )
    + flow(
        [
            "局部 endpoint / Frontend",
            "Pool Relay 事实发布",
            "跨域 consumer 维护新鲜视图",
            "策略消费者选择（须单独接线）",
        ]
    )
    + p(
        "跨 namespace 不自动等于跨 Kubernetes cluster，更不自动等于跨 DC。端到端仍需要发现作用域、网络、身份、模型/缓存域兼容和实际消费者接线。"
    ),
)
add(
    "implementation",
    "目录名不是能力证明：Rust 空壳与真实实现",
    X("lib/global-router/src/lib.rs", 1, 4, "Global Router Rust crate 当前仅模块描述")
    + X("lib/global-frontend/src/lib.rs", 1, 4, "Global Frontend Rust crate 当前仅模块描述")
    + X(
        "lib/global-view-broadcaster/src/lib.rs", 1, 4, "Broadcaster Rust crate 当前仅模块描述"
    )
    + p(
        "global-common / global-discovery-service 此版本同样只有模块描述，不能从 Cargo workspace 成员推导为运行服务。真实 Python Global Router / Global Planner 与已经实现的 Pool Relay、client、receiver 要分别阅读。旧大纲中的 kv_dc_relay 路径已不适合当前代码，入口应改为 pool_relay。"
    ),
)
add(
    "request",
    "Python Global Router：独立的 Prefill / Decode 客户端",
    X(GR + "handler.py", 38, 73, "按模式维护 pool clients 与 namespace 映射")
    + X(GR + "handler.py", 219, 274, "Prefill 取 ISL、TTFT target、priority 并转发")
    + p(
        "disagg 模式分别接 Prefill 与 Decode，agg 模式统一 generate。选择 pool 后转给该 namespace 的 local router，再由后者选 worker。nvext.router 转换后的 router 字段承载目标值；is None 检查保留显式 0，而不是用真假判断吞掉 fastest bucket 意图。"
    ),
)
add(
    "grid",
    "选池策略：配置映射，不是在线优化器",
    X(GR + "pool_selection.py", 153, 191, "ISL / TTFT grid 与 priority override")
    + X(GR + "pool_selection.py", 224, 262, "Decode request token count / ITL grid")
    + X(GR + "pool_selection.py", 308, 373, "Aggregated 的 TTFT / ITL 与可选 ISL 维度")
    + p(
        "输入按配置范围离散化并 clamp，查 mapping 后应用 priority override。Decode 的参数名保留 context_length，但传入的是生成前请求 token_ids 的长度，不是不断增长的 GPU 实时上下文。目标 TTFT/ITL 是选桶输入，并非后端已承诺满足的 SLO。"
    ),
)
add(
    "retry",
    "跨池 retry：只在尚未转发输出时尝试",
    X(GR + "handler.py", 75, 147, "priority retry、取消检查、已输出保护与 Decode caveat")
    + p(
        "priority 数字越小表示更快 pool；可按配置向更快候选重试。已有任何输出后不能安全重试，取消也不继续迁移。源码明确提示 Decode retry 可能遇到 Prefill KV 已退休，而当前 backend 尚不支持该缺失恢复语义；不能把存在 retry 代码当作 PD 跨池无损故障转移保证。"
    )
    + p(
        "官方 Global Router README 列出 vLLM、Mocker、SGLang 路径；TensorRT-LLM 没有专用端到端集成验证。共享 Frontend 能处理 TRT PD，不自动代表该全局拓扑受支持。"
    ),
)
add(
    "relay",
    "Pool Relay：catalog、缓存过滤器与运行事实",
    X(RE + "wan/grpc/protocol/relay.proto", 8, 42, "gRPC RPC、兼容 major 与结构化错误 reason")
    + p(
        "Relay 暴露 GetRelayInfo、WatchKvPoolCatalog、SubscribeKvPool、SubscribeServingReadiness、SubscribeKvPoolLoad 与 SubscribeServingLoad。版本号 1 是兼容 major，未知 safety-relevant 类型不能按默认解释。CKF 是近似缓存事实，真实使用前仍需本地引擎/路由验证；pool identity、producer 变化与 snapshot/delta generation 不可混用。"
    )
    + note(
        "Relay 是 Experimental。配置的 logical namespace 过滤不会扩大 Kubernetes discovery 的 RBAC/watch 作用域；可选 gRPC listener 在当前指南中是 plaintext，跨信任域的 TLS/mTLS 由外部代理方案负责。"
    ),
)
add(
    "discovery",
    "GlobalDiscoveryClient：注册租约与完整列表",
    X(
        "lib/global-discovery-client/src/client.rs",
        58,
        121,
        "HTTP client、禁止 redirect、routers 全量读取与 pool heartbeat",
    )
    + X(
        "lib/global-discovery-client/src/watch.rs",
        5,
        28,
        "Resync 与 Exact / Estimated completeness",
    )
    + p(
        "register_pool 的重复 POST 同时刷新 heartbeat lease；routers 返回全量，任一条解析错误使整个调用失败，避免客户端使用悄悄截断的列表。watch 的 Resync 替换旧状态，Estimated 明确表示 replica 仅基于已接收更新。client 契约实现不能证明同版本占位的 server crate 已可运行。"
    ),
)
add(
    "view",
    "PoolViews：重连 generation fencing 与 snapshot 优先",
    X(
        "lib/global-view-receiver/src/lib.rs",
        14,
        36,
        "StreamToken 与 StaleStream / SnapshotRequired / DuplicatePool",
    )
    + X("lib/global-view-receiver/src/lib.rs", 63, 122, "open 替换源并验证完整 snapshot 后原子更新")
    + X(
        "lib/global-view-receiver/src/lib.rs",
        125,
        172,
        "snapshot 后 upsert / remove / 显式 disconnect",
    )
    + p(
        "receiver 按 source 分别保存值，不定义网络传输或多源 merge policy。重连先移除旧源视图，完整 snapshot 到达才激活更新；重复 pool snapshot 拒绝且不破坏当前视图。旧 stream 的更新/断开不能影响新 generation；仅 Drop token 不会清状态，adapter 必须在 EOF/error/cancel 调 disconnect。"
    ),
)
add(
    "load-types",
    "三种 load 的来源不同，不能混成 GPU 使用率",
    X(RE + "load.rs", 39, 59, "worker-authoritative KV pool load")
    + X(RE + "serving_load.rs", 5, 16, "scheduler 与 Frontend 双输入")
    + X(RE + "serving_load.rs", 49, 97, "新鲜度、retention 与 model coverage")
    + table(
        ["事实", "来源", "适合回答"],
        [
            [
                "KV pool load",
                "worker-authoritative kv_used_blocks + 容量",
                "该池存储/使用了多少可计量 KV 块",
            ],
            [
                "scheduler load",
                "各 router 对 pool/rank 的活跃 Prefill/Decode 视图",
                "路由器已承诺多少请求工作量",
            ],
            [
                "Frontend model load",
                "HTTP RequestMetrics 共享对象发布 frame",
                "模型的 awaiting/generating、输入输出累计量",
            ],
        ],
    )
    + p(
        "这里 load 不等于 Prometheus 任意 query，也不等于 CUDA 利用率。freshness 当前按 publish interval 的三倍计算；source retention 为十分钟，用于保留来源身份而非把十分钟旧值都当 fresh。"
    ),
)
add(
    "coverage",
    "观测源码：COMPLETE / DEGRADED / UNAVAILABLE",
    X(
        RE + "serving_load/frontend.rs",
        320,
        389,
        "只汇总 fresh Frontend；unknown/coverage 与 totals 分开",
    )
    + X(RE + "serving_load/frontend.rs", 415, 447, "预期与观测 frontend 数、发现健康和新鲜度")
    + X(
        RE + "wan/grpc/protocol/relay.proto",
        387,
        441,
        "ServingLoad RPC 的完整窗口与 coverage 契约",
    )
    + p(
        "发现正常且全部预期生产者新近发布才 COMPLETE；只有部分或发现不健康为 DEGRADED，没有新近发布则 UNAVAILABLE。新鲜 frontend 未声明模型时为该模型贡献零，不是贡献未知。Partial coverage 不输出一个看似低负载的部分总和，而是保持缺失/降级语义；消费者不能用零填平。完整窗口中下一次缺失的 pool/model 视为消失；跨窗口累计计数、source incarnation 与旧 frame 去重还需沿 record 路径核对。"
    ),
)
add(
    "global-planner",
    "Global Planner：伸缩意图与 GPU 总预算",
    X(
        "components/src/dynamo/global_planner/orchestrator.py",
        437,
        504,
        "意图缓存、standalone 总量与互补 intent 配对",
    )
    + X(
        "components/src/dynamo/global_planner/kubernetes_capacity_manager.py",
        295,
        328,
        "实际 scale 与 current replicas 的 connector",
    )
    + p(
        "Orchestrator 先缓存 intent，即使本次被拒，后续互补池仍可配对；再计算 GPU delta、预算边界和候选 partner，执行经 CapacityManager 抽象。预算决定不是请求路由；缩容申请被接受也不证明新副本已 ready。该处存在边界恢复与 partner 排序 TODO，不能宣称是全局最优分配器。"
    ),
)
add(
    "planner-observe",
    "伸缩观测：no-operation、授权声称与真实执行分开",
    X(
        "components/src/dynamo/global_planner/scale_handler.py",
        138,
        190,
        "caller 检查、no-operation 与 request 日志",
    )
    + p(
        "no-operation 明确可返回 SUCCESS 但不触碰 backend；因此审计应同时看响应语义、日志和实际 replicas。caller_namespace 当前来自 payload，源码 TODO 要求使用可信 runtime principal 并绑定 Kubernetes 目标；不能把命名空间字符串检查说成已完成跨租户身份认证。请求级 Trace/metrics 需要确认具体 endpoint 接入，本文不虚构专用 Global Router counter。"
    ),
)
add(
    "integration",
    "真正接入跨域路由还需完成什么",
    table(
        ["工作", "验收证据"],
        [
            ["固定实现与范围", "Python grid router / Relay consumer / 自定义策略的实际入口与版本"],
            ["建立网络与身份", "请求、响应、event、discovery、Relay 的作用域和可信认证"],
            ["核对兼容域", "模型/tokenizer/hash/LoRA、KV layout、后端协议和 PD 传输可达"],
            [
                "维护视图",
                "producer generation、snapshot、delta、heartbeat、coverage、disconnect",
            ],
            ["接线策略", "显式记录谁读取 Relay load/CKF，如何筛除 stale/unsupported 与选择目标"],
            ["观测验收", "同请求 pool→local worker→engine 归因，拒绝/重试/清理可解释"],
            ["故障演练", "源重连、落后 subscriber、部分 snapshot、跨池失败和 Decode KV 缺失"],
        ],
    )
    + docs(
        [
            (
                "components/src/dynamo/global_router/README.md",
                "Global Router",
                "实际 Python 模式与后端限制",
            ),
            ("components/src/dynamo/pool_relay/README.md", "Pool Relay", "部署范围与运行诊断"),
            (
                "docs/fern/pages/developer-guide/knowledge-base/modular-components/router/multi-dc-kv-routing.md",
                "Multi-DC KV routing",
                "跨域事实与概念",
            ),
            (
                "lib/llm/src/pool_relay/docs/grpc-contract.md",
                "Relay gRPC contract",
                "版本、snapshot/delta 与错误码",
            ),
        ]
    )
    + p(
        '未部署跨集群系统或验证 WAN 性能；邻章为<a href="planner.html">Planner</a>与<a href="control-plane.html">控制面</a>。'
    ),
)
finish()

# Module 12: verification, replay and performance evidence.
MO = "lib/mocker/src/"
chapter(
    "validation",
    "验证、回放与性能实验",
    "把单元/契约测试、Mocker、offline/online replay 与真实 AIPerf 测量分开，追踪输入、确定性、telemetry 和报告的证据边界。",
)
add(
    "ladder",
    "不同工具回答不同问题",
    table(
        ["层", "能验证", "不能由此证明"],
        [
            ["单元 / property / paused-clock", "纯函数、状态迁移、排序、不变量", "跨进程 GPU 推理正确性或吞吐"],
            ["Backend conformance / fake RPC", "流式契约、取消、指标声明、wire 转换", "真实引擎所有能力和数据面"],
            [
                "Mocker live serving",
                "Frontend / Router / Runtime 的实际请求路径和模拟 engine 行为",
                "真实模型输出质量、CUDA 或 RDMA 性能",
            ],
            ["Offline replay", "虚拟时间下策略、缓存和 workload 的可比行为", "完整线上墙钟开销"],
            [
                "Online/live replay",
                "真实 Tokio 任务与等待下的 Mocker 模拟",
                "自动包括 HTTP / WAN / 真实 GPU",
            ],
            ["AIPerf + real backend", "客户端测得的实际部署延迟与吞吐", "仅凭总时延定位某一源码函数"],
        ],
    )
    + p("本章分析验证工具实现并提供实验设计；本次没有执行推理、replay、故障注入或 GPU benchmark。文档浏览器校验不替代这些行为验证。"),
)
add(
    "conformance",
    "Backend 契约验证：先确认 terminal 与取消",
    X("lib/backend-common/src/testing.rs", 532, 562, "setup_metrics 的声明与 opt-in 验证")
    + X("lib/backend-common/src/testing.rs", 564, 606, "取消后要求 Cancelled terminal")
    + p(
        "conformance 检查 start/generate、并发、KV sources、metrics ranks、cleanup 和取消；应该优先断言结构化输出/错误，而不是用易变日志文字代替语义。缺失 terminal、终态后继续 yield、取消被当 Length 等都可能让上层统计看似正常而实际不正确。"
    ),
)
add(
    "timing",
    "Mocker 使用性能模型，不计算真实模型 token",
    X(MO + "engine_adapter.rs", 144, 172, "DynamoPerfTimingModel 将批量/前缀/上下文输入交给模型")
    + X(MO + "scheduler/metrics.rs", 5, 56, "模拟 scheduler rank 的运行状态与缓存比例")
    + p(
        "Prefill 时间由 batch_size、mean ISL/prefix 等预测，Decode 时间还看活跃 KV 与上下文。模型参数来自配置/校准，不能把其预测直接当某 GPU 实测。gpu_cache_usage_perc 字段在本实现中计算 active_blocks / total_blocks，范围通常为比例，不能因为字段名有 perc 就额外假定已经乘以 100。"
    ),
)
add(
    "input",
    "输入 trace：时间、长度、前缀与会话",
    X(
        "lib/data-gen/src/request_trace/load.rs",
        27,
        89,
        "Dynamo request trace schema 与可选阶段指标",
    )
    + p(
        "trace replay 可以保留 arrival、输出长度、session/tool/workload 等关系；纯长度输入并不能还原原文 tokenizer 或真实 prefix hashes。比较 KV 路由时必须记录 trace_format、block size、prefix 重建策略、arrival speedup 和 agentic 依赖。结构化 request trace 用于 workload/replay，不等价于 OTel span 全量导出。"
    ),
)
add(
    "api",
    "Python API：显式选择 offline / live、拓扑与输出",
    X("components/src/dynamo/replay/api.py", 380, 422, "run_trace_replay 配置面")
    + p(
        "API 控制 worker 数、Prefill/Decode 参数、concurrency、router mode、SLA、性能模型、时间上限、per-request capture、planner 与 telemetry。agentic_lanes 与 concurrency/arrival trace 体现不同负载语义，不能只靠同一总请求数宣称等价。到达率变化与引擎速度变化也不是同一实验变量。"
    ),
)
add(
    "offline",
    "Offline 主循环属于 AISimulate core，Dynamo 做适配",
    X(
        MO + "replay/offline/entrypoints.rs",
        119,
        154,
        "ReplaySpec 的 topology、placement、scaling 与 workload 参数",
    )
    + X(
        MO + "replay/offline/entrypoints.rs",
        196,
        254,
        "canonical engine config、capture 与 Replayer 调用",
    )
    + p(
        "本仓库把 MockerConfig / workload 转为 canonical ReplaySpec，并为 Router / Planner / KV events 安装组合适配。虚拟时间事件循环与 Generalized Mocker Engine 驱动来自 aisimulate_core，本文不冒称该外部依赖的全部内部实现已在此仓库逐行核对。"
    )
    + p(
        "调试时分清组合边界与引擎模型：相同 Router 可能因事件可见时点、KV eviction 模型或 performance model 不同而产生不同决策。"
    ),
)
add(
    "determinism",
    "确定性有多个层级：arrival seed 还不够",
    X(MO + "replay/mod.rs", 169, 215, "ReplayScope、determinism 与调用作用域")
    + X(
        "components/src/dynamo/replay/api.py",
        415,
        422,
        "retune 内部 common random numbers 开关",
    )
    + p(
        "确定性事件循环不自动固定 Router 相同成本的随机破平。arrival seed 只控制到达生成；回放需另记录 router seed、ordinal request IDs、engine/profile、哈希与候选顺序。retune 内层显式使用 canonical router seed 与 ordinal IDs 形成 common random numbers，不应把私有 _deterministic 参数宣传成通用稳定 CLI。"
    ),
)
add(
    "kv-lag",
    "KV 事件 lag 可以和请求完成分开建模",
    X(
        "components/src/dynamo/replay/api.py",
        424,
        450,
        "kv_event_lag、wall-time 与同步 telemetry 文档契约",
    )
    + p(
        "kv_event_lag_ms 在 offline KV-router replay 延迟 block store/remove 的索引可见时间；Prefill/请求完成仍按响应面的 in-band 语义立即观察。这能模拟缓存视图陈旧，不能当 WAN 传输、engine compute 或每条生命周期消息都被同等延迟。"
    ),
)
add(
    "online",
    "Online replay：每次调用拥有新 Tokio runtime",
    X(MO + "replay/online/entrypoints.rs", 68, 99, "两种 live runtime 构造与 block_on")
    + p(
        "它运行真实调度任务和等待，仍是模拟 engine 的 replay；是否经过网络/HTTP 取决于实际入口。内部 Err 首次观察后立即传播，task panic 以 JoinError 浮现；Drop replay-owned runtime 清理未完任务。失败不产完整部分报告，也不承诺恢复 Router 或结算所有 outstanding 请求。"
    ),
)
add(
    "settlement",
    "成功路径先完成生命周期，再出完整报告",
    X(
        MO + "replay/online/live_runtime.rs",
        101,
        133,
        "等待请求完成及 grouped completion boundary",
    )
    + X(
        MO + "replay/online/live_runtime.rs",
        153,
        189,
        "engine → admission forwarder → router → recorder 顺序",
    )
    + p(
        "请求 task 看到终态后，grouped dispatcher 可能还未发布完成副作用，因此要等显式 completion boundary。成功关闭顺序保证报告与路由状态可结算；外部 cancellation 随时返回 online replay cancelled，不应把成功收口机械复制成内部失败恢复协议。"
    ),
)
add(
    "recorder",
    "Recorder 从结构化事件构成报告",
    X(
        MO + "replay/online/recorder.rs",
        31,
        58,
        "arrival、admission、Decode assignment、terminal 与 Finish 事件",
    )
    + X(MO + "replay/online/recorder.rs", 156, 173, "报告 finalize 等待与任务错误传播")
    + p(
        "报告由明确事件构成，不是从散落日志正则拼出来。arrival、admission、first token、terminal 的时钟/序列关系应与 workload 对齐；失败与 pending 请求必须保留分类。per-request report、summary、telemetry 和原始事件的覆盖范围不同，比较时需先核对请求集合。"
    ),
)
add(
    "telemetry",
    "观测源码：采样 cadence 与策略 cadence 独立",
    X(MO + "replay/mod.rs", 68, 94, "ReplayTelemetryOptions 与 policy-neutral observers")
    + X(
        "components/src/dynamo/replay/api.py",
        330,
        347,
        "memory / callback / JSONL sink 与无 sink 校验",
    )
    + p(
        "telemetry 有独立 sample_interval_ms，不应通过打开观测改变 planner 决策调用频率。回调和 JSONL 写入同步运行在 replay loop，其延迟计入 replay wall time；最终 buffered flush 在 simulator 结束 wall_time_ms 后，若关心完整持久化开销要从外层 API 计时。写失败使 replay 失败，已写行仍存在且最后一行可能不完整，不能把有 JSONL 文件当完整成功报告。"
    ),
)
add(
    "units",
    "时钟、throughput 与 goodput 的分母",
    table(
        ["口径", "应记录", "常见错误"],
        [
            ["virtual time", "模拟 arrival / terminal 与阶段耗时", "用模拟时长评价代码 CPU 执行速度"],
            [
                "replay wall time",
                "Rust runtime 构建/执行边界，外层准备和最终 flush 单列",
                "把模拟 throughput 与模拟器处理速度混同",
            ],
            [
                "client TTFT / ITL / E2E",
                "请求发出、首响应与 token 计数语义、错误集合",
                "把平均 latency 等于 p99；把 SSE 帧等于 token",
            ],
            ["output throughput", "成功/总 token 数与测量窗口", "混用输入+输出与纯输出吞吐"],
            ["SLO goodput", "同时满足哪些 SLO、成功请求/时间或 token/时间", "只看最大吞吐忽略拒绝和尾延迟"],
            [
                "cache hit",
                "router estimate / engine reused / logical hash hit",
                "把三种分母不一致的比率合并",
            ],
        ],
    ),
)
add(
    "aiperf",
    "AIPerf 真实测量：先冻结 workload 与参考配置",
    p(
        "AIPerf 是外部独立项目，向实际 endpoint 发流量；本仓库官方指南提供 live deployment 基准流程。一次可比实验至少冻结模型/权重/tokenizer、DGD/镜像/引擎参数、GPU/拓扑、输入输出分布、prefix reuse、并发或到达率、warmup、请求数、超时与错误策略、是否 streaming、SLO 和采样窗口。"
    )
    + table(
        ["阶段", "验收"],
        [
            ["Smoke", "真实请求可完成、输出/usage/终态符合预期，PD 归因可见"],
            ["Baseline", "原始 AIPerf artifacts、manifest/hash、版本与 Collector 配置可追溯"],
            ["Candidate", "一次主要变量；相同 workload 与足够的重复/稳态窗口"],
            ["Attribution", "client + Frontend/Router/engine + GPU/NIXL 指标对齐时间与单位"],
            ["Decision", "按预先定义 SLO/goodput 判断，记录误差与失败样本"],
        ],
    )
    + p(
        "不要把 port-forward 本地链路的网络开销归因于 GPU，也不要用 health probe 替代真实 PD KV 传输。真实负载测试需单独授权与资源预算，本章未启动任何测量。"
    ),
)
add(
    "experiments",
    "给本教程各模块设计有判别力的实验",
    table(
        ["假设", "最小有判别力的设计"],
        [
            [
                "Frontend CPU 是瓶颈",
                "固定 mocker capacity 与 workload，观察 CPU/preprocess/transport，比较重复稳态",
            ],
            ["KV 路由有益", "相同 prefix trace / engine cache，比较 RR/KV；分开估计命中与真实 reuse"],
            ["PD 改善 ITL", "固定 GPU 总量和 workload，对照聚合/PD；同时检查 TTFT、队列和传输成本"],
            ["新策略更稳", "先 deterministic replay 看决策/生命周期，再真实 workload 看尾延迟和错误"],
            ["分层缓存减少重算", "固定内存容量和 cold/warm 条件，核对传输完成、GPU 等待和净收益"],
            [
                "全局 stale 处理正确",
                "fake source 断开/重连/缺 snapshot；检查旧 generation 不污染 replacement",
            ],
        ],
    )
    + docs(
        [
            (
                "docs/fern/pages/reference/components/dynosim-replay-cli-reference.mdx",
                "DynoSim replay reference",
                "固定版本 CLI 与模式",
            ),
            (
                "lib/mocker/src/replay/offline/README.md",
                "Offline replay adapters",
                "组合边界与随机性",
            ),
            (
                "docs/fern/pages/kubernetes/operations/benchmarking-with-aiperf.mdx",
                "Benchmark with AIPerf",
                "live workload 与可比性",
            ),
            (
                "docs/fern/pages/recipes/feature-benchmarks/benchmarking-guide.md",
                "Benchmarking guide",
                "实验配置与归因",
            ),
        ]
    ),
)
finish()

# Module 00: whole-system integration map.
chapter(
    "system-map",
    "系统地图：运行形态与跨模块边界",
    "用实际流水线组装和所有权模型连接 00–12 章，按请求、KV、状态事件、观测与控制反馈五条路径理解整个仓库。",
)
add(
    "layers",
    "Rust、Python 与 Kubernetes 三层如何协作",
    X("Cargo.toml", 5, 62, "workspace 边界、版本与独立 AISimulate core 依赖")
    + X("pyproject.toml", 5, 30, "Python wheel 的 runtime、AISimulate 与平台依赖")
    + table(
        ["层", "常见入口", "主要职责"],
        [
            [
                "Python 组装",
                "python -m dynamo.frontend / sglang / vllm / trtllm / planner",
                "CLI、工厂、引擎绑定与运维/规划",
            ],
            [
                "Rust 核心",
                "lib/runtime、llm、kv-router、backend-common",
                "网络、typed pipeline、选择/准入、流与生命周期",
            ],
            ["内存与传输", "KVBM、GPU Memory Service、NIXL integration", "块所有权、分层搬运、共享分配与数据面"],
            ["Kubernetes", "deploy/operator 与 Helm", "声明式拓扑、Pod/Service/探针与监控接入"],
        ],
    )
    + p(
        "目录存在不等于纳入当前根 workspace 或已实现；外部依赖也不是本仓库 vendored 源码。Python wheel 与 native extension 需要匹配版本，模型引擎自身还有独立依赖与设备要求。"
    ),
)
add(
    "five-flows",
    "同一系统至少有五条独立流",
    table(
        ["流", "路径", "排障问题"],
        [
            [
                "Request / response",
                "Client → Frontend → Router → Worker → engine → stream",
                "被谁接受、派到谁、终态是什么",
            ],
            [
                "KV data",
                "engine / KVBM → connector / device / network / storage",
                "真实字节在哪里、何时可用、谁保护源块",
            ],
            [
                "State events",
                "engine KV / metrics → indexer / active sequences / Relay",
                "状态是否陈旧、丢失、重建或近似",
            ],
            [
                "Observability",
                "日志/Trace/Metrics → exporters → Collector/Prometheus",
                "记录了哪个边界、是否采样/抓取",
            ],
            [
                "Control feedback",
                "Planner / Operator → 目标容量/拓扑 → runtime 新成员",
                "谁决定、谁执行、何时就绪",
            ],
        ],
    )
    + p(
        "状态事件不是 Prometheus scrape；Trace span 不是 KV transfer；伸缩目标不是当前可用容量。跨图阅读时先写清当前讨论的是哪条流。"
    ),
)
add(
    "startup",
    "一条模型注册如何变为可服务流水线",
    X("lib/llm/src/local_model.rs", 674, 709, "LocalModel attach 发布模型、输入模态、角色与依赖")
    + X(
        "lib/llm/src/discovery/controller.rs",
        968,
        1005,
        "构建结果按 checksum 与 generation fencing",
    )
    + flow(
        [
            "engine ready 后解析事实",
            "注册 endpoint / MDC",
            "Frontend 接纳 WorkerSet",
            "异步构建匹配 pipeline",
            "校验 generation 再 commit",
            "模型 serving ready",
        ]
    )
    + p(
        "静态 architectural limit、engine runtime limit、声明式 router policy 和瞬时负载不属于同一个 owner。Discovery 反复事件/重连可能产生晚到构建，generation fencing 防止旧结果复活已退役服务；同名模型也可能同时存在多个部署世代。"
    ),
)
add(
    "pipeline",
    "默认 Rust token 流水线：向下处理，向上投影",
    X(
        "lib/llm/src/entrypoint/input/common.rs",
        472,
        515,
        "Preprocessor → Migration → token backend → Encoder → Prefill → backend 及 backward edges",
    )
    + p(
        "这里的 segment link 是 typed operator 的双向组装，不是每个 edge 都跨进程。preprocessor 负责模板和响应投影，token backend 处理 token 流，encoder/prefill 可按需求激活，最终 backend 路由到生成 worker。Migration 包住可重试 attempt 的状态；Router 选择、Runtime 传输与 engine 执行分别追踪。"
    ),
)
add(
    "custom-processor",
    "自带处理器时少掉哪些 Rust stages",
    X(
        "lib/llm/src/entrypoint/input/common.rs",
        517,
        549,
        "build_preprocessed_pipeline 的差异",
    )
    + p(
        "vLLM/SGLang processor 可以送入 PreprocessedRequest 并自行处理公共协议，这条链不再重复装默认 preprocessor/token backend。字段 owner、指标产生位置和 Trace 路径都随形态变化；不能仅看 Frontend 命令名称就确定所有步骤在 Rust 内。"
    ),
)
add(
    "modes",
    "四种常用运行形态",
    table(
        ["形态", "主路径", "需要额外判断"],
        [
            ["聚合", "一个 generation worker 同时执行上下文与生成", "chunked Prefill、缓存、批量与 ITL 干扰"],
            [
                "PD",
                "PrefillRouter → Prefill + Decode，engine 协调 KV",
                "Completed / Bootstrap、拓扑兼容、staged KV 清理",
            ],
            [
                "Sidecar",
                "Dynamo planes → native engine RPC",
                "proto 能力、独立启动、指标采集与实验支持矩阵",
            ],
            [
                "分层 / global",
                "Global Router 选 pool → local Router 选 worker",
                "当前 grid 策略和 Relay consumer 是否实际接线",
            ],
        ],
    )
    + p(
        "多节点 TP 是单个模型引擎的并行分片；DP 是多个请求副本/秩组；Prefill/Decode 是职责划分。三者可以组合，不能用‘有多个 Pod’推断是 PD 或跨 DC。混合聚合/PD 拓扑需按真实请求 worker attribution 验证。"
    ),
)
add(
    "state-owners",
    "每份状态的权威 owner 与一致性",
    table(
        ["状态", "主要 owner", "读取语义"],
        [
            [
                "模型接纳/流水线",
                "Frontend-local discovery controller",
                "不同 Frontend 可选择不同先到 incumbent",
            ],
            ["engine KV", "engine / connector / KVBM 强引用", "索引可见不等于实际块永久可用"],
            [
                "Router active lifecycle",
                "owning Frontend ActiveSequences",
                "peer replica 与 PromptRegistry 可延迟",
            ],
            [
                "Pool Relay facts",
                "DC-local advertised producers",
                "snapshot / generation / freshness / coverage",
            ],
            [
                "Kubernetes desired state",
                "DGD/DCD 与 controller",
                "spec、status、实际 ready Pod 分开",
            ],
            [
                "GPU shared allocations",
                "GMS server 与 client session",
                "layout commit 与内容 commit 分开",
            ],
        ],
    )
    + p("不要通过增加一个全局锁想当然地统一所有事实；各层故意使用不同一致性/可用性边界。排障先确认你看到的是权威状态、派生快照、近似索引还是历史计数。"),
)
add(
    "observability",
    "跨模块可观测：记录、传播、导出、消费",
    X("lib/runtime/src/metrics.rs", 694, 731, "Runtime 层级化 metric 来源")
    + X(
        "lib/llm/src/frontend_load.rs",
        130,
        175,
        "Frontend metrics 同时作为 Relay load frame 来源",
    )
    + X("lib/backend-common/src/metrics.rs", 136, 164, "Worker 生命周期观测的明确更新点")
    + table(
        ["信号", "核心追踪链"],
        [
            ["Metrics", "定义/单位/标签 → 更新点 → registry/bridge → exporter → scrape/query"],
            [
                "Trace",
                "入口 parent → metadata/header → worker span → engine span/link → exporter",
            ],
            ["Logs", "阶段状态/结构化 ID → 级别过滤 → 采集 → 与请求/worker 关联"],
            ["控制信号", "自然生产者 → event topic/RPC → freshness/coverage → 决策 → 执行"],
        ],
    )
    + p(
        "客户端、Frontend、Router、backend、engine 的 TTFT/ITL 与计数分母不同；秒/毫秒/微秒也不能凭名字猜。章节分别给出实际源码采样位置，不承诺全链路所有数据面已有 span。"
    ),
)
add(
    "reliability",
    "资源释放贯穿所有模块",
    X("lib/backend-common/src/engine.rs", 252, 281, "abort 与 Prefill drain 的边界")
    + X("lib/kvbm-logical/src/blocks/mutable.rs", 96, 119, "type state / Drop 保护内存所有权")
    + p(
        "HTTP disconnect 触发 Context 状态，Router 释放/转交 lease，adapter 通知 engine，Prefill/Decode 为 staged KV 可能继续独立清理。block RAII 与远端 async cleanup 又属于不同时间点。正常终态、Drop、网络 EOF 与设备静默不等价，失败和重试也不是无条件重新发一次相同请求。"
    ),
)
add(
    "reading",
    "00–12 的系统阅读顺序与产出",
    table(
        ["模块", "应掌握的可检查产出"],
        [
            ['<a href="observability.html">01 可观测性</a>', "一个指标/Trace 从产生到消费的完整路径"],
            [
                '<a href="frontend.html">02 Frontend</a> / <a href="runtime.html">03 Runtime</a>',
                "协议 owner、typed pipeline 与跨进程边界",
            ],
            [
                '<a href="router.html">04 Router</a> / <a href="backends.html">05 引擎与 Sidecar</a>',
                "worker 选择理由、引擎请求/响应契约",
            ],
            [
                '<a href="pd-serving.html">06 PD</a> / <a href="kv-cache.html">07 KV 内存</a>',
                "控制与数据时序、块保护和传输完成点",
            ],
            [
                '<a href="planner.html">08 Planner</a> / <a href="control-plane.html">09 控制面</a>',
                "观测到容量目标，再到 ready 资源的闭环",
            ],
            [
                '<a href="reliability.html">10 可靠性</a> / <a href="global-routing.html">11 全局路由</a>',
                "终态与故障域、分层状态和实施现状",
            ],
            ['<a href="validation.html">12 验证与回放</a>', "区分语义证据、预测与真实测量的实验方案"],
        ],
    )
    + p(
        "Storage &amp; Events、PD Trace、部署记录作为跨模块专题保留。‘已展开’代表该主题的教程已提供架构和关键源码路径，不代表逐文件全量审计、所有功能完成或生产能力都经过验证。"
    ),
)
add(
    "fieldwork",
    "用真实部署反查源码的工作顺序",
    flow(
        [
            "镜像 / tag / digest / engine 版本",
            "逻辑 namespace / role / model card",
            "实际请求 / worker / pool 归因",
            "配置与源码对应路径",
            "观测采样与 exporter",
            "以可复现实验确认行为",
        ]
    )
    + p(
        'ACK 的历史快照与 maas-dev 审计分别保留，不把源码版本 1.7.0 的能力直接外推到私有 1.4.1 镜像。先从<a href="ack-LB97i2CZNR/index.html">ACK 部署入口</a>识别事实，再依各专题验证版本差异。本文只完成离线分析与文档检查。'
    ),
)
finish()

for report in reports:
    print(json.dumps(report, ensure_ascii=False))
