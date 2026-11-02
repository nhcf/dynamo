# litellm 网关跨集群接入 Trace Demo 验收报告

> 时间：2026-10-29 ｜ 目标集群：`ack-LB97i2CZNR`（服务端）｜ 来源集群：`maas-dev`（调用端）
> 前置：[07-verification-report.md](./07-verification-report.md)（独立 Trace 栈已就绪）
> 2026-11-02 更新：30000 的后端已由 `dynamo-trace-demo` 交接到 `dynamo-trace-demo-mixed`，并新增 PD 路由；拓扑见 §1，交接过程见 §10。

---

## 1. 目标

从 `maas-dev` 集群 `litellm-system` 命名空间的 LiteLLM 网关注册一条模型路由，
打通到 `nhi` 命名空间 `dynamo-trace-demo` 的完整推理链路，并确认该链路的 OTel trace 正常落库。

```
client
  → litellm (maas-dev:4000, simple-shuffle + otel callback)
  → 10.202.72.x:30000 (ack-LB NodePort)
  → dynamo-trace-demo-mixed-frontend (nhi)
      ├── aggregated worker (SGLang, Qwen3.8-27B, tp4)
      └── prefill + decode (SGLang, Qwen2.5-7B-Instruct, mooncake_tcp)
  → OTel → otel-collector(nhi) → vector-agg-traces → Kafka ✓
```

> 10-29 首轮验收时，30000 的后端是单模型的 `dynamo-trace-demo-frontend`（只有 aggregated）；
> 11-02 起换成 `dynamo-trace-demo-mixed`，一个 Frontend 同时服务 aggregated 与 PD 两种模式，
> 交接原因与过程见 §10。trace 出口口径也已从 ClickHouse 查询改为确认推送至 Kafka。

`litellm` 侧本就开启了 OTel（`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.maas-dev.svc.cluster.local:4317`），
因此这条跨集群调用会产生**两段独立 trace**：maas-dev 的 litellm span 与 ack-LB 的 dynamo span，
各自落在所属集群的 ClickHouse。本文验收的是 ack-LB 这一段。

---

## 2. 结论速览

| 项 | 结果 |
|---|---|
| 跨集群网络通路 | **仅 `IP:端口` 精确放通**：2049 / 9990 / 30000 三个端口可达，其余 30000–32767 全部 DROP |
| 30000 端口边界 | maas-dev → ack-LB 三个节点 `:30000` 全部通；本机 → ack-LB 节点 `:30000` **不通**（本机不在放通名单） |
| 端口归属 | 30000 为本项目预先申请预留，复用无需新增申请 |
| litellm 路由注册 | 成功，`/model/new` 动态注册，未重启、未改 ConfigMap |
| 端到端推理 | 成功，1.30s，`finish_reason: stop`，usage 59/39/98 |
| trace 落库 | 成功，单 trace `a844f08ba32e1c7d4a1999fcd5e8b789`，**32 span** |
| 既有故障清理 | `Qwen3-Coder-Next-FP8` / `Qwen3-Coder-Probe` 已从 litellm 移除 |
| 4090d 部署 | **未做任何变更**（经核实，其当前部署与 Qwen3-Coder 无关，详见 §6）|
| PD 路由复用同一 Frontend | 成功（11-02）：新建 `dynamo-trace-demo-mixed`（4 组件 6 卡）+ 切 30000 selector，**零端口申请、零中断**，详见 §10 |

---

## 3. ack-LB 集群对外暴露面现状

本轮先做只读盘点，结论是**这个集群没有公网出口，也没有任何 dynamo 服务走 Ingress**。

### 3.1 Ingress

- IngressClass 两个来源：`nginx`（`k8s.io/ingress-nginx`）
- 共 22 条 Ingress，分布在 `ai-task-inspector` / `bmcp-control` / `nhss-job`(15) / `nhss` / `stor`(5)
- **全部** ADDRESS 指向 `10.200.99.4`，**全部**无 TLS、无 IP 白名单、无 basic auth
- 承载 SLB `nginx-ingress-lb` 的 annotation 明确 `alibaba-cloud-loadbalancer-address-type: intranet`
- 节点仅 InternalIP（`10.200.99.1/.2/.3/.5/.6`），**无 EIP**

