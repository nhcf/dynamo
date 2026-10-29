#!/usr/bin/env python3
"""Offline tutorial generator. SPDX-License-Identifier: Apache-2.0"""
import hashlib
import json
import subprocess
from html import escape as E
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
PIN = "2f296b59274867529ea8deef994e6954520024b6"
manifest = []


def src(key, path, start, end, title):
    raw = subprocess.check_output(["git", "show", f"{PIN}:{path}"], cwd=ROOT)
    lines = raw.decode().splitlines()
    if end > len(lines):
        raise ValueError(path)
    manifest.append(
        dict(
            id=key,
            path=path,
            start=start,
            end=end,
            revision=PIN,
            sha256=hashlib.sha256(raw).hexdigest(),
        )
    )
    code = "\n".join(
        f'<span class="line"><i>{i}</i>{E(lines[i - 1])}</span>'
        for i in range(start, end + 1)
    )
    url = f"https://github.com/ai-dynamo/dynamo/blob/{PIN}/{path}#L{start}-L{end}"
    return f'<details class="source" id="src-{key}"><summary><b>源码 · {E(title)}</b><small>{path}:{start}–{end}</small></summary><div class="sourcebar"><a href="{url}" target="_blank" rel="noopener">固定提交原文 ↗</a><button class="copy">复制源码</button></div><pre><code>{code}</code></pre></details>'


def table(head, rows):
    return (
        '<div class="tablewrap"><table><thead><tr>'
        + "".join(f"<th>{x}</th>" for x in head)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(f"<td>{x}</td>" for x in r) + "</tr>" for r in rows)
        + "</tbody></table></div>"
    )


def flow(items):
    return (
        '<div class="flow">'
        + "".join(f"<div><b>{i + 1:02}</b>{s}</div>" for i, s in enumerate(items))
        + "</div>"
    )


def note(s, warn=False):
    return f'<aside class="note {"warn" if warn else ""}">{s}</aside>'


def section(id, title, s, q=None, a=None):
    ex = (
        f'<details class="exercise"><summary>阅读检查 · {q}</summary><p>{a}</p></details>'
        if q
        else ""
    )
    return f'<section id="{id}" class="lesson"><div class="sectionhead"><h2>{title}</h2><label class="done"><input type="checkbox" data-read="{id}"> 已读</label></div>{s}{ex}</section>'


