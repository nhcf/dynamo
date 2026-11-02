# Dynamo Request Plane Trace 采集方案（草案，待确认）

> 编制：2026-10-29
> 目标集群：`ack-LB97i2CZNR`，context `kubernetes-admin-c37065475b3804893815ca672b2ed3643`（`https://10.200.99.252:6443`）
> 对象：`nhss-job` / `DynamoGraphDeployment` / `dynamo-qwen38-27b-mixed-demo`
> 状态：**草案。以下命令一条都未在集群生效**，需逐条确认后再执行。

---

## 0. 证据基础

本方案的全部配置依据来自 2026-10-29 对上述 context 的实机只读核查（`kubectl get/exec`，无写入），
以及本仓库源码对 env 语义的交叉验证。不沿用旧文档中未经复核的推断。

---

## 1. 必须先纠正的三条旧结论

| 旧结论 | 实测结果 | 影响 |
|---|---|---|
| Frontend 未配置 readiness/liveness 探针（见 `dynamo-test-deployment.md` §4.5、工作清单 P1） | **已有**：readiness `/health`（initialDelay 10s）、liveness `/live`（initialDelay 15s） | 原 P1 待办可关闭；且滚动更新是安全的 |
| 候选片段使用 `spec.envs`（`request-plane-trace-env.fragment.yaml` 第 13 行） | 本 DGD 是 `nvidia.com/v1beta1`，结构为 `spec.components[].podTemplate.spec.containers[].env` | **旧片段不能直接 apply**，必须改写 |
| §11 结论"集群内接口不可达"，模型名 `IGD-agg` 与目标不符 | 换回正确 context 后实测 **200**：`/v1/models` 返回 `IGD-agg`；`/v1/chat/completions` 正常返回 | §11 的"未验证"结论作废，目标端点为 `igd-142-agg-0-router-ep-0`，健康 |

补充确认：`igd-142-agg-0-router-ep-0` 是 NodePort Service，ClusterIP `172.23.159.229:8000`，
Endpoint `172.22.9.67:8000`，属 Grove / nanhu 体系（`grove.io/podclique=igd-142-agg-0-router`），
**不是**本 DGD 的一部分。上次失败纯粹是 kubeconfig 指向了 `c642d589...` 那套集群。

---

## 2. 采集拓扑

```
客户端 / 上游网关
      │  traceparent (W3C)
      ▼
┌─────────────────────────────┐
│ Frontend (nhss-job)         │  OTEL_EXPORT_ENABLED=true
│  :8000  DYN_ROUTER_MODE=kv  │  → OTLP gRPC
└──────────┬──────────────────┘
           │ tcp request plane (DYN_TRACE 传播靠 traceparent 注入)
           ▼
┌──────────────────────────────────────────────┐
│ prefill / decode / aggregated (nhss-job)     │  ← 阶段 2 才接入
│  SGLang 引擎 span                             │  ← 阶段 3 才接入
└──────────┬───────────────────────────────────┘
           │
           ▼
┌────────────────────────────────────────────────────────────┐
│ nhi/otel-collector  0.120.1   172.23.171.88:4317           │
│  receivers : otlp(grpc 4317, http 4318)                    │
│  processors: memory_limiter → k8sattributes → batch        │
│  exporters : otlp/vector 、 kafka                          │
└──────────┬─────────────────────────────┬───────────────────┘
           │ otlp/vector                 │ kafka
           ▼                             ▼
┌───────────────────────────┐   ┌──────────────────────────┐
│ nhi-vector-agg-traces     │   │ brokers 10.107.159.7-10  │
│ 172.23.76.234:4317        │   │ topic llm-trace-dev      │
│ → ClickHouse              │   └──────────────────────────┘
│ isg.fed.clickhouse...:80  │
│ db llm_observability      │
│ table llm_traces_otlp     │
└───────────────────────────┘
```

**结论：采集平台已存在，本任务不需要新建 Collector / 存储，只需要让 Dynamo 往它发数据。**

实测连通性（在 `nhss-job` 的 Frontend Pod 内发起）：

| 目标 | 结果 |
|---|---|
| `otel-collector.nhi.svc.cluster.local:4317` | TCP OK |
| 同服务 `:4318` | **超时**（Service 只暴露 4317，尽管 Collector 内部开了 4318） |
| `nhi-vector-agg-traces.nhi.svc.cluster.local:4317` | TCP OK |
| ClickHouse `10.107.159.5:80` | TCP OK（8123/9000 不通） |
| 全集群 NetworkPolicy | 无 |

