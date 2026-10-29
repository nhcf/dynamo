#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render a read-only deployment audit from retained evidence."""
import html
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
d = json.loads((OUT / "deployment-evidence.json").read_text())
r = json.loads((OUT / "runtime-checks.json").read_text())
m = json.loads((OUT / "monitoring-evidence.json").read_text())
a = json.loads((OUT / "additional-checks.json").read_text())
E = html.escape


def table(h, rows):
    return (
        '<div class="scroll"><table><thead><tr>'
        + "".join("<th>" + x + "</th>" for x in h)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join("<td>" + str(x) + "</td>" for x in row) + "</tr>"
            for row in rows
        )
        + "</tbody></table></div>"
    )


def evidence(name, obj):
    return (
        "<details><summary>展开证据 · "
        + name
        + "</summary><pre>"
        + E(json.dumps(obj, ensure_ascii=False, indent=2))
        + "</pre></details>"
    )


def sec(id, title, content):
    return '<section id="' + id + '"><h2>' + title + "</h2>" + content + "</section>"


rows = []
for p in d["dynamo_pods"]:
    statuses = {s["name"]: s for s in p["status"]["containerStatuses"]}
    for c in p["containers"]:
        s = statuses[c["name"]]
        rows.append(
            [
                E(p["name"]) + "<br><small>" + c["name"] + "</small>",
                "<code>" + E(c["image"]) + "</code>",
                "Ready=" + str(s["ready"]) + "<br>restarts=" + str(s["restartCount"]),
                "<code>" + E(s["imageID"]) + "</code>",
            ]
        )
