# maas-dev / higress-gateway 指标采集方案

> 状态：**方案待确认，尚未对任何集群做写操作**
> 编写时间：2026-10-30
> 目标集群：maas-dev（`kube-config/maas.kubeconfig`）
> 采集组件：`maas-dev/otel-collector`（继承 litellm 之后的第二条 metrics 线）
> 出口：`prometheusremotewrite` → ONE Prometheus 接入点 `63e4f890-a942-4372-a2c8-7fc830ae414f`

---

## 1. 结论摘要

higress-gateway 的指标可以直接采集，**不需要改网关本身**：

| 项 | 结论 |
|---|---|
| 采集端点 | `pod:15020/stats/prometheus`（istio-agent 合并端点），实测 HTTP 200、10068 条有效序列 |
| 发现方式 | `kubernetes_sd_configs: role: pod` + namespace `higress-system` + label `app=higress-gateway` |
| RBAC | 现有 SA `maas-dev/otel-collector` 已有全集群 `list pods`，**无需新增授权** |
| 改动面 | 新增 1 个 receiver、1 个 processor、1 条 pipeline，**litellm 那条 pipeline 一行不动** |
| 数据量 | 全量 6733 点/15s（3878 万点/天）；推荐裁剪档 572 点/15s（330 万点/天） |
| 重启影响 | 改 ConfigMap 需重启 collector，期间 trace 与 litellm 指标有 10~30s 空窗 |

**三个必须先知道的坑**（详见第 3 节）：

1. 15020 上**没有 `istio_requests_total`**，因为 meshConfig 未启用 telemetry v2。只能用 envoy 原生指标。
2. pod 有 2 个容器（`higress-gateway` + `promtail`），pod 类型 SD 会为每个容器各生成一个 target，**不过滤容器名会导致数据翻倍**。
3. `envoy_cluster_*` 带 `cluster_name` 维度，当前 107 个集群，且**随 ack 集群上的推理服务增减而变化**，是唯一的基数增长源。

---

## 2. 现状勘察（只读证据）

### 2.1 工作负载

```
$ kubectl -n higress-system get deploy,svc
deployment.apps/higress-gateway          1/1   higress-gateway,promtail
service/higress-gateway   NodePort   172.21.6.251   80:62703/TCP,443:30444/TCP
```

- `higress-gateway-78ddf4cc54-t29bx`，2/2 Running，8d，pod IP `172.26.0.8`，节点 `10.200.44.107`
- 容器端口：`higress-gateway` 声明了 `istio-prom=15020`、`http-envoy-prom=15090`
- `promtail` 侧车声明 `http-metrics=3101`（无关，但要过滤掉）
- Service **只暴露 80/443**，15020 不在 Service 上 → 无法用 service 名做静态 target，必须用 pod SD

### 2.2 端点实测

| 端口 | 路径 | HTTP | 有效序列 |
|---|---|---|---|
| 15020 | `/stats/prometheus` | **200** | **10068** |
| 15020 | `/metrics` | 200 | 10068（与上者同内容） |
| 15020 | `/stats` | 404 | — |
| 15090 | 任意路径 | 超时 | — |

15020 是 istio-agent 的合并端点（envoy stats + agent 自身指标），是 Istio/Higress 官方推荐的抓取目标；15090 是 envoy admin，只监听回环，port-forward 不可达，**不要用**。

### 2.3 指标构成

```
envoy_cluster     8640 行   (2020 行是 envoy_cluster_update_duration_bucket —— xDS 更新耗时)
envoy_http         596 行
envoy_listener     310 行
envoy_extension    239 行
istio_agent         77 行   (全是 agent 自身的 go/process 指标)
envoy_wasm          76 行
envoy_server        50 行
route_upstream      22 行   ← Higress AI 网关的 token 计量，最有业务价值
envoy_overload      22 行
其余                36 行
```

### 2.4 配置层现状

```yaml
# maas-dev/otel-collector-config 现有相关部分
receivers:
  prometheus:
    config:
      scrape_configs:
        - job_name: 'litellm'
          scrape_interval: 15s
          static_configs:
            - targets: ['litellm.litellm-system.svc.cluster.local:4000']
          metrics_path: /metrics/
          bearer_token: '<redacted>'      # 实测无效，建议删除，见 §7
processors:
  resource:
    attributes:
      - {key: service.name, value: litellm, action: upsert}
      - {key: cluster,      value: maas-dev, action: upsert}
exporters:
  prometheusremotewrite:
    endpoint: http://10.107.205.20:58897/integration/prometheus/metric/63e4f890-...
    remote_write_queue: {num_consumers: 5, queue_size: 1000}
service:
  pipelines:
    metrics:
      receivers: [prometheus]
      processors: [memory_limiter, resource, batch]
      exporters: [prometheusremotewrite]
```