→ **只能走 gRPC 4317，不能用 4318。**

---

## 3. 关键契约（1.4.1 镜像实测）

镜像：`10.200.88.53/nhss/dynamo-sglang-runtime:1.4.1-amd64`
digest：`sha256:bf0ef3042110a7ad1b9a18ab7953a55d2cd78b44969f81116c20c7ef24e61385`（四组件一致）

| 项目 | 实测值 |
|---|---|
| ai-dynamo | 1.4.1 |
| sglang | 0.5.16 |
| opentelemetry-sdk / exporter-otlp | 1.44.0（含 gRPC exporter） |
| `_core.abi3.so` 中的 OTel 变量 | `OTEL_EXPORT_ENABLED`、`OTEL_SERVICE_NAME`、`OTEL_RESOURCE_ATTRIBUTES`、`OTEL_EXPORTER_OTLP_ENDPOINT`、`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`、`OTEL_EXPORTER_OTLP_PROTOCOL`、`OTEL_TRACES_SAMPLER`、`OTEL_TRACES_SAMPLER_ARG`、`OTEL_TRACES_SAMPLE_RATIO` |
| `DYN_LIFECYCLE_TRACE_*` | **不存在** → 1.4.1 没有 lifecycle trace，该 span 族不可得 |
| `dynamo.sglang --help` | 支持 `--enable-trace`、`--otlp-traces-endpoint <ip>:<port>`、`--sglang-trace-level {1..4}`、`--trace-modules`、`--fpm-trace` |

### 3.1 traces 与 logs 是绑在一起的（这是最容易踩的坑）

源码 `lib/runtime/src/logging.rs`：

- `OTEL_EXPORT_ENABLED` 由 `env_is_truthy` 判定（接受 `1/true/on/yes`），**未设置 = 不导出任何东西**
- 开启后日志原话是 `"OpenTelemetry OTLP export enabled (traces and logs)"` —— **同时建 trace 和 log 两个 exporter**
- 端点解析顺序：signal-specific（如 `..._TRACES_ENDPOINT`）→ generic → 默认 `http://localhost:4317`

而 `nhi/otel-collector` 的 pipeline **只有 traces 和 metrics，没有 logs**。
若把 logs 也指向 Collector，会被拒收并持续重试，污染两边日志。

→ 因此本方案**只显式设置 TRACES 端点**，logs 端点保持默认（`localhost:4317`，本进程内无人监听，
按上游设计静默丢弃），避免给 Collector 制造无效负载。**已定案。**

### 3.2 Collector 的 traces pipeline 处于空跑状态（关键风险）

通过 `kubectl -n nhi port-forward deploy/otel-collector 18888:8888` 读取 Collector 自身 telemetry，实测：

| 指标 | 实测 |
|---|---|
| `otelcol_receiver_accepted_metric_points{receiver="otlp",transport="grpc"}` | 64 |
| `otelcol_receiver_accepted_spans` | **不存在** |
| `otelcol_exporter_sent_spans{exporter="otlp/vector"}` | **不存在** |
| `otelcol_exporter_send_failed_metric_points{prom}` | 64（全失败，印证 §7 旁项） |

`otelcol_receiver_accepted_spans` 连指标都没注册，说明**该 Collector 自进程启动以来没有处理过任何一条 span**。

由此可推：

1. 生产上目前**没有任何业务往这条 traces 链路发数据** → 我们接入不会与他人互相干扰
2. 但 `Collector → Vector → ClickHouse` 这段**从未被真实数据验证过**，不能假设"平台已存在所以一定通"
3. Vector 日志在 2026-09-16/17 有 ClickHouse `Request timed out` 记录，sink 健康状况存疑

→ **这是阶段 0 探针存在的理由。在改任何 DGD 之前，必须先证明后段是通的。**

--- 

## 4. 配置设计

### 4.1 落点

`v1beta1` 路径：

```
spec.components[<i>].podTemplate.spec.containers[0].env
```

四个组件的 `components[].name` 分别是 `Frontend`、`decode`、`prefill`、`aggregated`（顺序 0/1/2/3），
容器名统一为 `main`。**不是** `spec.envs`。

