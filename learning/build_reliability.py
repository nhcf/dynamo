#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the offline reliability and streaming lifecycle source walkthrough."""
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
parts = []
nav = []


def S(key, path, start, end, title):
    return src("rl-" + key, path, start, end, title)


def add(key, title, body, question=None, answer=None):
    title = f"{len(nav) + 1:02} / {title}"
    nav.append((key, title))
    parts.append(section(key, title, body, question, answer))


add(
    "rl-scope",
    "可靠性保证来自多个边界",
    "<p><strong>可靠性不是遇到异常就重试：必须先识别已提交的副作用、响应已交付到哪一层，以及由谁释放资源。</strong>本章沿 HTTP/SSE → Router → Request Plane → Backend/Engine 的主路径分析准入、流式终态、取消、超时、故障抑制、迁移和优雅退出。</p>"
    + table(
        ["边界", "主要责任", "不能单独保证"],
        [
            ["HTTP / SSE", "连接 Drop 检测、首个错误预检、协议事件、指标与终态", "客户端最终收到所有字节或语义完整"],
            [
                "Router / admission",
                "选择/预留容量、队列、取消、cleanup 和 retry lifecycle",
                "引擎立即停止 GPU 工作",
            ],
            ["Runtime context / TCP", "Stop/Kill 状态、子上下文与控制消息", "断网后无限快地检测故障"],
            [
                "Migration",
                "保存 token/decoder replay 状态，重新派发允许重试的错误",
                "原 GPU/KV/采样器状态完全重建",
            ],
            [
                "Engine / backend",
                "精确 abort、流资源释放、first-token/PD KV 协调",
                "所有语言/版本都采用同一个实现",
            ],
            [
                "Shutdown / Kubernetes",
                "停止新流量、等待、drain、cleanup、进程退出",
                "SIGKILL/OOM 下仍执行 Drop/finally",
            ],
        ],
    )
    + note(
        "源码固定于 "
        + PIN
        + "，workspace 1.7.0。该代码提交是本地文档提交的父提交，避免用只存在本地的文档提交 SHA 生成源码链接。当前工作树中分析源码与此提交一致；线上私有 SGLang 1.4.1 必须另行核对。未执行推理、故障注入或集群修改。"
    ),
)

add(
    "rl-architecture",
    "请求状态与所有权如何交接",
    flow(
        [
            "HTTP handler 与连接 guard",
            "预处理 / Router / admission 预留",
            "Backend setup / response handshake",
            "流式帧与 token 处理",
            "正常终态 / 错误 / 取消 / 迁移",
            "释放占用与完成指标",
        ]
    )
    + '<div class="diagram"><div class="lane"><b>取消向下</b><span>HTTP Drop → Context Kill/Stop → linked child → TCP control/context → backend cancellation monitor → engine.abort / SGLang abort_request。</span></div>'
    '<div class="lane"><b>终态向上</b><span>Engine output/error → Router attempt cleanup → Migration 判断 → HTTP preflight 或 SSE error/finalizer → metrics/terminal。</span></div>'
    '<div class="lane"><b>资源归属</b><span>HTTP in-flight guard、Router reservation/lease、backend permit、engine stream、staged KV 分属不同 owner；Drop、异步 finish、远端清理是不同机制。</span></div></div>'
    '<p><a href="reliability-lifecycle.mmd">生命周期与故障分支 Mermaid 源图</a>。正常终止标记、流 EOF、HTTP body Drop、远端引擎释放和 GPU quiescent 不是同一个事件。</p>',
)

