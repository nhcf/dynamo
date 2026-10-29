#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build offline storage and events architecture with source evidence."""
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
    return src("se-" + key, path, start, end, title)


def add(key, title, body, question=None, answer=None):
    title = f"{len(nav) + 1:02} / " + title.split(" / ", 1)[1]
    nav.append((key, title))
    parts.append(section(key, title, body, question, answer))


add(
    "se-map",
    "01 / 组件边界：缓存字节、成员索引与观测信号",
    "<p><strong>Storage 保存和搬运 KV 数据；Events 描述缓存驻留、移除和运行状态。</strong>事件不是 KV tensor 本身，路由索引也不是缓存存储。本章按当前源码拆解逻辑块、物理搬运、协调会话和事件传播，再追踪各层可观测信号的产生及导出。</p>"
    + table(
        ["组件", "职责", "需要避免的混淆"],
        [
            [
                "引擎 KV cache / connector",
                "引擎分配 KV、执行 attention，connector 协调 PD cache 传输",
                "用 NIXL 不等于使用 Dynamo KVBM",
            ],
            ["kvbm-logical", "块状态、哈希、注册、持有/淘汰与池度量", "逻辑块不等于一段可跨 layout 直接复制的裸地址"],
            [
                "kvbm-physical",
                "内存/layout 注册、策略选择、搬运与完成通知",
                "submit 不等于完成，通知事件不等于 Event Plane 消息",
            ],
            [
                "kvbm-engine",
                "Leader/Worker、分层 offload、onboarding、对象存储协调",
                "不同 backend/feature/配置可能只启用一部分",
            ],
            [
                "lib/llm/src/block_manager",
                "另一组仍在仓库中的块管理与 KVBM 指标实现",
                "不能把这里的字段、端口与新 crates 的指标当成同一套",
            ],
            [
                "kvbm-consolidator",
                "合并引擎与 KVBM 缓存事件、处理驻留引用并输出兼容事件",
                "中间聚合不自动保证持久回放/每消息必达",
            ],
            [
                "Runtime Event Plane / KV publisher",
                "通用 pub/sub；引擎事件转换、索引入队与外部发布",
                "KV routing advisory events 不等于一致性数据库日志",
            ],
        ],
    )
    + note(
        "源码固定于 "
        + PIN
        + "，workspace 1.7.0；集群快照是私有 SGLang 1.4.1。本文为静态源码分析，没有执行 CUDA/NIXL/S3 测试、部署、压测或线上抓包；不能用新 crates 的能力推定旧镜像已启用。"
    ),
)

add(
    "se-architecture",
    "02 / 架构：数据流与元数据流分开",
    '<div class="diagram"><div class="lane"><b>KV 数据</b><span>Engine G1 GPU ↔ G2 pinned host ↔ G3 disk；G4 object storage 可选。跨实例可先把远端 G3 stage 到远端 G2，再经 NIXL pull 到本地 G2；路径由配置、layout、能力与策略决定。</span></div>'
    '<div class="lane"><b>逻辑/协调</b><span>SequenceHash / BlockRegistry / pools → Leader 搜索与会话 → Worker / TransferManager → 执行和等待完成 → 目的层注册。</span></div>'
    '<div class="lane"><b>缓存事件</b><span>Engine 原生 ZMQ 或 KVBM EventsManager → 可选 consolidator / publisher → Event Plane ZMQ/NATS → Router indexer；只传播驻留与负载等元数据。</span></div>'
    '<div class="lane"><b>可观测</b><span>块池 atomics / legacy counters / pipeline timing logs / relay counters / NIXL telemetry → 对应 Registry 或 exporter → Prometheus/日志平台；OTel propagation 另有明确边界。</span></div></div>'
    '<p><a href="storage-events-architecture.mmd">架构图 Mermaid 源文件</a>；本页 HTML 图离线可读。图中箭头表示可能路径，不表示所有部署都启用每层或强制逐层经过。</p>',
)

add(
    "se-lifecycle",
    "03 / 逻辑块：状态机、持有与淘汰",
    S(
        "block-state",
        "lib/kvbm-logical/src/blocks/mod.rs",
        4,
        33,
        "Mutable / Complete / Immutable / Weak 的类型状态",
    )
    + flow(
        [
            "Reset pool 分配 MutableBlock",
            "写入后 stage/complete 成 CompleteBlock",
            "注册为 ImmutableBlock / SequenceHash",
            "强持有阻止回收；Weak 不保活",
            "释放后按生命周期进入可回收/缓存状态",
        ]
    )
    + "<p>注册表回答“某逻辑前缀/哈希是否有可持有的块”，池与淘汰后端管理可用 slot 和缓存候选。匹配得到强句柄后，需要保证异步传输期间源、目的内存仍有效；不能只保存一个裸 block ID 后让源被回收。</p>"
    + note(
        "逻辑块的状态转换不代表 KV bytes 已写入目的地。物理传输成功、目的块注册以及对外发驻留事件之间要保持正确顺序；否则 Router 可能认为一个尚未可用的块已经存在。"
    ),
)