### 4.2 公共 env（每个接入组件一套，`OTEL_SERVICE_NAME` 各自不同）

| 变量 | 值 | 说明 |
|---|---|---|
| `OTEL_EXPORT_ENABLED` | `"true"` | 总开关，不设则完全静默 |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `"grpc"` | 与 4317 对应；不设默认也是 grpc，写出来是为了自解释 |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | `"http://otel-collector.nhi.svc.cluster.local:4317"` | runtime 需要 URL 形式（带 scheme） |
| `OTEL_TRACES_SAMPLE_RATIO` | `"1.0"` | 联调全采，避免采样造成伪断链 |
| `OTEL_SERVICE_NAME` | 见下表 | 不改则全部叫 `dynamo`，无法区分组件 |

### 4.3 各组件 service name

| 组件 | `OTEL_SERVICE_NAME` |
|---|---|
| Frontend | `dynamo-qwen38-27b-frontend` |
| prefill | `dynamo-qwen38-27b-prefill` |
| decode | `dynamo-qwen38-27b-decode` |
| aggregated | `dynamo-qwen38-27b-aggregated` |

### 4.4 阶段 3 才需要的 worker args

在 prefill / decode / aggregated 现有完整 args 之后追加：

```
--enable-trace
--otlp-traces-endpoint otel-collector.nhi.svc.cluster.local:4317
```

注意：SGLang 这个参数是**裸 `host:port`**（无 `http://`），与 runtime 的 env 形式不同。
可选 `--sglang-trace-level 2`（默认已是 2，无需显式设置）。

---

## 5. 分阶段实施

### 阶段 0：端到端探针（前置，不改任何生产配置）

目的：在动 DGD 之前，把 `Frontend → Collector → Vector → ClickHouse` 的后三段先验证掉。

做法：在 Frontend Pod 内用镜像自带的 `opentelemetry-sdk 1.44.0` + gRPC exporter 发一条合成 span。
**不需要改 Deployment、不需要重启、不依赖 worker 是否可用。**

```python
# 在 nhss-job 的 Frontend Pod 内执行（不落盘，python3 -c 直接跑）
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

provider = TracerProvider(resource=Resource.create({
    "service.name": "dynamo-trace-probe",
    "probe.marker": "trace-chain-validation",
}))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
    endpoint="http://otel-collector.nhi.svc.cluster.local:4317", insecure=True)))
trace.set_tracer_provider(provider)

with trace.get_tracer("probe").start_as_current_span("probe-span") as s:
    s.set_attribute("probe.marker", "trace-chain-validation")
    s.set_attribute("probe.stage", "collector-vector-clickhouse")

provider.shutdown()
```

判读（三个计数器一眼定性）：

| 观测 | 结论 |
|---|---|
| `receiver_accepted_spans{receiver="otlp"}` 出现且 = 1 | 第 1 段（Pod→Collector）通 |
| `exporter_sent_spans{exporter="otlp/vector"}` = 1 | 第 2 段（Collector→Vector）通 |
| `exporter_send_failed_spans` > 0 或队列不下降 | 第 2 段断，卡在 Vector |
| 以上都正常但 ClickHouse 查不到 | 第 3 段（Vector→ClickHouse）断 |

**副作用**：向 `llm_observability.llm_traces_otlp` 写入 1 条 `service.name=dynamo-trace-probe` 的测试记录。
可识别、可事后按该 service.name 删除。**这一步需要你点头，因为它确实写了生产库。**

### 阶段 1：只开 Frontend（本次拟执行）

- **改动**：仅 Frontend component 的 `containers[0].env` 追加 §4.2 的 5 条
- **影响**：Frontend Deployment 滚动更新。`maxSurge=1 / maxUnavailable=0` + readiness `/health`
  → **先起新 Pod 并等 Ready，再删旧 Pod，服务不中断**
- **预计耗时**：< 60s（CPU-only，无模型加载）
- **能看到的 span**：Frontend handler span（`completions` 等，受 `DYN_LOG` 默认 info 放行）、
  派发 span `generate`（target `request_span`）、TCP 出口 `handle_payload`（client 侧）
