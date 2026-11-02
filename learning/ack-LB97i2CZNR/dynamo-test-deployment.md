# Dynamo 在 K8s 集群的部署信息

> 采集时间：2026-10-28
> Kubeconfig：`/Users/lucy/Documents/Code/gitlab/nhi-data-validator/kube-config/ack-LB97i2CZNR.kubeconfig`
> Context：`kubernetes-admin-c37065475b3804893815ca672b2ed3643`

---

## 1. 总览

集群中的 NVIDIA Dynamo 分为两部分：

| 层级 | 命名空间 | 对象 | 状态 |
|---|---|---|---|
| Dynamo 平台（Operator） | `dynamo-system` | Helm Release `dynamo-platform`（chart `dynamo-operator-1.4.1`） | 1/1 Running |
| Dynamo 推理实例 | `nhss-job` | `DynamoGraphDeployment/dynamo-qwen38-27b-mixed-demo` | Ready=True，state=successful |

被驱动出的运行时组件（同一 DGD 内混部了 3 种角色）：

```
DynamoGraphDeployment: dynamo-qwen38-27b-mixed-demo (nhss-job)
├── Frontend   (type=frontend)  1 replica  → svc :8000   → Pod 172.22.16.9
├── prefill    (type=prefill)   1 replica  → svc :9090   → Pod 172.22.18.82
├── decode     (type=decode)    1 replica  → svc :9090   → Pod 172.22.17.22
└── aggregated (type=worker)    1 replica  → svc :9090   → Pod 172.22.8.199
```

> 说明：`igd-*` 系列工作负载（见第 9 节）由另一套 nanhu-tech / Grove 体系管理，不是本 DGD 的一部分。

---

## 2. Dynamo Platform（Operator）部署

### 2.1 工作负载

| 项目 | 值 |
|---|---|
| Namespace | `dynamo-system` |
| Deployment | `dynamo-platform-dynamo-operator-controller-manager` |
| Ready | 1/1 |
| 镜像 | `nvcr.io/nvidia/ai-dynamo/kubernetes-operator:1.4.1` |
| Pod | `dynamo-platform-dynamo-operator-controller-manager-85f6df58bmk2` |
| Pod IP / Node | `172.22.1.232` / `cn-hangzhou-zjy-d01.10.200.99.6` |
| 重启次数 | 3（最近一次 23 天前） |
| Age | 64d |
| 启动参数 | `--config=/etc/dynamo-operator/config.yaml --operator-version=1.4.1 --operator-image=nvcr.io/nvidia/ai-dynamo/kubernetes-operator:1.4.1 --operator-image-pull-policy=IfNotPresent` |
| 端口 | `8080` metrics、`9443` webhook-server |
| 探针 | liveness `/healthz:8081`、readiness `/readyz:8081` |
| 资源 | requests `512m / 1Gi`，limits `1024m / 2Gi` |
| 安全上下文 | `allowPrivilegeEscalation=false`，drop `ALL` capabilities |

### 2.2 Service / 证书 / 配置

| 对象 | 说明 |
|---|---|
| Service `dynamo-platform-dynamo-operator` | ClusterIP `172.23.199.219:8080` |
| Service `dynamo-platform-dynamo-operator-webhook-service` | ClusterIP `172.23.183.218:443` |
| Secret `webhook-server-cert` | `kubernetes.io/tls`，4 个 key（webhook 证书） |
| Secret `sh.helm.release.v1.dynamo-platform.v1` | Helm Release 记录（status=deployed, version=1） |
| ConfigMap `dynamo-platform-dynamo-operator-config` | 见下 |

Operator 配置（`config.yaml`）关键项：