STYLE = """
main{min-width:0;overflow-wrap:anywhere}.sectionhead h2{min-width:0}.tab-panel{overflow-wrap:anywhere}.sourcebar{gap:12px;flex-wrap:wrap}
:root{--bg:#f5f5f0;--paper:#fff;--ink:#1e2935;--muted:#5d6c76;--line:#dbe1df;--accent:#146c59;--soft:#eaf4ef;--side:260px}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:25px}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.85 system-ui,-apple-system,"Microsoft YaHei",sans-serif}a{color:var(--accent);text-underline-offset:3px}button,input{font:inherit}button{cursor:pointer;border:1px solid var(--line);background:var(--paper);border-radius:7px;padding:7px 12px;color:var(--ink)}button:hover{border-color:var(--accent)}aside.nav{position:fixed;inset:0 auto 0 0;width:var(--side);padding:28px 20px;background:#edf0ea;border-right:1px solid var(--line);overflow:auto}.brand{font-size:12px;letter-spacing:.13em;color:var(--accent);font-weight:800}.nav h2{font-size:21px;margin:12px 0 20px}.nav a{display:block;padding:5px 0;text-decoration:none;font-size:14px;color:var(--muted)}.nav a.active{color:var(--accent);font-weight:750}.nav input[type=search]{width:100%;margin:15px 0;border:1px solid var(--line);border-radius:6px;padding:8px;background:var(--paper)}main{margin-left:var(--side);max-width:1400px;padding:45px 5vw 80px}.hero{padding-bottom:28px;border-bottom:1px solid var(--line)}.eyebrow{color:var(--accent);font-size:13px;font-weight:750;letter-spacing:.08em}h1{font-size:clamp(30px,4vw,50px);line-height:1.25;letter-spacing:-.035em;margin:16px 0}h2{font-size:24px;line-height:1.4;margin:0}h3{font-size:19px;margin-top:25px}.lede{font-size:19px;color:var(--muted)}.meta{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.pill{background:var(--soft);color:var(--accent);border-radius:100px;padding:4px 11px;font-size:12px;font-weight:650}.draft{background:#fff0d7;color:#965b14}.lesson{padding:35px 0;border-bottom:1px solid var(--line)}.sectionhead{display:flex;align-items:baseline;justify-content:space-between;gap:15px}.done{font-size:12px;white-space:nowrap;color:var(--muted)}p{margin:16px 0}code{font:13px/1.7 ui-monospace,Consolas,monospace;overflow-wrap:anywhere;background:#e9ede9;padding:2px 4px;border-radius:3px}pre{overflow:auto;background:#17252c;color:#d9e9e3;padding:18px;border-radius:9px;font:13px/1.75 ui-monospace,monospace;max-height:520px}pre code{background:none;color:inherit;padding:0;overflow-wrap:normal}pre .line{display:block;white-space:pre}pre .line i{display:inline-block;min-width:47px;text-align:right;padding-right:15px;color:#7f999e;user-select:none;font-style:normal}.tablewrap{overflow:auto;margin:20px 0}table{border-collapse:collapse;width:100%;font-size:14px;background:var(--paper)}th,td{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:#eaf0e9}td:first-child{min-width:110px}.note{background:var(--soft);border-left:3px solid var(--accent);padding:15px 20px;margin:22px 0;font-size:14px}.note.warn{background:#fff3df;border-color:#a15c12}.flow{display:flex;flex-wrap:wrap;gap:12px;margin:24px 0}.flow>div{flex:1;min-width:155px;background:var(--paper);border:1px solid var(--line);border-radius:9px;padding:14px;font-size:14px}.flow b{display:block;font:12px ui-monospace,monospace;color:var(--accent);margin-bottom:7px}.source{background:var(--paper);border:1px solid var(--line);border-radius:9px;margin:16px 0;overflow:hidden}.source summary{padding:14px 18px;cursor:pointer;font-size:14px}.source small{display:block;font:11px/1.7 ui-monospace,monospace;color:var(--muted);overflow-wrap:anywhere}.sourcebar{display:flex;justify-content:space-between;align-items:center;padding:0 18px 10px;font-size:12px}.source pre{margin:0;border-radius:0}.exercise{padding:14px 18px;background:#eff2f7;margin-top:25px;border-radius:8px;font-size:14px}.exercise summary{cursor:pointer;font-weight:650}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin:22px 0}.card{padding:22px;border:1px solid var(--line);border-radius:12px;background:var(--paper)}.card h3{margin:8px 0}.card p{font-size:14px;color:var(--muted)}.num{font:12px ui-monospace,monospace;color:var(--accent)}.toolbar{display:flex;flex-wrap:wrap;gap:8px;margin-top:20px}.fine{font-size:12px;color:var(--muted)}.sim,.diagram{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:23px}.sim label{display:block;margin:10px 0;font-size:14px}.sim input{width:100%;accent-color:var(--accent)}.sim-output{font:16px/1.8 ui-monospace,monospace;color:var(--accent);background:var(--soft);padding:15px;margin-top:16px;white-space:pre-line}.tabs{display:flex;flex-wrap:wrap;gap:8px;margin:15px 0}.tabs button[aria-selected=true]{background:var(--accent);color:white}.tab-panel{border:1px solid var(--line);border-radius:9px;padding:20px;background:var(--paper)}[hidden]{display:none!important}.lane{display:grid;grid-template-columns:125px 1fr;gap:16px;align-items:start;padding:14px 0;border-bottom:1px dashed var(--line)}.lane:last-child{border:0}.lane b{font-size:13px;color:var(--accent)}.lane span{font-size:14px}.progress{height:5px;background:var(--line);margin-top:15px;border-radius:4px;overflow:hidden}.progress div{height:100%;width:0;background:var(--accent)}footer{margin-top:35px;font-size:12px;color:var(--muted)}a:focus-visible,button:focus-visible,summary:focus-visible,input:focus-visible{outline:3px solid #cf9b2b;outline-offset:3px}@media(max-width:850px){aside.nav{position:static;width:auto;padding:20px;border-right:0;border-bottom:1px solid var(--line)}.nav h2{margin-bottom:8px}.nav nav{display:flex;gap:12px;overflow:auto;white-space:nowrap}.nav input[type=search]{max-width:400px}main{margin:0;padding:28px 20px 60px}.sectionhead{align-items:flex-start}.lane{grid-template-columns:1fr}}@media print{aside.nav,.toolbar,.done,.copy,.tabs{display:none!important}main{margin:0;max-width:none;padding:0}body{background:white;font-size:11pt}h1{font-size:28pt}pre{max-height:none;white-space:pre-wrap;font-size:9pt}.lesson{padding:20px 0}.tab-panel[hidden]{display:block!important}}
"""
JS = r"""
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
function progress(){let xs=$$('[data-read]'),n=xs.filter(x=>x.checked).length;$('#progress').style.width=(xs.length?100*n/xs.length:0)+'%';$('#progress-label').textContent='已读 '+n+' / '+xs.length+' 节'}
try{const state=JSON.parse(localStorage.getItem('dynamo-read-v1')||'[]');$$('[data-read]').forEach(x=>x.checked=state.includes(x.dataset.read))}catch{}
$$('[data-read]').forEach(x=>x.onchange=()=>{try{localStorage.setItem('dynamo-read-v1',JSON.stringify($$('[data-read]').filter(x=>x.checked).map(x=>x.dataset.read)))}catch{}progress()});progress();
$('#search').oninput=e=>{let q=e.target.value.trim().toLowerCase(),n=0;$$('section.lesson').forEach(s=>{s.hidden=!!q&&!s.textContent.toLowerCase().includes(q);if(!s.hidden)n++});$('#no-results').hidden=n>0};
$$('.copy').forEach(b=>b.onclick=async()=>{let t=[...b.closest('details').querySelectorAll('.line')].map(x=>x.textContent.slice(x.querySelector('i').textContent.length)).join('\n');try{await navigator.clipboard.writeText(t);b.textContent='已复制'}catch{b.textContent='请选中代码复制'}});
$$('[data-tab]').forEach(b=>b.onclick=()=>{$$('[data-tab]').forEach(x=>x.setAttribute('aria-selected',String(x===b)));$$('[data-panel]').forEach(x=>x.hidden=x.dataset.panel!==b.dataset.tab)});
function sim(){if(!$('#ttft'))return;let f=+$('#ttft').value,g=+$('#gap').value,t=+$('#tokens').value;$('#ttft-value').textContent=f;$('#gap-value').textContent=g;$('#tokens-value').textContent=t;$('#sim-output').textContent='首块：TTFT = '+f+' ms；不记录 ITL\n第二块：ITL = '+g+' / '+t+' = '+(g/t).toFixed(2)+' ms\nHistogram 新增 '+t+' 个 ITL 观测值，每个值相同。'}
$$('.sim input').forEach(x=>x.oninput=sim);sim();
$('#expand').onclick=()=>$$('details.source').forEach(x=>x.open=true);
$('#collapse').onclick=()=>$$('details.source').forEach(x=>x.open=false);
$('#print').onclick=()=>window.print();
if('IntersectionObserver'in window){let o=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting)$$('.nav nav a').forEach(a=>a.classList.toggle('active',a.hash==='#'+e.target.id))}),{rootMargin:'-10% 0px -70% 0px'});$$('main section').forEach(s=>o.observe(s))}
"""