add(
    "se-logical-metrics",
    "04 / 新块池指标：从操作到原子量",
    S(
        "atomics",
        "lib/kvbm-logical/src/metrics/pool_metrics.rs",
        15,
        45,
        "每个 BlockManager 的累计量与当前量",
    )
    + S(
        "increments",
        "lib/kvbm-logical/src/metrics/pool_metrics.rs",
        81,
        141,
        "Relaxed atomics 记录 allocation/registration/match/scan",
    )
    + S(
        "match",
        "lib/kvbm-logical/src/manager/mod.rs",
        135,
        168,
        "实际 match_blocks 记录输入 hash occurrences 与返回块数",
    )
    + "<p>热路径只更新 atomics，避免在每次操作上创建 Prometheus labels/protos；这仍有原子操作和竞争成本，不是绝对零开销。Relaxed 多原子快照也不是所有字段同一瞬间的事务一致视图。</p>"
    + table(
        ["指标族", "口径", "错误解释"],
        [
            [
                "kvbm_allocations_total / evictions_total / registrations_total",
                "块分配/淘汰/注册操作累计数",
                "不能直接当请求数或 GPU bytes",
            ],
            [
                "kvbm_match_hashes_requested_total / match_blocks_returned_total",
                "输入 hash 出现次数 / 返回 hit 出现次数；prefix 和 scattered 共用 match 指标",
                "不是全请求命中率，也不是全局唯一块数",
            ],
            [
                "kvbm_scan_hashes_requested_total / scan_blocks_returned_total",
                "scan 输入出现次数 / 返回 distinct matching hashes",
                "与 match 分子口径不同，不直接拼一个总命中率",
            ],
            [
                "kvbm_inflight_mutable / inflight_immutable / reset_pool_size / inactive_pool_size",
                "当前状态计数/gauge",
                "不能把 inflight_immutable 简单等同显存占用 bytes",
            ],
            [
                "kvbm_allocate_atomic_rollback_total 等 audit counters",
                "特定竞争/回滚/释放分支",
                "非零要按各分支契约判断，不能一律等同用户请求失败",
            ],
        ],
    ),
)

add(
    "se-scrape",
    "05 / 新指标导出：Collector 必须接上 Registry",
    S(
        "agg-wire",
        "lib/kvbm-logical/src/manager/builder.rs",
        450,
        463,
        "build 时仅在提供 aggregator 后注册 source",
    )
    + S(
        "agg-register",
        "lib/kvbm-logical/src/metrics/collector.rs",
        144,
        172,
        "source、外部标签与 register_with",
    )
    + S(
        "agg-collect",
        "lib/kvbm-logical/src/metrics/collector.rs",
        181,
        210,
        "scrape 时读取 snapshot、生成 pool 与外部 labels",
    )
    + "<p>完整链路是 BlockManager 操作 → BlockPoolMetrics → MetricsAggregator.register_source → register_with(目标 Registry) → 集成方暴露的 /metrics。MetricsAggregator 自身不会自动创建 HTTP server，也不会因为 Worker :9090 可用就自动出现在该端点。</p>"
    + note(
        "默认 pool 标签来自 short_type_name&lt;T&gt;，不是每次都固定为 G1/G2。instance/worker labels 需集成方设置；同一 pool label 的多个 sources 可能需要额外区分或合并，不能假定 source 名天然唯一。确认实际抓取端点与 labels 后再建面板。"
    ),
)

add(
    "se-stats",
    "06 / StatsCollector：局部采样率不等于导出指标",
    S(
        "stats",
        "lib/kvbm-logical/src/metrics/stats.rs",
        99,
        166,
        "sample：delta/dt、match/scan ratio 与梯度",
    )
    + "<p>StatsCollector 可选启用，sample() 在禁用时不工作。首个样本没有前值时使用 zero_stats；后续从原始累计值差和实际 dt 计算 allocations/s、evictions/s、命中比及每秒速率变化。这里只维护本地样本，不自动向 Registry 增加这些派生指标。</p>"
    + "<pre><code># Prometheus 侧计算逻辑 match occurrence 命中比\n# 补充真实集群/实例标签；空闲无样本时不强行填成 0\nsum by (pool) (rate(kvbm_match_blocks_returned_total[5m]))\n/\nsum by (pool) (rate(kvbm_match_hashes_requested_total[5m]))</code></pre>"
    + note(
        "scan 的 distinct 分子可能受重复输入影响；不要把 scan ratio 与 match occurrence ratio 当作完全相同定义。缓存命中还需区分 Router 预测、逻辑块匹配、引擎 prefix reuse 与用户实际避免的计算量。"
    ),
)