```yaml
server:
  metrics:
    bindAddress: "0.0.0.0"
    secure: true
  webhook:
    certProvisionMode: "auto"
    secretName: "webhook-server-cert"
leaderElection:
  enabled: true
  id: "dynamo.nvidia.com"
  namespace: "kube-system"
orchestrators:
  kaiScheduler:   { enabled: false }
  volcanoScheduler: { enabled: false }
  grove:          { enabled: false, terminationDelay: "4h" }
serviceMesh:
  enabled: false
mpi:
  sshSecretName: "mpi-run-ssh-secret"
  sshSecretNamespace: "dynamo-system"
```

- **未启用** Grove / KAI / Volcano 调度器集成，也未启用 service mesh。
- 引用的 ClusterRole：`dynamo-platform-dynamo-operator-planner`、`-dgdr-profiling`、`-epp`。

### 2.3 运行日志

- 当前日志仅有 `v1alpha1 ... is deprecated; use nvidia.com/v1beta1` 的周期性告警。
- 上一次异常退出（2026-10-05 05:33:54Z）：

```
leaderelection.go:461] "Error retrieving lease lock"
  err="Get https://172.23.0.1:443/apis/coordination.k8s.io/v1/namespaces/kube-system/leases/dynamo.nvidia.com?timeout=5s: context deadline exceeded"
leaderelection.go:304] "Failed to renew lease"
ERROR setup problem running manager {"error": "leader election lost"}
```

> 结论：Operator 因 **kube-apiserver 访问超时导致租约续约失败** 而退出并重启，属于集群网络/APIServer 抖动，不是配置错误。此后运行正常。

---

## 3. 已安装的 CRD

| CRD | 创建时间 |
|---|---|
| `dynamocheckpoints.nvidia.com` | 2026-08-25T01:14:01Z |
| `dynamocomponentdeployments.nvidia.com` | 2026-08-25T01:14:01Z |
| `dynamographdeploymentrequests.nvidia.com` | 2026-08-25T01:14:02Z |
| `dynamographdeployments.nvidia.com` | 2026-08-25T01:14:02Z |
| `dynamographdeploymentscalingadapters.nvidia.com` | 2026-08-25T01:14:02Z |
| `dynamomodels.nvidia.com` | 2026-08-25T01:14:02Z |
| `dynamoworkermetadatas.nvidia.com` | 2026-08-25T01:14:02Z |

`DynamoGraphDeployment` 版本情况：

| Version | Served | Storage |
|---|---|---|
| `v1alpha1` | true | **true**（实际存储版本） |
| `v1beta1` | true | false |

> 注意：存储版本仍是 `v1alpha1`，而 Operator 日志持续提示应迁移到 `v1beta1`。

---

## 4. DynamoGraphDeployment 实例

### 4.1 元数据与状态

| 项目 | 值 |
|---|---|
| API | `nvidia.com/v1beta1`（storage: v1alpha1） |
| Name | `dynamo-qwen38-27b-mixed-demo` |
| Namespace | `nhss-job` |
| 创建时间 | 2026-09-08T07:58:01Z（Age 50d） |
| Labels | `app.kubernetes.io/name=qwen38-27b-mixed-demo`、`app.kubernetes.io/part-of=dynamo-teaching` |
| Annotations | `nvidia.com/current-worker-hash-v2=76c417b5`、`nvidia.com/dynamo-operator-origin-version=1.4.1` |
| State | `successful` |
| Condition | `Ready=True`（`All resources are ready`，自 2026-10-24T00:27:17Z） |

### 4.2 组件拓扑

| 组件名 | type | replicas | 运行时 Deployment | 运行时 Namespace |
|---|---|---|---|---|
| `Frontend` | `frontend` | 1 | `dynamo-qwen38-27b-mixed-demo-frontend` | `nhss-job-dynamo-qwen38-27b-mixed-demo` |
| `prefill` | `prefill` | 1 | `...-prefill-76c417b5` | `nhss-job-dynamo-qwen38-27b-mixed-demo-76c417b5` |
| `decode` | `decode` | 1 | `...-decode-76c417b5` | 同上 |
| `aggregated` | `worker` | 1 | `...-aggregated-76c417b5` | 同上 |

