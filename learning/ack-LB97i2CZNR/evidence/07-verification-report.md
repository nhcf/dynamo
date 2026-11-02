# 独立 Trace 栈部署验收报告

> 时间：2026-10-29 ｜ 集群：`ack-LB97i2CZNR` ｜ 状态：**部署成功，端到端链路已打通**
> 前置设计：[06-independent-trace-stack.md](./06-independent-trace-stack.md)

---

## 1. 部署清单

| 对象 | 名称 | 说明 |
|---|---|---|
| Namespace | `nhi` | 与 `nhss-job` 完全隔离 |
| DGD | `nhi/dynamo-trace-demo` | 2 组件，1 副本 |
| Pod | `dynamo-trace-demo-frontend-8d8c86b9-h9wlq` | `i-d64r23uheob0gjak5n9g` / `172.22.10.136` |
| Pod | `dynamo-trace-demo-aggregated-a6ac8e74-559c74f98c-c9s2f` | `i-d64r26mheob0gjak5ne0` / `172.22.7.8`，4×GPU |
| PV/PVC | `nhi-dynamo-trace-models-pv` / `-pvc` | NFS `10.202.72.31:/var/lib/container/models`，**只读**（ROX） |
| Secret | `nhi/docker-registry-secret` | 从 `nhss-job` 复制，未回显内容 |

配置文件：
- [deploy/nhi-models-pv-pvc.yaml](./deploy/nhi-models-pv-pvc.yaml)
- [deploy/nhi-dgd.json](./deploy/nhi-dgd.json)（由现有 DGD 原件派生，非手写）

**对现有部署的影响：零。** `nhss-job/dynamo-qwen38-27b-mixed-demo` 全程未改动，4 个 Pod 未重启。

---

## 2. 验证结果

### 2.1 Discovery 隔离（方案核心目标）

| | `nhss-job` Frontend | `nhi` Frontend |
|---|---|---|
| `/health` 实例数 | 74 | **7** |
| 可见 namespace | 8 个（含 5 个 `igd-*` 外部部署） | **1 个**：`nhi-dynamo-trace-demo-agg-a6ac8e74` |

跨部署污染从根上消失。

### 2.2 Collector 从"空跑"到"有数据"

部署前：`otelcol_receiver_accepted_spans` **不存在**（Collector 自启动从未收到任何 span）。
发一条推理请求后：

| 指标 | 值 |
|---|---|
| `otelcol_receiver_accepted_spans{receiver="otlp",transport="grpc"}` | **33** |
| `otelcol_exporter_sent_spans{exporter="otlp/vector"}` | **33** |
| `otelcol_exporter_sent_spans{exporter="kafka"}` | **33** |
| `otelcol_exporter_send_failed_spans` | **0** |
| `otelcol_exporter_queue_size{data_type="traces"}` | **0** |

→ `Pod → Collector → Vector → ClickHouse` 全链贯通，**历史首次验证**。

### 2.3 ClickHouse 落库与完整 trace 树

表：`llm_observability.llm_traces_otlp`（Vector batch 60s 刷盘）

一条 `POST /v1/chat/completions` 产生 **17 个 span**，跨 3 个 service_name，同一 `trace_id`：

```
[FE ] http-request                       16529.742 ms   ← Frontend HTTP 入口
[rt ] handle_payload                     16524.267 ms   ← Dynamo worker runtime
[SGL] Req f6b55838                       16516.506 ms   ← SGLang 引擎请求
[SGL]   Scheduler [TP 0]                 16495.714 ms
[SGL]   Scheduler [TP 3]                 16489.975 ms
[SGL]   Scheduler [TP 1]                 16483.472 ms
[SGL]   Scheduler [TP 2]                 16483.437 ms
[SGL]   prefill_forward  ×4              ~15580–15903 ms
[SGL]   decode_forward   ×4              ~574–880 ms
```

（`FE`=dynamo-trace-demo-frontend, `rt`=dynamo-trace-demo-aggregated, `SGL`=sglang）

**关键结论：**
1. W3C traceparent 跨进程传播成功 —— Frontend → request plane(tcp/msgpack) → worker → SGLang 全程同一 `trace_id`
2. 三层可观测：Dynamo 前端路由、worker runtime、推理引擎内部（4 卡 TP 各自的 scheduler/prefill/decode）
3. `k8sattributes` 生效：`pod_name` / `pod_namespace` / `node_name` 全部回填，可定位到具体 Pod 与节点

### 2.4 Frontend KV router span（额外收获）

`dynamo-trace-demo-frontend` 下还产生了一组路由决策 span：
`kv_router.route_request` → `kv_router.schedule` → `kv_router.find_matches` / `compute_seq_hashes` / `compute_block_hashes` / `select_worker`。

---

## 3. 与设计方案的差异