### 3.2 判断 404 来源必须看响应头

对未匹配的 Host 发 `curl -i`：

```bash
curl -si -H 'Host: <unknown>' http://10.200.99.4/ | head -5
```

响应中**没有 `Server: nginx`**，body 是 `404 page not found`（Go 风格）——
说明这份 404 由上游应用自身返回，即**路由转发实际已成功**，不能据此判定 Ingress 未命中。

### 3.3 dynamo 服务的暴露方式

- 集群内 7 个 dynamo 相关 Service **全部是 NodePort**，无一走 Ingress
- `nhss-job/dynamo-qwen38-27b-prod-nodeport`：`http 8000:32081`
- `nhi` 命名空间原本 **0 个 Ingress**，全部 ClusterIP —— 这正是需要额外开 NodePort 的原因

---

## 4. 跨集群 ACL 探测方法（核心方法论）

ACL 是**按 `IP:端口` 精确白名单**，不是网段、不是端口段。判断三角色：

| 探测结果 | 含义 |
|---|---|
| `OPEN`（连上） | 端口有服务监听 |
| `REFUSED`（RST） | **网络可达**，仅该端口无监听 |
| `TIMEOUT`（DROP） | 被 ACL 丢弃 |

关键是**`REFUSED` 就代表网络通**——这个信号比 `OPEN` 更有用，因为它能把"没有服务"和"被拦截"区分开。

### 4.1 交叉验证法

固定一个变量做两组对照，才能排除"节点不通"和"端口被拦"的混淆：

```
同 IP 多端口（10.202.72.6）
  :22     TIMEOUT     ← 被拦
  :80     TIMEOUT     ← 被拦
  :2049   REFUSED     ← 通
  :8000   TIMEOUT     ← 被拦
  :9990   REFUSED     ← 通
  :30000  REFUSED     ← 通 ★
  :32081  TIMEOUT     ← 被拦
  :32767  TIMEOUT     ← 被拦

同端口多节点（:2049）
  10.202.72.6   REFUSED
  10.202.72.8   REFUSED
  10.202.72.20  REFUSED
  10.202.72.31  OPEN
```

结论：放通的是 `2049` / `9990` / `30000` 三个**具体端口号**。
再从 29995–30015 逐端口扫描，只有 30000 返回 RST，其余全 DROP，进一步坐实"单端口放通"。

> 探测必须在**目标侧 Pod 内**执行（`kubectl exec ... -- python3` socket 直连）。
> 本机执行会得到相反结论，因为本机走的是另一条 VPN/专线策略。
> 另外，不要用 `kubectl run busybox` 建临时探测 Pod —— 本集群私有镜像仓库拉不到公共镜像，会超时。

---

## 5. 接入实施

### 5.1 Step 1：ack-LB 侧开 NodePort 30000

配置文件：[deploy/nhi-frontend-nodeport.yaml](../deploy/nhi-frontend-nodeport.yaml)

```bash
kubectl --kubeconfig=ack-LB97i2CZNR.kubeconfig apply -f deploy/nhi-frontend-nodeport.yaml
# service/dynamo-trace-demo-frontend-nodeport created  8000:30000/TCP
# Endpoints: 172.22.10.136:8000 (ready)
```

**为什么不写进 DGD**：本 DGD 用的是 `v1beta1`，其 `spec` 只有 `components`/`podTemplate`，
**没有 `spec.services`**；只有 `v1alpha1` 才有 `spec.services` 能声明 Service 类型与 nodePort。
降级 CRD 版本需重写整个 DGD 结构且 Operator 行为不同，代价远大于一条独立 Service。
独立 Service 同样是声明式、可版本化，且**无需改动或重启 dynamo 运行时**。

### 5.2 Step 2：验证三节点均可路由