注意：Frontend 与 Worker 使用了**两个不同的运行时 Namespace 标识**（`...-mixed-demo` 与 `...-mixed-demo-76c417b5`），这是 Operator 注入 worker hash 的结果。

### 4.3 镜像与模型

| 项目 | 值 |
|---|---|
| 统一镜像 | `10.200.88.53/nhss/dynamo-sglang-runtime:1.4.1-amd64`（私有 Harbor） |
| imagePullSecret | `docker-registry-secret` |
| 模型路径 | `/mnt/nfs-models/default/Qwen3.8-27B`（NFS PVC，只读，`subPath: default`） |
| 对外模型名 | `Qwen3.8-27B` |
| 后端 | SGLang（`python3 -m dynamo.sglang`，`workingDir=/workspace/examples/backends/sglang`） |
| 上下文长度 | 262144 |
| TP | 4 |
| 显存占比 | `--mem-fraction-static 0.90` |

### 4.4 Worker 启动参数（prefill / decode / aggregated 基本一致）

```
--model-path /mnt/nfs-models/default/Qwen3.8-27B
--served-model-name Qwen3.8-27B
--enable-multimodal
--context-length 262144
--page-size 1
--tp 4
--trust-remote-code
--chunked-prefill-size 2048
--mem-fraction-static 0.90
--mamba-radix-cache-strategy extra_buffer_lazy
--dyn-tool-call-parser qwen3_coder
--dyn-reasoning-parser qwen3
--host 0.0.0.0
--enable-metrics
--disable-piecewise-cuda-graph
--kv-events-config {"publisher":"zmq","topic":"kv-events","endpoint":"tcp://*:5557"}
```

差异项：

| 组件 | 差异参数 | Dynamo Namespace |
|---|---|---|
| `prefill` | `--disaggregation-mode prefill`、`--disaggregation-transfer-backend nixl`、`--disaggregation-bootstrap-port 12345` | `nhss-job-dynamo-qwen38-27b-mixed-demo-pd` |
| `decode` | `--disaggregation-mode decode`、同上传输/端口 | `nhss-job-dynamo-qwen38-27b-mixed-demo-pd` |
| `aggregated` | 无 disaggregation 参数 | `nhss-job-dynamo-qwen38-27b-mixed-demo-agg` |

Worker 公共环境变量：

```yaml
DYN_REQUEST_PLANE: tcp
DYN_EVENT_PLANE: zmq
DYN_WORKER_GRACEFUL_SHUTDOWN_TIMEOUT: "300"
NCCL_DEBUG: WARN
```

> 事件面使用 **ZMQ** 而非 NATS/etcd，因此集群内没有额外的 NATS/etcd 组件。

### 4.5 Frontend 配置

```yaml
DYN_ROUTER_MODE: kv          # KV-cache 感知路由
DYN_REQUEST_PLANE: tcp
DYN_EVENT_PLANE: zmq
DYN_HTTP_BODY_LIMIT_MB: "200"
```

- 未配置 liveness/readiness 探针。
- 未声明 GPU，仅 CPU 推理路由。

### 4.6 资源与调度

| 组件 | CPU | Memory | GPU | 其他 |
|---|---|---|---|---|
| Frontend | req 4 | req 8Gi / limit 32Gi | — | — |
| prefill / decode / aggregated | req 32（无 limit） | req 128Gi / limit 256Gi | **4**（req=limit） | ephemeral-storage 8Gi、`/dev/shm` 64Gi（Memory emptyDir） |

- **节点亲和性**（硬约束）：
  - `dros.zjlab.io/clusterEnv = drosInCluster`
  - `dros.zjlab.io/spec = GPU_Z2280_24GB`
  - `dros.zjlab.io/resourcepool` / `reserve` / `nodegroup` 必须 **不存在**
