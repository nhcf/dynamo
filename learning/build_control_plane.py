#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the offline control-plane architecture and observability walkthrough."""
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
nav = []
parts = []
OP = "deploy/operator/"


def S(key, path, start, end, title):
    return src("cp-" + key, path, start, end, title)


def add(key, title, body, question=None, answer=None):
    nav.append((key, title))
    parts.append(section(key, title, body, question, answer))


add(
    "cp-scope",
    "01 / 控制面的边界与组件地图",
    "<p><strong>控制面维护部署意图、成员关系和容量目标；Request Plane 执行每次推理。</strong>本章覆盖 Kubernetes Operator、Admission、DGD/DCD、DGDR/Profiler、DGDSA、DynamoModel，以及运行在各进程中的 Discovery。Planner 的观测算法已有独立专题，这里补上它与执行器的闭环。</p>"
    + table(
        ["组件/机制", "主要输入 → 输出", "运行位置", "观测重点"],
        [
            [
                "Operator Manager",
                "Kubernetes watch → reconcile/status/events",
                "Go 常驻服务",
                "队列、调谐错误、存量、准入、健康",
            ],
            ["Admission", "API 写请求 → 默认化/校验/允许或拒绝", "Operator Webhook", "包装器计时与拒绝分类"],
            [
                "DGD / DCD",
                "图与组件意图 → provider 资源/Deployment/LWS/状态",
                "Operator controllers",
                "generation、Ready、rollout、资源变更事件",
            ],
            [
                "DGDR / Profiler",
                "模型/硬件/负载/SLA → 候选配置与 DGD",
                "Controller + Job",
                "阶段、失败详情、产物，不等于在线 autoscaling",
            ],
            [
                "Planner / DGDSA",
                "观测与部署状态 → 目标副本 → DGD",
                "Python + Operator",
                "输入完整性、决策、写入与实际收敛",
            ],
            [
                "DynamoModel",
                "模型与服务端点 → base/LoRA 注册与就绪状态",
                "Operator controller",
                "端点存在、直接就绪、fallback-covered",
            ],
            [
                "Discovery",
                "元数据 + endpoint readiness → 可选服务成员",
                "Rust Runtime 内部 daemon",
                "同步、watch、Pod UID、query 范围",
            ],
            [
                "Grove / LWS / DisaggregatedSet",
                "工作负载布局与副本意图 → 调度/部署",
                "provider/外部控制器",
                "provider 自身条件；不把 Pod 调度误算为 Planner 决策",
            ],
        ],
    )
    + note(
        "源码固定于 "
        + PIN
        + "，workspace 1.7.0；不是线上 SGLang 1.4.1 行为保证。未改动集群，也未执行 Go/Rust/Python 推理测试。代码图索引明确排除了 deploy/，Operator 采用直接源码核对；Python/Rust 优先图查询后核对固定提交。"
    ),
)

add(
    "cp-architecture",
    "02 / 三条控制闭环与一条请求路径",
    '<div class="diagram"><div class="lane"><b>部署闭环</b><span>用户 → Admission → Kubernetes API → DGD controller → 选定 provider program → DCD / provider workload → Pod / Service → status / watch → 再调谐</span></div>'
    '<div class="lane"><b>容量闭环</b><span>Frontend / Worker → Prometheus、FPM、部署状态 → Planner → DGDSA scale 或 DGD patch → Operator → Pod 可用 → 反馈</span></div>'
    '<div class="lane"><b>成员闭环</b><span>Worker 注册 → DynamoWorkerMetadata；EndpointSlice / Pod readiness → Runtime join/cache → DiscoveryQuery → Frontend / Router 的成员集合</span></div>'
    '<div class="lane"><b>推理路径</b><span>Client → Frontend / KV Router → Prefill / Decode / Engine；不要求每次请求都经过 Operator 或 kube-apiserver</span></div></div>'
    '<p><a href="control-plane-architecture.mmd">架构图 Mermaid 源文件</a> · <a href="control-plane-reconcile.mmd">调谐时序图 Mermaid 源文件</a>。上方架构以离线 HTML 展示，不依赖在线 Mermaid 渲染服务。</p>'
    + note(
        "运行中的请求可能继续执行，但控制面故障会影响新实例注册、成员更新、扩缩容、部署和恢复。影响依赖缓存、watch 和 provider 状态，不能简单断言控制面故障一定让所有请求立即失败，或完全没有影响。"
    ),
)