从 litellm Pod 内（注意：必须从 Pod 内测，本机不通）：

```bash
for h in 10.202.72.6 10.202.72.8 10.202.72.20; do
  curl -s --max-time 5 http://$h:30000/v1/models
done
# 三个节点均返回 {"data":[{"id":"Qwen3.8-27B",...}]}
```

### 5.3 Step 3：litellm 侧注册路由

沿用现有 `model_list` 的写法（`model_info.mode: chat` + `supported_endpoints`）。
因 `STORE_MODEL_IN_DB=True`，用管理 API 注册会写入 Postgres，重启不丢：

```bash
curl -X POST http://<litellm-host>:37400/model/new \
  -H "Authorization: Bearer $LITELLM_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model_name": "dynamo-trace-demo-Qwen3.8-27B",
    "litellm_params": {
      "model": "openai/Qwen3.8-27B",
      "api_base": "http://10.202.72.20:30000/v1",
      "api_key": "none",
      "custom_llm_provider": "openai"
    },
    "model_info": {
      "mode": "chat",
      "supported_endpoints": ["/v1/chat/completions", "/v1/responses", "/v1/messages"]
    }
  }'
```

- 管理 API 返回的 `litellm_params` 是**加密态**（`LITELLM_SALT_KEY`），无法直接回读核对，属正常
- 该模型名存在于 DB（UUID 型 id），区别于 config.yaml 来源的模型（64 位 hash 型 id）

---

## 6. 既有故障清理：Qwen3-Coder 系列

### 6.1 移除前状态

`/model/info` 显示两条失效配置，id 均为 64 位 hash（说明来自 `config.yaml` 而非 DB）：

| model_name | api_base | 实际状态 |
|---|---|---|
| `Qwen3-Coder-Next-FP8` | `http://10.202.72.6:9990/v1` | `ConnectionRefused`，调用返回 `litellm.InternalServerError: OpenAIException - Connection error` |
| `Qwen3-Coder-Probe` | `http://10.202.72.21:9990/v1` | 该节点 `i-d64r27mheobbqdu5dps0` 状态 `Unknown`(NotReady) |

根因是**节点漂移**：`igd-4090-allinone-repl` 的 router 用 `hostNetwork` 监听 9990，
Pod 迁移后所在节点变为 `10.202.72.11`，而配置里写死的 `.6` / `.21` 早已无服务。

### 6.2 4090d 现状核实（与预期不符，重要）

`nhi.zjlab.io/gpu-model=NVIDIA-GeForce-RTX-4090-D` 的节点确实在 `ack-LB` 的 `nhss-job` 命名空间内，
但逐 Pod 查启动参数后确认：**三个 4090 部署跑的都是 `Qwen2.5-7B-Instruct`，全集群已无任何 Qwen3-Coder 部署。**

| InferGraphDeployment | model-path | served-model-name | router |
|---|---|---|---|
| `igd-4090-allinone-repl` | `/models/Qwen/Qwen2.5-7B-Instruct` | `IGD-agg` | hostNetwork `:9990` |
| `igd-4090-pd-repl` | 同上 | `IGD-pd` | `:8010` → NodePort 31310 |
| `igd-4090-pd-repl-01` | 同上 | `IGD-pd` | `:8010` → NodePort 31472 |

`10.202.72.11:9990/v1/models` 实际返回 `["IGD-agg"]`。
因此"停掉 4090d 的 Qwen3-Coder 部署"已无对象可停 —— **本轮对 4090d 未做任何变更**。

### 6.3 清理操作

因两条配置纯来自 `config.yaml`（DB 内无对应记录，无需 `/model/delete`），只需改配置 + 重启：