add(
    "rl-admission",
    "准入与拒绝：两个容量维度、一份资源所有权",
    S(
        "admission-contract",
        "lib/llm/src/admission_gate.rs",
        32,
        58,
        "request/waiting 两种容量与释放边界",
    )
    + S(
        "permit-drop",
        "lib/llm/src/admission_gate.rs",
        1890,
        1903,
        "首 token 结束 waiting，EnginePermit Drop 释放剩余容量",
    )
    + S(
        "ticket-drop",
        "lib/llm/src/admission_gate.rs",
        2069,
        2076,
        "排队 Future 被丢弃时 AdmissionTicket 离队",
    )
    + "<p>request capacity 覆盖整个 engine stream；waiting capacity 直到首个生成 token 或结束。二者在同一锁下共同预留，避免占住一项等另一项。队列满可以拒绝；正常等待则因获准、取消或 future Drop 离开。</p>"
    + note(
        "Admission 的 DP-rank overload 不应粗暴隔离整个 Worker。应用准入队列、Router scheduler 队列、TCP work-handler pool 和引擎 scheduler queue 是不同层；容量和排队指标不能直接相加或互相替代。"
    ),
)

add(
    "rl-preflight",
    "HTTP 提交前：预检错误与保留缓冲帧",
    S(
        "preflight",
        "lib/llm/src/http/service/openai.rs",
        3072,
        3135,
        "UntilFirstEvent / Bounded / Skip；首个非 annotation 帧错误预检",
    )
    + "<p>在 HTTP response 提交前发现后端错误，可以返回合适 HTTP 错误；提交后只能通过对应流协议表达错误或关闭流，不能把已发出的 200 改成 5xx。Bounded preflight 使用函数入口捕获的一个 deadline，不因每个 annotation 重置窗口；超时将已缓冲帧按原顺序接回流。</p>"
    + "<p>UntilFirstEvent 表示该层自身没有预检窗口上限，不能解读为整个请求永不超时。前置 annotation 与真正输出 token、可渲染文本又不同；观测应在缓冲前记录到达时间，避免把 release time 错当 TTFT/ITL。</p>",
)

add(
    "rl-sse",
    "SSE 正常结束、错误与取消",
    S(
        "sse-default",
        "lib/llm/src/http/service/disconnect.rs",
        603,
        631,
        "stream guard 预设 Cancelled；优先处理 ready 的流帧",
    )
    + S(
        "sse-error",
        "lib/llm/src/http/service/disconnect.rs",
        685,
        724,
        "结构化 error/可选 DONE、EOF mark_ok 与 Kill 分支",
    )
    + "<p>正常 EOF 会按已记录 error signal 决定是否 mark_ok，按协议选择是否发 [DONE]。错误路径先分类、解除断连 guard，再发错误与可选终止 sentinel，避免正常错误关闭被误算作客户端断开。OpenAI、Responses、Anthropic 的终止格式不应混为一谈。</p>"
    + note(
        "SSE HTTP 200 和 [DONE] 都不是业务成功的充分条件：错误流也可能有终止 sentinel。代码在产生/提交终止事件处设置的 emitted 标志不是网络 ACK，不能证明客户端应用已收到或处理成功。"
    ),
)

add(
    "rl-disconnect",
    "断连的两个阶段：handler 与 response body",
    S(
        "connection-drop",
        "lib/llm/src/http/service/disconnect.rs",
        269,
        284,
        "armed/disarmed 状态在 Drop 时送 oneshot 信号",
    )
    + S(
        "two-guards",
        "lib/llm/src/http/service/disconnect.rs",
        293,
        318,
        "两个 owner 分别覆盖 handler 与流 body",
    )
    + S(
        "disconnect-counter",
        "lib/llm/src/http/service/disconnect.rs",
        321,
        358,
        "连接/流异常关闭：计数后 Kill context",
    )
    + "<p>返回 SSE 后 handler 本身会结束，所以连接 guard 必须把责任交给 response body 的 guard。后者在流被正常消费前 Drop 时触发取消。guard 在 body 第一次 poll 前就需要 armed，才能覆盖 lazy stream 从未被 poll 的断连场景。</p>"
    + note(
        "取消次数、断连次数和最终请求计数来自不同埋点，可能存在并发信号及两阶段路径。不能把各层 counter 简单相加为唯一用户取消数；需要用 request ID/attempt 和终态定位。"
    ),
)