**关键约束**：`resource` processor 是 pipeline 级的，会把 `service.name` 无条件 upsert 成 `litellm`。如果把 higress 塞进同一条 `metrics` pipeline，**higress 的指标会被误标成 litellm**。因此必须新增独立的 `resource/higress` + `metrics/higress`。

### 2.5 RBAC

```
$ kubectl auth can-i list pods --as=system:serviceaccount:maas-dev:otel-collector -A
yes
```

`list pods` = yes（`endpoints`/`services` = no，但本次方案不需要）。pod 类型服务发现可直接使用。

---

## 3. 三个必须先知道的坑

### 3.1 没有 `istio_requests_total`

```json
// higress-config CM 中的 meshConfig 片段
"enablePrometheusMerge": true,
"proxyStatsMatcher": {"inclusionRegexps": [".*"]},
// 没有 telemetry / defaultConfig.telemetry 段
```

实测 `grep -c '^istio_requests_total'` = **0**。15020 上只有 `istio_agent_*`（77 行，全是 agent 自身资源指标）和 envoy 原生指标。

**影响**：拿不到按 `response_code` / `destination_workload` / `source_workload` / `reporter` 维度的 Istio 语义指标，Higress 官方 Grafana 面板的基础指标缺失。请求量、延迟、成功率只能从 envoy 原生指标换算（维度是 `cluster_name` / `envoy_http_conn_manager_prefix`，没有 workload 语义）。

**可选补法**（本文档不包含，需单独确认）：在 meshConfig 里启用 telemetry v2，代价是要重启 higress-gateway pod（当前**单副本**），网关有秒级流量中断，且序列数会进一步上涨。

### 3.2 pod SD 会为每个容器生成一个 target

`role: pod` 的服务发现对 pod 内**每个容器**各产生一条 target，`__meta_kubernetes_pod_container_name` 区分。higress-gateway pod 有 `higress-gateway` 和 `promtail` 两个容器，如果不加容器过滤：

- relabel 后两条 target 的 `__address__` 都被改写成同一个 `podIP:15020`
- collector 会把同一份数据抓两遍 → **上报量直接翻倍**

因此必须有：

```yaml
- source_labels: [__meta_kubernetes_pod_container_name]
  regex: higress-gateway
  action: keep
```

### 3.3 `cluster_name` 是唯一的基数增长源

107 个 upstream cluster，其中只有少数与我们相关：

```
outbound|4000||litellm.litellm-system.svc.cluster.local     ← 我们的链路
outbound|4317||otel-collector.static
outbound|80||i-nhi-maas.dns
outbound|80||llm-ack-6I6LzJLytb-jb-aiinfer-160336910499210624.internal.static   ← ack 推理服务
... (约 100 个 llm-ack-* / 其他 service)
BlackHoleCluster, agent
```

`envoy_cluster_membership_total` + `_healthy` 各 107 点 = **214 点，占推荐档 572 点的 37%**。基数随 ack 集群上推理服务的创建/删除而增长，属于可控但有增长性的基数。

---

## 4. 数据量三档对比

每轮 = 一次 15s 抓取。折算规则：classic histogram 的 `_bucket{le=...}` N 条 + `_sum` + `_count` 合并为 1 个 OTLP Histogram 数据点。

| 档位 | 保留内容 | 点/轮 | 点/天 | 相对 litellm（155 点/轮） |
|---|---|---|---|---|
| **A 全量** | 不过滤 | 6733 | 3878 万 | 43 倍 |
| **B 去控制面噪音** | 丢 `envoy_cluster_{update_*,warming_state,version,lb_*,max_host_weight,total_match,membership_change,assignment_*}`、`envoy_http_compressor_*`、`envoy_extension_*`、`envoy_wasm_*`、`envoy_overload_*`、`envoy_runtime_*`、`envoy_filesystem_*`、`envoy_dns_*` | 3402 | 1959 万 | 22 倍 |
| **C 业务核心（推荐）** | 见 §4.1 白名单 | **572** | **330 万** | 3.7 倍 |

A 档里 5000+ 点都是 xDS 控制面统计（`warming_state`、`version`、`update_success` 等，107 集群 × 十几个指标）和 gzip compressor 计数，排障价值极低，**不建议**。