```bash
# 1) 备份
kubectl --kubeconfig=maas.kubeconfig get cm litellm-config -n litellm-system -o json > cm-backup.json

# 2) 删除 config.yaml 中 Qwen3-Coder-Next-FP8 与 Qwen3-Coder-Probe 两个条目（含尾部空行）
#    删除后 model_list 仅剩 sglang-router-test

# 3) 应用（merge patch 只改 config.yaml，sitecustomize.py 不受影响）
kubectl --kubeconfig=maas.kubeconfig patch cm litellm-config -n litellm-system \
  --type=merge --patch-file=cm-patch.json

# 4) 重启（subPath 挂载不会热加载，必须重启）
kubectl --kubeconfig=maas.kubeconfig -n litellm-system rollout restart deploy/litellm
kubectl --kubeconfig=maas.kubeconfig -n litellm-system rollout status deploy/litellm --timeout=240s
```

**中断评估：零。** Deployment 为 `replicas: 1` + `maxSurge: 1` / `maxUnavailable: 0`，
新 Pod ready 后才删旧 Pod，旧 Pod 全程对外服务。

清理后 `/v1/models` 为 5 个名字：`021SFM-Instruct`、`DeepSeek-V4-Flash`、
`dynamo-trace-demo-Qwen3.8-27B`、`gpt-4o-mini`、`sglang-router-test`。
再调 `Qwen3-Coder-Next-FP8` 返回 `400 Invalid model name`，确认彻底移除。

> `/v1/models` 只是**已注册名单**，不等于 maas-dev 可调用清单。其中 `DeepSeek-V4-Flash`
> 来自线上配置（仅作参考），`021SFM-Instruct` 与它同日批量写入，两条后端在 maas-dev 均不可达。
> 真实可用性以 §8.1 的实测表为准。

---

## 7. 验证证据

### 7.1 端到端推理（清理配置后回归）

```
elapsed: 1.30s
model: dynamo-trace-demo-Qwen3.8-27B
finish_reason: stop
content: '\n\nCONFIG_CLEAN_OK'
usage: prompt 59 / completion 39 / total 98
```

### 7.2 trace 落库

```sql
SELECT trace_id, count() AS spans, min(start_time), max(end_time)
FROM llm_observability.llm_traces_otlp
WHERE pod_namespace='nhi' AND start_time > now() - INTERVAL 30 MINUTE
GROUP BY trace_id ORDER BY 3 DESC LIMIT 5
```

```
a844f08ba32e1c7d4a1999fcd5e8b789   32   06:24:36.099  06:24:36.925
```

单次请求 32 个 span、耗时 826ms，与端到端 1.30s（含 litellm 转发与序列化）吻合。

> **落库延迟**：Vector 的 ClickHouse sink `batch.timeout_secs: 60`，span 落库最多延迟 60s。
> 刚发完请求立刻查会返回空，需等待或放宽时间窗。

### 7.3 查询通道

`nhi` 内 Pod 无法解析 `isg.fed.clickhouse.zhejianglab.org`，必须用 IP + Host 头：

```python
import base64, urllib.request, urllib.parse, os
HOST = '10.107.159.5'
AUTH = base64.b64encode(os.environ['CH_CRED'].encode()).decode()
req = urllib.request.Request(
    f'http://{HOST}/?query=' + urllib.parse.quote(sql),
    headers={'Host': 'isg.fed.clickhouse.zhejianglab.org',
             'Authorization': 'Basic ' + AUTH})
```

---

## 8. 运维手册

### 8.1 本机直接访问 litellm（可执行手册）

本机可直连 litellm 的 NodePort，无需进集群。以下命令均在 2026-10-29 实测通过。

**接入参数**

| 项 | 值 |
|---|---|
| Endpoint | `http://10.200.44.107:37400` |
| 备用节点 | `.109` / `.111` / `.112`，同端口，四个节点 `health/liveliness` 均实测 200 |
| 鉴权 | `Authorization: Bearer $LITELLM_KEY` |
| 内容类型 | `Content-Type: application/json`（缺失返回 415） |