- **容忍**：`node-role.alibabacloud.com/bmcp:NoSchedule`
- **安全上下文**（Worker）：追加 `IPC_LOCK`、`SYS_RESOURCE` capabilities
- **优雅退出**：Frontend 60s，Worker 300s
- **存储**：
  - `model-storage` → PVC `nhss-job-stor-nfs-mode-0a4aa44e-pvc`（只读）
  - `dshm` → emptyDir(medium=Memory, 64Gi)

节点实测规格（匹配 `GPU_Z2280_24GB` 标签的节点池）：

- GPU：`NVIDIA-GeForce-RTX-4090-D`，**每节点 6 卡**，单卡 24564 MiB
- CPU：约 384 核；内存：约 512 GiB
- 节点池 ID：`np0983273c81ca4ea9908c8ef6f7e2615c`

---

## 5. 运行时对象

### 5.1 Pod

| Pod | Ready | IP | Node | Age | Restarts |
|---|---|---|---|---|---|
| `...-frontend-6b548b946d-wqf2x` | 1/1 | 172.22.16.9 | `i-d64r23uheobbqdu5dpmg` | 4d10h | 0 |
| `...-prefill-76c417b5-84d5fb74f8-dxkql` | 1/1 | 172.22.18.82 | `i-d64r24mheobbqdu5dpo0` | 50d | 0 |
| `...-decode-76c417b5-d5bdf49cb-fnwnb` | 1/1 | 172.22.17.22 | `i-d64r28uheob0gjak5ngg` | 50d | 0 |
| `...-aggregated-76c417b5-85b9564c8pqs5l` | 1/1 | 172.22.8.199 | `i-d64r26eheobbqdu5dpqg` | 50d | 0 |

> Frontend Pod 于 4 天前重建（其余 50 天未重启），建议核对当时是否有配置变更或节点驱逐。

### 5.2 Service / Endpoints

| Service | 类型 | ClusterIP:Port | Endpoint | 用途 |
|---|---|---|---|---|
| `dynamo-qwen38-27b-mixed-demo-frontend` | ClusterIP | 172.23.234.143:8000 | 172.22.16.9 | OpenAI 兼容入口 |
| `dynamo-qwen38-27b-mixed-demo-prefill-76c417b5` | ClusterIP | 172.23.1.129:9090 | 172.22.18.82 | Worker metrics |
| `dynamo-qwen38-27b-mixed-demo-decode-76c417b5` | ClusterIP | 172.23.115.237:9090 | 172.22.17.22 | Worker metrics |
| `dynamo-qwen38-27b-mixed-demo-aggregated-76c417b5` | ClusterIP | 172.23.140.82:9090 | 172.22.8.199 | Worker metrics |

另有历史遗留 Service：

| Service | 类型 | NodePort | Endpoints | 结论 |
|---|---|---|---|---|
| `dynamo-qwen38-27b-prod-nodeport` | NodePort | `32081 → 8000` | **`<none>`** | **悬空**：selector 指向已不存在的 Dynamo Namespace `nhss-job-dynamo-qwen38-27b-prod` |

### 5.3 健康验证（实测）

在前端 Pod 内执行：

```bash
kubectl exec -n nhss-job deploy/dynamo-qwen38-27b-mixed-demo-frontend -- \
  curl -s http://localhost:8000/health
# => {"status":"healthy", ...}

kubectl exec -n nhss-job deploy/dynamo-qwen38-27b-mixed-demo-frontend -- \
  curl -s http://localhost:8000/v1/models
# => {"object":"list","data":[{"id":"Qwen3.8-27B","context_window":262144,"owned_by":"nvidia"}]}
```

指标端点：

| 端点 | 结果 |
|---|---|
| Frontend `:8000/metrics` | HTTP 200 |
| Worker `:9090/metrics` | HTTP 200 |

---

## 6. 可观测性现状

### 6.1 已具备