### 4.1 推荐档（C）的保留白名单

正则（`metric_relabel_configs` 使用）：

```
^(envoy_http_downstream_rq.*
 |envoy_http_downstream_cx_(active|total)
 |envoy_cluster_upstream_rq.*
 |envoy_cluster_upstream_cx_(active|total|connect_fail)
 |envoy_cluster_membership_(total|healthy)
 |envoy_listener_downstream_cx_(active|total)
 |envoy_server_(memory_allocated|memory_heap_size|live|uptime|version)
 |route_upstream_.*)$
```

实测量化：

| 指标 | 点/轮 | 用途 |
|---|---|---|
| `envoy_cluster_membership_total` / `_healthy` | 107 + 107 | 上游健康度（litellm 副本是否在集群内） |
| `envoy_http_downstream_rq` | 20 | 网关入口请求数，含 `_total` / `_active` / `_completed` / 各 `_httpN_total` |
| `envoy_http_downstream_rq_time` | 4 | **网关入口延迟直方图**（154 行折叠为 4 点） |
| `envoy_http_downstream_cx_(active|total)` | 8 | 并发连接 |
| `envoy_cluster_upstream_rq.*` | 约 150 | 上游请求数/延迟/超时/重试/断连，已含 `_200`/`_400`/`_401`/`_503` 等状态码 |
| `envoy_cluster_upstream_cx_*` | 18 | 上游连接与连接失败 |
| `envoy_listener_downstream_cx_*` | 6 | listener 层连接 |
| `envoy_server_*` | 5 | 网关进程内存、存活、版本、uptime |
| **`route_upstream_model_consumer_metric_*`** | **约 22** | **AI 网关 token 计量**（见 §4.2） |

可选的进一步收缩：去掉 `envoy_cluster_membership_*` → 358 点/轮（206 万点/天），但会失去"litellm 副本掉线"这个关键信号，**不推荐**。

### 4.2 AI 网关 token 指标（本方案的核心增值）

```
route_upstream_model_consumer_metric_input_token{ai_route="litellm-service-gpt-4o-mini",
  ai_cluster="outbound|4000||litellm.litellm-system.svc.cluster.local",
  ai_model="dynamo-trace-demo-Qwen3.8-27B", ai_consumer="none"} 573
route_upstream_model_consumer_metric_output_token{...}
route_upstream_model_consumer_metric_total_token{...}
route_upstream_model_consumer_metric_llm_duration{...}
route_upstream_model_consumer_metric_llm_first_token_duration{...}
route_upstream_model_consumer_metric_llm_service_duration{...}
route_upstream_model_consumer_metric_llm_stream_duration_count{...}
```

这条 `input_token=573` 是真实流量打出来的，`ai_model` 正是我们的 `dynamo-trace-demo-Qwen3.8-27B`。

**价值**：网关侧（Higress）和代理侧（litellm）现在各有一份 token 计量，两者可以交叉校验，并与 trace 链路的 span 对齐。这是"Higress → litellm → dynamo → sglang"全链路在**指标维度**上唯一能形成闭环的环节，因此必须在白名单里。

### 4.3 采集后能回答的问题

| 问题 | 对应指标 |
|---|---|
| 网关入口 QPS、并发、各 HTTP 版本占比 | `envoy_http_downstream_rq_total` / `_active` / `_http2_total` |
| 网关入口延迟分布（P50/P90/P99） | `envoy_http_downstream_rq_time_bucket` |
| 上游（litellm）成功率、5xx/4xx、超时、重试 | `envoy_cluster_upstream_rq_{200,400,401,503,timeout,retry*}` |
| litellm 副本是否健康、是否掉线 | `envoy_cluster_membership_{total,healthy}{cluster_name="outbound\|4000\|\|litellm..."}` |
| 某次流量实际消耗多少 prompt/输出 token | `route_upstream_model_consumer_metric_{input,output,total}_token` |
| LLM 首 token 延迟、整体耗时、流式耗时 | `..._llm_first_token_duration` / `_llm_duration` / `_llm_stream_duration_count` |
| 网关自身内存、存活、版本 | `envoy_server_memory_allocated` / `_live` / `_version` / `_uptime` |

**拿不到的**：按 `response_code` × `destination_workload` 的 Istio 语义拆分、按服务拓扑的 RED 指标——这些需要 telemetry v2（见 §3.1 与 §9 决策项 2）。

### 4.4 与 ack-LB 侧的分工