add(
    "rl-context",
    "Stop、Kill 与子上下文",
    S(
        "context-api",
        "lib/runtime/src/engine.rs",
        117,
        159,
        "AsyncEngineContext 的软停止与硬终止意图",
    )
    + S(
        "controller",
        "lib/runtime/src/pipeline/context.rs",
        444,
        505,
        "Controller 向已有 children 传播 Stop/Kill",
    )
    + "<p>stop_generating/stop 请求停止继续生成，已有结果仍可 drain；kill 进一步表达不再 drain 的偏好。它们都是上下文信号，不是 OS kill，更不是同步等待 GPU kernel 停止。child context 的连接与观察仍依具体调用点完成。</p>"
    + "<p>Controller.link_child 在这里将 child 加入列表；迁移创建新 attempt 后另行检查 parent/attempt 是否已经 stopped，避免把“刚关联一个 child”当作天然已经处理所有取消竞态。保持上下文图无环也是调用者责任。</p>",
)

add(
    "rl-tcp",
    "跨进程取消与被动连接故障",
    S(
        "tcp-control",
        "lib/runtime/src/pipeline/network/tcp/client.rs",
        435,
        464,
        "TCP Stop/Kill 控制消息与 Sentinel 分开处理",
    )
    + S(
        "tcp-log",
        "lib/runtime/src/pipeline/network/tcp/client.rs",
        36,
        46,
        "request_span 上记录 signal/source=upstream",
    )
    + "<p>Stop/Kill 消息显式修改请求 Context；Sentinel 表示请求流端结束，不应一概当取消。连接读写失败还会通过当前传输实现关闭/错误路径影响流。本文重点是已部署 TCP 主路径，不宣称其他 transport 的内部清理完全相同。</p>"
    + note(
        "Worker 的 cancellation signal 指标/日志证明收到了信号，不证明 engine abort 已完成。确认计算停止、slot/KV 释放，应继续追 backend 与引擎注册表/容量变化。"
    ),
)

add(
    "rl-rust-backend",
    "Rust EngineAdapter：取消通知与 stream Drop 的分工",
    S(
        "cancel-monitor",
        "lib/backend-common/src/adapter.rs",
        128,
        151,
        "CancelMonitorGuard 持有 stream/drop channel",
    )
    + S(
        "abort-select",
        "lib/backend-common/src/adapter.rs",
        340,
        374,
        "监听 stopped/killed/drop；普通 Drop 不等于主动 abort",
    )
    + S(
        "abort-wait",
        "lib/backend-common/src/adapter.rs",
        383,
        414,
        "必要时等 first-token 后 abort；abort 有 5s timeout",
    )
    + "<p>EngineAdapter 的 abort 主要用于 out-of-band 通知；单纯 stream Drop 的清理还依赖 generate 流持有的 RAII 资源。取消与 Drop 竞态、first-token 与 abort 注册竞态需要分开处理，不能假定 async generator 的 Drop 总能执行任意 await 清理。</p>"
    + note(
        "ABORT_TIMEOUT=5s 限制本 adapter 等 abort 的时间，超时后释放取消流；不是承诺引擎在 5s 内停止计算，也不等于远端 KV 立即回收。该路径属于 Rust backend-common，不直接代表所有 Python backend。"
    ),
)

add(
    "rl-sglang",
    "Python SGLang：精确请求 ID、注册与有序 abort",
    S(
        "sg-abort",
        "components/src/dynamo/sglang/request_handlers/cancellation.py",
        92,
        102,
        "按 request IDs 调 tokenizer_manager.abort_request",
    )
    + S(
        "sg-monitor",
        "components/src/dynamo/sglang/request_handlers/cancellation.py",
        259,
        309,
        "监听取消/shutdown，区分精确注册、有序 abort 与请求集合",
    )
    + S(
        "sg-finally",
        "components/src/dynamo/sglang/request_handlers/cancellation.py",
        449,
        490,
        "退出 monitor 时保留有序清理、取消任务、再传播错误",
    )
    + "<p>request 可能先提交、稍后才在 tokenizer manager 注册。当前代码通过 request_id_future、注册状态和有序 abort 避免取消过早导致 abort 找不到请求；必要时还有受限重试。日志中的 Aborted 是调用动作，不等价于证明 scheduler 已完全清空。</p>"
    + "<p>具体 batch/multiple IDs 与 registry state identity 必须按本版本实现理解，不能只在 Python generator 的 finally 里打印一条日志就宣布清理完成。1.4.1 私有镜像需要检查对应文件和引擎 API。</p>",
)