def page(title, subtitle, body, nav, draft=False):
    links = "".join(f'<a href="#{id}">{name}</a>' for id, name in nav)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="Dynamo 源码解读：系统阅读路线与可观测性实现"><title>{title} · Dynamo 源码解读</title><style>{STYLE}</style></head><body><aside class="nav"><div class="brand">DYNAMO / SOURCE NOTES</div><h2>源码解读教程</h2><a href="index.html">← 系统地图与讨论大纲</a><a href="observability.html">01 · 可观测性源码</a><a href="planner.html">08 · Planner 源码</a><a href="pd-trace.html">专题 · PD 全链路 Trace</a><input id="search" type="search" aria-label="搜索章节" placeholder="搜索章节 / 函数 / 配置"><nav aria-label="本页目录">{links}</nav><div class="progress"><div id="progress"></div></div><p id="progress-label" class="fine"></p><p class="fine">离线可读 · 无外部脚本<br>代码基线 {PIN[:9]}</p></aside><main><header class="hero"><div class="eyebrow">学习路径 / 源码证据 / 工程验证</div><h1>{title}</h1><p class="lede">{subtitle}</p><div class="meta"><span class="pill">2026-10-28</span><span class="pill">commit {PIN[:9]}</span><span class="pill {"draft" if draft else ""}">{"大纲讨论稿 v0.1" if draft else "首个源码专题 v0.1"}</span></div><div class="toolbar"><button id="expand">展开全部源码</button><button id="collapse">折叠全部源码</button><button id="print">打印 / 保存 PDF</button></div></header><p id="no-results" hidden>没有匹配的章节，清空搜索恢复全文。</p>{body}<footer>静态源码与官方文档交叉核对；未运行推理集群或负载实验。源码摘录固定于完整提交 {PIN}。<br>交互示例解释实现，不代表真实性能测量。禁用本地存储时，阅读进度仅在本次页面有效。</footer></main><script>{JS}</script></body></html>"""


mods = [
    (
        "00",
        "系统地图与运行形态",
        "职责、进程、Rust/Python/Kubernetes 边界，聚合、PD 分离与 Sidecar。",
        "Cargo.toml；lib/{runtime,llm,backend-common,sidecar}；components/src/dynamo；deploy/",
        "画出跨进程、跨语言的请求路径。",
    ),
    (
        "01",
        "可观测性",
        "指标注册与导出、请求生命周期、Trace、诊断通道与健康探测。",
        "lib/runtime/src/{metrics.rs,logging.rs,system_status_server.rs}；lib/llm/src/http/service/metrics.rs",
        "解释一个指标从产生到查询的完整过程。",
    ),
    (
        "02",
        "Frontend 与协议流水线",
        "OpenAI 入口、模型发现、预处理、tokenization、流式响应与取消。",
        "components/src/dynamo/frontend；lib/llm/src/http",
        "区分 HTTP、内部请求对象和引擎参数的语义所有者。",
    ),
    (
        "03",
        "Runtime 与通信",
        "Runtime / DistributedRuntime、namespace/component/endpoint、发现与请求/事件传输。",
        "lib/runtime/src；lib/bindings/python",
        "区分请求数据面、发现与事件分发，理解生命周期。",
    ),
    (
        "04",
        "KV-aware Router 与调度",
        "KV 索引、候选筛选、评分、排队、负载跟踪和插件。",
        "lib/kv-router；lib/llm/src/kv_router；lib/router-plugins",
        "解释一次 Worker 选择依赖哪些状态与约束。",
    ),
    (
        "05",
        "引擎适配与 Rust Sidecar",
        "现有 Python 适配、统一后端契约与 Rust Sidecar 的关系。",
        "components/src/dynamo/{vllm,sglang,trtllm}；lib/backend-common；lib/sidecar",
        "先选一个后端深入，分清编排、适配和引擎自身。",
    ),
    (
        "06",
        "Prefill / Decode 分离",
        "Prefill 路由、Decode 执行、KV handoff、NIXL 与失败路径。",
        "lib/llm；后端 request_handlers；NIXL 适配",
        "分别画出请求控制流与 KV 数据流。",
    ),
    (
        "07",
        "KV 缓存与内存管理",
        "KV block 生命周期、逻辑/物理管理、分层存储与 GPU Memory Service。",
        "lib/kvbm-*；lib/gpu_memory_service；lib/llm/src/block_manager",
        "解释缓存复用、迁移和容量约束。",
    ),
    (
        "08",
        "Planner / Profiler 与伸缩",
        "流量统计、性能估计、SLO 反馈、容量决策和执行。",
        "components/src/dynamo/{planner,profiler,aisimulate,global_planner}",
        "将观测信号映射为伸缩输入，识别数据缺失的影响。",
    ),
    (
        "09",
        "Kubernetes Operator 与部署",
        "DGD/DCD、reconcile、工作负载生成、探针和监控配置。",
        "deploy/operator；deploy/helm/charts/platform",
        "从声明式资源追踪到具体 Pod 配置。",
    ),
    (
        "10",
        "可靠性与流式生命周期",
        "取消、超时、拒绝、迁移、Worker 健康与优雅关闭。",
        "lib/llm/src/http/service；lib/runtime/src；后端处理器",
        "沿异常路径检查资源释放与统计正确性。",
    ),
    (
        "11",
        "多集群与全局路由",
        "全局发现、视图同步、全局前端/路由与策略边界。",
        "lib/global-*；components/src/dynamo/{global_router,global_planner,kv_dc_relay}",
        "区分单部署路由状态和跨域容量视图。",
    ),
    (
        "12",
        "验证、回放与性能实验",
        "Mocker、request replay、单元/集成测试、AIPerf 和性能归因。",
        "lib/mocker；components/src/dynamo/replay；tests；benchmarks",
        "设计可重复实验，避免混淆负载和实现差异。",
    ),
]
index = section(
    "scope",
    "先约定阅读边界",
    "<p>本版交付系统框架大纲和第一个深入专题。其他模块是待讨论的阅读路线，尚未逐模块完成源码解读。默认面向有 Python / Kubernetes 经验、希望理解 Rust 核心并能参与接入和排障的工程师。</p>"
    + note(
        "建议一起确定三点：更偏 Rust 二次开发还是平台接入；优先 vLLM、SGLang 还是 TensorRT-LLM；Sidecar 和全局路由放主线还是后续专题。"
    ),
)
index += section(
    "map",
    "系统框架：三层与两类数据流",
    """<div class="diagram"><div class="lane"><b>入口与编排</b><span>客户端 → Frontend → 预处理 / 模型选择 → Router → 聚合 Worker 或 Prefill / Decode</span></div><div class="lane"><b>Rust 基础能力</b><span>LLM 流水线 · KV Router · Runtime / 发现 / 传输 · KVBM · 后端契约与 Sidecar</span></div><div class="lane"><b>Python 扩展</b><span>启动与配置 · 引擎适配 · Planner / Profiler · 自定义处理器</span></div><div class="lane"><b>Kubernetes 控制面</b><span>CRD → reconcile → Pod / Service / 探针 / 监控发现</span></div><div class="lane"><b>观测与反馈</b><span>Metrics / Logs / Trace → 采集存储 → 看板 / 告警；部分运行指标也由 Planner 查询</span></div></div><p>请求控制流不等于 KV 数据传输；监控指标不等于路由实时状态事件。每章先标明所属路径，再进入实现。Router 可以内嵌于 Frontend，也可以独立部署。</p>"""
    + src("workspace", "Cargo.toml", 5, 57, "Cargo workspace：系统边界"),
)
cards = ""
for n, t, d, p, o in mods:
    link = (
        '<a href="observability.html">进入源码专题 →</a>'
        if n == "01"
        else ('<a href="planner.html">进入 Planner 源码 →</a>' if n == "08" else "")
    )
    cards += f'<article class="card"><span class="num">MODULE {n} · {"已展开" if n in ("01", "08") else "阅读大纲"}</span><h3>{t}</h3><p>{d}</p><p><b>源码范围</b><br><code>{p}</code></p><p><b>学习产出</b><br>{o}</p>{link}</article>'
index += section("outline", "建议模块大纲 · 供讨论", '<div class="cards">' + cards + "</div>")
index += section(
    "route",
    "阅读顺序与记录模板",
    flow(
        [
            "系统地图<br>明确进程和模块边界",
            "可观测性优先<br>建立定位问题的工具",
            "沿请求链路<br>Frontend → Runtime → Router → 引擎",
            "再读专项机制<br>PD / KVBM / Planner / Operator",
        ]
    )
    + table(
        ["每章记录", "要求"],
        [
            ["职责与输入输出", "写清问题、关键对象和外部契约。"],
            ["主调用链", "区分直接调用、框架回调和跨进程关系。"],
            ["源码摘录", "固定 commit、路径、行号，原文可离线展开。"],
            ["状态和异常", "明确计时起点、所有权、取消、失败和关闭。"],
            ["验证与练习", "区分已证实实现、源码推断和待验证实验。"],
        ],
    ),
)
index += section(
    "method",
    "证据和维护方式",
    '<p>结构发现优先使用 codebase-memory-mcp，再核对关键函数原文。图关系只辅助导航；Rust trait、闭包、PyO3、框架回调的真实调用不能完全依靠静态图判断。</p><p>页面无 CDN 依赖。使用 <code>python3 learning/build_tutorial.py</code> 生成；生成器校验提交及引用文件是否匹配，避免旧解释混入新源码。<a href="source-manifest.json">source-manifest.json</a> 记录范围与 SHA256。</p><p><a href="observability.html">开始可观测性源码章节 →</a></p>',
)
b = section(
    "overview",
    "01 / 阅读目标与信号架构",
    "<p>一个指标在哪里注册？什么时候更新？从哪个出口被采集？异常时还能相信它吗？本章先跟踪现有 Python Worker → PyO3 → Rust Runtime 链路，再分析 Frontend 本地指标与请求生命周期。</p>"
    + table(
        ["信号", "代码职责", "平台职责"],
        [
            ["Metrics", "注册、更新、汇总、/metrics 与可选 OTLP exporter", "发现、采集、存储、查询、告警"],
            ["Logs / Spans", "结构化事件、上下文传播、OTLP 导出", "接收、存储、采样治理、日志/链路关联"],
            ["Request Trace / FPM", "请求或调度遥测捕获、广播到 sink", "保留、分析、回放、内容访问控制"],
            ["Health", "系统状态与可选主动推理探测", "探针、模型和真实推理验收"],
        ],
    )
    + """<div class="diagram"><div class="lane"><b>拉取</b><span>Prometheus → /metrics → Registry 汇总 → Rust collectors + Python 引擎文本回调</span></div><div class="lane"><b>指标推送</b><span>Runtime 定时任务 → 原生 collectors + 类型化回调 → OTLP → Collector</span></div><div class="lane"><b>日志与链路</b><span>tracing subscriber → OTel provider / bridge → Collector → Trace / Log 存储</span></div><div class="lane"><b>诊断分支</b><span>请求事件 / 已归一 FPM → 有界广播队列 → 各 sink 独立消费</span></div></div>"""
    + note(
        "本章主线是已核对的现有 Worker 路径。Rust Sidecar、新后端和不同模型任务存在独立入口，不声称所有部署都经过相同 Python 回调。"
    ),
)
b += section(
    "registry",
    "02 / Registry：注册本层，采集子树",
    "<p><code>MetricsRegistry</code> 持有本地 Registry、子 Registry 列表和三类回调。Arc 让克隆句柄共享注册状态。过去向所有父层级重复注册会造成描述符与常量标签冲突；现在只在本层注册，根抓取时再合并 family。</p>"
    + src("registry", "lib/runtime/src/metrics.rs", 796, 829, "Registry 数据结构")
    + flow(
        [
            "create_metric_with_options",
            "注入 namespace / component / endpoint 等标签",
            "仅向当前层级注册 collector",
            "抓取子树、合并 family、编码",
        ]
    )
    + src("create", "lib/runtime/src/metrics.rs", 284, 375, "创建与自动标签")
    + src("register", "lib/runtime/src/metrics.rs", 494, 505, "只注册本层")
    + src("scrape", "lib/runtime/src/metrics.rs", 932, 963, "文本汇总出口")
    + src("typed", "lib/runtime/src/metrics.rs", 974, 1014, "类型化汇总出口")
    + "<p>调用方不能重复手工设置自动标签。文本与类型化出口共享 Registry 树，但引擎回调是两套：文本供 scrape，类型化供 OTLP。类型化合并还保留 unit 和 Prometheus type 元数据，接受 family 后才写入侧边元数据。</p>",
    "重复注册父层级有什么风险？",
    "子树会被遍历，重复注册可能重复执行回调并产生重复序列；当前绑定只在所在层级注册。",
)
b += section(
    "bridge",
    "03 / Python → PyO3 → Rust",
    flow(
        ["引擎 CollectorRegistry", "Python 标签注入、注册双回调", "PyO3 包装 callable", "Rust 在采集时调用"]
    )
    + src(
        "python",
        "components/src/dynamo/common/utils/prometheus.py",
        158,
        239,
        "引擎标签与双回调",
    )
    + "<p>源指标标签优先，Dynamo 自动标签其次，自定义补充标签最后。辅助函数在注册阶段准备参数，采集阶段才执行回调。</p>"
    + src(
        "binding",
        "lib/bindings/python/rust/prometheus_metrics.rs",
        109,
        131,
        "RuntimeMetrics 注册到当前层级",
    )
    + src(
        "pyo3", "lib/bindings/python/rust/prometheus_metrics.rs", 15, 91, "GIL 与返回值契约"
    )
    + "<p>文本回调执行或返回值提取失败时，会记录错误并返回空字符串，其他指标仍可能正常输出。类型化回调则返回 Rust 上层错误；从 tuple/list 提取数据后，用 Rust 构造 family，避免文本往返。GIL 只覆盖 Python 调用和提取。</p>"
    + table(
        ["后端阅读入口", "重点"],
        [
            ["vLLM setup_metrics_collection", "REGISTRY 与 multiprocess Registry 的区别"],
            ["SGLang setup_sgl_metrics / setup_prometheus_registry", "引擎开关与多进程采集"],
            ["TensorRT-LLM init_llm_worker", "--publish-metrics 与附加统计"],
        ],
    ),
    "为什么 HTTP 200 不足以验收引擎指标？",
    "单个 Python 文本回调可以失败并返回空文本，但其他 collectors 仍能编码成功；必须检查预期序列和错误日志。",
)
b += section(
    "exports",
    "04 / 出口与线程模型",
    """<div class="tabs" role="tablist" aria-label="指标出口"><button id="tab-pull" role="tab" aria-controls="panel-pull" data-tab="pull" aria-selected="true">Prometheus 拉取</button><button id="tab-push" role="tab" aria-controls="panel-push" data-tab="push" aria-selected="false">OTLP 推送</button></div><div class="tab-panel" id="panel-pull" role="tabpanel" aria-labelledby="tab-pull" data-panel="pull"><b>Worker：metrics_handler → render_scrape_at → spawn_blocking → prometheus_expfmt_combined</b><p>Mutex 串行保护最近渲染结果。等待者在发现另一次渲染的起点晚于或等于自己的请求起点时复用结果；不是固定 TTL 缓存。</p></div><div class="tab-panel" id="panel-push" role="tabpanel" aria-labelledby="tab-push" data-panel="push" hidden><b>Runtime → ExportConfig → tokio::spawn(run) → tick → 类型化汇总 → OTLP RPC</b><p>默认 60000ms，连接/RPC deadline 为周期与 30 秒的较小值。连接失败在后续 tick 重试，RPC 失败清空连接，partial_success 的拒绝点数会记录。</p></div>"""
    + src(
        "system",
        "lib/runtime/src/system_status_server.rs",
        537,
        587,
        "Worker handler 与并发抓取合并",
    )
    + src(
        "bootstrap", "lib/runtime/src/distributed.rs", 357, 410, "Runtime 启动 exporter"
    )
    + src("exportconfig", "lib/runtime/src/metrics/otlp_export.rs", 453, 520, "环境变量解析")
    + src(
        "exportloop", "lib/runtime/src/metrics/otlp_export.rs", 567, 683, "导出循环、关闭和重连"
    )
    + "<h3>为什么 spawn_blocking？</h3><p>采集回调可能等待 Python GIL，不能让同步工作阻塞 Tokio 异步执行线程。OTLP 路径等待 collection 完成再处理关闭，因为已启动的 blocking task 无法直接取消；否则 Python teardown 后可能仍有任务访问 GIL。</p>"
    + note(
        "当前 Runtime 中，配置解析错误进入 Err 分支，记录 misconfigured; not exporting，然后继续初始化。不能把“协议只支持 gRPC、解析返回错误”推断为“整个进程必然启动失败”。",
        True,
    )
    + "<h3>Frontend 有自己的 Registry</h3><p>Frontend handler 先编码本地 Registry，再追加可选 DRT 子树。它未直接复用 Worker 的 render_scrape_at；不能把 Worker 的抓取隔离和 exporter 覆盖结论无条件推广到 Frontend 本地指标。</p>"
    + src(
        "httpregistry",
        "lib/llm/src/http/service/service_v2.rs",
        1369,
        1407,
        "Frontend 本地注册",
    )
    + src(
        "frontend", "lib/llm/src/http/service/metrics.rs", 2968, 3009, "Frontend 输出拼接"
    ),
    "为什么必须继续读 ExportConfig 的调用方？",
    "配置函数返回 Result；调用方决定退出还是降级。本版本调用方记录错误并跳过 exporter。",
)
b += section(
    "lifecycle",
    "05 / 请求指标：Guard 从进入到退出",
    "<p>InflightGuard::new 增加 in-flight 和 started，默认状态为错误，正常路径再标记成功。Drop 会扣减并发、增加完成计数、记录耗时和 outcome；提前退出也能走收尾。</p>"
    + src("guardnew", "lib/llm/src/http/service/metrics.rs", 2128, 2162, "请求进入")
    + flow(
        [
            "进入：in-flight +1 / started +1",
            "生成：观测输出与时延",
            "完成 / 错误 / 取消状态",
            "Drop：in-flight -1 / 完成计数 / 耗时",
        ]
    )
    + src("guarddrop", "lib/llm/src/http/service/metrics.rs", 2212, 2287, "请求收尾")
    + src("finish", "lib/llm/src/http/service/metrics.rs", 878, 891, "幂等 finish")
    + "<p>RequestMetrics::finish 用原子 FINISHED 位避免重复扣减，并依据是否进入 generating，扣减对应 token 与等待计数。</p>"
    + note(
        "流式 HTTP 返回 Header 不等于请求结束，需要继续跟踪 response stream 对 Guard 的持有与 disconnect 包装。这里的服务端生命周期也不证明客户端已完整接收；强制终止进程不能保证执行 Drop。"
    ),
    "默认错误、正常路径再 mark_ok 有何意义？",
    "提前返回而没有完成成功标记时仍记录失败，减少误记成功；取消等路径仍需明确分类。",
)
b += section(
    "latency",
    "06 / TTFT 与 ITL：计时起点和响应块",
    "<p>ResponseMetricCollector::new 使用 Instant::now 记录自身起点，并预先解析 per-model handle。要确认某个入口的计时是否含 tokenization 或 routing，必须追踪该入口在哪里创建 Collector，不能从名称猜测。</p>"
    + src(
        "collectornew",
        "lib/llm/src/http/service/metrics.rs",
        2377,
        2425,
        "Collector 起点",
    )
    + "<p>observe_response 首先更新 token 状态。零输出块直接返回，不产生 TTFT/ITL，也不更新 last_response_time。首次有效输出块记录 TTFT；即使首块有多个 token，也不补造块内 ITL。后续块以“块间时间 ÷ 本块 token 数”折算 ITL，并向 Histogram 写入同等数量的该值。</p>"
    + src(
        "observe", "lib/llm/src/http/service/metrics.rs", 2574, 2670, "首 token 与块间 ITL"
    )
    + """<div class="sim"><h3>交互演算：两次有效输出块</h3><p class="fine">仅解释源码公式，不模拟调度、GPU 和网络性能。</p><label>首块到达：<b id="ttft-value"></b> ms<input id="ttft" type="range" min="50" max="2000" step="10" value="500"></label><label>第二块间隔：<b id="gap-value"></b> ms<input id="gap" type="range" min="10" max="500" step="10" value="120"></label><label>第二块 token 数：<b id="tokens-value"></b><input id="tokens" type="range" min="1" max="16" value="4"></label><div id="sim-output" class="sim-output" aria-live="polite"></div></div>"""
    + "<p>热路径缓存 GaugeVec handle，使用局部 Histogram、达到阈值后 flush。Drop 再 flush 剩余观测，记录有效 OSL 与 span 摘要；从未输出的失败请求不强行记零长度 Histogram，避免污染均值。</p>"
    + src(
        "collectordrop",
        "lib/llm/src/http/service/metrics.rs",
        2674,
        2725,
        "flush、输出长度与 span 摘要",
    ),
    "第二块间隔 120ms、含 4 token，记录什么？",
    "4 个 0.03 秒 ITL 观测值；无法据此还原引擎内部四个 token 的真实生成时刻。",
)
b += section(
    "tracing",
    "07 / Trace Context 与结构化日志",
    flow(
        [
            "HTTP 提取上游 Context",
            "请求 span / 内部 request_id",
            "Header 注入上下文",
            "接收组件子 span 与关联日志",
        ]
    )
    + src("rootspan", "lib/runtime/src/logging.rs", 623, 665, "HTTP span 和父上下文")
    + src("inject", "lib/runtime/src/logging.rs", 852, 873, "传输 Header 注入")
    + "<p>区分业务 x-request-id、内部 request_id 和分布式 trace_id。内部 ID 缺失时会生成 UUID；x-request-id 是可选的上游关联字段。上下文传播与远程导出是两回事。</p>"
    + src("logging", "lib/runtime/src/logging.rs", 1243, 1386, "subscriber 与 OTel 装配")
    + table(
        ["配置", "作用"],
        [
            ["DYN_LOGGING_CONSOLE_FORMAT=jsonl", "控制台结构化；也可建立本地关联上下文"],
            ["OTEL_EXPORT_ENABLED=true", "导出 traces 与 runtime logs"],
            ["OTEL_TRACES_SAMPLE_RATIO", "配置 ParentBased + TraceIdRatioBased"],
            ["OTEL_METRICS_EXPORTER=otlp", "独立 metrics 开关"],
        ],
    )
    + "<p>setup_logging 组合控制台、TraceId、tracing-opentelemetry 和可选日志 bridge。JSONL 不等于远程导出，Dynamo span 正常也不代表引擎内部 span 已启用，需要核对实际后端和依赖。</p>",
    "日志有 trace_id，Trace 存储却查不到？",
    "检查是否仅启用 JSONL，再查 OTEL_EXPORT_ENABLED、采样、网络、Collector traces pipeline 和对应 exporter。",
)
b += section(
    "capture",
    "08 / Request Trace 与 FPM：诊断数据的可靠性",
    "<p>Request Trace 为每个 sink 启动独立广播订阅 worker。消费者落后时收到 Lagged，记录丢失条数；关闭时尝试排空接受的记录，再执行 sink.shutdown。它不是无损交付契约。</p>"
    + src(
        "requestsink", "lib/llm/src/request_trace/sink.rs", 496, 571, "sink fan-out 与关闭"
    )
    + "<p>FPM publish_payload 使用非阻塞广播；sampled 按生产者键保留最新状态并定期 flush，full 使用另一消费路径。持久化只是事件发布的附加诊断分支。</p>"
    + src("fpm", "lib/llm/src/fpm_trace/mod.rs", 137, 164, "FPM 非阻塞发布")
    + src("fpmsampled", "lib/llm/src/fpm_trace/sink.rs", 401, 459, "sampled 消费")
    + table(
        ["类型", "记录语义", "边界"],
        [
            ["分布式 span", "组件操作与父子关系", "不保证每请求、每迭代都覆盖"],
            ["Request Trace", "选定请求、路由、工具事件和可选 payload", "存在消费者滞后和记录丢失"],
            ["FPM sampled", "各键最近变化后的状态", "不保留全部中间迭代"],
            ["FPM full", "消费者尝试保留接受的 payload", "仍受生产队列容量限制"],
        ],
    ),
    "诊断文件能直接当唯一计费账本吗？",
    "不能假设无损；需另行设计完整性、持久化确认、幂等和对账。",
)
b += section(
    "health",
    "09 / Health：状态查询和主动推理分开读",
    "<p>普通 LLM Worker 的主动 canary 经本地注册 Endpoint 发出最小推理请求，等待首响应。首响应无错记 ready；空流、首响应错误、生成失败或超时记 notready。后续流由另一任务继续消费。</p>"
    + src("canary", "lib/runtime/src/health_check.rs", 201, 296, "首响应判定与状态更新")
    + note(
        "当前 Worker /live 与 /health 使用相同系统状态语义；Frontend 的存活、就绪和模型 readiness 不同。主动 canary 默认关闭，多模态/Omni 路径需独立检查。"
    )
    + "<p>canary 不证明外部网关可达、全部输出完成或内容语义正确。服务入口的真实推理验收仍有必要。</p>",
    "首响应成功证明完整服务正常吗？",
    "只证明指定本地 Endpoint 探测获得无错首响应。网关、HTTP 投影、完整输出和客户端接收另行验证。",
)
b += section(
    "kubernetes",
    "10 / Kubernetes 与 Planner：接到平台",
    "<p>Helm 负责生成 PodMonitor。命名端口、组件标签和 metrics-enabled 标签决定匹配目标。Monitor 存在、Prometheus 发现和业务序列存在是三个不同条件。</p>"
    + src(
        "podmonitor",
        "deploy/helm/charts/platform/components/operator/templates/prometheus.yaml",
        15,
        88,
        "生成条件、标签与端口选择",
    )
    + src(
        "servicemonitor",
        "deploy/helm/charts/platform/components/operator/templates/operator-servicemonitor.yaml",
        15,
        49,
        "Operator HTTPS 抓取",
    )
    + src(
        "operator",
        "deploy/operator/internal/observability/metrics.go",
        35,
        115,
        "控制面指标",
    )
    + "<p>Operator 用 controller-runtime Registry 注册自己的控制面指标，与 Worker Registry 分开。Planner 还会读取指标，因此采集数据缺失可能影响运行控制。</p>"
    + src(
        "planner",
        "components/src/dynamo/planner/monitoring/traffic_metrics.py",
        249,
        277,
        "Planner 查询 ITL / TTFT",
    )
    + src(
        "otelcollector",
        "dev/observability/otel-collector.yaml",
        1,
        35,
        "Collector 示例 pipeline",
    )
    + note(
        "该示例只有 traces/logs pipeline。启用 OTLP metrics 时还要增加 metrics pipeline 与 exporter；不能只改 Worker 环境变量。",
        True,
    )
    + table(
        ["接入检查", "对应动作"],
        [
            ["监控资源生成", "podMonitors.enabled：null 自动检测；GitOps 离线渲染显式确认"],
            ["资源发现", "核对 Monitor / namespace selector 与权限"],
            ["端点可达", "核对命名端口、路径、网络；Operator HTTPS 与 Token"],
            ["业务序列", "发送匹配流量，检查引擎开关和回调错误"],
            ["标签治理", "区分 dynamo_namespace 与 namespace，补齐部署身份"],
            ["重复统计", "避免同目标重复抓取或拉取/推送混合求和"],
        ],
    ),
)
b += section(
    "practice",
    "11 / 跟读与验证计划",
    "<p>下列实验是阅读后的验证计划，尚未在本环境执行推理或故障注入。已有部署才可使用对应检查命令。</p>"
    + table(
        ["实验", "动作", "观察"],
        [
            ["首次序列", "空闲抓取 → 发匹配请求 → 再抓取", "区分空 family、未创建 series 和采集失败"],
            [
                "计时窗口",
                "跟踪入口在哪里创建 Guard / Collector",
                "标出 tokenization / route / dispatch 是否包含",
            ],
            ["多 token 块", "用本页滑块演算，再读 observe_response", "区分折算 ITL 和逐 token 时间戳"],
            ["Collector 断连", "隔离环境阻断 OTLP 后恢复", "看连接、导出错误与恢复，不假设持久化补传"],
            ["回调失败", "测试里抛异常或返回错误类型", "验证 HTTP 成功但部分指标缺失"],
            ["慢消费者", "隔离测试制造诊断消费者滞后", "看 Lagged / dropped，核对非阻塞边界"],
        ],
    )
    + """<pre><code># 替换为已有部署的地址和 namespace；这些命令只读