| 集群 | collector | 采集对象 | ONE 接入 ID |
|---|---|---|---|
| maas-dev | `maas-dev/otel-collector` | litellm（已有）+ **higress-gateway（本方案）** | `63e4f890-a942-4372-a2c8-7fc830ae414f` |
| ack-LB97i2CZNR | `nhi/otel-collector` | dynamo frontend `:8000` + dynamo worker `:9090` | `76146db7-faa5-4873-9de5-c013c7dd5b9f` |

两集群各自本地采集、分别上报，ONE 侧按 `cluster` + `service_name` 标签聚合，不存跨集群抓取。sglang 的 metrics 目前两边都没进 ONE（maas-dev 的 vmagent 那条线走 VM，与本链路无关），是下一段的缺口。

---

## 5. 具体改动（最小侵入）

### 5.1 设计原则

1. **新增命名实例，不改现有 litellm 链路**：`prometheus` / `resource` / `metrics` 三个原有实例全部保持原样，新增带 `/higress` 后缀的实例。这样即使 higress 侧出问题，回滚只需删掉新增的三块。
2. **共用 exporter**：`prometheusremotewrite` 保持单实例，两条 pipeline 共享其队列（`5 consumers / queue_size 1000`），不额外占用连接。
3. **共用 `memory_limiter`**：`memory_limiter` 是全局资源保护，多 pipeline 复用同一实例是标准做法。

### 5.2 新增的 receiver

```yaml
receivers:
  # === 新增（2026-10-30）：higress-gateway 指标 ===
  prometheus/higress:
    config:
      global:
        scrape_interval: 15s
        scrape_timeout: 10s
      scrape_configs:
        - job_name: higress-gateway
          kubernetes_sd_configs:
            - role: pod
              namespaces:
                names: [higress-system]
          relabel_configs:
            # 只保留 higress-gateway pod
            - source_labels: [__meta_kubernetes_pod_label_app]
              regex: higress-gateway
              action: keep
            # 只保留主容器，排除 promtail 侧车（否则数据翻倍，见 §3.2）
            - source_labels: [__meta_kubernetes_pod_container_name]
              regex: higress-gateway
              action: keep
            - source_labels: [__meta_kubernetes_pod_phase]
              regex: Running
              action: keep
            # 抓 istio-agent 合并端点 15020，而非 envoy admin 15090（见 §2.2）
            - source_labels: [__meta_kubernetes_pod_ip]
              target_label: __address__
              replacement: $1:15020
            - target_label: __metrics_path__
              replacement: /stats/prometheus
            # 保留可用的 k8s 维度
            - source_labels: [__meta_kubernetes_namespace]
              target_label: kubernetes_namespace
            - source_labels: [__meta_kubernetes_pod_name]
              target_label: kubernetes_pod_name
            - source_labels: [__meta_kubernetes_pod_node_name]
              target_label: kubernetes_node
          # 采集侧裁剪，未入选序列不进入 pipeline（见 §4.1）
          metric_relabel_configs:
            - source_labels: [__name__]
              regex: '^(envoy_http_downstream_rq.*|envoy_http_downstream_cx_(active|total)|envoy_cluster_upstream_rq.*|envoy_cluster_upstream_cx_(active|total|connect_fail)|envoy_cluster_membership_(total|healthy)|envoy_listener_downstream_cx_(active|total)|envoy_server_(memory_allocated|memory_heap_size|live|uptime|version)|route_upstream_.*)$'
              action: keep
```

### 5.3 新增的 processor

```yaml
processors:
  # === 新增（2026-10-30）===
  resource/higress:
    attributes:
      - key: service.name
        value: higress-gateway
        action: upsert
      - key: cluster
        value: maas-dev
        action: upsert
      - key: component
        value: higress
        action: upsert
```

因为 exporter 上 `resource_to_telemetry_conversion.enabled = true`，这三个 resource attribute 会落成指标 label：`service_name="higress-gateway"`、`cluster="maas-dev"`、`component="higress"`，与 litellm 的 `service_name="litellm"` 天然区分，在 ONE 侧可直接按 `service_name` 分流。

### 5.4 新增的 pipeline

```yaml
service:
  pipelines:
    # 保持原样
    metrics:
      receivers: [prometheus]
      processors: [memory_limiter, resource, batch]
      exporters: [prometheusremotewrite]
    # === 新增（2026-10-30）===
    metrics/higress:
      receivers: [prometheus/higress]
      processors: [memory_limiter, resource/higress, batch]
      exporters: [prometheusremotewrite]
```