add(
    "cp-contract",
    "03 / CRD 是意图与状态契约，不是命令队列",
    "<p>DGD 表达完整推理图，DCD 是组件子资源及其重试、所有权、状态边界。DGDR 表达自动选型需求；Profiler 给出配置，autoApply 决定是否继续部署。DGDSA 为组件暴露独立的 scale 意图，DynamoModel 管理模型及端点关联。WorkerMetadata 是服务注册元数据，不是部署期望。</p>"
    + S(
        "program",
        OP + "internal/controller/dgd_workload_program.go",
        34,
        99,
        "program request/result 与 provider 分发",
    )
    + "<p>当前 DGD controller 选择且只调用一个完整 program：component、Grove 或 DisaggregatedSet。不要把所有部署都画成 DGD → DCD → Deployment 的固定链；provider 负责自身布局与 rollout。Kubernetes Deployment/LWS/调度器也各有自己的控制循环。</p>"
    + S(
        "conditions",
        OP + "internal/controller/dgd_workload_program.go",
        144,
        180,
        "结果应用、ObservedGeneration 与 Ready 条件",
    )
    + note(
        "spec 是期望，status 是上次观测。ObservedGeneration 对齐说明该 spec 已被相应调谐处理，并不单独证明所有 Pod 已 Ready；还需读 Conditions 和组件状态。更新 schema、provider 或 API 版本时应核对实际版本，不能把本章 components API 直接粘到历史 1.4.1 services 清单。"
    ),
)

add(
    "cp-reconcile",
    "04 / 从 API 变更到幂等收敛",
    flow(
        [
            "API 接受资源写入",
            "watch 入队：namespace/name",
            "读取最新对象/删除与 finalizer",
            "选择 program，执行必要变更",
            "保存完整 status",
            "成功保存后发状态转换 Event，等待下一轮",
        ]
    )
    + S(
        "reconcile",
        OP + "internal/controller/dynamographdeployment_controller.go",
        192,
        243,
        "调用 program、保留部分状态、先持久化再发状态事件",
    )
    + S(
        "component",
        OP + "internal/controller/dgd_component_program.go",
        106,
        161,
        "component program：rollout、共享资源、restart、workload、scaling adapter",
    )
    + "<p>调谐基于最新期望/观测状态反复收敛，事件只是触发信号。一次 reconcile 返回 nil，不等于整个图已经服务可用；返回可重试错误通常由框架安排重试。部分无效配置可能已写 Failed 状态后返回 nil，故只盯 reconcile_errors 会漏掉业务失败。</p>",
    "为什么 reconcile 很快不等于模型启动很快？",
    "它可能只创建资源并等待下一次事件；GPU 调度、拉镜像、加载权重和就绪发生在外部异步阶段。",
)

add(
    "cp-admission",
    "05 / Admission 指标：从注册到实际 Observe/Inc",
    S(
        "register",
        OP + "internal/webhook/validation/register.go",
        18,
        34,
        "Lease-aware validator → ObservedValidator → admission handler",
    )
    + S(
        "wrapper",
        OP + "internal/observability/webhook_wrapper.go",
        43,
        89,
        "CREATE / UPDATE / DELETE 的计时与允许/拒绝",
    )
    + S(
        "webhook-metrics",
        OP + "internal/observability/metrics.go",
        43,
        98,
        "Histogram/Counter 定义与记录",
    )
    + table(
        ["指标", "类型/labels", "真正计量范围"],
        [
            [
                "dynamo_operator_webhook_duration_seconds",
                "Histogram；resource_type, operation",
                "被包装 validator 的执行时间，duration.Seconds()；bucket 1ms 到 5s",
            ],
            [
                "dynamo_operator_webhook_requests_total",
                "Counter；resource_type, operation, result",
                "validator 返回后按 err 是否为空记录 allowed/denied",
            ],
            [
                "dynamo_operator_webhook_denials_total",
                "Counter；resource_type, operation, reason",
                "只在 validator 返回 error 时增加",
            ],
        ],
    )
    + "<p>包装器包含 LeaseAwareValidator，但不覆盖整个 API 请求、排队、网络、完整 admission decode/response，也不等于 defaulting webhook 的耗时。Feature Gate 位于 admission handler 外层；在到达 validator 前返回、decode 失败或 panic 打断正常返回的请求，不保证走完这些自定义计数。</p>"
    + S(
        "reason",
        OP + "internal/observability/metrics.go",
        105,
        137,
        "实际拒绝原因按 Kubernetes 错误分类",
    )
    + note(
        "官方参考页列出的 immutable_field_changed / invalid_config 是示例描述；本提交 categorizeError 实际产生 validation、bad_request、conflict、forbidden、timeout、internal 等。普通未匹配 error 会落到 internal，不应按不存在的业务 reason 编写告警。"
    ),
)