add(
    "se-legacy",
    "07 / 既有 block_manager 指标：定义、更新和独立出口",
    S(
        "legacy-fields",
        "lib/llm/src/block_manager/metrics_kvbm.rs",
        21,
        62,
        "旧路径 offload/onboard、滑窗 hit-rate 与对象失败字段",
    )
    + S(
        "legacy-endpoint",
        "lib/llm/src/block_manager/metrics_kvbm.rs",
        155,
        192,
        "create_endpoint=false 只返回指标；true 建立独立 HTTP server",
    )
    + S(
        "legacy-update",
        "lib/llm/src/block_manager/metrics_kvbm.rs",
        240,
        253,
        "hit rates 直接 Set；对象失败按 num_blocks 增加",
    )
    + S(
        "legacy-registry",
        "lib/llm/src/block_manager/metrics_kvbm.rs",
        268,
        298,
        "独立 Registry 及 kvbm_ 前缀",
    )
    + "<p>旧路径的 kvbm_offload_blocks_d2h / kvbm_offload_blocks_h2d 等名称没有新逻辑池指标的 _total 后缀，但类型是 Counter。D2D 在这些字段中指 device→disk，不应套用 CUDA D2D=device→device 的缩写含义。对象失败指标以传入 num_blocks 计量，不能当作 HTTP/S3 请求失败次数。</p>"
    + note(
        "调用图未找到本提交 KvbmMetrics::new 的直接生产调用者，这只是 best-effort 结果，不证明所有外部集成都不用它。这里分析定义与可选出口，未证明当前 Worker 实例化或导出了它；hit-rate 更新函数存在也不证明持续被调用。"
    ),
)

add(
    "se-counter-boundary",
    "08 / 计数边界：offload 尝试与成功完成不同",
    S(
        "legacy-attempt",
        "lib/llm/src/block_manager/offload.rs",
        478,
        499,
        "拿到目的 block 后先 metric.inc，再 enqueue_transfer",
    )
    + "<p>该 offload 路径在分配到目标块后、传输排队前增加计数。因此计数包含已准备并提交的工作，不是严格的完成确认；目标池满而跳过的分支不会走到这里。不要用 offload_blocks × block_bytes 宣称“成功落盘带宽”，也不能用 offload 与 onboard 的差值直接推断缓存剩余量。</p>"
    + "<p>成功字节量、失败/取消量、排队等待、执行耗时应按不同边界观测；涉及多个 rank/layout 时，块数到字节还需要真实张量 layout、dtype、层数、KV 维度与分片信息。</p>",
    "为什么 offload counter 增加，但看不到目标层命中？",
    "计数可能在传输提交前增加；还需验证执行完成、目的层注册、后续是否被淘汰，以及读请求实际查询的层与 namespace。",
)

add(
    "se-offload",
    "09 / 分层 Offload：策略、前置条件、批处理、提交",
    flow(
        [
            "PolicyEvaluator 决定是否迁移",
            "Batcher 合并符合条件的 containers",
            "PreconditionAwaiter 等待 forward/CUDA 条件",
            "取消检查与 Weak→Strong 升级",
            "Worker 执行并等待完成",
            "目的层 stage/register，再交给下游链",
        ]
    )
    + S(
        "pipeline-wiring",
        "lib/kvbm-engine/src/offload/pipeline.rs",
        347,
        382,
        "当前实际接线：Policy → BatchCollector → PreconditionAwaiter → Executor",
    )
    + "<p>配套 offload.md 的概览图将 Precondition 放在 Batcher 前，而当前 pipeline.rs 实际先 batch 后 await；本章以接线代码为准。</p>"
    + "<p>Container 是取消单元，提交前 sweep 已取消工作；Weak→Strong upgrade 后持有实际块，进入不能简单撤销的阶段。不能把单次推理请求取消等同于所有已经提交的后台 cache 搬运都被中断。批处理可能聚合不同请求产生的块。</p>"
    + S(
        "timing",
        "lib/kvbm-engine/src/offload/batch.rs",
        24,
        42,
        "TimingTrace 是本进程 Instant 阶段时间点",
    )
    + S(
        "batch-log",
        "lib/kvbm-engine/src/offload/pipeline.rs",
        1600,
        1650,
        "传输后注册目的块，记录阶段毫秒数",
    )
    + S(
        "timing-finish",
        "lib/kvbm-engine/src/offload/pipeline.rs",
        1691,
        1710,
        "完成时间在前述日志之后才标记",
    )
    + S(
        "precondition-timeout",
        "lib/kvbm-engine/src/offload/pipeline.rs",
        1262,
        1295,
        "等待超时代码为 300 秒，错误日志文案却为 30s",
    )
    + note(
        "按上方普通 BlockTransferExecutor 成功路径的调用顺序，日志在 mark_transfer_complete 前读取 total_duration，尚未填充的值经 unwrap_or(0) 记为 0；因此该 total_ms 不能视为可靠总耗时。precondition_ms 从 policy_complete 计到 precondition_complete，可能包含批处理等待，不是纯 GPU 前置事件等待。超时阈值也应以 Duration::from_secs(300) 为准，不按日志文字推断 30 秒。这里是静态推导，未做运行复现。"
    )
    + note(
        "Batch transfer complete 日志包含 blocks、containers、policy_ms、precondition_ms、xfer_ms、registration_ms、total_ms。TimingTrace 不是 OTel Span，没有天然的跨进程 epoch/Trace ID；Instant 不能用于直接对齐不同机器的时间点。日志阶段值中 unwrap_or(0) 还会把缺失时间记为 0，不能把所有 0 解读成真实零耗时。"
    ),
)