add(
    "rl-pd-cleanup",
    "PD 取消特例：为释放 staged KV 继续 dispatch",
    S(
        "pd-policy",
        "lib/llm/src/kv_router/routing_host/cancellation.rs",
        47,
        97,
        "Decode + StagedKv::Present 才保留 cleanup dispatch",
    )
    + S(
        "pd-budget",
        "lib/llm/src/kv_router/routing_host/cancellation.rs",
        18,
        43,
        "整个 cleanup route 共享 120 秒预算",
    )
    + S(
        "pd-await",
        "lib/llm/src/kv_router/routing_host/cancellation.rs",
        103,
        130,
        "上下文停止后用剩余预算等当前操作",
    )
    + "<p>远端 Prefill 暂存的 KV 可能只能由原定 Decode Worker 在 transfer-complete 路径释放。客户端已断开时仍继续这段 dispatch，是为释放已产生的副作用，不是继续给用户生成答案。仅有 Decode phase 不够：conditional-disaggregation bypass 没有 staged KV，不需要这个例外。</p>"
    + note(
        "预算从第一个观察到停止的相关 stage 开始，共享给选择、记录、dispatch 等阶段，不是每阶段另给 120s。耗尽时记录 request_id/stage/budget，返回 Cancelled，避免再迁移到另一个 Worker；若原定 Worker 未被到达，staged KV 可能要等过期才能释放。"
    ),
)

add(
    "rl-timeouts",
    "超时、心跳与慢消费者",
    S(
        "timeout-config",
        "lib/llm/src/http/service/disconnect.rs",
        42,
        58,
        "HTTP safety net 使用 backend inactivity 配置的 2 倍",
    )
    + S(
        "timeout-router",
        "lib/runtime/src/pipeline/network/egress/push_router.rs",
        2061,
        2093,
        "Request Plane 原值 T：无下一项时隔离 Worker 并产出 typed error",
    )
    + S(
        "timeout-http",
        "lib/llm/src/http/service/disconnect.rs",
        729,
        771,
        "只用真实流活动重置，超时 Kill context 释放 guard",
    )
    + table(
        ["机制", "范围", "边界"],
        [
            [
                "DYN_HTTP_BACKEND_STREAM_TIMEOUT_SECS=T",
                "后端 stream inactivity；unset/0 禁用相应超时",
                "不是整请求 wall-clock deadline；HTTP 2T 是兜底而非精确执行先后保证",
            ],
            [
                "DYN_RESPONSE_STREAM_ESTABLISH_TIMEOUT_SECS",
                "response stream 建立阶段",
                "与首 token/逐帧 inactivity 不同",
            ],
            ["SSE keep-alive", "维护 HTTP 连接/代理空闲状态", "不应把死后端永久伪装成活动"],
            [
                "pull-based stream / send await",
                "本地背压与有限 buffer 的消费节奏",
                "慢客户端不保证引擎/GPU 同步暂停；流不被 poll 时异步计时也非独立硬实时 watchdog",
            ],
        ],
    )
    + "<p>长 Prefill、工具/多模态阶段和协议延迟输出都可能长时间无可见 token；设定 T 要结合真实 backend activity 和最长合法静默，不能一律按用户期望 ITL 配置。</p>",
)

add(
    "rl-fault",
    "Worker 故障检测与迁移资格",
    S(
        "inhibition",
        "lib/runtime/src/pipeline/network/egress/push_router.rs",
        2043,
        2060,
        "仅开启 fault detection 时对符合条件错误 report_instance_down",
    )
    + S(
        "migration-reasons",
        "lib/llm/src/migration.rs",
        89,
        147,
        "blocking reasons 优先，遍历完整错误链判定资格",
    )
    + "<p>连接失败、stream disconnected/incomplete、backend response timeout、engine shutdown、worker unavailable 等原因可参与重试；请求取消/deadline、backend cancelled、整体 capacity exhaustion 与 classifier rejection 等阻止迁移。Worker-rank overload 与全池耗尽不是同一语义。</p>"
    + note(
        "report_instance_down 是本地路由故障抑制的一环，不等于删除 Pod、永久摘除 Worker 或触发 Kubernetes 重启。Discovery 恢复、抑制窗口与副本控制是另一层；同一故障并不必然造成一次可成功迁移。"
    ),
)