| 能力 | 资源 | 说明 |
|---|---|---|
| Operator 指标 | `ServiceMonitor/dynamo-platform-dynamo-operator-operator`（`dynamo-system`） | HTTPS `/metrics`，15s |
| Frontend 指标 | `PodMonitor/dynamo-frontend` | `namespaceSelector.any=true`，5s，路径 `/metrics`，port `http` |
| Worker 指标 | `PodMonitor/dynamo-worker` | 5s，端口 `system` / `system-0` / `system-1` |
| Planner / Router 指标 | `PodMonitor/dynamo-planner`、`dynamo-router` | 5s |
| 应用侧开关 | Worker `--enable-metrics`、Pod 标签 `nvidia.com/metrics-enabled=true` | 已开启 |
| 健康/模型接口 | `/health`、`/v1/models`、`/metrics` | 均正常 |

Worker 指标端口实际声明（Pod spec）：

```
9090  -> name: system
19090 -> name: nixl
```

Frontend 指标端口：`8000 -> name: http`。

### 6.2 缺口与风险

1. **`PodMonitor/dynamo-worker` 的 `system-0` / `system-1` 端点无法解析**
   Pod 只声明了 `system`（9090）与 `nixl`（19090），不存在 `system-0` / `system-1` 端口名。
   结果：多卡 worker 的 per-card 指标抓取会失败，仅 `system` 生效。
   （如 runc/容器运行时会为多端口生成 `system-0/1`，需通过 `kubectl get cm -n <prom-operator> -o yaml` 或 Prometheus targets 页确认实际 `up` 状态。）

2. **没有告警规则**
   `PrometheusRule` 中未找到任何与 dynamo/qwen 相关的规则，当前仅有指标采集，无告警。

3. **没有 Grafana Dashboard 线索**
   未在集群内发现 Dynamo 相关 Dashboard ConfigMap。

4. **Dynamo 发现范围可能过宽（重要）**
   Frontend `/health` 返回的 endpoint 注册表不仅包含本 DGD 的
   `nhss-job-dynamo-qwen38-27b-mixed-demo-agg/pd-76c417b5`，还包含：
   - `nhss-job-igd-142-agg`
   - `nhss-job-igd-142-agg-mn`
   - `nhss-job-igd-142-mixed`
   - `nhss-job-igd-4090-allinone-repl`
   - `nhss-job-igd-4090-pd-repl`

   原因：这些 igd Pod 都带有 `nvidia.com/dynamo-discovery-enabled=true` +
   `nvidia.com/dynamo-discovery-backend=kubernetes`，Frontend 的 K8s 发现机制按
   **同一 K8s Namespace（`nhss-job`）** 聚合，因此会看到其他体系的 Worker。

   **影响**：`DYN_ROUTER_MODE=kv` 的 Frontend 可能把请求路由到 igd-* 的 Worker，
   导致跨部署流量串扰。若预期是严格隔离，需要为 igd 的 Pod 去掉 discovery 标签，
   或改用不同的 K8s Namespace / 显式配置发现范围。**建议与 nanhu 团队确认这是否为预期行为。**

5. **Operator 指标为 HTTPS 且 `insecureSkipVerify=true`**
   属于 Helm chart 默认配置，可接受，但建议后续启用证书校验。

---

## 7. 其他发现

1. **`dynamo-qwen38-27b-prod-nodeport` 悬空**
   该 NodePort 的 selector 指向 `nvidia.com/dynamo-namespace=nhss-job-dynamo-qwen38-27b-prod`，
   但当前不存在该 Dynamo Namespace，Endpoints 为空。外部 `:32081` 访问会失败。
   → 若不使用应删除；若需要对外暴露，应把 selector 改为
   `nhss-job-dynamo-qwen38-27b-mixed-demo`。

2. **CRD 存储版本仍是 v1alpha1**
   Operator 日志持续告警 v1alpha1 已废弃。后续升级 Operator 前需评估迁移到 v1beta1。

3. **Frontend 无探针**
   前端 Deployment 未配置 readiness/liveness，滚动更新时可能出现短暂不可用。

4. **Worker 无 CPU limit**
   仅设置 requests（32 核），异常时可能挤占节点 CPU。