curl -fsS http://&lt;frontend-host&gt;:8000/metrics
curl -fsS http://&lt;worker-host&gt;:9090/metrics
kubectl get podmonitor,servicemonitor -n &lt;namespace&gt;

# 示例 PromQL：须补充部署筛选，先合并 bucket 再计算分位数
histogram_quantile(0.95,
  sum by (le, model) (
    rate(dynamo_frontend_time_to_first_token_seconds_bucket[5m])
  )
)</code></pre>"""
    + "<h3>已有测试的阅读入口</h3><p>Frontend metrics.rs：test_active_requests_tracks_inflight_guard、frontend_request_metrics_ignore_updates_after_guard_finishes。FPM sink.rs：sampled_mode_keeps_latest_event_per_worker_and_rank。先读断言再读状态变更；本页未声称已运行这些测试。</p>"
    + note("源码推断：GIL 回调、编码、标签数量与全量诊断有开销；具体吞吐影响必须实测。存在 spawn_blocking 不等于没有成本。"),
    "目标 UP 但引擎指标缺失，排查顺序？",
    "匹配流量 → 引擎 metrics 开关 → multiprocess Registry → 回调注册 → PyO3 错误日志 → 前缀过滤 → 平台查询标签。",
)
b += section(
    "references",
    "12 / 参考、版本差异与下一步",
    """<p>官方 dev 文档用于语义核对，内容会继续更新，源码结论以本页固定提交为准。</p><ul><li><a href="https://docs.nvidia.com/dynamo/dev/reference/observability/metrics-catalog">Metrics Catalog</a></li><li><a href="https://docs.nvidia.com/dynamo/dev/reference/observability/otlp-metrics-export">OTLP Metrics Export</a></li><li><a href="https://docs.nvidia.com/dynamo/dev/reference/observability/health-checks">Health Check Reference</a></li><li><a href="https://docs.nvidia.com/dynamo/dev/reference/observability/request-traces">Request Trace Reference</a></li><li><a href="source-manifest.json">本地源码证据清单</a></li></ul><p>差异记录：metrics 配置只接受 gRPC，但当前 Runtime 调用方会记录解析错误并跳过 exporter，不应解释为进程必然退出。Worker Registry 和 Frontend 本地 Registry 也必须区分：某指标出现在一个出口不足以证明另一个出口覆盖它。</p><p>下一步在确定优先后端后，沿 Frontend → Runtime → Router → 引擎补齐各入口的 Collector 创建时刻、上下文传播和异常释放。</p><p><a href="index.html#outline">返回系统大纲参与讨论 →</a></p>""",
)
index += section(
    "deployment",
    "实战案例 / maas-dev 已部署组件",
    '<p>已读取 maas-dev 的真实 Helm、Pod、CRD 和可观测配置，并验证部分运行端点。平台版本 1.4.1，与本教程源码基线不同；当前没有 Dynamo 推理工作负载。</p><p><a href="maas-dev-deployment/index.html">查看部署实战分析 →</a></p>',
)
index += section(
    "target-cluster",
    "完整部署目标 / ack-LB97i2CZNR",
    '<p>用户已确认完整 Dynamo 部署位于独立 kubeconfig 所指集群；maas-dev 是另一个集群的快照。已根据用户提供的 2026-10-28 部署记录补齐 SGLang 1.4.1 混合拓扑，并整理对应 Trace 工作清单。</p><p><a href="ack-LB97i2CZNR/index.html">查看部署记录与 Trace 接入清单 →</a></p>',
)
index += section(
    "planner-topic",
    "专题 / Planner 与可观测数据",
    '<p>已展开 Prometheus 查询、FPM 事件与 rank 完整性、性能模型、预测与扩缩容，以及 Planner 自身指标的来源、单位和失效边界。</p><p><a href="planner.html">进入 Planner 源码解读 →</a></p>',
)
index += section(
    "pd-trace-topic",
    "专题 / PD 模式的全链路 Trace",
    '<p>已核对 Request Plane 的上下文传播、Prefill/Decode 前驱 Link，以及通用 Event Plane、KV 存储和 NIXL 的边界。请求主链可串联，但不能把整个数据面视为已开箱即用全覆盖。</p><p><a href="pd-trace.html">查看 PD Trace 能力与源码证据 →</a></p>',
)
(OUT / "index.html").write_text(
    page(
        "Dynamo 源码阅读路线",
        "先建立系统地图，再沿请求和观测数据流深入实现。当前交付：模块大纲讨论稿 + 可观测性源码专题。",
        index,
        [
            ("scope", "阅读边界"),
            ("map", "系统分层"),
            ("outline", "模块大纲"),
            ("route", "阅读顺序"),
            ("method", "证据与维护"),
            ("deployment", "实战部署分析"),
            ("target-cluster", "完整部署目标"),
            ("planner-topic", "Planner 专题"),
            ("pd-trace-topic", "PD Trace 专题"),
        ],
        True,
    )
)
(OUT / "observability.html").write_text(
    page(
        "可观测性：从埋点到导出",
        "沿真实源码追踪 Registry、跨语言回调、请求生命周期与诊断通道，用实现解释监控平台中的数据。",
        b,
        [
            ("overview", "01 观测架构"),
            ("registry", "02 Registry / 标签"),
            ("bridge", "03 Python / PyO3"),
            ("exports", "04 导出与线程模型"),
            ("lifecycle", "05 请求生命周期"),
            ("latency", "06 TTFT / ITL"),
            ("tracing", "07 日志与上下文"),
            ("capture", "08 Request Trace / FPM"),
            ("health", "09 健康探测"),
            ("kubernetes", "10 Kubernetes / Planner"),
            ("practice", "11 跟读与验证"),
            ("references", "12 参考与差异"),
        ],
    )
)
(OUT / "source-manifest.json").write_text(
    json.dumps(
        dict(
            revision=PIN,
            analysis_date="2026-10-28",
            method="Graph discovery followed by source verification; inference experiments not executed.",
            excerpts=manifest,
        ),
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(f"Built 2 HTML pages, {len(manifest)} verified source excerpts.")