add(
    "rl-migration",
    "迁移原理：逻辑请求下的多个物理 attempt",
    flow(
        [
            "当前 attempt 的 setup 或流返回 typed failure",
            "检查错误链与显式路由限制",
            "保存已输出 token/decoder checkpoint",
            "消费一次重试预算，创建 linked attempt",
            "路由健康 Worker、追加 replay prompt",
            "继续向同一上游逻辑响应输出",
        ]
    )
    + S(
        "attempts",
        "lib/llm/src/migration.rs",
        531,
        573,
        "预算、attempt 编号、migration_link 与 route trace context",
    )
    + S(
        "stop-before-retry",
        "lib/llm/src/migration.rs",
        573,
        616,
        "派发前重查取消，记录取消 outcome",
    )
    + "<p>初始调用和替代调用都由 RetryManager 管理。同一个逻辑 request ID 可以对应多个 Worker、attempt 和 engine.generate span；诊断不能只按 request ID 聚合后丢掉 attempt。重试预算是有限次数，不是无限重试；此处不能凭空承诺指数退避或跨所有 backend 的相同策略。</p>",
)

add(
    "rl-migration-limits",
    "启用条件、硬路由和不能重放的状态",
    S(
        "disable-migration",
        "lib/llm/src/migration.rs",
        383,
        440,
        "guided decoding、n>1、generation artifact 禁用；初始 attempt +1",
    )
    + S(
        "explicit-pin",
        "lib/llm/src/migration.rs",
        158,
        195,
        "显式 worker pin 约束当前 phase 的迁移资格",
    )
    + S(
        "replay-cap",
        "lib/llm/src/migration.rs",
        854,
        872,
        "超过 migration max sequence length 停止继续重试",
    )
    + "<p>Frontend 的 --migration-limit / DYN_MIGRATION_LIMIT 默认 0；max sequence replay cap 用 --migration-max-seq-len / DYN_MIGRATION_MAX_SEQ_LEN。它们不是 Worker 自动迁移开关。内部预算在设置值基础上 +1，用于原始 attempt，不应把配置 N 解读为总共 N 次派发。</p>"
    + note(
        "源码明确留有 prompt_embeds continuation、sampler penalties/thinking budgets replay 的 TODO；不能据此宣称所有状态已完整转移。guided-decoding FSM、n>1 多 choice 和 generation-artifact 已显式禁止重试。会话 affinity 与用户显式固定 worker 是不同机制，不能用后者限制解释所有 affinity 场景。"
    ),
)

add(
    "rl-replay",
    "重放、解码与 usage 口径",
    S(
        "replay",
        "lib/llm/src/migration.rs",
        755,
        800,
        "捕获 decoder checkpoint、已输出 token、递减剩余生成预算",
    )
    + S(
        "usage",
        "lib/llm/src/migration.rs",
        817,
        850,
        "重试 prompt 中的 replay tokens 重新计入 completion，限制 cached tokens",
    )
    + "<p>已输出 token 被加入下一次 prompt，用于继续生成；detokenizer/隐藏 stop-prefix 等 checkpoint 也要一致。新 Worker 的底层 usage 会把 replay tokens 当作 prompt，而客户端视角它们是已经完成的 completion，因此需要 rebase，而不是简单累加各 Worker usage。</p>"
    + note(
        "这不是恢复原 GPU/KV/随机数生成器的完整 checkpoint，也不是整个协议/网络层的 exactly-once 保证。工具调用、结构化输出、隐藏缓冲、采样状态和多 choice 的安全性必须按所支持契约确认；不要将文档中“无中断”的概述当作所有请求类型承诺。"
    ),
)