add(
    "cp-inventory",
    "06 / 资源存量 Gauge：周期、作用域和旧序列",
    S(
        "resource-def",
        OP + "internal/observability/metrics.go",
        28,
        41,
        "resources_total 实际是 GaugeVec",
    )
    + S(
        "resource-run",
        OP + "internal/observability/resource_counter.go",
        67,
        108,
        "启动时刷新、30 秒周期、每副本运行",
    )
    + S(
        "resource-count",
        OP + "internal/observability/resource_counter.go",
        110,
        160,
        "按 namespace/state 计数及 List 错误处理",
    )
    + "<p>ResourceCounter 使用 Manager 的缓存 client，作用域受 manager cache 与 excludedNamespaces 共同限制。NeedLeaderElection=false，非 leader 也暴露存量；相同管理范围的多个副本不能直接 sum。名称以 total 结尾仍是 Gauge，不使用 rate() 计算资源数量。</p>"
    + table(
        ["情况", "源码行为", "读数影响"],
        [
            ["资源仍处于某状态", "只为本轮观察到的 namespace/state 执行 Set", "近似缓存快照，刷新 30s，加上缓存/抓取延迟"],
            [
                "一个状态最后一个资源迁走/删除",
                "本轮没有此组合，没有 Reset/Delete/显式 Set(0)",
                "旧序列可能留存直到进程重启或另有同组合更新",
            ],
            ["List 失败", "记录 error 并返回，不清理旧值", "保留旧值，不能视为最新状态"],
            ["多 Operator 副本", "每副本均统计自身缓存可见资源", "先限定实例或按相同 scope 去重；max 只能去重复，不能修复旧序列"],
        ],
    )
    + note(
        "静态推导例：A 资源从 successful 变为 failed，新 failed=1 被设置，但若 successful 组合在本轮完全消失，旧 successful=1 不会被置零。两状态相加会读到 2。这里记录源码限制，没有执行故障复现，也未修改实现。"
    ),
)

add(
    "cp-export",
    "07 / 指标注册、HTTPS 暴露与 ServiceMonitor",
    S(
        "registry",
        OP + "internal/observability/metrics.go",
        73,
        81,
        "四组自定义指标注册到 controller-runtime Registry",
    )
    + S(
        "manager", OP + "cmd/main.go", 235, 260, "Metrics server、认证授权过滤、leader election"
    )
    + S(
        "resource-add",
        OP + "cmd/main.go",
        379,
        389,
        "Manager 注册 ResourceCounter runnable",
    )
    + S(
        "monitor",
        "deploy/helm/charts/platform/components/operator/templates/operator-servicemonitor.yaml",
        15,
        49,
        "ServiceMonitor 三态开关及 HTTPS scrape",
    )
    + S(
        "metric-service",
        "deploy/helm/charts/platform/components/operator/templates/metrics-service.yaml",
        15,
        37,
        "Metrics Service 与 ServiceMonitor 创建条件不同",
    )
    + "<p>自定义 Go metrics 与 controller-runtime 调谐 metrics 共用 Registry/metrics server；应用侧 Rust/Python Registry 是另一路。默认安全服务启用认证授权过滤，ServiceMonitor 使用 metrics 端口、HTTPS、15s 和 bearer token。Service 存在不代表 Monitor 存在，也不代表 Prometheus 已选择它。</p>"
    + note(
        "metricsService.enabled=null：Service 可以创建，ServiceMonitor 依 Helm Capabilities 检测 monitoring.coreos.com/v1；true 强制创建；false 跳过。GitOps/helm template 场景尤其要核对渲染结果。模板 insecureSkipVerify=true 是所审默认配置，不要据此推断所有环境都如此。"
    )
    + "<p>排查顺序：实际监听端口/协议 → Service selector/port → Monitor 对象 → Prometheus 的 Monitor selector → bearer token 和授权 → target UP → 指标是否产生。401/403 与网络不通是不同失败层。</p>",
)