b = sec(
    "conclusion",
    "01 / 当前状态与分析边界",
    '<p><strong>范围更新：</strong>用户随后确认完整 Dynamo 部署在另一个 kubeconfig 集群；此处仅为 maas-dev 快照，不代表完整部署。<a href="../ack-LB97i2CZNR/index.html">目标集群连接检查</a>。</p>'
    + """<div class="callout">当前是“平台基础设施已安装、当前未见 Dynamo 推理工作负载”的状态，不能据此评价推理吞吐、TTFT/ITL 或模型端到端服务健康。</div>"""
    + table(
        ["检查项", "结果", "结论边界"],
        [
            [
                "Context",
                "maas-dev；cluster 名 maas-product1",
                "从 maas-prod 切换，所有后续查询显式指定 maas-dev；当前 context 保留为 maas-dev。",
            ],
            [
                "平台",
                "Operator / NATS / Grove 共 3 个 Running、Ready Pod",
                "全部容器 Ready、当前实例重启数 0；不是历史 SLA 证明。",
            ],
            [
                "Dynamo CR",
                "DGD / DCD / DGDR / DynamoModel / ScalingAdapter 全命名空间均为 0",
                "只描述采集时刻；不代表从未部署过或其他推理平台没有模型。",
            ],
            [
                "推理 Pod",
                "未发现 Dynamo 命名/镜像或 Dynamo 标签的推理 Pod",
                "未取得 Frontend / Worker / Planner 引擎配置和运行指标。",
            ],
            [
                "访问方式",
                "Kubernetes/Helm 只读查询；本机临时 port-forward",
                "未创建/修改/重启任何工作负载，未发起模型推理请求。",
            ],
        ],
    )
    + evidence(
        "采集说明", {k: d[k] for k in ["context", "captured_at", "collection_policy"]}
    ),
)
b += sec(
    "versions",
    "02 / 版本、镜像与发布元数据",
    table(
        ["层次", "实际值", "判断"],
        [
            [
                "Helm release",
                "dynamo-system / namespace dynamo / revision 6",
                "deployed；最近 Helm 更新时间 2026-08-25。",
            ],
            [
                "平台 Chart",
                "dynamo-platform-1.4.1 / appVersion 1.4.1",
                "与 Operator 的镜像和 --operator-version 一致。",
            ],
            [
                "Operator",
                "nvcr.io/nvidia/ai-dynamo/kubernetes-operator:1.4.1",
                "实际 imageID 已记录，复现优先使用 digest。",
            ],
            ["NATS", "nats:2.10.21-alpine", "/varz 实测 version=2.10.21；不能只看 label。"],
            [
                "NATS reloader",
                "natsio/nats-server-config-reloader:0.16.0",
                "独立 sidecar，只负责配置重载。",
            ],
            ["Grove", "v0.1.0-alpha.12-rc1", "与官方 1.4.1 release 的 Grove 依赖一致。"],
            [
                "本地源码",
                "2f296b592，workspace.version=1.7.0",
                "不是集群正在运行的版本，教程中的新功能需要按部署版本重新验证。",
            ],
            [
                "推理引擎",
                "未部署可识别 Dynamo Worker",
                "不能将 release notes 中的 vLLM / SGLang / TensorRT-LLM 版本当作集群实装版本。",
            ],
        ],
    )
    + table(["Pod / Container", "期望镜像", "当前健康", "实际 digest"], rows)
    + """<p>NATS ConfigMap 的 app.kubernetes.io/version 标签为 2.11.1，但运行镜像、Helm computed image 和 /varz 都是 2.10.21。这里是 Chart 元数据与运行版本不同，不是镜像偏离 Helm 配置的证据。</p><p>版本参考：<a href="https://github.com/ai-dynamo/dynamo/releases/tag/v1.4.1">官方 v1.4.1 release notes</a>。本报告不把当前 dev 文档直接作为 1.4.1 功能清单。</p>""",
)
op = next(p for p in d["dynamo_pods"] if "controller-manager" in p["name"])
b += sec(
    "operator",
    "03 / Operator：启动、调度与探针",
    """<p>manager 使用 <code>/manager --config=/etc/dynamo-operator/config.yaml</code>，并显式传入 operator-version、operator-image 和 image-pull-policy。配置挂载自 dynamo-system-config。</p>"""
    + table(
        ["配置项", "实际值", "含义"],
        [
            [
                "副本/资源",
                "1 replica；requests 100m CPU / 256Mi；limits 500m / 1Gi",
                "单个控制面实例；leader election 已开启，不代表当前有多副本。",
            ],
            [
                "调度",
                "Grove enabled；KAI=false；Volcano=false；terminationDelay=4h",
                "Grove 接管协同编排；不要从它的安装推断已有 GPU 工作负载。",
            ],
            [
                "基础通信",
                "nats://dynamo-system-nats.dynamo.svc.cluster.local:4222",
                "已配置 NATS；本次没有据此推导所有发现路径或 etcd 是否必需。",
            ],
            ["服务网格", "serviceMesh.enabled=false", "不通过当前配置开启网格接入。"],
            [
                "Metrics",
                "bindAddress=0.0.0.0；secure=true；Service 8080",
                "HTTPS 认证接口，与健康端口不同。",
            ],
            [
                "Liveness",
                "HTTP /healthz:8081；initialDelay=15s；period=20s；timeout=1s",
                "控制器进程健康，不是模型推理探测。",
            ],
            [
                "Readiness",
                "HTTP /readyz:8081；initialDelay=5s；period=10s；timeout=5s",
                "控制面就绪，不代表模型可服务。",
            ],
            [
                "OTLP/JSONL",
                "Pod spec 未发现 DYN_LOG / OTEL_* 环境变量；所读日志是文本格式",
                "未取得 Operator 主动导出 OTLP 的配置证据；也未验证外部日志 Agent 是否采集它。",
            ],
        ],
    )
    + evidence("Operator 实际启动与资源", op["containers"])
    + evidence(
        "Operator ConfigMap",
        next(c["data"] for c in d["configmaps"] if c["name"] == "dynamo-system-config"),
    ),
)
b += sec(
    "infra",
    "04 / NATS、Grove 与残留资源",
    table(
        ["组件", "实际配置/实测", "影响与注意点"],
        [
            [
                "NATS",
                "单副本；4222 客户端、8222 monitor；JetStream 已开启；max_payload=15728640（15MiB）",
                "本次 /varz connections=0、in_msgs=0、out_msgs=0；符合无推理工作负载的快照，不是历史利用率。",
            ],
            [
                "NATS 存储",
                "fileStore PVC disabled；/data 未挂持久化卷",
                "store_dir 位于容器可写层；Pod/容器替换可能丢失文件状态。当前存储统计为 0，未观察到数据丢失事件。",
            ],
            [
                "NATS 观测",
                "8222 /varz=200；/metrics=404；当前 Pod 没有 Prometheus exporter sidecar",
                "8222 是 JSON monitoring 接口，不能直接当 Prometheus exposition 抓取。",
            ],
            [
                "Grove",
                "1 replica；requests 50m / 128Mi / ephemeral-storage 128Mi；memory limit 1Gi",
                "CPU limit 未显式设置。health 9444、webhook 9443、metrics 9445。",
            ],
            [
                "Grove 功能",
                "logLevel=info；logFormat=json；topologyAwareScheduling=false；autoMNNVL=false",
                "配置声明这些高级功能关闭，不等于 GPU 网络存在故障。",
            ],
            [
                "Grove 观测",
                "HTTP 9445 /metrics 实测 200；170 个 TYPE family",
                "端点可输出指标，但本次未找到对应 Grove Monitor 资源；是否经外部平台采集仍待验证。",
            ],
            [
                "Global Planner 残留",
                "nanhu-infer-global-planner-shadow-soak Service / Helm release 仍存在；Endpoints 地址数 0",
                "dynamo namespace 当前没有对应 Deployment/Pod。不能视为正在运行的 Planner，也没有判定其历史用途。",
            ],
        ],
    )
    + evidence(
        "NATS / Grove 运行端点",
        {k: r[k] for k in ["nats_runtime", "nats_prometheus_path", "grove_metrics"]},
    ),
)
mon = []
for o in d["podmonitors"]["items"]:
    s = o["spec"]
    ends = s["podMetricsEndpoints"]
    mon.append(
        [
            o["metadata"]["name"],
            E(json.dumps(s["selector"], ensure_ascii=False)),
            ", ".join(
                str(e.get("port", "relabel → 9090"))
                + " "
                + e.get("path", "/metrics")
                + " / "
                + e.get("interval", "")
                for e in ends
            ),
            "any=true；当前匹配 Dynamo 推理 Pod=0",
        ]
    )