add(
    "rl-resources",
    "清理必须覆盖成功、失败、取消与重试",
    S(
        "router-finish",
        "lib/llm/src/kv_router/routing_host/request_guard.rs",
        860,
        892,
        "finish/abort/release_for_retry 的分类与状态交接",
    )
    + S(
        "router-drop",
        "lib/llm/src/kv_router/routing_host/request_guard.rs",
        918,
        924,
        "Drop 记录 attempt 观测，成员资源随后释放",
    )
    + S(
        "inflight-drop",
        "lib/llm/src/http/service/metrics.rs",
        2212,
        2235,
        "InflightGuard Drop：减 gauge、终态 counter、duration、RequestMetrics finish",
    )
    + S(
        "metrics-finish",
        "lib/llm/src/http/service/metrics.rs",
        878,
        900,
        "请求级 finish 幂等，释放 awaiting/generating token gauges",
    )
    + "<p>Router 的 lease、occupancy、LoRA load 和分类生命周期不能因 retry 被误判为最终完成；release_for_retry 会把可延续 lifecycle 交给下一 attempt，再释放当前 attempt 的占用。HTTP guard 和 engine permit 又是独立 owner，不能只看到一个 gauge 归零就证明所有远端资源释放。</p>"
    + note(
        "Drop/finally 只覆盖进程与 runtime 仍有机会执行清理的路径；SIGKILL、OOM、节点掉电不能保证运行这些代码，远端 TTL/发现/故障恢复与持久状态设计仍不可替代。"
    ),
)

add(
    "rl-observe",
    "取消与准入指标：信号不等于最终效果",
    S(
        "admission-metrics",
        "lib/llm/src/admission_gate/metrics.rs",
        52,
        66,
        "独立 dynamo_backend_admission 指标族",
    )
    + S(
        "admission-cancel",
        "lib/llm/src/admission_gate/metrics.rs",
        142,
        161,
        "admit/source、reject、cancel 的帮助与记录含义",
    )
    + table(
        ["信号", "准确含义", "关联方式"],
        [
            [
                "dynamo_frontend_model_cancellation_total",
                "Frontend 连接/流取消检测路径累计",
                "结合 endpoint/request_type 与逻辑请求终态",
            ],
            [
                "dynamo_component_cancellation_total / request_span cancellation.signal",
                "Runtime 取消信号（依所用 transport/路径）",
                "不能当 engine 已 abort 的证明",
            ],
            [
                "dynamo_backend_admission_cancellation_total",
                "backend admission 前/排队中的取消",
                "按 dp_rank；与活跃引擎取消分开",
            ],
            [
                "dynamo_backend_admission_engine_request_count / engine_request_waiting_count / request_queue_count",
                "引擎占用、等首输出占用、准入队列状态",
                "与 Router/HTTP active gauges 对照，而非相加",
            ],
            [
                "Frontend active / awaiting_first_token / inflight_input_tokens",
                "HTTP 逻辑请求进度与已知 token 归属",
                "首字节、首 token 和最终流完成口径分别核查",
            ],
        ],
    )
    + "<p>HTTP 200 率、最终模型 request counter、SSE semantic failure、取消检测 counter 是不同维度；只看 status code 会漏掉已提交流中的错误。错误分类优先保留 canonical error class/reason，避免统一压成 internal 后丢掉 retry/overload/cancel 的语义。</p>",
)