5. **混部模式**
   同一 DGD 同时存在 `aggregated` 与 `prefill+decode`，属于“混合演示”形态；
   `aggregated` 组与 PD 组共享同一批 4090 节点，注意显存与 NIXL 带宽竞争。

---

## 8. 集群中其他 Dynamo 相关工作负载（非本 DGD）

`nhss-job` 命名空间下还有一套由 **Grove Operator** 驱动的推理图部署：

| 对象 | 管理者 | 标识 |
|---|---|---|
| `igd-142-agg`、`igd-142-agg-mn`、`igd-142-mixed` | `grove-operator` | `nanhu-tech.io/infergraphdeployment` |
| `igd-4090-allinone-repl`、`igd-4090-pd-repl` | `grove-operator` | 同上 |

特征：

- Pod 由 `PodClique`（`grove.io/v1alpha1`）创建，存在 `planner` / `router` / `worker` / `decode` / `prefill` 等角色。
- **只带 Dynamo 的 discovery 标签**（`nvidia.com/dynamo-discovery-enabled=true`），
  没有 `nvidia.com/dynamo-namespace` 标签，也不由 Dynamo Operator 管理。
- 对应 NodePort 入口：
  - `igd-142-agg-0-router-ep-0`：`8000:31024`
  - `igd-142-mixed-0-router-ep-0`：`8000:31949`
  - `igd-4090-allinone-repl-0-router-ep-0`：`9990:31792`
  - `igd-4090-pd-repl-0-router-ep-0`：`8010:31310`

---

## 9. 常用运维命令

```bash
export KUBECONFIG=/Users/lucy/Documents/Code/gitlab/nhi-data-validator/kube-config/ack-LB97i2CZNR.kubeconfig

# Operator
kubectl -n dynamo-system get deploy,pod,svc
kubectl -n dynamo-system logs deploy/dynamo-platform-dynamo-operator-controller-manager -f

# DGD 实例
kubectl -n nhss-job get dgd
kubectl -n nhss-job describe dgd dynamo-qwen38-27b-mixed-demo
kubectl -n nhss-job get pods -l app.kubernetes.io/name=qwen38-27b-mixed-demo -o wide

# 健康与模型
kubectl -n nhss-job exec deploy/dynamo-qwen38-27b-mixed-demo-frontend -- \
  curl -s http://localhost:8000/health
kubectl -n nhss-job exec deploy/dynamo-qwen38-27b-mixed-demo-frontend -- \
  curl -s http://localhost:8000/v1/models

# 指标
kubectl -n nhss-job exec deploy/dynamo-qwen38-27b-mixed-demo-frontend -- \
  curl -s http://localhost:8000/metrics | head
kubectl -n nhss-job exec deploy/dynamo-qwen38-27b-mixed-demo-prefill-76c417b5 -- \
  curl -s http://localhost:9090/metrics | head

# 端口转发调试
kubectl -n nhss-job port-forward svc/dynamo-qwen38-27b-mixed-demo-frontend 8000:8000
```

---

## 10. 结论与建议（按优先级）

| 优先级 | 事项 |
|---|---|
| P0 | 确认 Frontend 发现到 `igd-*` Worker 是否为预期；若不是，隔离 discovery 标签或命名空间 |
| P0 | 清理或修正 `dynamo-qwen38-27b-prod-nodeport`（当前 Endpoints 为空） |
| P1 | 修复 `PodMonitor/dynamo-worker` 的 `system-0` / `system-1` 端口名不匹配 |
| P1 | 为 Frontend 增加 readiness/liveness 探针 |
| P2 | 补充 Dynamo 相关 PrometheusRule 告警（TTFT、ITL、队列深度、GPU 利用率、KV 命中率） |
| P2 | 评估 CRD 从 `v1alpha1` 迁移到 `v1beta1` |
| P3 | 为 Worker 设置 CPU limit，避免资源挤占 |
| P3 | 关注 Operator 因 apiserver 超时丢失 leader 的历史重启，评估租约超时参数 |