> **凭据不入库**：本仓库（`nhcf/dynamo`）是 **public 公开仓库**，且 `learning/` 目录已被 git 跟踪，
> 因此 master key **不得**以明文写入本文档。执行前先注入到环境变量：
> ```bash
> export KUBECONFIG=/Users/lucy/Documents/Code/gitlab/nhi-data-validator/kube-config/maas.kubeconfig
> export LITELLM_KEY=$(kubectl get secret litellm-secrets -n litellm-system \
>   -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)
> ```
> 该 key 为 litellm master key，持有它可调用全部模型，并可访问 `/model/new`、`/model/delete`
> 等管理接口——权限等同于代理商权限，切勿外传或提交到仓库。
>
> 若需要单次查看：`echo $LITELLM_KEY`。

#### 模型清单：以 maas-dev 真实配置为准（2026-10-29 实测）

`/v1/models` 返回 5 个名字，但**注册名单 ≠ 可调用清单**。按配置来源逐一核对：

| 模型 | 配置来源 | api_base | maas-dev 实测 |
|---|---|---|---|
| `dynamo-trace-demo-Qwen3.8-27B` | DB（2026-10-29 本次注册） | `http://10.202.72.20:30000/v1` | **200**，真实推理 |
| `sglang-router-test` | ConfigMap `litellm-config` → `config.yaml` 的 `model_list` | `http://sglang-router.cxd.svc.cluster.local:9990/v1` | **200**，mock echo |
| `gpt-4o-mini` | DB（2026-10-20） | `http://10.200.44.107:48765/v1` | **200**，mock echo |
| `021SFM-Instruct`（2 条） | DB（2026-10-12 批量导入，与下一条同批） | `10.107.254.26:30305` / `:32051` | **Connection refused** |
| `DeepSeek-V4-Flash` | DB（2026-10-12，**来自线上配置，仅作参考**） | `http://10.200.110.16:9990/v1` | **超时，不可达** |

结论：maas-dev 当前真正可用的只有 `dynamo-trace-demo-Qwen3.8-27B`、`sglang-router-test`、`gpt-4o-mini`。
`DeepSeek-V4-Flash` 来自线上配置、`021SFM-Instruct` 与其同日批量写入，两条都只在注册表里占位，
**不得当作本环境可用模型引用**；模型可用性一律以 maas-dev 实机配置与调用结果为准，
不以线上配置或 `/v1/models` 的返回作为依据。

核对命令（连通性必须在集群内测，本机网络策略不同会得出相反结论）：

```bash
# 1) ConfigMap 侧真实 model_list（maas-dev 当前只有 sglang-router-test 一条）
kubectl --context=maas-dev -n litellm-system get cm litellm-config \
  -o jsonpath='{.data.config\.yaml}' | sed -n '/model_list/,$p'

# 2) DB 侧注册记录（含 api_base）
curl -s http://10.200.44.107:37400/model/info -H "Authorization: Bearer $LITELLM_KEY" \
  | python3 -c 'import json,sys;[print(m["model_name"], m["litellm_params"].get("api_base")) for m in json.load(sys.stdin)["data"]]'

# 3) 从 litellm Pod 内验后端连通性（TIMEOUT=被拦或不存在，REFUSED=网络通但无服务）
kubectl --context=maas-dev -n litellm-system exec deploy/litellm -- python3 -c "
import socket
for h,p in [('10.200.110.16',9990),('10.107.254.26',30305),('10.202.72.20',30000)]:
    s=socket.socket(); s.settimeout(4)
    try: s.connect((h,p)); print(h,p,'OPEN')
    except Exception as e: print(h,p,type(e).__name__)
"
```

#### 1) 列出全部模型

```bash
curl -s http://10.200.44.107:37400/v1/models \
  -H "Authorization: Bearer $LITELLM_KEY" | python3 -m json.tool
```

#### 2) 跨集群调用 dynamo trace demo（主用例）

```bash
curl -s http://10.200.44.107:37400/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "dynamo-trace-demo-Qwen3.8-27B",
    "messages": [
      {"role": "user", "content": "只回复四个字：链路正常"}
    ],
    "max_tokens": 400
  }'
```

实测响应（HTTP 200 / 0.67s）：