add(
    "rl-migration-observe",
    "迁移指标与 Trace：恢复成功如何解释",
    S(
        "migration-metrics",
        "lib/llm/src/http/service/metrics.rs",
        1541,
        1564,
        "迁移 Counter 与 duration Histogram 的 labels/buckets",
    )
    + S(
        "migration-outcome",
        "lib/llm/src/migration.rs",
        732,
        754,
        "exhausted 的 attempt/reason 日志，outcome duration 记录",
    )
    + S(
        "replacement-success",
        "lib/llm/src/migration.rs",
        627,
        650,
        "替代 stream 建立成功即记录 success，而非等待完整请求终态",
    )
    + "<p>model_migration_total 按 new_request / ongoing_request 统计迁移活动；duration 从检测到迁移故障到替代建立/最终失败/取消。替代流拿到以后仍可能再次失败，因此 migration outcome=success 不等于完整用户请求最终成功。</p>"
    + "<pre><code># 补充真实集群/模型筛选\nsum by (model, migration_type) (\n  rate(dynamo_frontend_model_migration_total[5m])\n)\n\nhistogram_quantile(0.95,\n  sum by (le, model, migration_type, outcome) (\n    rate(dynamo_frontend_model_migration_duration_seconds_bucket[5m])\n  )\n)</code></pre>"
    + "<p>request_span 记录 attempt、reason、from_worker_id、tokens_completed；worker_trace_link 在有输出/可用上下文时形成后续 migration_link，表现为 Span Link，不是强制把多个既有 Trace ID 合并。没有首个可用 link 的 setup 失败不能要求一定有 Worker span 前驱。</p>",
)

add(
    "rl-terminal",
    "生命周期 Trace 的终态：只能完成一次",
    S(
        "terminal-once",
        "lib/runtime/src/telemetry.rs",
        366,
        382,
        "LifecycleTerminal.finish 的 atomic once",
    )
    + S(
        "stream-terminal",
        "lib/llm/src/http/service/openai.rs",
        149,
        184,
        "stream Drop 时保留已发出的 semantic failure，否则 cancelled/failed",
    )
    + S(
        "http-terminal",
        "lib/llm/src/http/service/openai.rs",
        3810,
        3841,
        "在首次 body poll 前持有 fallback，流结束后分类 terminal",
    )
    + "<p>当前 DYN_LIFECYCLE_TRACE_ENABLED 生命周期阶段可帮助区分 preprocessing、dispatch、admission、worker operation、response.streaming；它不自动填补所有引擎/存储事件。terminal emitted/finish once 防止多层 Drop 把一个已确定错误覆盖为 cancellation，但最终状态仍取决于观测到的边界。</p>"
    + note(
        "Request Trace 捕获、JSONL 诊断、生命周期 span 和 Prometheus 是不同通道。生产排查应关联 request ID、attempt、worker ID、Trace ID 与规范错误，不必默认记录完整 prompt/输出载荷。"
    ),
)

add(
    "rl-shutdown",
    "优雅退出：先撤流量，再 grace/drain/cleanup",
    S(
        "py-shutdown",
        "components/src/dynamo/common/utils/graceful_shutdown.py",
        107,
        150,
        "Python：unregister、grace、可选 drain/pre-shutdown、shutdown event",
    )
    + S(
        "rust-shutdown",
        "lib/backend-common/src/worker.rs",
        1341,
        1364,
        "Rust backend-common 的 grace→drain→cleanup",
    )
    + S(
        "prefill-drain",
        "lib/backend-common/src/worker.rs",
        1374,
        1427,
        "Prefill 才 poll quiescent；有限预算、心跳和超时",
    )
    + S(
        "shutdown-gauges",
        "lib/backend-common/src/metrics.rs",
        124,
        163,
        "model_load/drain/cleanup 是 Gauge，不是 Histogram",
    )
    + "<p>Rust drain 默认 30s，预算受 graceful_shutdown_timeout 减 cleanup reserve 约束；每 0.5s poll，约 5s 进度日志。Some(true) 才提前退出；None/false/Err 继续到预算。Python helper 的 drain 是可选 callback；SGLang 安装自己的 signal ownership 后调用该 helper，不直接等同 Rust Worker 的实现。</p>"
    + note(
        "撤注册不能瞬间刷新所有客户端缓存，grace 也不等于所有请求都已完成。Kubernetes terminationGracePeriodSeconds、runtime graceful timeout、drain、engine cleanup 必须协调。下线末期才 Set 的 gauge 可能来不及被 scrape，不能只靠最后一次 Prometheus 样本证明清理执行。"
    ),
)