add(
    "cp-framework",
    "08 / 调谐、队列和健康不能混作一个 Ready",
    table(
        ["信号", "能说明什么", "不能单独说明什么"],
        [
            [
                "controller_runtime_reconcile_time_seconds / reconcile_total / reconcile_errors_total",
                "按 controller 统计执行耗时、结果、错误（框架指标）",
                "某个 DGD 的业务 Ready、模型加载时间或目标 namespace 级根因",
            ],
            [
                "workqueue_*（以实际 /metrics 为准）",
                "队列等待/积压，用于解释触发到执行前的延迟",
                "引擎队列或推理 token 延迟",
            ],
            ["Operator /healthz", "所注册的 healthz.Ping 响应", "kube-apiserver/所有资源/模型均健康"],
            [
                "Operator /readyz",
                "这里注册 webhookServer.StartedChecker()",
                "所有推理图 Ready，或一定持有 leader",
            ],
            [
                "DGD Ready / component conditions",
                "部署控制器最近观测的工作负载可用状态",
                "单次请求成功或模型 SLA 达标",
            ],
        ],
    )
    + S("health", OP + "cmd/main.go", 481, 491, "Operator liveness/readiness 注册的实际检查")
    + "<p>框架依赖由 deploy/operator/go.mod 固定到 controller-runtime v0.24.1；本章不把依赖包内部的计时逻辑伪装成 Dynamo 自定义实现。调谐错误使用 rate/增量，阶段阻塞还要查 status、Kubernetes Events、Pod 状态与 provider conditions。</p>",
)

add(
    "cp-events",
    "09 / 状态、日志、Kubernetes Event 与 Trace 的关系",
    "<p>DGD 的 programResult 保存完整 status、部分进度和待发状态事件；外层在 Status().Update 成功后才发送这些状态转换 Event。这能避免“状态没保存，成功事件已对外宣告”的矛盾。该约束针对这条 DGD 路径，不能推广成所有 controller 都按相同顺序写事件。</p>"
    + table(
        ["载体", "定位键/用途", "保留与边界"],
        [
            [
                "CR status/conditions",
                "namespace/name、generation、reason/message",
                "当前观测状态；不是完整历史",
            ],
            [
                "Kubernetes Events",
                "involved/related object UID、reason、时间",
                "离散诊断事件；可能聚合、过期，不是审计日志",
            ],
            [
                "Operator 结构化日志",
                "controller、namespace/name、具体错误和动作",
                "由日志平台保存；需保留集群/Pod身份",
            ],
            [
                "OTel spans",
                "Trace ID/Span ID/Link",
                "本次所审 Go main/observability 路径未见推理 traceparent 注入/提取契约",
            ],
        ],
    )
    + note(
        "给 Frontend/Worker 开 OTEL 不会自动把 Operator reconcile、admission 和 DGDR Job 接成推理请求的同一条 Trace。若要部署生命周期 trace，需要定义独立 operation ID、异步因果关系、各语言埋点与导出；本章没有把这项扩展当作现成能力。"
    ),
)

add(
    "cp-dcd",
    "10 / DCD：子资源执行与故障定位边界",
    S(
        "dcd",
        OP + "internal/controller/dynamocomponentdeployment_controller.go",
        184,
        231,
        "初始化 Conditions/Events，选择 LWS 或 Deployment，处理 Service",
    )
    + "<p>组件 program 把图意图转交组件资源；DCD controller 再收敛 Deployment/LWS、Service/入口与可用状态。DCD 能独立重试与报告失败，不应将所有子资源等待都归到 DGD 自身的函数耗时。</p>"
    + "<p>排障从 DGD Ready/组件状态下钻 DCD conditions，再查 Deployment/LWS、Pod scheduling/container 状态和模型初始化日志。DCD 返回 nil 但 Available=False 是合法的“等待外部状态”，不等于 controller_runtime_reconcile_errors 会增长。</p>",
)

