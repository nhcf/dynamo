#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the Planner deployment and Frontend interaction walkthrough."""
import json
import runpy
from pathlib import Path

OUT = Path(__file__).resolve().parent
PIN = "a908c1a14501b4bb03a7c80833edec0f0e571986"
base = runpy.run_path(str(OUT / "build_tutorial.py"))
base["src"].__globals__["PIN"] = PIN
base["manifest"].clear()
src, table, flow, note, section, page = [
    base[name] for name in ["src", "table", "flow", "note", "section", "page"]
]
P = "components/src/dynamo/planner/"
parts, nav = [], []


def S(path, start, end, title):
    return src(
        "planner-deploy-" + str(len(base["manifest"]) + 1), path, start, end, title
    )


def add(key, title, body):
    title = f"{len(nav) + 1:02} / {title}"
    nav.append((key, title))
    parts.append(section(key, title, body))


add(
    "scope",
    "先明确结论与版本",
    "<p><strong>Planner 是独立容量控制组件。Frontend 负责请求服务，Planner 观察流量和 Worker 状态并调整 Worker 副本，正常推理请求不经过 Planner。</strong>二者主要通过 Prometheus 观测和 Worker 成员变化形成反馈，不需要把 Planner 地址配置成 Frontend 的请求下一跳。</p>"
    + note(
        "本章于 2026-11-03 核对，源码固定于官方代码提交 "
        + PIN
        + "，与旧 Planner 教程的 2f296b592 基线分开。分析本地源码和同版本官方仓库文档，未读取实时集群、运行推理或修改部署。"
    ),
)
add(
    "architecture",
    "交互全图：一条请求路径、三条控制链",
    '<div class="diagram"><div class="lane"><b>推理请求</b><span>Client → Frontend / Router → Prefill、Decode 或 aggregated Worker → Client。Planner 不处在这条同步调用链上。</span></div><div class="lane"><b>流量观测</b><span>Frontend /metrics → Prometheus scrape → Planner PrometheusTrafficProvider → 流量预测与 SLA throughput proposal。</span></div><div class="lane"><b>引擎负载</b><span>Worker/rank FPM → Runtime Event Plane → Planner RuntimeFpmProvider → load proposal / 性能模型。</span></div><div class="lane"><b>副本反馈</b><span>Planner → Kubernetes Connector → DGDSA /scale 或兼容 DGD 写入 → Operator / workload provider → Worker 注册/撤销 → Frontend 发现并更新路由。</span></div></div><p><a href="planner-deployment-interaction.mmd">Mermaid 交互图源</a>。Frontend 不需要逐请求向 Planner 请示，Planner 的 generate endpoint 也不是推理转发服务。</p>'
    + S(P + "__main__.py", 33, 67, "初始化、健康用途 mock endpoint 与 --config"),
)
add(
    "axes",
    "部署位置、Connector、运行模式、优化策略分别选",
    S(
        P + "config/planner_config.py",
        1103,
        1123,
        "environment、namespace、backend、mode 与默认目标",
    )
    + table(
        ["维度", "取值 / 建议", "不要混淆"],
        [
            [
                "进程/Pod",
                "一般每个受管 DGD 配 1 个 Planner component；使用专用 planner image",
                "不是 Frontend 线程，也不是每个 Frontend Pod 配一个 Planner",
            ],
            [
                "environment",
                "kubernetes / virtual / global-planner",
                "决定伸缩执行出口，不决定推理是聚合还是 PD",
            ],
            [
                "mode",
                "agg / disagg / prefill / decode",
                "决定受管 Worker 角色，不是 Connector 类型",
            ],
            [
                "optimization_target",
                "throughput / latency / load / sla",
                "throughput 目标与 SLA enable_throughput_scaling 是不同开关",
            ],
        ],
    )
    + "<p>单副本是当前示例的常规形态。不能将多副本 Planner 直接当作已具备 HA leader election 的配置，也应避免同一目标同时由不协调的 HPA/KEDA/另一个 Planner 写入。</p>",
)
add(
    "dgdr",
    "方式一：DGDR 自动生成 Planner（新部署）",
    "<p>在 DynamoGraphDeploymentRequest 的 <code>spec.features.planner</code> 开启/填写 PlannerConfig；Profiler 生成最终 DGD，添加 Planner component、配置 ConfigMap 和挂载，并传入 <code>--config /.../planner_config.json</code>。有额外性能引导数据时还会准备相应数据挂载；不是所有路径都必须有 profiling NPZ。</p>"
    + S(
        "components/src/dynamo/profiler/utils/dgd_generation.py",
        497,
        545,
        "专用 Planner image、ConfigMap 和挂载",
    )
    + S(
        "components/src/dynamo/profiler/utils/dgd_generation.py",
        337,
        371,
        "非 advisory 的 Worker scaling adapter opt-in",
    )
    + "<p>参考完整例子 <code>examples/deployments/dgdr/planner.yaml</code>。其 <code>searchStrategy: rapid</code> 是部署前配置搜索；Planner 的 <code>pre_deployment_sweeping_mode</code> 是运行性能模型引导设置，两者不能仅按相同 rapid 字样混为一步。<code>autoApply: true</code> 才进入自动应用生成部署的路径。</p>",
)
add(
    "manual-dgd",
    "方式二：已有 DGD 增加 Planner component",
    S(
        "examples/backends/sglang/deploy/disagg_planner.yaml",
        7,
        52,
        "Frontend 与独立 Planner component 的实际示例",
    )
    + "<p>Planner 使用 <code>type: planner</code>、<code>replicas: 1</code>，容器运行 <code>python3 -m dynamo.planner --config ...</code>；配置既可内联 JSON，也可挂载 JSON/YAML 文件。SGLang/vLLM/TensorRT-LLM 分别有 disagg_planner.yaml 示例；替换 registry/tag、模型及并行参数后再使用，不要覆盖现有私有镜像配置。</p><p>从 1.1.0 起这些示例要求专用 dynamo-planner image：backend runtime 不保证包含 Planner 的完整依赖。当前 mode=agg 与 PD 均支持；单纯需要 reactive 扩缩容时不必先完成真实 GPU profiling。</p>"
    + S(P + "connectors/kubernetes.py", 92, 119, "手工独立运行时必须知道受管 DGD 身份"),
)
add(
    "operator",
    "Operator 注入什么：身份、Prometheus、RBAC、探针",
    S(
        "deploy/operator/internal/dynamo/component_common.go",
        102,
        124,
        "DYN_NAMESPACE 与受管 DGD 名称/命名空间",
    )
    + S(
        "deploy/operator/internal/dynamo/component_common.go",
        195,
        204,
        "基础设施 Prometheus URL 注入",
    )
    + S(
        "deploy/operator/internal/dynamo/component_planner.go",
        60,
        89,
        "较长 startup probe、指标端口与 ServiceAccount",
    )
    + table(
        ["配置/端口", "用途", "检查内容"],
        [
            [
                "DYN_NAMESPACE",
                "Dynamo 逻辑部署作用域",
                "不能直接当 Kubernetes namespace 或 Prometheus job",
            ],
            [
                "DYN_PARENT_DGD_K8S_NAME / NAMESPACE",
                "受管 Kubernetes DGD 的定位",
                "Planner 放到不同 Pod 不等于自动知道目标 DGD",
            ],
            [
                "PROMETHEUS_ENDPOINT",
                "Planner 查询的 Prometheus HTTP API",
                "必须是实际可达地址，不能指向 Frontend /metrics",
            ],
            ["Planner 9085", "PLANNER_PROMETHEUS_PORT，自身决策/诊断指标", "不是输入流量采集地址"],
            [
                "Planner 9090",
                "DYN_SYSTEM_PORT，/live /health",
                "与 Frontend HTTP API/metrics 端口分开",
            ],
            [
                "planner-serviceaccount",
                "读模型/Pod/EndpointSlice、修改 DGD/DGDSA",
                "对应 namespace 的 RoleBinding 和 /scale 权限",
            ],
        ],
    )
    + "<p>端口值来自当前 Operator 常量，Pod template 可覆盖时应以渲染结果为准。Helm 的 <code>components/operator/templates/planner.yaml</code> 定义权限；cluster-wide chart 模式依然通过 namespace-scoped RoleBinding 限定 Planner 权限。单独建 Deployment 时要自行提供环境变量、ServiceAccount/RoleBinding、配置挂载和相应发现权限。</p>",
)
add(
    "non-k8s",
    "方式三：本地/自定义环境与全局仲裁",
    S(P + "core/planner_factory.py", 32, 68, "三种 Connector 的真实选择")
    + S(P + "connectors/virtual.py", 32, 46, "Virtual 只发布决策，由外部环境执行并回报")
    + "<p>命令统一为 <code>python3 -m dynamo.planner --config planner.yaml</code>。<code>environment: virtual</code> 需要 model_name、Runtime 发现/FPM，以及消费 VirtualConnectorCoordinator 决策的客户端；仅启动 Planner 并不会替你创建/销毁引擎进程。</p><p><code>environment: global-planner</code> 则把局部伸缩意图送到 Global Planner，做共享预算仲裁后执行。Global Router 的跨池选路与 Global Planner 容量协调是独立职责；pool-local Planner 可用 <code>throughput_metrics_source: router</code> 观察局部流量，而不是每个池都读取公共 Frontend 的全局请求量。</p>",
)
add(
    "frontend",
    "Frontend 如何成为 Planner 的观测源",
    S(
        "lib/llm/src/http/service/metrics.rs",
        1784,
        1796,
        "Frontend 接受请求时递增 started counter",
    )
    + S(
        P + "environment/metrics_provider/prometheus_traffic_provider.py",
        34,
        62,
        "Provider 持有 Prometheus API client，并指定 namespace/source",
    )
    + S(
        P + "monitoring/traffic_metrics.py",
        145,
        162,
        "按 model 与 dynamo_namespace 汇总多 Frontend 实例",
    )
    + "<p>Prometheus 抓取各 Frontend 的 <code>/metrics</code>，Planner 查询 Prometheus，而非逐个轮询 Frontend HTTP 或订阅每次请求通知。正常情况下不需要向 Planner 配置 Frontend Service URL。多个 Frontend 负载均衡后，Planner 汇总匹配模型和逻辑 namespace 的序列；不能只抓一个 Pod，或让抓取 relabel 丢失/重写这些业务标签。</p>",
)
add(
    "promql",
    "具体读取什么，以及怎样处理多副本与缺失",
    S(
        P + "monitoring/traffic_metrics.py",
        386,
        433,
        "优先 increase(requests_started_total)，缺失才回退 completed",
    )
    + S(P + "monitoring/traffic_metrics.py", 209, 242, "Histogram sum/count 的窗口加权平均")
    + S(
        P + "environment/metrics_provider/prometheus_traffic_provider.py",
        167,
        190,
        "TTFT/ITL 秒转毫秒与 ISL/OSL/请求量",
    )
    + table(
        ["Frontend 信号", "Planner 的使用", "解释边界"],
        [
            [
                "dynamo_frontend_requests_started_total",
                "窗口 increase 的接纳/开始请求数，用于流量预测",
                "不是只看完成请求；函数名 count/avg 不能当 RPS，换率要除窗口秒数",
            ],
            ["requests_total fallback", "兼容未暴露 started 系列的版本", "仅完成量可能低估在途压力；缺失不能当无流量"],
            [
                "input_sequence_tokens / output_sequence_tokens",
                "窗口 ISL / OSL",
                "通过 Histogram 的 sum/count 估计形状",
            ],
            [
                "time_to_first_token_seconds / inter_token_latency_seconds",
                "TTFT / ITL，Provider 转为毫秒",
                "此处是窗口平均，不是 histogram_quantile P95",
            ],
        ],
    )
    + "<p>Histogram 先把各实例的 sum/count 增量分别相加，再相除，不能平均各 Pod 的均值。started 明确测到 0 可表达闲时；没有可用请求计数返回 None/UNKNOWN，避免误判零需求。其他 Histogram helper 某些失败返回 0，语义与 demand 的 None 不同，排障应逐信号检查。</p>",
)
add(
    "fpm",
    "Frontend 指标不是唯一输入：FPM 直接来自 Worker",
    S(
        P + "environment/metrics_provider/runtime_provider.py",
        144,
        181,
        "按实际 worker component/endpoint 创建 FpmEventSubscriber",
    )
    + S(P + "core/planner_factory.py", 109, 136, "连接 FPM 与 Traffic 两类 provider")
    + "<p>FPM 描述引擎/rank 的排队、运行、KV 与 forward-pass 工作量，经 Runtime Event Plane 进入 Planner；它既不是 Frontend 的 HTTP 指标，也不需要先导入 Prometheus。启用 load-based scaling 时，订阅发现、事件面连通、所需 rank 覆盖与数据新鲜度是核心前提。</p><p><code>optimization_target: throughput/latency/load</code> 自动采用负载型逻辑，主要依赖 FPM；<code>sla + enable_throughput_scaling</code> 才让 Frontend 流量预测成为容量下界的重要依据。两种 SLA scaling 同时开启时，预测给下界，load 在其上调整并经预算约束。因此 Prometheus、FPM 两条链要分别验收。</p>",
)
add(
    "scale",
    "Planner 改变 Worker；Frontend 经发现面感知",
    S(P + "connectors/kubernetes.py", 1563, 1583, "按是否声明 scalingAdapter 选择副本权威")
    + S(P + "connectors/kubernetes.py", 1597, 1611, "DGDSA /scale 与旧 DGD 写入路径")
    + "<p>目标是 Prefill/Decode（agg 映射到相应受管 Worker），常规伸缩不负责增加 Frontend HTTP 副本。已经声明 scalingAdapter 的组件从 DGDSA 读取权威 desired 并写 /scale；未声明时仍有兼容的 DGD replicas 更新。Operator/provider 再创建/删除工作负载，ready Worker 发布模型与端点，Frontend 更新可路由成员。</p><p>这个反馈经过调谐、调度、模型加载、就绪与发现传播，有延迟。Frontend 会在成员/准入约束内继续做每请求选择；新增 Pod 不等于马上获得可服务容量。当前在线 NativePlannerBase 明确拒绝 config.retune，提示该版本为 replay-only；因此不能把回放中的 Router 调参当作线上 Planner 已向 Frontend 下发配置。常规扩缩容更不是每请求调度指令。</p>"
    + S(P + "core/base.py", 128, 141, "当前在线 Planner 拒绝 replay-only retune"),
)
add(
    "readiness",
    "两个 namespace、初始化与故障边界",
    S(
        P + "connectors/kubernetes.py",
        134,
        162,
        "从 Worker status.runtimeNamespace 解析当前世代",
    )
    + S(
        P + "connectors/kubernetes.py",
        1193,
        1213,
        "仅 Frontend 不 ready 时，允许继续 Worker 伸缩的窄例外",
    )
    + "<p>配置的基础逻辑 namespace 与实际 Worker generation namespace 可以不同，Connector 根据 status.runtimeNamespace 或旧 backing-kind/hash 规则解析；不能手工盲目拼后缀。FPM 按解析后的 Worker endpoint 订阅，Prometheus 流量仍须匹配公共 Frontend 的模型与 namespace 标签。</p><p>Planner._async_init 完成后才注册健康用途 generate endpoint，Operator startup probe 允许等待 Worker 启动。当前 Connector 对“仅 Frontend 不 ready”有继续伸缩 Worker 的窄例外，仍检查 Worker 生命周期；不能将其推广为所有不 ready 都可以缩容。</p><p>稳定运行且 Worker 健康时，Planner 故障主要意味着容量决策停止，不应让每个推理请求失去必需下一跳；Frontend 故障则影响请求入口和流量可见性。额外配置的部署依赖、全部 Worker 不可用或模型准入异常会改变具体结果。</p>",
)
add(
    "checklist",
    "接入工作清单与排查顺序",
    table(
        ["顺序", "需要拿到的证据"],
        [
            [
                "1. 固定版本与模型",
                "Operator / Planner / backend 镜像 tag/digest、model、TP/PP/DP，与线上私有版本区别",
            ],
            [
                "2. 选部署入口",
                "已有 DGD 增 Planner，或 DGDR features.planner 自动生成；一般单副本专用 CPU Planner Pod",
            ],
            [
                "3. 定位控制目标",
                "K8s namespace、parent DGD、基础/实际 Dynamo namespace、受管 role 与 GPU预算",
            ],
            [
                "4. 准备权限与网络",
                "ServiceAccount/RoleBinding、DGD/DGDSA /scale、Discovery/Event Plane 和 Prometheus 可达",
            ],
            [
                "5. 先验指标",
                "Prometheus 有完整 Frontend 标签；started/ISL/OSL/TTFT/ITL 查询命中，FPM/rank 新鲜",
            ],
            ["6. 选策略", "throughput/latency/load 用 FPM；SLA 明确目标与预测开关、性能模型 warmup"],
            ["7. 先观察建议", "advisory:true 时核对建议、诊断、metrics；不会执行容量变更"],
            [
                "8. 验证整个闭环",
                "决策 → DGDSA/DGD desired → ready Pod → Discovery → Frontend 实际 worker 归因",
            ],
        ],
    )
    + '<p>Planner 自身 9085 metrics 只能证明它发布了哪些决策信号；不证明它成功读到了 Frontend，也不证明 Operator 已把目标副本变成 ready GPU。HPA/KEDA 对 Frontend 的扩缩容可另行配置，避免与 Worker 容量控制混在一起。</p><p>可从已保存的 <a href="ack-LB97i2CZNR/dynamo-test-deployment.md">ACK 部署快照</a>反查是否已有 Planner、Prometheus 和权限，但该快照属于历史私有镜像证据，本次未重新核验集群。</p>',
)
add(
    "mixed-dgd-audit",
    "现有混合 DGD 清单：没有 Planner，需先确定管理范围",
    '<p>已核查本地 <a href="ack-LB97i2CZNR/deploy/nhi-trace-demo-mixed-dgd.yaml">nhi-trace-demo-mixed-dgd.yaml</a>：DGD 为 <code>nhi/dynamo-trace-demo-mixed</code>，仅包含下列四个组件。</p>'
    + table(
        ["组件", "类型", "副本", "Worker 逻辑 namespace"],
        [
            ["Frontend", "frontend", "1", "按 Operator / Frontend 的命名空间配置发现"],
            ["aggregated", "worker", "1", "nhi-dynamo-trace-demo-mixed-agg"],
            ["prefill", "prefill", "1", "nhi-dynamo-trace-demo-mixed-pd"],
            ["decode", "decode", "1", "nhi-dynamo-trace-demo-mixed-pd"],
        ],
    )
    + "<p>该文件没有 type: planner 或运行 dynamo.planner 的容器，也未显式声明 Worker scalingAdapter。它可用于固定副本推理与 Trace 验证，但本清单本身没有配置 Planner 自动扩缩容；不能据此判断集群是否另有独立控制器。</p>"
    + "<p>启用时先明确只管理 PD 池还是聚合池，以及对应的流量、FPM namespace 与 Worker 选择。不要直接假设一个普通 Planner 同时管理两种运行模式的三类 Worker。私有 SGLang 1.4.1 还需匹配版本的 Planner image、配置和 Operator 权限。</p>"
    + "<p>文件头记录了组件集合不可原地增删的准入约束；新增 Planner 前需核对线上 Operator/webhook，不能将追加组件并直接 apply 视为已验证的操作。本项是本地清单核查，没有修改此 YAML 或连接集群。</p>",
)
add(
    "references",
    "同版本官方资料与相邻专题",
    table(
        ["资料", "用途"],
        [
            [
                f'<a href="https://github.com/ai-dynamo/dynamo/blob/{PIN}/{path}">{title}</a>',
                purpose,
            ]
            for path, title, purpose in [
                (
                    "docs/fern/pages/kubernetes/auto-deployment/dynamo-planner.mdx",
                    "Tune the Planner",
                    "策略与两种 SLA scaling 的区别",
                ),
                (
                    "docs/fern/pages/developer-guide/knowledge-base/modular-components/planner/planner-guide.md",
                    "Planner Guide",
                    "配置与 advisory、数据源和生成部署",
                ),
                (
                    "docs/fern/pages/developer-guide/knowledge-base/modular-components/planner/planner-examples.md",
                    "Planner Examples",
                    "手工部署与 virtual 接入",
                ),
                (
                    "examples/deployments/dgdr/planner.yaml",
                    "DGDR example",
                    "自动生成 DGD 的完整入口",
                ),
            ]
        ],
    )
    + '<p>相邻章节：<a href="planner.html">Planner 算法与观测源码（旧基线）</a> · <a href="frontend.html">Frontend</a> · <a href="control-plane.html">控制面</a> · <a href="global-routing.html">全局路由</a>。</p>',
)
result = page(
    "Planner 的部署与 Frontend 交互",
    "独立部署、双观测通道、Worker 伸缩和 Frontend 路由反馈。",
    "".join(parts),
    nav,
)
result = result.replace(
    '<span class="pill">2026-10-28</span>', '<span class="pill">2026-11-03</span>'
).replace("首个源码专题 v0.1", "Planner 部署专题 v1.0")
(OUT / "planner-deployment.html").write_text(result)
(OUT / "planner-deployment-source-manifest.json").write_text(
    json.dumps(
        {
            "revision": PIN,
            "analysis_date": "2026-11-03",
            "runtime_verified": False,
            "excerpts": base["manifest"],
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(
    f"Built Planner deployment: {len(nav)} sections, {len(base['manifest'])} excerpts"
)