add(
    "rl-cluster",
    "对照 ACK：核查项与分阶段验收",
    '<p>依据 <a href="ack-LB97i2CZNR/dynamo-test-deployment.md">ACK 1.4.1 部署记录</a>，当前是 SGLang 混合 aggregated/PD、TCP/ZMQ、Worker grace 300s、Frontend grace 60s。记录未给出迁移配置和完整取消实现，不把本源码的新行为视为线上已启用。</p>'
    + table(
        ["用例", "执行/观测重点", "完成证据"],
        [
            [
                "正常 SSE 与非流式",
                "TTFT/ITL、终止格式、in-flight/queue 回落",
                "唯一 request ID、实际 worker/attempt、完整协议结果",
            ],
            [
                "首 token 前断开",
                "handler Drop、准入排队取消、已提交引擎请求",
                "HTTP/runtime/admission/engine 各层信号与占用变化",
            ],
            [
                "首 token 后断开",
                "response body Drop、engine abort 与残留请求",
                "被取消请求不继续长期占用，其他请求正常",
            ],
            [
                "PD staged KV 后断开",
                "确认真实 PD，cleanup dispatch 是否达到指定 Decode",
                "stage/budget、KV释放/过期、没有无意义迁移",
            ],
            [
                "后端静默与连接故障",
                "区分 establish/inactivity/client heartbeat",
                "typed error、Worker inhibition、请求终态与资源回落",
            ],
            [
                "受控 Worker 中断并迁移",
                "启用配置、允许请求类型、替代容量与 token replay",
                "attempt/link、usage、输出边界、恢复及最终终态",
            ],
            [
                "SIGTERM 与强杀",
                "分别验收 graceful/shutdown 和故障恢复",
                "撤注册/流量变化、grace/drain日志、重试与最终资源状态",
            ],
        ],
    )
    + "<p>故障注入必须在隔离实例/受控窗口执行，先确认路由不会误入 igd-* 或 aggregated 旁路。这里仅给验收设计，没有对现有集群执行断连压测、杀 Pod 或更新配置。</p>",
)

add(
    "rl-evidence",
    "阅读证据、覆盖与后续边界",
    '<p><a href="reliability-source-manifest.json">固定提交源码清单</a>包含路径、行号、revision 和 SHA-256。相关专题：<a href="observability.html">可观测</a>、<a href="pd-trace.html">PD Trace</a>、<a href="storage-events.html">Storage &amp; Events</a>、<a href="control-plane.html">控制面</a>。</p>'
    + "<p>配套官方仓库文档位于 docs/fern/pages/developer-guide/knowledge-base/concepts/fault-tolerance/，覆盖 cancellation、migration、rejection、admission 与 graceful shutdown。部分文档是原理概述，本文用固定实现限定其保证范围。</p>"
    + "<p>已有测试可作为阅读入口：disconnect 的 unpolled/terminal/error/timeout cases，routing_host/cancellation 的 shared-budget tests，adapter 的 abort-stall tests，migration 的 guided decoding/usage/replay cases。此次只校验文档，不把阅读测试源码说成已执行推理测试。</p>"
    + note(
        "不逐行覆盖所有传输、所有 Python backend、各引擎 scheduler 内部、GPU kernel 中断、存储故障一致性及进程崩溃后的全部恢复协议。以请求完成/资源释放/远端恢复三套边界核验可靠性，不能用其中一种信号替代另外两种。"
    ),
)

result = page(
    "可靠性与流式生命周期：架构、原理与源码",
    "沿响应所有权、取消传播、资源清理、故障重试和优雅退出核对可靠性保证。",
    "".join(parts),
    nav,
)
result = result.replace(
    '<span class="pill">2026-10-28</span>', '<span class="pill">2026-10-29</span>'
)
result = result.replace("首个源码专题 v0.1", "可靠性与流式生命周期 v1.0")
(OUT / "reliability.html").write_text(result)
(OUT / "reliability-source-manifest.json").write_text(
    json.dumps(
        {
            "revision": PIN,
            "analysis_date": "2026-10-29",
            "scope": "HTTP/SSE lifecycle, admission, cancellation, PD cleanup, fault handling, migration, resource release and shutdown observability",
            "runtime_verified": False,
            "excerpts": base["manifest"],
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(
    f"Built reliability chapter: {len(base['manifest'])} excerpts, {len(nav)} sections."
)