---

## 11. 集群内接口验证记录（2026-10-29）

> 采集时间：2026-10-29
> 执行位置：本机，使用既有 kubeconfig 指向的 context
> 结论：**两条请求均未取得 HTTP 响应**，本次可访问的 context 上无法完成验证。本节是新的独立观测，不覆盖、也不替代第 5.3 节 2026-10-28 的快照证据。

### 11.1 待验证请求

按给出的集群内接口测试原样记录：

```bash
# 1. 模型列表
curl -s http://172.23.159.229:8000/v1/models

# 2. 非流式对话
curl -s http://172.23.159.229:8000/v1/chat/completions \
     -H 'Content-Type: application/json' \
     -d '{"model":"IGD-agg","messages":[{"role":"user","content":"你好，用一句话介绍你自己"}],"max_tokens":64}'
```

目标端点 `http://172.23.159.229:8000`，模型名 `IGD-agg`。

### 11.2 实测结果

对目标地址发起第 1 条请求：

```bash
curl -sS -m 8 -o /dev/null \
  -w 'http_code=%{http_code} connect=%{time_connect}s total=%{time_total}s\n' \
  http://172.23.159.229:8000/v1/models
```

实测输出：

```text
curl: (28) Connection timed out after 8004 milliseconds
http_code=000 connect=0.000000s total=8.004775s
exit=28
```

判读：

- `connect=0.000000s` 且 `http_code=000` 说明 **TCP 握手根本没有发生**。这不是 HTTP 4xx/5xx，也不是 connection refused（后者会立即返回 RST，`time_connect` 接近 0 但 curl 会报 `Connection refused`）。
- 因此属于**路由/可达性失败**：目标地址在当前网络环境下没有可达的监听者。
- 由于第 1 条已证明端点不可达，**第 2 条（`/v1/chat/completions`）未再发送**。这是基于已测结论的主动省略，不是漏做；端点不可达时继续发第二条只会复现同一个超时，不产生新信息。

### 11.3 为什么无法在集群内验证

"集群内接口测试"需要在集群内有一个执行环境（`kubectl exec` 进已有 Pod，或临时调试 Pod）。当前 context 上两条路径都不可用：

| 检查项 | 命令 | 结果 |
|---|---|---|
| 命名空间内是否有 Pod | `kubectl -n nhss-job get pods` | `No resources found in nhss-job namespace.`（命名空间本身存在，`184d`） |
| 是否有 Service 暴露 `:8000` | `kubectl get svc -A -o wide \| grep ':8000'` | exit 1，无匹配 |
| 是否有 Service 持有该 IP | `kubectl get svc -A -o wide \| grep '172.23.159.229'` | exit 1，无匹配 |
| 本 context 的 Service 地址段 | `kubectl get svc -A -o wide` | `172.21.x.x`，不是 `172.23.x.x` |
| `igd-*` 工作负载状态 | `kubectl -n nhss-job get infergraphdeployment` | `igd-142-agg  Failed  20h` |
| Dynamo CRD 是否安装 | `kubectl get crd \| grep -Ei 'dynamo\|infergraph'` | 仅 `dynamoworkermetadatas.nvidia.com`、`infergraphdeployments.nanhu-tech.io`、`infergraphscalingadapters.nanhu-tech.io`；**没有** `dynamographdeployments.nvidia.com` |
| 当前 context | `kubectl config current-context` | `kubernetes-admin@c642d589f7b3e478f88bce2572e5b8ce7`（`https://10.200.88.247:6443`） |

与本文第 1–6 节记录的 2026-10-28 集群对比：