add(
    "se-onboarding",
    "10 / Onboarding：找到块、保留块、准备与拉取",
    S(
        "onboard-status",
        "lib/kvbm-engine/src/leader/onboarding.rs",
        10,
        66,
        "Holding/Preparing/Prepared/Staging/Complete 的位置与数量",
    )
    + "<p>Leader 搜索并持有本地/远端匹配块；Hold 仅保留，Prepare 将 G3 stage 到 G2，Full 执行远端 G2→本地 G2 的 pull。具体模式可在不同阶段结束，不强制每次都走完整顺序。G4 load 有 pending/loaded/failed 等位置统计。</p>"
    + "<p>这些是操作状态数据，不天然是 Prometheus 指标。诊断应区分“匹配到缓存”“已经 pin 住”“已 stage 到可 RDMA 的 G2”“远端 pull 完成”“引擎可消费”；远端命中不保证低 TTFT，还受磁盘准备、网络与队列影响。</p>",
)

add(
    "se-object",
    "11 / G4 对象存储：数据、可见性标记与部分失败",
    "<p>ObjectBlockOps 把 has/put/get 与物理 layout/blocks 联系起来；ObjectLockManager 协调多个实例的重复 offload。典型流程是检查 meta → 争取锁 → 上传各块/rank 数据 → 创建 meta → 释放锁。S3 锁实现使用 conditional PUT 及过期恢复；这里只分析接口与 pipeline 可见性，不逐行证明底层锁算法。</p>"
    + S(
        "object-lock-contract",
        "lib/kvbm-engine/src/object/mod.rs",
        277,
        309,
        "meta existence、lock acquisition、create_meta、release_lock 契约",
    )
    + S(
        "object-results",
        "lib/kvbm-engine/src/offload/pipeline.rs",
        1935,
        1979,
        "按输入 block 对齐结果；分别记录 success_count/fail_count",
    )
    + S(
        "object-meta",
        "lib/kvbm-engine/src/offload/pipeline.rs",
        1982,
        2018,
        "成功 hash 创建 meta 后释放锁，失败也需释放；错误日志",
    )
    + "<p>同一逻辑 hash 在不同 SPMD rank 上可能对应不同物理切片，因此对象 key 需要正确的 rank/schema 隔离。数据上传成功不自动等于元数据可见；create_meta、release_lock 的失败也需要检查。这里逐块结果和日志不能替换为“调用没抛错就是整批成功”。</p>"
    + note(
        "本路径对结果个数不匹配会标记块失败并 return Ok；正确结果还可能部分成功、部分失败。观测应保留 expected/actual、success_count/fail_count、失败 hash 和 meta/lock 错误。不要只统计 Rust Result::Err 或只复用旧 KvbmMetrics 的对象失败字段，就声称已覆盖新 G4 管线。"
    ),
)

add(
    "se-physical",
    "11 / 物理搬运与 NIXL：提交不等于完成",
    S(
        "strategy",
        "lib/kvbm-physical/src/transfer/strategy.rs",
        16,
        76,
        "Memcpy/CUDA/NIXL 与 Direct/TwoHop 的不同策略",
    )
    + S(
        "nixl-post",
        "lib/kvbm-physical/src/transfer/executor/nixl.rs",
        351,
        381,
        "创建请求、post、按同步完成或注册 status 轮询返回 notification",
    )
    + S(
        "slow",
        "lib/kvbm-physical/src/transfer/notifications/mod.rs",
        54,
        75,
        "等待超过 60s 后警告、后续约 30s 限频",
    )
    + S(
        "poll",
        "lib/kvbm-physical/src/transfer/notifications/mod.rs",
        111,
        157,
        "状态成功触发完成事件、错误 poison 事件",
    )
    + "<p>TransferManager 根据 src/dst layout、locality、能力与偏好选执行器；NIXL Read/Write 的远端 agent 取决于方向。post 返回 pending 时，要保留请求/内存直到完成通知，不能直接复用 buffer。轮询正常周期为 1ms，但它不是网络耗时精度或 completion SLA 保证。</p>"
    + note(
        "慢传输 warning 只是诊断，所示函数不会在 60s 自动 cancel/timeout。Velo EventHandle、CUDA event 和 NIXL notification 是完成同步机制，不是 Runtime Event Plane 的 KV 驻留事件。"
    ),
)