```json
{
  "id": "chatcmpl-344c98b2-f064-482a-8bc1-0918a27ff2ed",
  "model": "dynamo-trace-demo-Qwen3.8-27B",
  "choices": [{
    "finish_reason": "stop",
    "message": {
      "content": "\n\n链路正常",
      "reasoning_content": "用户要求只回复四个字：\"链路正常\"。因此，我只需要输出这四个字..."
    }
  }],
  "usage": {"prompt_tokens": 58, "completion_tokens": 29, "total_tokens": 87}
}
```

#### 3) 其他单模型调用（仅列 maas-dev 实测可用的）

```bash
# sglang-router-test（config.yaml 来源，mock echo）
curl -s http://10.200.44.107:37400/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"sglang-router-test","messages":[{"role":"user","content":"hi"}],"max_tokens":100}'

# gpt-4o-mini（DB 来源，mock echo）
curl -s http://10.200.44.107:37400/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"max_tokens":100}'
```

`DeepSeek-V4-Flash` 与 `021SFM-Instruct` **不提供调用示例**：`DeepSeek-V4-Flash` 来自线上配置、
`021SFM-Instruct` 与其同批写入，两条在 maas-dev 调用的结果是超时（前者，客户端会一直等到
litellm 600s 超时）或 `500 OpenAIException - Connection error`（后者），见上表。

#### 4) 流式

```bash
curl -N http://10.200.44.107:37400/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "dynamo-trace-demo-Qwen3.8-27B",
    "messages": [{"role": "user", "content": "数到五"}],
    "max_tokens": 400,
    "stream": true
  }'
```

#### 参数与错误对照

| 现象 | 原因 |
|---|---|
| `content: null` + `finish_reason: length` | `max_tokens` 太小。`Qwen3.8-27B` 是 reasoning 模型，先产 `reasoning_content`，token 被思维链耗尽。**给到 400 以上**，或直接读 `reasoning_content` |
| `400 Invalid model name passed in model=xxx` | 模型名不匹配。必须与 `/v1/models` 返回的 id 完全一致，大小写敏感 |
| `415` | 缺 `Content-Type: application/json` |
| `401` | `Authorization` 缺失或 key 错误 |

> 每发起一次请求，ack-LB 侧约 60s 后可在 ClickHouse 查到对应 trace（Vector sink 的 `batch.timeout_secs`）。
> 单次请求实测 32 个 span，查询方法见 §7.3。

### 8.2 回退

```bash
# litellm 侧：删除跨集群模型（DB 记录）
curl -X POST http://10.200.44.107:37400/model/delete \
  -H "Authorization: Bearer $LITELLM_KEY" \
  -d '{"id":"a2a6b1c2-9f4f-4ad2-ab2a-bdea801fb7d6"}'

# ack-LB 侧：收回 30000
kubectl --kubeconfig=ack-LB97i2CZNR.kubeconfig -n nhi delete svc dynamo-trace-demo-frontend-nodeport
```

### 8.3 排查入口

| 现象 | 先看哪 |
|---|---|
| litellm 报 `Connection error` | litellm Pod 内直连 `节点IP:30000/v1/models`，确认 Endpoints ready |
| 请求通但 trace 查不到 | 等 60s；再看 `otel-collector` 的 `otelcol_exporter_send_failed_spans` |
| `/v1/models` 少了模型 | 区分来源：UUID 型 id 在 Postgres，64 位 hash 型 id 在 `config.yaml` |
| 调 `DeepSeek-V4-Flash` 超时、调 `021SFM-Instruct` 报 500 | 不是网关故障：两条都是历史遗留注册（前者来自线上配置），后端在 maas-dev 不可达（§8.1 实测表） |
| Pod 重启后配置没生效 | 挂载用的是 `subPath`，ConfigMap 变更不会热加载，必须 rollout restart |

---

## 9. 遗留风险与生产化建议