- 记录的 context 是 `kubernetes-admin-c37065475b3804893815ca672b2ed3643`，当前 context 是 `c642d589f7b3e478f88bce2572e5b8ce7`，**不是同一个**。
- 第 3 节列有 `dynamographdeployments.nvidia.com`（创建于 2026-08-25T01:14:02Z），当前 context **没有**该 CRD。
- 第 5.2 节的 Service ClusterIP 全部落在 `172.23.x.x`，当前 context 的 Service IP 落在 `172.21.x.x`——Service CIDR 不一致。

综合判断：当前 kubeconfig 指向的是**另一套集群**，不是 2026-10-28 采集的那一套。记录的 kubeconfig 路径
`/data/home/bage/kubeconfig/ack-LB97i2CZNR.kubeconfig` 在本机不存在，无法切回目标集群。

### 11.4 目标地址与模型名的归属

- `172.23.159.229` 不在第 5.2 节记录的 Dynamo DGD Service 列表中（frontend 实际为 `172.23.234.143:8000`），只是落在同一个 `172.23.x.x` 地址段内。
- 模型名 `IGD-agg` 与 Dynamo DGD 实际服务的 `Qwen3.8-27B` 不同，而与第 8 节记录的 nanhu-tech `igd-*` 系列吻合（该系列中 `igd-142-agg-0-router-ep-0` 的 NodePort 为 `8000:31024`）。
- 因此这两条请求**不是**对第 1–6 节 `dynamo-qwen38-27b-mixed-demo` 的验证，不应据此更新该 DGD 的健康结论。

### 11.5 如何补做

要真正完成这两条验证，需满足其一：

1. 提供指向 2026-10-28 那套集群的 kubeconfig（或 `ack-LB97i2CZNR.kubeconfig` 在本机的实际路径），并确保 `nhss-job` 中存在可 `kubectl exec` 的 Pod——这样两条 curl 可原样执行；
2. 若 `igd-142-agg` 才是要验证的对象，先修复其 `Failed` 状态（属于修改集群，需另行授权），再在集群内发起上述两条 curl。

在此之前，本节结论保持为：**未验证（端点不可达）**，不作为通过证据。

## 12. 配套材料索引

本节列出本目录内与本部署记录配套的材料，便于从结论回溯到原始证据。

| 路径 | 内容 | 性质 |
|------|------|------|
| [`trace-collection-plan.md`](trace-collection-plan.md) | Request Plane trace 采集方案（草案，待确认） | 方案文档 |
| [`request-path-trace-design.html`](request-path-trace-design.html) | span 设计（前驱关系、属性归属） | 方案文档 |
| [`request-path-trace-walkthrough.html`](request-path-trace-walkthrough.html) | 端到端链路走查 | 方案文档 |
| [`otel-collector-reference.yaml`](otel-collector-reference.yaml) | collector 配置参考（多文档） | 配置参考 |
| [`request-plane-trace-env.fragment.yaml`](request-plane-trace-env.fragment.yaml) | 注入到工作负载的 env 片段 | 合并参考，非完整清单 |
| [`deploy/nhi-dgd.json`](deploy/nhi-dgd.json) | DGD 实例导出 | 集群快照 |
| [`deploy/nhi-frontend-nodeport.yaml`](deploy/nhi-frontend-nodeport.yaml)、[`deploy/nhi-models-pv-pvc.yaml`](deploy/nhi-models-pv-pvc.yaml) | 请求面新建的 NodePort 与 PV/PVC | 可 apply 清单 |
| `evidence/01`–`04` | DGD 快照、工作负载宽表、patch 前后对比 | 原始证据 |
| `evidence/05`–`08` | 实例清单 → 独立栈设计 → 部署验收 → litellm 跨集群接入验收 | 阶段报告 |
| `backup/otel-collector-config-before-*.yaml` | 两次变更前从集群导出的 ConfigMap | 回滚参照，**凭证已脱敏** |

`backup/` 下的两份快照里 `x-br-acid` 已替换为占位符（真实值只保存在本地被 gitignore 的 `.vscode/`），因此它们**只能用于结构对照，不能直接 `kubectl apply`**；真要回滚需先从集群直取或把占位符换回真实值。