add(
    "se-nixl-observe",
    "12 / NIXL 遥测：独立开关、端口与 rank",
    S(
        "nixl-enable",
        "components/src/dynamo/common/utils/nixl_telemetry.py",
        100,
        130,
        "识别 enable + prometheus exporter，拒绝不可声明的随机端口",
    )
    + S(
        "nixl-rank",
        "components/src/dynamo/common/utils/nixl_telemetry.py",
        151,
        172,
        "按 Pod 内 local rank 派生 exporter 端口",
    )
    + S(
        "sglang-env",
        "components/src/dynamo/sglang/nixl_telemetry.py",
        132,
        149,
        "在 SGLang scheduler 子进程内设置 NIXL exporter port",
    )
    + "<p>当前 Dynamo helper 识别 NIXL_TELEMETRY_ENABLE、NIXL_TELEMETRY_EXPORTER=prometheus 与固定 NIXL_TELEMETRY_PROMETHEUS_PORT。每个共置 scheduler 的 NIXL agent 可能要独立端口；应在 agent 构造前、正确 rank 的进程设置，不能只在父进程临时改值。</p>"
    + note(
        "NIXL upstream main 已有 prometheus_mp 多进程聚合描述，但本固定提交 helper 明确将该适配留作 TODO。必须核对实际安装版本和 exporter，不能把 main 文档直接当作集群镜像能力。也不能仅凭 Pod 声明 nixl:19090 就证明 exporter 监听、遥测开启或所有 TP rank 已被采集。"
    )
    + '<p>主库能力参考 <a href="https://github.com/ai-dynamo/nixl/blob/main/docs/telemetry.md">NIXL 官方 Telemetry</a>；Dynamo 本地 wrapper 的范围以上方源码为准。NIXL metrics 是传输级观测，不自动成为用户请求级 Trace。</p>',
)

add(
    "se-event-kinds",
    "13 / 同名 Events 背后是不同契约",
    table(
        ["事件种类", "内容与用途", "是否天然可请求级关联"],
        [
            [
                "CUDA / Velo / NIXL completion",
                "本地或跨实例同步某次操作完成/失败",
                "transfer UUID/EventHandle 不是 OTel Trace ID",
            ],
            [
                "KVBM Create / Remove / batch / Shutdown",
                "逻辑块驻留生命周期与实例上下文",
                "共享块/批次可能对应多个请求",
            ],
            [
                "Engine 原生 KV events",
                "引擎 cache Store/Remove/Clear 与 engine seq/rank",
                "格式由 backend 协议决定",
            ],
            [
                "Runtime EventEnvelope",
                "publisher_id、sequence、published_at、topic、payload",
                "外层没有通用 traceparent/tracestate 字段",
            ],
            [
                "FPM / Worker load telemetry",
                "调度迭代、负载、容量输入",
                "通常按 worker/rank/iteration；详见 Planner 专题",
            ],
            ["Kubernetes Event", "控制器资源变更诊断", "不是本章消息总线，也不是 OTel event 自动转发"],
        ],
    )
    + '<p>FPM 的完整生产/消费口径见 <a href="planner.html">Planner 观测输入专题</a>；请求生命周期传播见 <a href="pd-trace.html">PD Trace 专题</a>，这里重点追 KV 存储和事件可靠性。</p>',
)

add(
    "se-kvbm-events",
    "14 / KVBM 内部事件：RAII → broadcast → batching → publisher",
    S(
        "kvbm-protocol",
        "lib/kvbm-logical/src/events/protocol.rs",
        24,
        61,
        "逻辑事件与批次的 instance_id",
    )
    + S(
        "kvbm-emit",
        "lib/kvbm-logical/src/events/manager.rs",
        153,
        201,
        "普通订阅过滤 lag error；loss-aware 订阅及注册触发 Create",
    )
    + S(
        "kvbm-publish",
        "lib/kvbm-logical/src/events/publisher.rs",
        133,
        163,
        "MessagePack 编码、失败记录日志、结束时 Shutdown/flush",
    )
    + "<p>注册 hook 根据 emission policy 发送 Create，并 attach RAII release handle，使最终释放产生 Remove。默认 subscribe() 过滤 broadcast lag 错误并继续；subscribe_loss_aware() 提供显式错误给需要严格局部语义的消费者。晚订阅者不会自动得到过去事件。</p>"
    + note(
        "Publisher 日志失败后继续，不自动提供持久回放或每批重试。这里自己的 KvbmCacheEvents wire schema 与 Runtime EventEnvelope 是不同接口；只有经具体 adapter 桥接时才建立关系。"
    ),
)