| 风险 | 说明 | 建议 |
|---|---|---|
| `api_base` 绑单节点 | 现为 `10.202.72.20`，frontend Pod 漂移即失效 | 改为同 `model_name` 的多条 deployment（`.20` / `.8` / `.6` 三节点），由 litellm `simple-shuffle` 负载；或改用内网 SLB VIP 统一入口 |
| 30000 是本项目预留 | 若他人申请重叠会造成端口冲突 | 已在 `deploy/nhi-frontend-nodeport.yaml` 注释中标注归属 |
| ACL 是单端口白名单 | 新增端口需重新申请，不能按端口段放通 | 生产化时连同端口申请一并纳入变更流程 |
| 两段 trace 不互联 | litellm 与 dynamo 的 span 分属不同集群、不同 ClickHouse，无 traceparent 传递 | 若要串联全链路，需让 litellm 把 `traceparent` 透传给后端（现 `forward_client_headers_to_llm_api: true` 已具备条件），并统一 trace 后端 |
| 动态注册与 config.yaml 双源 | DB 模型（UUID id）与 config 模型（hash id）并存，排障易混淆 | 长期看应选定单一事实来源 |

---

## 10. 复用同一 Frontend 承载 PD（2026-11-02）

### 10.1 诉求与两条被证伪的路

目标：让 PD 分离实例也走 trace 链路，但**不新增端口申请**。已知的跨集群 ACL 是 `IP:端口` 精确白名单，新端口意味着一轮外部申请，因此考虑复用已有的 30000。

| 方案 | 结论 | 证据 |
|---|---|---|
| 把 prefill/decode 加进 `dynamo-trace-demo` | **机制上不可能** | operator 准入 webhook 按**组件名集合做差集**校验拓扑不可变，`spec.components: Forbidden: component topology is immutable...components added: [decode prefill]`（[dynamographdeployment.go:887-905](../../../deploy/operator/internal/webhook/validation/dynamographdeployment.go)）；重命名组件等同一增一删，同样禁止，只有 reorder 允许；dry-run 阶段即拒绝，不会部分生效 |
| PD 单独建 DGD、跨 DGD 共享一个 Frontend | **服务发现不认** | operator 按 DGD 名注入 `DYN_NAMESPACE` / `DYN_NAMESPACE_PREFIX`，worker pod 带 `nvidia.com/dynamo-namespace` + `nvidia.com/dynamo-discovery-enabled=true`；`DYN_DISCOVERY_BACKEND=kubernetes` 下 Frontend 严格按该 label 过滤，异名 DGD 的 worker 不可见 |
| 新建承载完整拓扑的 DGD + 切 NodePort selector | **采用** | 现网已有先例：`nhss-job/dynamo-qwen38-27b-mixed-demo` 的 `frontendComponents=1`，同一 Frontend 挂 aggregated（`-agg`）与 prefill+decode（`-pd`）两种模式，靠同 DGD 内不同 model namespace 区分；Frontend 自身不带 `--namespace` |

### 10.2 实施

```bash
# 1. 备份 live 对象（含 status，仅作快照，不可直接 apply 回滚）
# 2. 从 live deepcopy 生成 mixed：4 组件（Frontend + aggregated + prefill + decode），6 卡
#    剔除 operator 私有状态 annotation（current-worker-hash-v2 / origin-version）
#    与 last-applied-configuration；同步改写 podAntiAffinity labelSelector 里的旧 DGD 名
kubectl -n nhi apply -f deploy/nhi-trace-demo-mixed-dgd.yaml   # 2m55s 到 READY=True
# 3. 验证 mixed 自身两模型就绪后，才切 30000 后端
kubectl -n nhi patch svc dynamo-trace-demo-frontend-nodeport -p \
  '{"spec":{"selector":{"nvidia.com/dynamo-namespace":"nhi-dynamo-trace-demo-mixed"}}}'
```

顺序是「先建新、验证、再切 selector」，因此旧的两个 DGD 在整个过程中 pod 未被重启（`4d3h Running`），30000 无中断。selector 三个 label 均为 DGD 身份级（不含 `pod-template-hash`），推理滚动更新不会造成 endpoint 失配。