add(
    "cp-dgdr",
    "11 / DGDR + Profiler：从 SLA 需求到可部署配置",
    flow(
        [
            "DGDR Pending：校验需求/生成 profiling Job",
            "Profiling：搜索/测量或模拟/输出配置",
            "Ready：产物可用、等待应用",
            "Deploying：创建/跟踪 DGD",
            "Deployed：DGD 满足就绪；失败另有 Failed",
        ]
    )
    + S(
        "dgdr-state",
        OP + "internal/controller/dynamographdeploymentrequest_controller.go",
        495,
        513,
        "实际阶段分发",
    )
    + S(
        "profile-start",
        "components/src/dynamo/profiler/profile_sla.py",
        343,
        395,
        "run_profile 初始化状态并进入配置搜索",
    )
    + "<p>autoApply=true 时可从 profiling 完成进入 Deploying；false 时停在 Ready 供检查/手动应用，不必逐次经历所有图中状态。Profiler 产物包括候选/选型结果和部署配置，性能数据应带上模型、硬件、负载与测量口径；不是通过 Job 成功就直接证明生产 SLA。</p>"
    + note(
        "当前 controller 遇到 spec.V1Beta2 envelope carrier 会提前返回；本章阶段机分析的是实际实现的原生 v1beta1 路径，不把尚未实现的 reconciler 视为已可用。"
    ),
)
add(
    "cp-profiler-observe",
    "12 / Profiler 进度与失败：文件 → sidecar → ConfigMap → status",
    S(
        "profile-status",
        "components/src/dynamo/profiler/utils/profiler_status.py",
        35,
        73,
        "写状态、UTC timestamp、phase/message/error；写失败只 warning",
    )
    + S(
        "copier",
        OP + "internal/controller/dynamographdeploymentrequest_controller.go",
        139,
        197,
        "output-copier relay phase：仅阶段改变才更新 ConfigMap",
    )
    + S(
        "subphase",
        OP + "internal/controller/dynamographdeploymentrequest_controller.go",
        572,
        616,
        "controller 读取 phase/message 并更新 Profiling/Succeeded conditions",
    )
    + S(
        "profile-failure",
        OP + "internal/controller/dynamographdeploymentrequest_controller.go",
        698,
        734,
        "Job 完成后继续核对 profiler_status=failed",
    )
    + table(
        ["环节", "源码语义", "运维含义"],
        [
            [
                "Python 状态文件",
                "写 profiler_status.yaml；异常记录 warning，函数不重新抛出",
                "任务仍运行但文件缺失时先查输出目录/权限/日志",
            ],
            [
                "sidecar",
                "循环约 10s 轮询；同一 phase 不重复 relay message；输出到 ConfigMap",
                "kubectl phase 是粗粒度进度，不是每条实时消息",
            ],
            [
                "controller",
                "watch Job/ConfigMap，校验 phase；保存 status/conditions",
                "阶段数据需经历文件、轮询、API写入、watch等延迟",
            ],
            [
                "完成判断",
                "Job completed 仍检查 profiler_status/profiler_error",
                "Job Succeeded 不保证 Profiler 业务成功",
            ],
        ],
    )
    + "<p>这条进度链是控制面可观测数据，也是状态机输入。排查 profiling 卡住应同时保存 DGDR conditions、Job/两个容器日志、状态文件与输出 ConfigMap；只看 Prometheus 资源总数不足以恢复搜索阶段和错误细节。</p>",
)