- **看不到的 span（预期内）**：Worker 入口 `handle_payload`（server 侧）、worker 边界
  `worker.operation.prefill/decode`、SGLang 引擎 span。**trace 会断在 Frontend→Worker 的传输处，这是预期而非故障**
- **回退**：`kubectl patch --type=json` 用 `evidence/03-dgd-before-patch.json` 里的原始 4 条 env 覆盖回去

### 阶段 2：三个 Worker 的 runtime span（需另行确认）

- 追加同样的 5 条 env（仅 `OTEL_SERVICE_NAME` 不同）
- 影响：3 个 4 卡 GPU Worker 重建，Qwen3.8-27B 需重新加载，**服务中断数分钟**
- 收益：补齐 Worker 入口 span 与 `worker.operation.*` 边界，Frontend↔Worker 的 trace 才能连起来

### 阶段 3：SGLang 引擎 span（需另行确认）

- 追加 `--enable-trace` 与 `--otlp-traces-endpoint`
- 同样需要重建 GPU Worker
- 收益：引擎内部 prefill/decode 执行时间（`worker.operation.*` 只是 runtime 边界，不等于引擎耗时）

---

## 6. 验收方法

### 6.1 配置生效（阶段 1 完成判据）

```bash
# 新 Pod 内确认 env 已注入
kubectl -n nhss-job exec deploy/dynamo-qwen38-27b-mixed-demo-frontend -- \
  sh -c 'tr "\0" "\n" < /proc/1/environ | grep ^OTEL_'
# 启动日志应出现 "OpenTelemetry OTLP export enabled (traces and logs)"
```

### 6.2 发一条带 traceparent 的请求

```bash
TRACE_ID=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
PARENT_ID=$(python3 -c 'import secrets;print(secrets.token_hex(8))')
kubectl -n nhss-job exec deploy/dynamo-qwen38-27b-mixed-demo-frontend -- \
  curl -sS -N --max-time 120 http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H "traceparent: 00-$TRACE_ID-$PARENT_ID-01" \
  -H "x-request-id: trace-check-$TRACE_ID" \
  -d '{"model":"Qwen3.8-27B","messages":[{"role":"user","content":"请用一句话介绍分布式推理。"}],"max_tokens":32,"stream":true}'
```

### 6.3 确认 span 真的被 Collector 收到

**主手段：Collector 自身 telemetry 计数器（只读，已实测可用）**

```bash
kubectl -n nhi port-forward deploy/otel-collector 18888:8888 &
curl -s http://127.0.0.1:18888/metrics | grep -E 'otelcol_(receiver_accepted_spans|exporter_sent_spans|exporter_send_failed_spans)'
```

说明：Collector 镜像是 distroless（`sh` 都不存在，`kubectl exec` 进不去），但 telemetry 默认绑
`localhost:8888`，`kubectl port-forward` 走 Pod 的 netns 可以直接摸到，**无需改任何配置**。

判读：

| 指标 | 含义 | 期望 |
|---|---|---|
| `otelcol_receiver_accepted_spans{receiver="otlp"}` | Collector 从 Pod 收到了多少 span | 与发出的请求数同量级 |
| `otelcol_receiver_refused_spans` | 被拒收（限流/memory_limiter） | 0 |
| `otelcol_exporter_sent_spans{exporter="otlp/vector"}` | 成功转给 Vector | 与 accepted 相等 |
| `otelcol_exporter_send_failed_spans` | 转发失败 | 0 |
| `otelcol_exporter_queue_size{data_type="traces"}` | 积压 | 回落到 0 |

**这一组数字能直接定位断点在第一段（Pod→Collector）还是第二段（Collector→Vector）。**

**辅手段**

| 方式 | 用途 | 限制 |
|---|---|---|
| ClickHouse 查询 | 终态验收，按 trace_id 回溯 | 表字段名待确认、需凭据；只能判断"到没到"，断点定位靠上面的计数器 |
| 临时抓包 Collector | 看 span 内容/属性/父子关系是否正确 | 需创建一次性 Deployment |

**不建议**：打开 Collector 日志。当前 `level=info` 且无 debug exporter，正常 span **不会打印**，
日志只能反映错误；且镜像是 distroless，连 `exec` 都进不去。若真要打印 span，必须给公共 Collector
加 `debug` exporter 或调 `service.telemetry.logs.level`，那是公共组件改动，会放大日志量，不纳入本次。