> 命名从初稿的 `-v2` 改为 `-mixed`：live annotation key 原文就是 `nvidia.com/current-worker-hash-v2`，`-v2` 后缀会与 operator 的内部概念撞名；且该 annotation 的值 `a6ac8e74` 正是旧 aggregated deployment 名里的 hash 段，属 operator 私有状态，必须从新对象剔除。

### 10.3 验证

| 项 | 结果 |
|---|---|
| mixed `/v1/models` | 2 个模型：`Qwen3.8-27B`、`Qwen2.5-7B-Instruct` |
| PD 路径（7B） | `content='12'`，`finish=stop`，usage 42/3/45，`cached_tokens=24` |
| aggregated 路径（27B） | `content='\n\n12'` + `reasoning_content`，usage 65/39/104，`cached_tokens=64`（reasoning 模型，`max_tokens` 给小时 content 为空、`finish=length`，不是故障） |
| 跨集群 `:30000` | `10.202.72.20 / .8 / .6` 三节点全 OPEN，各自返回 2 个模型 |
| endpoint | `172.22.10.136` → `172.22.4.154`，`ready=True` |
| litellm 新路由 | `dynamo-trace-demo-Qwen2.5-7B-Instruct` 注册成功（`model_info.id` 36 位 UUID = DB 来源），端到端 3/3 成功，均返回 `7` |
| litellm 旧路由回归 | `dynamo-trace-demo-Qwen3.8-27B` HTTP OK，usage 58/24/82 |

### 10.4 trace → Kafka 守恒（回收后复测）

collector 自监控指标须 `port-forward` 到 **pod**（`svc/otel-collector` 只映射 4317，pf 到 svc 的 8888 会 exit 1）；启动 pf 后加就绪断言再采样，否则拿到的是空串而不是「无数据」。

```
A(start)     accepted=1044 sent_kafka=1044 failed=0 refused=0
B(silent)    accepted=1044 sent_kafka=1044 failed=0 refused=0   # 静默 60s，Δ=0 → 无背景噪声
C(after)     accepted=1149 sent_kafka=1149 failed=0 refused=0   # 3 次 litellm PD 请求后
D(converged) accepted=1149 sent_kafka=1149 failed=0 refused=0
```

`Δ = 1149 − 1044 = +105 = 3 × 35 spans/请求`，与交接前独立测得的单请求 span 数吻合；`accepted == sent_kafka` 且 `failed = refused = 0`，PD 链路的 span 完整推达 Kafka。

### 10.5 资源回收与回退材料

验证通过后删除 `dynamo-trace-demo` 与 `dynamo-trace-pd`，`nhi` 命名空间 GPU 占用 **12 卡 → 6 卡**，DGD 仅剩 `dynamo-trace-demo-mixed`（READY=True）。共享的 `nhi-dynamo-trace-models-pvc` 未受影响（`Bound`，`ownerReferences` 为空、PV 为 `Retain`，不随 DGD 生命周期删除）。

回退材料（三份均对 live 做过 `apply --dry-run=server` 并返回 `unchanged`，即字段级等价、可原样重建）：

| 对象 | 文件 |
|---|---|
| `dynamo-trace-demo`（aggregated，4 卡） | [deploy/nhi-dgd.json](../deploy/nhi-dgd.json) |
| `dynamo-trace-pd`（PD，2 卡） | [deploy/nhi-trace-pd-dgd.yaml](../deploy/nhi-trace-pd-dgd.yaml) |
| `dynamo-trace-demo-mixed`（当前形态，6 卡） | [deploy/nhi-trace-demo-mixed-dgd.yaml](../deploy/nhi-trace-demo-mixed-dgd.yaml) |
| 30000 入口 + selector 回退说明 | [deploy/nhi-frontend-nodeport.yaml](../deploy/nhi-frontend-nodeport.yaml) |

回退顺序：先 `kubectl apply -f deploy/nhi-dgd.json` 建回旧 DGD，再把 NodePort 的 `nvidia.com/dynamo-namespace` 选择器改回 `nhi-dynamo-trace-demo`。