### 5.5 改动清单

| 位置 | 动作 |
|---|---|
| `receivers` | 新增 `prometheus/higress`（§5.2） |
| `processors` | 新增 `resource/higress`（§5.3） |
| `service.pipelines` | 新增 `metrics/higress`（§5.4） |
| `exporters` / `traces` pipeline / litellm 相关 | **不动** |

---

## 6. 验证方法

### 6.1 apply 之前

```bash
export KUBECONFIG=/Users/lucy/Documents/Code/gitlab/nhi-data-validator/kube-config/maas.kubeconfig

# 1. 备份现有 CM
kubectl -n maas-dev get cm otel-collector-config -o yaml > /tmp/maas-otel-backup-$(date +%s).yaml

# 2. 语法校验（在 collector 容器内，用同版本二进制校验）
kubectl -n maas-dev exec deploy/otel-collector -- /otelcol-contrib validate --config=/conf/config.yaml
```

⚠️ `prometheus receiver` 是否接受 `metric_relabel_configs` 字段需靠第 2 步确认。若报 `unknown field`，改用 §8 的 filter processor 备选方案。

### 6.2 apply 之后（运行层）

```bash
kubectl -n maas-dev rollout status deploy/otel-collector --timeout=120s
kubectl -n maas-dev port-forward deploy/otel-collector 18888:8888 &
```

对照检查（8888 即使未声明 containerPort 也可 port-forward，litellm 那次已实测）：

| 指标 | 期望 |
|---|---|
| `otelcol_receiver_accepted_metric_points{receiver="prometheus/higress"}` | 从 0 开始，每 15s 稳定增长 ≈572 |
| `otelcol_receiver_refused_metric_points{receiver="prometheus/higress"}` | 恒为 0 |
| `otelcol_receiver_accepted_metric_points{receiver="prometheus"}` | litellm 原值，每 15s 仍 +155（**确认未被影响**） |
| `otelcol_exporter_sent_metric_points{exporter="prometheusremotewrite"}` | 增量 ≈ 155 + 572 = 727（histogram 折叠导致的采样抖动 ±1 属正常） |
| `otelcol_exporter_send_failed_metric_points` | 不增长 |
| 日志 | 无 `scrape failed` / 无 `error` |

连续采样 3 轮（每轮 15s）确认增量稳定，再判定通过。

### 6.3 数据层验证

发一次真实请求走网关，确认 AI token 指标增长。AI 指标是网关自己累加的 counter，直接查网关侧最准（collector 侧有 15s 抓取延迟，需等两轮）：

```bash
# 1. 直连网关 pod 的 15020，取请求前的 AI token 计数
kubectl -n higress-system port-forward deploy/higress-gateway 15020:15020 &
sleep 3
BEFORE=$(curl -s http://127.0.0.1:15020/stats/prometheus \
  | grep '^route_upstream_model_consumer_metric_input_token' \
  | awk -F'[{}]' '{print $0}' | sed -E 's/^[^{]*\{[^}]*\} //' | paste -sd+ - | bc)

# 2. 发一次请求（完整可用 curl 见本地 `.vscode/curl-cheatsheet.md`，该目录被 gitignore，未入库）
#    若该 NodePort 不通，改用 `.vscode/curl-cheatsheet.md` 里已验证的入口
curl -s -X POST http://10.200.44.107:62703/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"dynamo-trace-demo-Qwen3.8-27B","messages":[{"role":"user","content":"hi"}],"max_tokens":8}'

# 3. 取请求后的计数（等待 >15s 让网关 flush metrics）
sleep 20
AFTER=$(curl -s http://127.0.0.1:15020/stats/prometheus \
  | grep '^route_upstream_model_consumer_metric_input_token' \
  | awk -F'[{}]' '{print $0}' | sed -E 's/^[^{]*\{[^}]*\} //' | paste -sd+ - | bc)

echo "本次请求 input_token = $((AFTER - BEFORE))"
```

期望 `AFTER - BEFORE` 等于本次请求的 prompt token 数（非 0）。同时在 collector 侧（§6.2 的 18888 端口）确认 `otelcol_receiver_accepted_metric_points{receiver="prometheus/higress"}` 同步增长。

---