add(
    "cp-scale",
    "13 / Planner 与伸缩执行：决定、提交、收敛分开观测",
    '<p>Planner 的 Prometheus/FPM 输入、SLA 决策与自身指标详见 <a href="planner.html">Planner 源码专题</a>。这里沿写入边界继续追踪：声明 scalingAdapter 的组件以 DGDSA 意图为准；严格 scale patch 失败不会悄悄再写 DGD，避免两个副本真值来源。</p>'
    + S(
        "planner-write",
        "components/src/dynamo/planner/connectors/clients/kubernetes_api.py",
        207,
        236,
        "Planner 严格写 DGDSA Scale subresource",
    )
    + S(
        "adapter",
        OP + "internal/controller/dynamographdeploymentscalingadapter_controller.go",
        107,
        148,
        "DGDSA → DGD replicas、Scaled Event 和 LastScaleTime",
    )
    + S(
        "prom-env",
        OP + "internal/dynamo/component_common.go",
        172,
        204,
        "Operator 注入 PROMETHEUS_ENDPOINT，保留用户覆盖",
    )
    + note(
        "adapter.Status.Replicas 在这里直接赋成 adapter.Spec.Replicas；LastScaleTime 是成功提交 DGD 副本更新的时间，不是新 Pod 就绪时间。既要观测决策目标/提交错误，又要读 DGD observedGeneration、rollout/组件就绪及实际可服务容量。HPA/KEDA/Planner 共用目标时还须明确唯一写入者。"
    ),
)

add(
    "cp-model",
    "14 / DynamoModel：模型端点与 LoRA 管理",
    S(
        "model-empty",
        OP + "internal/controller/dynamo_model_controller.go",
        112,
        151,
        "EndpointSlice 候选缺失、probe/LoRA 加载和失败事件",
    )
    + S(
        "model-status",
        OP + "internal/controller/dynamo_model_controller.go",
        164,
        195,
        "直接 ready 与 fallback-covered 的状态区分",
    )
    + "<p>DynamoModel 从模型关联的 Service/EndpointSlice 查候选，驱动端点探测/LoRA 管理并维护 Conditions。它不同于 Request Plane 每次选择 Worker；也不同于 Runtime 的 WorkerMetadata 注册。</p>"
    + note(
        "ReadyEndpoints 只统计直接可服务的端点；fallback-covered 另有字段。base model 与 LoRA 的 Ready 语义也不同，不应把所有模型的 EndpointsReady 或 ReadyEndpoints/TotalEndpoints 当作相同可用率公式。"
    ),
)

add(
    "cp-discovery",
    "15 / Discovery 是分布式成员控制，不是一个中心路由 Pod",
    S(
        "discover-register",
        "lib/runtime/src/discovery/kube.rs",
        280,
        301,
        "Worker 元数据保存到 DynamoWorkerMetadata",
    )
    + S(
        "discover-source",
        "lib/runtime/src/discovery/kube/daemon.rs",
        136,
        164,
        "按 Kubernetes namespace/标签 watch readiness source",
    )
    + S(
        "discover-join",
        "lib/runtime/src/discovery/kube/daemon/state.rs",
        246,
        262,
        "就绪源与 Metadata 需匹配 Pod UID",
    )
    + "<p>Pod 模式从 EndpointSlice 获取 readiness，container 模式改用 Pod/container readiness；元数据从 DynamoWorkerMetadata 来。JoinTable 只合并对应 Pod incarnation 的数据，避免旧同名 Pod 的元数据与新 Pod 的地址错误拼接。客户端随后仍按 DiscoveryQuery 做逻辑 namespace/component/endpoint 过滤。</p>"
    + "<p>因此“同一 Kubernetes namespace 内可见”不等于“所有请求都把所有 Worker 纳入候选”。Kubernetes namespace、Dynamo 逻辑 namespace、模型/endpoint 选择及标签过滤是多个边界，实际隔离必须沿查询与路由选择核验。</p>",
)