---

## 7. 风险

| 风险 | 影响 | 处置 |
|---|---|---|
| KV 路由跨部署选点（P0，未解决） | `/health` 显示 Frontend 能看到 `igd-142-agg`、`igd-142-agg-mn`、`igd-142-mixed`、`igd-4090-allinone-repl`、`igd-4090-pd-repl` 共 5 个外部 namespace，请求可能落到非本 DGD 的 Worker | 阶段 1 只验证 Frontend 侧 span，不依赖落点；阶段 2 前必须先确定如何把请求钉到本 DGD |
| 本 DGD 内部 agg / pd 双路径 | 一次请求可能落 `aggregated` 也可能落 `prefill+decode` | 验收时记录实际落点，两条路径分别取证 |
| 1.0 全采样 | 短期数据量放大，Collector 单副本 limits 200m CPU / 512Mi | 联调结束后降到 0.1；Collector 已有 memory_limiter 512Mi 保护 |
| Collector 无 logs pipeline | 若误设 `OTEL_EXPORTER_OTLP_ENDPOINT`（generic），logs 会被拒收并重试 | 方案明确只设 TRACES 端点（§3.1） |
| 旁项：Collector metrics 管线已坏 | `prometheusremotewrite/prom` 自 2026-09-29 持续 404 丢数据 | 与 trace 无关，单独排期 |
| 旁项：Vector sink 曾超时 | 2026-09-16/17 有 ClickHouse `Request timed out` 警告 | 阶段 1 验收时顺带确认 sink 当前是否正常 |

---

## 8. 决策记录

| 编号 | 问题 | 决定 |
|---|---|---|
| Q1 | 端点走 Collector 还是直连 Vector | **走 `otel-collector.nhi:4317`**（保留 k8sattributes 补的 pod/namespace/node 元数据） |
| Q2 | 是否连 logs 一起采 | **不采**。只设 TRACES 端点，不改公共 Collector |
| Q3 | 阶段 1 后是否立即做阶段 2 | **暂缓**。现有 Worker 不可动，本次只交付 Frontend 段 |
| Q4 | KV 路由跨部署（P0） | **本次不处理**，单独立项 |

### 仍待确认

**Q5-a｜阶段 0 探针是否放行？**
探针会向生产 ClickHouse 写入 1 条 `service.name=dynamo-trace-probe` 的记录。
不放行的话，就只能等阶段 1 上线后从 `receiver_accepted_spans` 反推，断点定位会慢很多。

**Q5-b｜ClickHouse 读取凭据与表结构由谁提供？**
按 trace_id 回溯需要 `llm_observability.llm_traces_otlp` 的字段定义和读权限。
拿不到的话，本次验收止步于"Collector 已接收并成功转发 Vector"，即链路前两段。

### 关于"Worker 不可用"对链路的影响（Q3 展开）

分两种情况，都不影响阶段 1 的交付：

| Worker 状态 | 请求能否返回 | Frontend span 是否生成 | trace 完整性 |
|---|---|---|---|
| worker 正常运行、只是不能重启 | 能 | 能 | 有 Frontend 段，缺 Worker 段（预期） |
| worker 已不可服务 | 否（报错/超时） | **仍会生成**（含错误状态） | 只有 Frontend 段，但能定位失败发生在哪一跳 |

关键点：**Frontend 的导出不经过 Worker**。它自己直连 Collector，所以 Worker 状态如何都不影响
"Frontend span 能否到达采集后端"。真正依赖 Worker 的只有"trace 能不能连成完整一棵树"。

---

## 9. 本次已产出的证据文件

| 文件 | 内容 |
|---|---|
| `evidence/01-dgd-snapshot.yaml` | 改动前 DGD 完整快照 |
| `evidence/02-workloads-wide.txt` | deploy/pod/svc 全景 |
| `evidence/03-dgd-before-patch.json` | 回退用的原始对象（含原始 env 数组） |
| `evidence/04-frontend-otel-patch.json` | 阶段 1 的 JSON Patch，**尚未应用** |

> 附：撰写本文期间曾误发一条 `kubectl patch`，命令在等待阶段被中断。事后核验
> `metadata.generation` 仍为 1、Frontend env 仍为原始 4 条、Pod 仍为 5d2h 旧实例，
> 集群未被改动，无需回退。