add(
    "se-consolidator",
    "15 / Consolidator：同一逻辑块多处驻留如何合并",
    S(
        "consolidator",
        "lib/kvbm-consolidator/src/lib.rs",
        4,
        19,
        "引擎 G1 与 KVBM G2/G3 事件的 canonical hash 与聚合",
    )
    + S(
        "bridge",
        "lib/kvbm-consolidator/src/ingress/kvbm_bridge.rs",
        4,
        17,
        "仅 hash 的 Create 不立刻发布缺元数据的 Store",
    )
    + S(
        "consolidator-out",
        "lib/kvbm-consolidator/src/egress/zmq_publisher.rs",
        153,
        201,
        "定时 drain、转换、序列化与三帧引擎兼容输出",
    )
    + "<p>Tracker 用 canonical SequenceHash 维护同一逻辑块的多源驻留引用：完整元数据可用时发首个有效 Store，最后一个来源消失才发 Remove。外部字符串 hash 需要映射到 canonical hash；对 Router wire 的 u64 投影也在显式边界处理。</p>"
    + "<p>KVBM broadcast bridge 只有 hash，没有 tokens/block_size 等完整 Store 信息，因此不能伪造空元数据 Store。引擎后续提供信息后才可发布。输出 drain 之后若转换/发送失败，所示循环 warn 并继续，不应称作 durable exactly-once；需要同时观察输入、tracker 和 egress 才能定位缺失。</p>",
)

add(
    "se-runtime-events",
    "16 / 通用 Event Plane：Envelope、ZMQ、NATS 与发现",
    S(
        "envelope",
        "lib/runtime/src/transports/event_plane/traits.rs",
        12,
        25,
        "通用 Envelope：发布身份、序列与 Unix 毫秒时间",
    )
    + S(
        "event-publish",
        "lib/runtime/src/transports/event_plane/mod.rs",
        662,
        683,
        "先编码 payload/envelope，再调用 transport",
    )
    + S(
        "zmq-frames",
        "lib/runtime/src/transports/event_plane/zmq_transport.rs",
        281,
        300,
        "通用 ZMQ 四帧：topic / publisher_id / sequence / Frame",
    )
    + S(
        "nats-publish",
        "lib/runtime/src/distributed.rs",
        739,
        750,
        "NATS Core publish；未配置 client 时可跳过并返回 Ok",
    )
    + "<p>Event Plane 支持 ZMQ direct publisher 与 broker 拓扑，订阅端通过 Discovery 找地址；NATS 使用 subject。Request Plane 选择 TCP 不限制 Event Plane 同时使用 ZMQ。通用四帧协议不能直接拿去解码引擎原生三帧消息。</p>"
    + note(
        "NATS 安装了 JetStream 不代表这条调用用了 durable stream/ack；这里是 Core client.publish。返回 Ok 也不证明消费者应用成功，尤其未配置 client 的跳过分支。published_at 是发布方墙钟，跨机计算消息延迟需要时钟同步，不能替代单机 Instant 测量。"
    ),
)

add(
    "se-engine-pipeline",
    "17 / 引擎事件进入 Router：两条序列空间与一次重编号",
    flow(
        [
            "Engine ZMQ source_cursor / rank",
            "listener decode、过滤、normalization",
            "accepted event 分配输入 event_id",
            "processor 检查输入 id 缺口",
            "batching/dedup、分层索引入队",
            "输出重新编号并通过 Event Plane 发布",
        ]
    )
    + S(
        "relay-stages",
        "lib/llm/src/kv_router/publisher/zmq_listener.rs",
        186,
        226,
        "received / accepted / filtered / conversion / suspicious 的实际记录位置",
    )
    + S(
        "input-gap",
        "lib/llm/src/kv_router/publisher/event_processor.rs",
        54,
        78,
        "processor 观察 event_id 缺口，按 gap 大小累加",
    )
    + S(
        "output-id",
        "lib/llm/src/kv_router/publisher/batching.rs",
        218,
        239,
        "输出事件重编号，与输入/native batch cursor 分开",
    )
    + "<p>至少区分三种计数：引擎批次 source_cursor；归一化后进入处理器的 event_id（processor 名为 raw_input）；通用 EventEnvelope.sequence。合并/过滤/去重会改变数量关系，不能逐一对齐或把它们的差都称为丢包。</p>"
    + note(
        "engines_dropped_events_total 的帮助文本说是 engine drops，但这个累加点直接证明的是处理器所见输入 event_id 的缺口，不足以单独定位网络、引擎或本地管线哪一段丢失；原生 ZMQ source_cursor 的 discontinuity 又是独立检测。"
    ),
)