add(
    "cp-discovery-observe",
    "16 / Discovery 的可观测语义：connect、sync、watch 三件事",
    S(
        "discover-connect",
        "lib/runtime/src/discovery/kube.rs",
        190,
        199,
        "connectivity 只验证有限 List 权限/可达性",
    )
    + S(
        "discover-ready",
        "lib/runtime/src/discovery/kube.rs",
        88,
        113,
        "首轮同步前等待 daemon Ready",
    )
    + S(
        "discover-watch",
        "lib/runtime/src/discovery/kube.rs",
        439,
        467,
        "订阅与初始快照在读锁保护下衔接",
    )
    + S(
        "discover-lag",
        "lib/runtime/src/discovery/kube.rs",
        552,
        574,
        "broadcast lag 后从 list_state 重建差异",
    )
    + S(
        "discover-errors",
        "lib/runtime/src/discovery/kube/daemon.rs",
        261,
        301,
        "reflector warning、daemon Failed 和状态通道",
    )
    + table(
        ["观测事实", "正确解释"],
        [
            [
                "check_connection 成功",
                "API 可达且能 List Metadata（空集合合法），不证明 watch 健康/缓存已同步/存在 Worker",
            ],
            ["await_daemon_ready 成功", "首轮完整视图已经建立，不等于每个模型存在可用 endpoint"],
            ["reflector warning", "watch/reflector 出错并走 backoff，需结合后续恢复与成员变化"],
            ["Broadcast receiver lagged", "本地消费者落后，触发从缓存重建差异；不是自动判定所有 Worker 故障"],
            ["daemon Failed/Stopped", "同步循环终止，关注生命周期/取消/错误原因"],
        ],
    )
    + note(
        "所审发现路径主要使用 Rust tracing 日志和内部状态通道；没有据这些局部代码宣称完整的 discovery Prometheus 指标集合。建议接入层监测初始同步耗时、反射器错误/恢复、成员数、last-change/last-sync 和查询范围时，先确认现有指标，缺失者作为后续埋点需求。"
    ),
)

add(
    "cp-queries",
    "17 / 查询示例与可验证的排障顺序",
    "<p>示例必须补充真实 cluster/job/instance 筛选；以下按框架/自定义指标语义书写，不承诺你的 1.4.1 集群已产生这些序列。</p>"
    "<pre><code># 调谐错误速率：低流量时也观察绝对增量\nsum by (controller) (rate(controller_runtime_reconcile_errors_total[5m]))\n\n# 准入验证 p95（不是整个 API 写入延迟）\nhistogram_quantile(0.95,\n  sum by (le, resource_type, operation) (\n    rate(dynamo_operator_webhook_duration_seconds_bucket[5m])\n  )\n)\n\n# 拒绝分类：reason 以实际源码/序列为准\nsum by (resource_type, operation, reason) (\n  rate(dynamo_operator_webhook_denials_total[5m])\n)\n\n# 同管理范围副本的库存去重示意：必须先筛 cluster/job/scope\n# 该写法不能消除状态组合残留问题，不作为强一致库存账本\nmax by (resource_type, namespace, status) (\n  dynamo_operator_resources_total\n)</code></pre>"
    + table(
        ["症状", "先查", "再下钻"],
        [
            [
                "API 写入被拒绝",
                "webhook allowed/denied、reason、API error",
                "validator 日志、gate/namespace lease、规则与版本",
            ],
            [
                "DGD 一直 pending，但 reconcile_errors=0",
                "Ready.reason / component status / generation",
                "DCD/provider workload、Pod 调度/加载模型/rollout",
            ],
            [
                "Job Succeeded，DGDR Failed",
                "profiler_status/profiler_error、Profiling/Succeeded",
                "Profiler 和 output-copier 日志、输出 ConfigMap",
            ],
            [
                "副本目标已变，容量仍没增加",
                "DGDSA spec、DGD spec/status、Ready组件数",
                "调度容量、镜像、初始化、注册/发现",
            ],
            [
                "Frontend 看不到 Worker",
                "Discovery API连通、初始sync、逻辑query",
                "DWM、EndpointSlice/Pod readiness、Pod UID 与 watch 日志",
            ],
            [
                "metrics 401/403 或缺目标",
                "HTTPS/认证授权、Monitor selector",
                "delegated-auth RBAC、Prometheus 选择范围、实际端口",
            ],
        ],
    ),
)