| 项 | 方案 | 实际 |
|---|---|---|
| 组件 | Frontend + aggregated | 一致 |
| OTel 配置 | Frontend 5 条 + worker 5 条 + 引擎 3 参数 | 一致，一次到位，未分批 |
| 存储 | 计划 PV/PVC | 实际用 **ReadOnlyMany + `ro` 挂载**，比方案更保守 |
| 阶段 0 探针 | 需要合成 span | **省略**——真实请求已证明全链 |

---

## 4. 已知问题（不影响 trace）

### 4.1 logs exporter 噪音

日志中每 ~5s 一条：

```
ERROR opentelemetry_sdk: name="BatchLogProcessor.ExportError"
  ... 127.0.0.1:4317 ... Connection refused
```

**成因**：`OTEL_EXPORT_ENABLED=true` 会**同时**创建 traces 和 logs 两个 exporter（见 `lib/runtime/src/logging.rs:1267-1328`）。我们只设了 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`，logs 走默认 `http://localhost:4317`。

**为什么不能设 `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` 解决**：Collector 配置只有 `traces` 和 `metrics` 两条 pipeline，**没有 `logs`**。即使指向 Collector，也会返回 `Unimplemented`，仍是 ERROR 级日志。

**处理**：保留现状。trace 链路与此完全独立（`send_failed_spans=0` 佐证）。彻底消除需给公共 `otel-collector` 增加 logs pipeline，属独立议题（用户此前明确不改公共 Collector）。

### 4.2 Vector 刷盘延迟

Vector 的 ClickHouse sink 配置 `batch.timeout_secs: 60`，因此 span 落库有最多 60s 延迟。查询短时间窗口时需等待。

### 4.3 ClickHouse 计数波动

`llm_traces_otlp` 在合并过程中 `count()` 会有小幅波动（观测到 18 → 15），属正常。查询建议用 `start_time > now() - INTERVAL N MINUTE` + `trace_id LIKE '<prefix>%'`。

---

## 5. 复现验证命令

### 发请求
```bash
export KUBECONFIG=/Users/lucy/Documents/Code/gitlab/nhi-data-validator/kube-config/ack-LB97i2CZNR.kubeconfig
FE=$(kubectl -n nhi get pods --no-headers | grep frontend | awk '{print $1}')
kubectl -n nhi exec $FE -c main -- curl -sS -m 120 http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen3.8-27B","messages":[{"role":"user","content":"用三个词介绍你自己"}],"max_tokens":32}'
```

### 看 Collector 计数
```bash
kubectl -n nhi port-forward deploy/otel-collector 18888:8888 >/dev/null 2>&1 &
curl -s http://127.0.0.1:18888/metrics | grep -E '^otelcol_(receiver_accepted_spans|exporter_sent_spans|exporter_send_failed_spans)'
```

### 查 ClickHouse（在 frontend Pod 内，本机 DNS 不可达）

> **凭据不入库**：本仓库为 public，ClickHouse 账号密码不以明文写入文档。
> 先取凭据（来源：`nhi/nhi-vector-agg-traces` ConfigMap 的 Vector sink 配置，格式 `user:password`）：
> ```bash
> export CH_CRED=$(kubectl -n nhi get cm nhi-vector-agg-traces -o json | python3 -c "
> import json,sys,re
> v=json.load(sys.stdin)['data']['vector.yaml']
> m=re.search(r'auth:\s*\n\s*password:\s*(\S+)\s*\n\s*user:\s*(\S+)', v)
> print(f'{m.group(2)}:{m.group(1)}')
> ")
> ```

```python
import base64, urllib.request, urllib.parse, os
HOST='10.107.159.5'
AUTH=base64.b64encode(os.environ['CH_CRED'].encode()).decode()
def q(sql):
    req=urllib.request.Request(f'http://{HOST}/?query='+urllib.parse.quote(sql),
        headers={'Host':'isg.fed.clickhouse.zhejianglab.org','Authorization':'Basic '+AUTH})
    return urllib.request.urlopen(req, timeout=60).read().decode()

print(q("""SELECT service_name, name, duration_ms FROM llm_observability.llm_traces_otlp
WHERE pod_namespace='nhi' AND start_time > now() - INTERVAL 30 MINUTE
ORDER BY start_time DESC LIMIT 30 FORMAT TSV"""))
```

---

## 6. 回退

```bash
kubectl -n nhi delete dgd dynamo-trace-demo        # 级联删除 Deployment/Service/SA
kubectl delete pvc -n nhi nhi-dynamo-trace-models-pvc
kubectl delete pv nhi-dynamo-trace-models-pv
kubectl -n nhi delete secret docker-registry-secret
```

PV `reclaimPolicy: Retain` + 只读挂载 ⇒ 删除不会触碰 NFS 上任何模型文件。