add(
    "se-event-metrics",
    "18 / KV publisher 指标：定义、标签、注册与含义",
    S(
        "relay-metrics",
        "lib/llm/src/kv_router/metrics.rs",
        95,
        174,
        "MetricsHierarchy 注册与进程级 OnceLock memoization",
    )
    + table(
        ["指标后缀（完整名加 dynamo_component_）", "记录/labels", "解释边界"],
        [
            [
                "kv_publisher_zmq_events_total",
                "stage=received/accepted，event_type",
                "received 在成功 decode batch 后逐 raw event 记；accepted 在 preprocessing 后，转换仍可能失败",
            ],
            [
                "kv_publisher_zmq_filtered_events_total",
                "event_type, reason",
                "预处理过滤，可能是预期忽略而非故障",
            ],
            [
                "kv_publisher_zmq_conversion_issues_total",
                "event_type, reason",
                "转换失败/无结果，需查具体原因和 schema",
            ],
            [
                "kv_publisher_zmq_suspicious_events_total",
                "event_type, reason",
                "可疑但仍被转发，不能当作 dropped 数",
            ],
            [
                "kv_publisher_engines_dropped_events_total",
                "按输入 event_id gap 的数量增加",
                "不是 gap 事件发生次数，也不是端到端确认丢失的精确计数",
            ],
        ],
    )
    + "<p>Hierarchy 自动提供 dynamo_namespace/component/worker 等标签并注册到 DRT Registry；进程全局 OnceLock 只在首次初始化构建。若进程承载多个逻辑组件，需要核查指标归属，不能假定每次 from_component 都重新绑定。</p>"
    + "<pre><code># 补充集群/组件筛选；各 stage 分别看，不直接相加当事件数\nsum by (stage, event_type) (\n  rate(dynamo_component_kv_publisher_zmq_events_total[5m])\n)\n\nsum by (event_type, reason) (\n  rate(dynamo_component_kv_publisher_zmq_conversion_issues_total[5m])\n)</code></pre>",
)

add(
    "se-reliability",
    "19 / 可靠性：普通提示与 Cleared 屏障",
    "<p>普通 Store/Remove 使用本地索引队列 admission 语义，不等于 index 已应用完成或所有订阅者收到；普通 gap/局部失败通常 warning 后继续。Cleared 是排序屏障，需要完成受影响层的清理后再对外发布。state-agent/恢复路径有独立身份校验与 fencing，不把所有 advisory 路径都描述成同一强一致协议。</p>"
    + S(
        "local-admit",
        "lib/llm/src/kv_router/publisher/sinks.rs",
        153,
        165,
        "本地 admission 契约及 Cleared 更强语义",
    )
    + S(
        "heartbeat",
        "lib/llm/src/kv_router/publisher/zmq_listener.rs",
        101,
        120,
        "只有协商 liveness 才会因 heartbeat 过期清 residency",
    )
    + "<p>Publisher 的 advisory 规则见固定提交中 publisher/AGENTS.md 与 README。显式协商 heartbeat 的 legacy source 对 sequence discontinuity/expiry 有清理行为；未启用者不应被描述为每次 gap 自动清空。去重只在指定 publisher/sequence 与有限缓存窗口内，不等于持久 exactly-once。</p>"
    + note(
        "可观测重点是语义边界：原生 seq 断点、decode/convert 失败、队列准入失败、Clear 完成、publication 错误、消费者进度和 recovery 状态；不要把任何一层单独的吞吐 counter 当端到端正确性证明。"
    ),
)

add(
    "se-trace",
    "20 / 全链路追踪：已有信号、缺口与正确关联方式",
    table(
        ["层", "已有证据", "关联限制"],
        [
            ["逻辑池", "原子量、pool/external labels、匹配日志", "通常按池/实例聚合，不天然带每请求 Trace ID"],
            [
                "offload batch",
                "TimingTrace + Batch transfer complete 字段",
                "一批多 container；Instant 与缺失值不是 OTel 上下文",
            ],
            [
                "physical transfer",
                "transfer UUID/EventHandle、pending/status warning",
                "提交/完成需成对关联；UUID 不等于 Trace ID",
            ],
            [
                "EventEnvelope / KVBM batches",
                "publisher/sequence/topic/instance",
                "没有通用 W3C context 外层字段；可自定义 payload 但非自动契约",
            ],
            ["NIXL telemetry", "传输级性能事件与 exporter（依安装版本）", "不自动串联用户请求、缓存异步处理和全部订阅消费者"],
        ],
    )
    + '<p>与 <a href="pd-trace.html">PD Trace 结论</a>一致：Request Plane 的上下文契约不能自动覆盖 Storage &amp; Events。要补齐，需定义请求→共享块/批次/transfer 的多对多关联、异步完成边界、跨语言传播和 exporter；更适合用 Span Link 表达共享/批量因果，避免把一个共享块硬挂到单个请求。</p>'
    + note(
        "不能仅凭在几个日志里找到相同 request_id，或 NIXL exporter 有指标，就宣称存储和事件已全链路追踪。本章给出实施边界，不自动修改任何 wire schema 或热路径。"
    ),
)

