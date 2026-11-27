# 独立最小 Dynamo 请求面单元（nhi namespace）设计

> 决策来源：用户 Q4 指示——"直接部署一套独立的最小单元的 dynamo 请求面组件，延用现在的配置，命名空间放到 nhi 下"
> 编制：2026-10-29 ｜ 状态：**设计草案，未创建任何资源**

---

## 1. 为什么放 `nhi` 就能隔离（已验证）

Dynamo 的 K8s discovery 作用域是 **k8s namespace**，不是 `--namespace` 参数：

- `nhss-job` 里所有 Dynamo Service（本 DGD 的 4 个 + `igd-*` 的 12 个）互相可见
- 本 DGD Frontend 的 `/health` 因此列出了 `igd-142-agg`、`igd-4090-*` 等 5 个外部 namespace
- Operator 会为每个 DGD 在其所在 ns 自动创建 `<dgd>-k8s-service-discovery-binding`（Role/RoleBinding）

实测 `nhi` namespace：

| 检查项 | 结果 |
|---|---|
| 已有 Dynamo 组件 | **无**（只有 mooncake / vector / otel-collector） |
| 已有 DGD | 无（全集群仅 `nhss-job` 一个） |
| ResourceQuota / LimitRange | 无 |
| PodSecurity 标签 | 无（默认放行） |
| `docker-registry-secret` | **缺失 ← 阻塞点** |

→ 在 `nhi` 建 DGD 后，其 Frontend 的 `/health` 将**只看到自己**，跨部署污染问题从根上消失。

---

## 2. 最小单元构成

```
┌──────────────────────────────────┐
│ Frontend  (CPU 4c/8Gi, 1 副本)   │  :8000
└──────────────┬───────────────────┘
               │ tcp/msgpack (request plane)
               ▼
┌──────────────────────────────────┐
│ aggregated worker (4×GPU, 1 副本)│  Qwen3.8-27B TP=4
└──────────────────────────────────┘
```

**2 个 Pod、4 张 GPU。** 选 `aggregated` 而非 `prefill+decode`：
- request plane 只一跳，trace 最干净
- 不需要 NIXL / disaggregation 配置，故障面最小
- 与现有 `aggregated` 组件配置完全一致

被裁掉的（相对现有 mixed-demo）：`prefill`、`decode` 两个 PD 组件（各省 4 卡）。

---

## 3. 资源需求与可用性

| 项 | 现有配置（沿用） | 可用性 |
|---|---|---|
| GPU | `nvidia.com/gpu: 4`（TP=4） | 匹配 affinity 的节点 **32 个**，其中 22 个有 ≥4 卡空闲，总空闲 **139 卡** |
| CPU / 内存（worker） | 32c / 128Gi req，256Gi limit | 与现有 worker 同规格 |
| CPU / 内存（Frontend） | 4c / 8Gi req，32Gi limit | |
| 存储 | NFS `10.202.72.31:/var/lib/container/models`（RWX，1Pi） | 静态 PV，可跨 ns 重新引用 |
| 调度约束 | `dros.zjlab.io/spec=GPU_Z2280_24GB` 等 5 条 nodeAffinity + bmcp toleration | 沿用 |

> 注意：这是一次**真实的 4 卡占用**，模型为 Qwen3.8-27B（TP=4，约 54GB+buffer）。
> 不影响现有任何服务（节点资源充足、独立 ns），但确实消耗 4 张卡。

---

## 4. 命名

| 对象 | 名称 |
|---|---|
| Namespace | `nhi` |
| DGD | `dynamo-trace-demo` |
| 组件 | `Frontend`、`aggregated` |
| Service（Frontend） | `dynamo-trace-demo-frontend`（8000） |
| Dynamo discovery ns | `nhi-dynamo-trace-demo-agg` |
| PV / PVC | `nhi-dynamo-trace-models-pv` / `nhi-dynamo-trace-models-pvc` |
| `OTEL_SERVICE_NAME`（Frontend） | `dynamo-trace-demo-frontend` |
| `OTEL_SERVICE_NAME`（worker） | `dynamo-trace-demo-aggregated` |

---

## 5. 配置来源（逐项沿用现有 DGD）