add(
    "cp-cluster",
    "18 / 对照你的 ACK 部署：哪些存在，哪些尚未证明",
    '<p>依据 <a href="ack-LB97i2CZNR/dynamo-test-deployment.md">用户整理的 2026-10-28 快照</a>：Operator 1.4.1，DGD 为 Frontend/prefill/decode/aggregated，TCP 请求面、ZMQ 事件面、SGLang/NIXL。以下只做快照映射，不重新宣称现场验收。</p>'
    + table(
        ["对象", "快照结论", "后续读取项"],
        [
            [
                "Operator",
                "存在，ServiceMonitor HTTPS/15s",
                "实际 /metrics 版本、labels、鉴权及 Prometheus target",
            ],
            [
                "DGD 与组件",
                "Ready=True；四角色混合",
                "DCD/provider 状态、generation/hash、当前 API schema",
            ],
            ["独立 Planner", "快照未列出；PodMonitor 存在不能证明 Pod 存在", "实际组件/Pod、配置与输入数据源"],
            [
                "DGDR/Profiler/DGDSA/DynamoModel",
                "安装相应 CRD 不等于存在实例",
                "get 对应 CR、ownerReferences、Job/scale 目标",
            ],
            [
                "Discovery",
                "Frontend 可见多组 endpoint/igd-*",
                "WorkerMetadata/readiness 与当前模型的查询/路由范围；不直接断言串流",
            ],
            [
                "NATS/etcd/Grove",
                "本 DGD 未要求新增；其他 igd 体系使用 Grove",
                "按独立部署所有权区分，不从其他体系推导本 DGD provider",
            ],
        ],
    )
    + '<p>Request Plane 接入另见 <a href="ack-LB97i2CZNR/request-plane-trace-checklist.html">实际部署 Trace 工作清单</a>。本章控制面观测不要求把该清单重做成 Operator 请求 trace。</p>',
)

add(
    "cp-evidence",
    "19 / 源码阅读入口、版本与维护",
    '<p><a href="control-plane-source-manifest.json">源码摘录清单</a>记录每段路径、行号、固定 revision 和整文件 SHA-256。新增章节可独立重建，页面搜索、展开源码、阅读进度和打印均离线可用。</p>'
    + "<ul><li>架构依据：本提交的 docs/fern/pages/developer-guide/knowledge-base/concepts/system-architecture/architecture.md 与各实现文件。</li>"
    '<li><a href="https://docs.nvidia.com/dynamo/dev/reference/observability/operator-metrics">官方 Operator Metrics</a> 用于指标语义对照，dev 页面持续变化；差异以本章固定源码解释为准。</li>'
    '<li><a href="planner.html">Planner 专题</a>使用较早源码基线，跨页比较先核对两页 revision；本章补充当前 scale 提交链。</li>'
    "<li>Go 指标/资源计数、Python 状态与副本 API、Rust discovery 已阅读相关源码。Grove/LWS/controller-runtime/NIXL 等依赖内部实现、Profiler 全搜索数学、Global Planner 算法不是本章逐行覆盖范围。</li></ul>"
    + note(
        "本次是静态分析和文档交付。源码限制、潜在旧序列和文档差异均未当作线上故障定论；没有改生产代码、创建 Issue、执行部署、启动 Profiler 或进行推理压测。"
    ),
    "要证明一次扩容真正完成，至少需要串哪几类证据？",
    "观测输入与决策 → 目标副本写入 → Operator 处理对应 generation → provider/Pod 就绪 → Runtime 注册与客户端成员视图 → 实际请求恢复/容量测量。",
)

result = page(
    "控制面：架构、原理与可观测源码",
    "沿部署意图、容量调节、成员发现三条闭环，追到指标记录、状态持久化和采集出口。",
    "".join(parts),
    nav,
)
result = result.replace(
    '<span class="pill">2026-10-28</span>', '<span class="pill">2026-10-29</span>'
)
result = result.replace("首个源码专题 v0.1", "控制面专题 v1.0")
(OUT / "control-plane.html").write_text(result)
(OUT / "control-plane-source-manifest.json").write_text(
    json.dumps(
        {
            "revision": PIN,
            "analysis_date": "2026-10-29",
            "scope": "Operator, admission, deployment, profiling, scaling, model management and Kubernetes discovery observability",
            "runtime_verified": False,
            "discovery_method": "Graph-first for indexed Python/Rust; deploy subtree excluded from graph, read fixed source directly",
            "excerpts": base["manifest"],
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(
    f"Built control plane chapter: {len(base['manifest'])} excerpts, {len(nav)} sections."
)