b += sec(
    "monitoring",
    "05 / 可观测配置：配置存在与实际采集分开看",
    """<h3>应用指标发现</h3><p>四个 PodMonitor 均存在，跨 namespace 选择；Helm computed values 的 podMonitors.enabled=null，安装时已生成资源。nixlTelemetry=false，Worker Monitor 当前没有 NIXL 端点。</p>"""
    + table(["PodMonitor", "Pod selector", "抓取端点", "当前情况"], mon)
    + """<h3>Operator 指标</h3>"""
    + table(
        ["检查", "结果", "结论"],
        [
            [
                "ServiceMonitor",
                "dynamo-system-operator；15s；HTTPS /metrics；命名端口 metrics；bearerTokenFile；insecureSkipVerify=true",
                "配置了带 Token 的 HTTPS 抓取。",
            ],
            [
                "选择标签",
                "ServiceMonitor 有 release=prometheus；PodMonitor 只有 managed-by=Helm",
                "若平台按 release 标签选择，二者可能被区别对待；ARMS 实际选择规则未验证。",
            ],
            [
                "端点请求",
                "临时端口转发后，未带认证请求返回 401 Unauthorized",
                "证明 HTTPS 服务响应并要求认证，不是认证抓取成功的证据。",
            ],
            [
                "Prometheus CR",
                "集群不提供 prometheuses.monitoring.coreos.com 资源类型",
                "不能按标准 kube-prometheus-stack 的 Prometheus CR 来解释全部采集行为。",
            ],
        ],
    )
    + """<h3>集群已有监控链路</h3>"""
    + table(
        ["链路", "读取到的证据", "Dynamo 接入判断"],
        [
            [
                "ARMS",
                "arms-prom 中有 arms-prometheus-operator:v1.0，带云平台访问凭据引用/配置；未读取凭据值",
                "未取得云端目标和选择规则，无法确认 Operator ServiceMonitor 是否被采集。",
            ],
            [
                "Higress Prometheus",
                "/prometheus/api/v1/targets 成功；7 个 active targets；jobs=prometheus、k8s_nodes、k8s_pods",
                "当前 targets 无 Dynamo；dynamo_operator_resources_total 即时查询为空。仅描述该 Prometheus。",
            ],
            [
                "Higress 配置",
                "Pod 抓取仅保留 higress-system 的 higress-gateway；保留期 6h",
                "它不是现成的 Dynamo 应用指标接入链路。",
            ],
            [
                "maas-dev OTel Collector",
                "0.120.0；traces receiver=otlp；metrics receiver=prometheus；仅 litellm 抓取 job",
                "不能因有 OTLP receiver 就认定 metrics 接受 OTLP。当前 metrics pipeline 未引用 otlp。",
            ],
            [
                "日志链路",
                "该 Collector 无 logs pipeline；Dynamo namespace 无所见 Loki/Alloy/Tempo 部署",
                "其他 namespace 有日志/OTel组件，但未验证其是否收集 Dynamo。不能宣称集群全局没有日志采集。",
            ],
            [
                "模型/硬件/传输",
                "当前无 Dynamo Worker，未得到 TTFT/ITL、引擎、GPU 或 NIXL 样本",
                "不能判断推理可观测闭环已完成。",
            ],
        ],
    )
    + evidence(
        "实际 Monitor 配置",
        {"podmonitors": d["podmonitors"], "servicemonitors": d["servicemonitors"]},
    )
    + evidence("Collector pipeline", m["maas_dev_collector"])
    + evidence(
        "实测 Prometheus targets / query",
        {
            k: r[k]
            for k in [
                "higress_targets",
                "higress_dynamo_query",
                "operator_metrics_unauthenticated",
            ]
        },
    ),
)
crd = json.loads((OUT / "crd-versions.json").read_text())
b += sec(
    "differences",
    "06 / 版本边界和配置差异",
    table(
        ["事实", "应如何理解"],
        [
            [
                "Operator 运行版本 1.4.1；本地教程基线为 1.7.0 workspace",
                "此前源码中的 OTLP metrics、FPM、Frontend 统计等能力需按实际运行镜像重新验证。",
            ],
            [
                "DGD/DCD/ScalingAdapter CRD 同时 served v1alpha1 和 v1beta1；storage 仍是 v1alpha1",
                "Operator 最近 INFO 日志内反复出现 v1alpha1 deprecation warning；这是弃用提示，不是已证实 reconcile 失败。",
            ],
            [
                "CRD 标签无法直接确定 release",
                "仅凭 API 形状不能断定 CRD 来源或不兼容；更新前应核对 Operator、CRD、Chart 组合。",
            ],
            [
                "Worker Monitor generation=4；当前含 system-0/system-1",
                "与部分新教程形态相似，也不能据此认定 Operator 二进制是新版。以镜像 digest 为准。",
            ],
            [
                "NATS Label 2.11.1，镜像与 /varz 2.10.21",
                "运行版本优先于 Chart appVersion 标签；computed image 与 Pod 一致。",
            ],
        ],
    )
    + evidence("实际 CRD 版本", crd),
)
b += sec(
    "next",
    "07 / 下一步实施与验收顺序",
    table(
        ["优先级", "工作", "验收标准"],
        [
            [
                "P0",
                "先确认 maas-dev 当前是否只作为平台预置；若需推理实战，选定一个模型与实际 runtime 镜像版本",
                "出现对应 DGD/DCD/Frontend/Worker，能关联部署声明、镜像 digest 和模型服务。",
            ],
            [
                "P1",
                "确认 ARMS 是否消费 Dynamo Monitor，并验证抓取身份 /metrics 权限",
                "在实际采集后台找到 Operator target UP，并查到该版本真实存在的指标。",
            ],
            [
                "P1",
                "明确应用指标采用 ARMS PodMonitor 或 Collector Prometheus receiver；防止重复采集",
                "有流量后看到 Frontend/Worker/引擎序列，标签能关联模型、部署和 Pod。",
            ],
            [
                "P1",
                "补齐日志、Trace Collector 路由和部署端变量，遵循实际 runtime 版本能力",
                "请求 ID / Trace ID 可关联到前端和 Worker；不能只验收 Collector Running。",
            ],
            [
                "P2",
                "补齐 Grove 与 NATS 遥测；按需要设计 NATS PVC 和副本策略",
                "观测端点与采集格式匹配；若需持久化，单独验证重建后状态。",
            ],
            [
                "P2",
                "核对 CRD/Operator/Chart 版本组合与弃用 API 迁移方案",
                "不依据当前源码直接升级或修改 CRD storage。",
            ],
        ],
    )
    + """<p>以上是分析后的实施建议，本次均未执行配置修改、部署新模型或升级。NATS 持久化与控制面 API 迁移应在有具体工作负载和恢复要求后再确定方案。</p>""",
)
b += sec(
    "evidence",
    "08 / 证据与复查命令",
    """<p>证据保留在本地目录，未查询 Kubernetes Secret，配置中敏感值已做筛除。配置快照、运行端点和外部监控后台的可见性是不同证据层级。</p><ul><li><a href="deployment-evidence.json">部署、Helm、容器、Monitor 与 ConfigMap 摘要</a></li><li><a href="monitoring-evidence.json">现有 Collector pipeline 与 ARMS 组件摘要</a></li><li><a href="runtime-checks.json">Operator / Grove / NATS / Prometheus 端点实测</a></li><li><a href="additional-checks.json">Higress 配置与 Operator 日志摘要</a></li><li><a href="crd-versions.json">CRD served/storage/deprecated 版本</a></li></ul><pre>kubectl config current-context
kubectl --context=maas-dev get pods -n dynamo -o wide
helm list --kube-context maas-dev -n dynamo
kubectl --context=maas-dev get dynamographdeployments,dynamocomponentdeployments -A
kubectl --context=maas-dev get podmonitor,servicemonitor -n dynamo
python3 learning/maas-dev-deployment/collect.py</pre><p>collect.py 重新采集主要配置；本次临时端点检查结果在 runtime-checks.json，不会仅靠重跑 collect.py 自动刷新。历史快照不保证当前集群状态不变。</p><p><a href="../index.html">返回系统教程大纲</a> · <a href="../observability.html">回到可观测性源码解读</a></p>""",
)
css = """*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f5f5f0;color:#20303c;font:16px/1.85 system-ui,'Microsoft YaHei',sans-serif;overflow-wrap:anywhere}main{max-width:1200px;margin:auto;padding:45px 28px 90px}h1{font-size:40px;line-height:1.3}h2{font-size:25px;margin:0 0 20px}h3{font-size:19px}header{border-bottom:1px solid #d6e0d8;padding-bottom:28px}section{padding:35px 0;border-bottom:1px solid #d6e0d8}.kicker,a{color:#146c59}.kicker{letter-spacing:.1em;font-weight:750;font-size:13px}.lead{color:#596c77;font-size:19px}.meta,small{color:#667985;font-size:12px}nav{display:flex;flex-wrap:wrap;gap:15px}nav a{font-size:14px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;background:white;font-size:14px}th,td{padding:12px 14px;text-align:left;vertical-align:top;border-bottom:1px solid #dce4dd}th{background:#eaf0e9}td:first-child{min-width:120px}code{font:12px/1.7 ui-monospace,monospace;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#17252c;color:#d9e9e3;font:12px/1.8 ui-monospace,monospace;padding:20px;border-radius:8px;max-height:560px;overflow:auto}details{background:white;border:1px solid #dce4dd;border-radius:8px;margin:16px 0;padding:14px}summary{cursor:pointer;font-size:14px;font-weight:650}.callout{border-left:3px solid #146c59;background:#eaf4ef;padding:18px;margin:20px 0}footer{color:#667985;font-size:12px;margin-top:25px}@media(max-width:600px){main{padding:25px 16px}h1{font-size:30px}}@media print{body{background:white}nav{display:none}main{padding:0}pre{max-height:none}}"""
nav = [
    ("conclusion", "状态"),
    ("versions", "版本与镜像"),
    ("operator", "Operator"),
    ("infra", "NATS/Grove"),
    ("monitoring", "可观测性"),
    ("differences", "版本边界"),
    ("next", "下一步"),
    ("evidence", "证据"),
]
page = (
    '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>maas-dev Dynamo 部署实战分析</title><style>'
    + css
    + '</style></head><body><main><header><div class="kicker">DYNAMO / LIVE DEPLOYMENT AUDIT</div><h1>maas-dev：Dynamo 部署实战分析</h1><p class="lead">从 Helm 声明、运行镜像、CRD、观测发现到端点响应，逐层区分“已配置”和“已验证”。</p><p class="meta">主快照 '
    + E(d["captured_at"])
    + " · 后续端点核对：见保留的端点证据"
    + " · namespace dynamo · 只读分析</p><nav>"
    + "".join('<a href="#' + i + '">' + t + "</a>" for i, t in nav)
    + "</nav></header>"
    + b
    + "<footer>本报告保存在本地，不会向外部服务发布。context 已按请求切换至 maas-dev；port-forward 完成后关闭。</footer></main></body></html>"
)
(OUT / "index.html").write_text(page)
print("Wrote " + str(OUT / "index.html"))