add(
    "se-cluster",
    "21 / 对照 ACK 快照与接入验收",
    '<p>依据 <a href="ack-LB97i2CZNR/dynamo-test-deployment.md">用户部署记录</a>：SGLang/NIXL PD、TCP Request Plane、ZMQ Event Plane、原生 KV events tcp://*:5557，Worker metrics:9090，Pod 声明 nixl:19090，TP=4。</p>'
    + table(
        ["需要核实", "为什么", "完成证据"],
        [
            [
                "实际版本、connector 与 KVBM 是否启用",
                "PD=NIXL 不能推导分层 KVBM 已接入",
                "镜像 digest、包版本、启动参数、已加载 connector/库",
            ],
            [
                "5557 对应哪段 ZMQ 协议",
                "可能是引擎原生 ingress，不是通用 Event Plane egress",
                "publisher/listener 的两端地址、topic、帧格式、rank 和 source cursor",
            ],
            [
                "9090 实际指标与 Registry",
                "逻辑池 aggregator/旧独立 exporter/DRT 是不同入口",
                "抓取样例、TYPE、labels、Collector 注册位置和 targets",
            ],
            [
                "19090 实际 exporter/rank 覆盖",
                "端口声明不是 enable/监听/成功采集证据",
                "实际 NIXL 配置/版本、rank 的端口映射、Prometheus targets",
            ],
            [
                "驻留事件完整性",
                "metrics 200 不保证 Router index 跟随 cache",
                "Store→命中、Remove/Clear→失效、错误/重连后的状态变化",
            ],
            ["底层性能", "块命中不保证 TTFT 降低", "匹配、staging、queue、传输、注册与实际请求时延的同口径对照"],
        ],
    )
    + '<p>继续保留已有的 <a href="ack-LB97i2CZNR/request-plane-trace-checklist.html">Request Plane Trace 清单</a>；Storage/Event 接入是额外验证范围，不要求给当前 ZMQ 部署新增 NATS。</p>',
)

add(
    "se-evidence",
    "22 / 证据、排障入口与后续阅读",
    '<p><a href="storage-events-source-manifest.json">固定提交源码摘录清单</a>记录每段路径、行号、revision、文件 SHA-256。页面提供搜索、源码展开、阅读进度与打印；没有保留临时图索引诊断或浏览器截图。</p>'
    + table(
        ["现象", "先查的边界"],
        [
            [
                "hit ratio 高但 TTFT 仍高",
                "hit 的口径与层级 → G3/G4 staging → NIXL queue/xfer → 引擎实际 reuse",
            ],
            ["offload 数增长但目的层无块", "尝试计数时点 → transfer completion/错误 → 注册与淘汰"],
            [
                "路由认为有缓存但引擎 miss",
                "Store/Remove/Clear 时序 → source identity/rank → index 应用与恢复 → 选择时状态陈旧",
            ],
            [
                "收到 accepted 事件但输出不足",
                "conversion_none / batching / dedup → local admission → publish/subscribe",
            ],
            [
                "没有 NIXL metrics",
                "enable/exporter/版本 → scheduler 子进程环境 → 端口占用/监听 → targets",
            ],
        ],
    )
    + "<ul><li>本仓库配套设计：lib/kvbm-engine/docs/architecture.md、offload.md、session.md；kvbm-logical/README.md；kvbm-physical/README.md；KV publisher/README.md。</li>"
    '<li><a href="https://docs.nvidia.com/dynamo/dev/reference/components/kvbm-configuration">官方 KVBM 配置参考</a>：用于查配置入口，dev 内容不能替代镜像版本核查。</li>'
    '<li>相关章节：<a href="observability.html">可观测</a>、<a href="planner.html">Planner</a>、<a href="control-plane.html">控制面</a>、<a href="pd-trace.html">PD Trace</a>。</li></ul>'
    + note(
        "本章未逐行覆盖 S3 SDK/对象锁实现、NIXL/UCX 内部协议、CUDA kernel、各引擎 connector 全部内部代码与 KVCR 恢复全状态机。这些依赖或专项需要按安装版本进一步分析；不把局部结构缺字段推广成全仓库绝对不支持。"
    ),
)

result = page(
    "Storage & Events：架构、原理与可观测源码",
    "把逻辑块、物理搬运、异步事件和遥测出口串成可核对的阅读路径。",
    "".join(parts),
    nav,
)
result = result.replace(
    '<span class="pill">2026-10-28</span>', '<span class="pill">2026-10-29</span>'
)
result = result.replace("首个源码专题 v0.1", "Storage & Events 专题 v1.0")
(OUT / "storage-events.html").write_text(result)
(OUT / "storage-events-source-manifest.json").write_text(
    json.dumps(
        {
            "revision": PIN,
            "analysis_date": "2026-10-29",
            "scope": "KVBM logical/physical/engine and legacy metrics, NIXL telemetry, consolidator and event-plane observability",
            "runtime_verified": False,
            "excerpts": base["manifest"],
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(
    f"Built storage/events chapter: {len(base['manifest'])} excerpts, {len(nav)} sections."
)