| 项 | 取值 |
|---|---|
| 镜像 | `10.200.88.53/nhss/dynamo-sglang-runtime:1.4.1-amd64`（digest 四组件一致） |
| 模型 | `/mnt/nfs-models/default/Qwen3.8-27B`，`--served-model-name Qwen3.8-27B` |
| worker args | 完整沿用现有 `aggregated` 的 17 个参数（`--tp 4`、`--context-length 262144`、`--mem-fraction-static 0.90`、`--kv-events-config` 等），**去掉** disaggregation 相关项 |
| Frontend args | 无（镜像默认入口） |
| env | 沿用 `DYN_ROUTER_MODE=kv`、`DYN_REQUEST_PLANE=tcp`、`DYN_EVENT_PLANE=zmq`、`DYN_HTTP_BODY_LIMIT_MB=200`；worker 沿用 `DYN_WORKER_GRACEFUL_SHUTDOWN_TIMEOUT`、`NCCL_DEBUG` |
| affinity / tolerations | 逐字沿用 |
| volumes | `dshm`（64Gi Memory emptyDir）+ 模型 PVC（换成本 ns 的） |

---

## 6. 与现有方案的差异：一次开到底

因为这是**全新部署、不中断任何现有服务**，不需要分批：

| 组件 | OTel 配置 |
|---|---|
| Frontend | `OTEL_EXPORT_ENABLED=true`、`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://otel-collector.nhi.svc.cluster.local:4317`、`OTEL_EXPORTER_OTLP_PROTOCOL=grpc`、`OTEL_TRACES_SAMPLE_RATIO=1.0`、`OTEL_SERVICE_NAME=...` |
| aggregated | **同样 5 条** + 追加 `--enable-trace`、`--otlp-traces-endpoint otel-collector.nhi.svc.cluster.local:4317`、`--sglang-trace-level 2` |

→ 一次请求即可拿到 **Frontend → worker(runtime) → SGLang 引擎** 三层 span，不需要阶段 2/3。

额外优势：`nhi` 与 Collector 同 ns，无需跨 ns DNS。

---

## 7. 阻塞点与待办

| # | 事项 | 说明 | 需谁做 |
|---|---|---|---|
| 1 | **复制 `docker-registry-secret`** | `nhi` 无此 secret，从 `nhss-job` 复制一份到 `nhi`（内容相同，跨 ns 复制镜像仓库凭据） | 需你授权 |
| 2 | 创建 PV/PVC | 指向同一 NFS `10.202.72.31:/var/lib/container/models`，RWX | 我执行 |
| 3 | 创建 DGD | 按 §5 配置 | 我执行（需确认 4 卡占用） |
| 4 | 阶段 0 探针 | 可省略——新部署本身就是最干净的探针 | 合并到部署后验收 |

> 关于 #1：不复制 secret 的话，Pod 会 `ImagePullBackOff`（`10.200.88.53` 是私有仓库）。
> 也可以在 `nhi` 建一个 `ServiceAccount` 并在 DGD 里指定，但 `imagePullSecrets` 只能引用同 ns 的 secret，复制是最直接的做法。

---

## 8. 预期结果

部署完成后应看到：

1. `nhi/dynamo-trace-demo-frontend` 的 `/health` **只列 1 个 namespace**：`nhi-dynamo-trace-demo-agg`（对比现在 nhss-job 的 8 个）
2. 发一条请求 → Collector `otelcol_receiver_accepted_spans` 从 0 变正数
3. ClickHouse 中出现完整父子链：HTTP handler → `generate` → worker `handle_payload` → SGLang 引擎 span

---

## 9. 回退

一条命令：
```bash
kubectl -n nhi delete dgd dynamo-trace-demo
kubectl delete pvc -n nhi nhi-dynamo-trace-models-pvc
kubectl delete pv nhi-dynamo-trace-models-pv
```
PV 的 `reclaimPolicy` 需设为 `Retain` 且**不复用原 NFS 上的数据目录**，删除 PV 不会影响现有模型文件。

---

## 10. 需要你确认

1. **4 张 24GB GPU 占用**，是否放行？（这是一次真实占用，非压测，但按规约需确认）
2. **从 `nhss-job` 复制 `docker-registry-secret` 到 `nhi`**，是否放行？
3. 组件命名用 `dynamo-trace-demo` / `nhi-dynamo-trace-demo-agg`，是否合适？
4. `nhi` 是观测专用 ns，把推理 Pod 放进去是否可以？（好处：与 Collector 同 ns；代价：ns 语义混杂）