## 7. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| ConfigMap 改动需重启 pod | trace 与 litellm 指标 10~30s 空窗 | 测试环境可接受；重启后靠 §6.2 确认两条 pipeline 都恢复 |
| `metric_relabel_configs` 在 0.120.1 不被接受 | pod CrashLoopBackOff，**trace 也会一起挂** | 必须先执行 §6.1 第 2 步 validate；失败则改用 §8 备选 |
| pod SD 容器过滤写错 | 数据翻倍 | apply 后核对 accepted 增量是否 ≈572（翻倍则 ≈1144） |
| collector → `172.26.0.8:15020` 网络不通 | accepted 恒为 0、日志 `scrape failed` | litellm 走的是 svc DNS（172.21 网段），higress 是 pod IP（172.26 网段），首次需实测；不通则退回用 Service 或 hostNetwork 方案 |
| `cluster_name` 基数随 ack 推理服务增长 | ONE 侧序列数缓慢上涨 | 当前 107 可控；如后续超 300 再考虑按 `cluster_name` 做 relabel 过滤 |
| 日志噪音 | `debug` exporter 挂在 traces pipeline（verbosity: detailed） | 与本方案无关，但建议顺手改 `basic` |

**回滚**：

```bash
kubectl -n maas-dev replace -f /tmp/maas-otel-backup-<ts>.yaml
# 或直接删掉 §5.2/5.3/5.4 三块后 apply
kubectl -n maas-dev rollout restart deploy/otel-collector
```

---

## 8. 备选方案：filter processor

若 `metric_relabel_configs` 不被 0.120.1 接受，把 §5.2 里的 `metric_relabel_configs` 整段删掉，改为新增一个 processor 并挂到 pipeline 上：

```yaml
processors:
  filter/higress:
    error_mode: ignore
    metrics:
      include:
        match_type: regexp
        metric_names:
          - '^envoy_http_downstream_rq.*$'
          - '^envoy_http_downstream_cx_(active|total)$'
          - '^envoy_cluster_upstream_rq.*$'
          - '^envoy_cluster_upstream_cx_(active|total|connect_fail)$'
          - '^envoy_cluster_membership_(total|healthy)$'
          - '^envoy_listener_downstream_cx_(active|total)$'
          - '^envoy_server_(memory_allocated|memory_heap_size|live|uptime|version)$'
          - '^route_upstream_.*$'

service:
  pipelines:
    metrics/higress:
      receivers: [prometheus/higress]
      processors: [memory_limiter, filter/higress, resource/higress, batch]
      exporters: [prometheusremotewrite]
```

差别：`filter` 在采集**之后**过滤，因此全量 10068 条序列仍会进入 collector 内存再被丢弃，CPU/内存开销高于 `metric_relabel_configs`。数据一致，只是效率低。

---

## 9. 待决策项

| # | 决策点 | 选项 | 建议 |
|---|---|---|---|
| 1 | 采集档位 | A 全量 3878 万/天 · B 去噪 1959 万/天 · **C 核心 330 万/天** | **C** |
| 2 | 是否补开 telemetry v2 拿 `istio_requests_total` | 不动 / 改 meshConfig + 重启网关（单副本，秒级中断） | **先不动**，用 envoy 原生指标 |
| 3 | 是否同时抓 higress-controller（svc 端口含 8888/15014） | 抓 / 不抓 | 本轮不抓，未验证其 metrics 内容；作为后续可选 |
| 4 | 是否顺手删掉 litellm 的 `bearer_token`（实测无效且是明文 master key） | 删 / 留 | **删** |
| 5 | `debug` exporter 是否从 traces pipeline 降级为 basic 或移除 | 降级 / 移除 / 不动 | 降级为 `basic` |

---

## 10. 附录：原始指标全量构成（前 20）

| 序列数 | 指标名 |
|---|---|
| 2020 | `envoy_cluster_update_duration_bucket` |
| 140 | `envoy_cluster_upstream_rq_time_bucket` |
| 120 | `envoy_cluster_upstream_rq_per_cx_bucket` |
| 120 | `envoy_cluster_upstream_cx_length_ms_bucket` |
| 120 | `envoy_cluster_upstream_cx_connect_ms_bucket` |
| 107 | `envoy_cluster_warming_state` / `version` / `update_success` / `update_no_rebuild` / `update_failure` / `update_empty` / `update_attempt` / `total_match` / `membership_total` / `membership_healthy` / `membership_excluded` / `membership_degraded` / `membership_change` / `max_host_weight` / `lb_zone_routing_sampled`（各 107） |

> `update_duration_bucket` 一项就占 2020/10068 = 20%，是 xDS 控制面更新耗时，与业务无关，A 档的主要冗余来源。
