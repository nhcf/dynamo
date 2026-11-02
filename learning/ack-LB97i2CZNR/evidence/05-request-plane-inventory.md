# Dynamo Request Plane 实例清单（采集范围确认用）

> 采集时间：2026-10-29
> 集群：`ack-LB97i2CZNR`，namespace `nhss-job`
> 依据：`dynamo-qwen38-27b-mixed-demo-frontend` 的 `/health` 实时输出 + `kubectl get pods -o wide`
> 规模：14 个实例 IP，74 个 endpoint，transport 全部 `tcp`，codec 全部 `msgpack`

---

## 1. 本 DGD 控制的 Pod（可自行改配置）

| # | Pod | Pod IP | 角色 | endpoint 数 | 当前 OTEL_* | 本次动作 |
|---|---|---|---|---|---|---|
| 1 | `dynamo-qwen38-27b-mixed-demo-frontend-6b548b946d-wqf2x` | `172.22.16.9` | Frontend（入口 + KV router） | 3 | 无 | **阶段 1 改** |
| 2 | `dynamo-qwen38-27b-mixed-demo-prefill-76c417b5-84d5fb74f8-dxkql` | `172.22.18.82` | prefill worker | 6 | 无 | 阶段 2（暂缓） |
| 3 | `dynamo-qwen38-27b-mixed-demo-decode-76c417b5-d5bdf49cb-fnwnb` | `172.22.17.22` | decode worker | 6 | 无 | 阶段 2（暂缓） |
| 4 | `dynamo-qwen38-27b-mixed-demo-aggregated-76c417b5-85b9564c8pqs5l` | `172.22.8.199` | aggregated worker | 6 | 无 | 阶段 2（暂缓） |

四者同属 `nvidia.com/dynamo-graph-deployment-name=dynamo-qwen38-27b-mixed-demo`，
容器名统一 `main`，全部 `Running` / 0 重启。

> **注意第 1 行**：Frontend 自己也在 request plane 上注册了 endpoint（`component` 显示 `backend,prefill`，
> 覆盖 `...-agg-76c417b5` 与 `...-pd-76c417b5` 两个 namespace）。它不是纯客户端。

---

## 2. 同一 request plane 上的外部 Deployment（不归本 DGD 管，但请求会打过去）

以下 10 个实例全部被本 DGD 的 Frontend 通过 discovery 发现，**随时可能成为一次真实请求的落点**。

| # | Pod | Pod IP | 角色 | endpoint 数 | 归属 namespace | 备注 |
|---|---|---|---|---|---|---|
| 5 | `igd-142-agg-0-worker-0-worker-v2l7k` | `172.22.18.111` | worker | 6 | `igd-142-agg` | |
| 6 | `igd-142-agg-0-router-88fk7` | `172.22.9.67` | router | 1 | `igd-142-agg` | NodePort `172.23.159.229:8000` 对外的入口 |
| 7 | `igd-142-agg-mn-0-worker-0-worker-ldr-8mlrx` | `172.22.4.43` | worker | 6 | `igd-142-agg-mn` | |
| 8 | `igd-142-agg-mn-0-worker-0-worker-wkr-lttzd` | — | worker | 0 | `igd-142-agg-mn` | **Pod 在跑但未注册到 discovery** |
| 9 | `igd-142-agg-mn-0-router-smz96` | `172.22.9.52` | router | 1 | `igd-142-agg-mn` | |
| 10 | `igd-142-mixed-0-worker-0-worker-f9jjb` | `172.22.12.8` | aggregated | 6 | `igd-142-mixed` | |
| 11 | `igd-142-mixed-0-prefill-0-prefill-ls7bh` | `172.22.15.14` | prefill | 6 | `igd-142-mixed` | |
| 12 | `igd-142-mixed-0-decode-0-decode-g9xc9` | `172.22.4.44` | decode | 5 | `igd-142-mixed` | |
| 13 | `igd-142-mixed-0-router-bsnbk` | `172.22.9.51` | router | 3 | `igd-142-mixed` | 同时代理 aggregated/backend/prefill |
| 14 | `igd-4090-allinone-repl-0-worker-0-worker-hkrrr` | `10.202.72.11` | worker | 11（与 15 共用） | `igd-4090-allinone-repl` | **hostNetwork**，IP 与 #15 冲突 |
| 15 | `igd-4090-pd-repl-0-decode-0-decode-m9jnd` | `10.202.72.11` | decode | 11（与 14 共用） | `igd-4090-pd-repl` | 同上 |
| 16 | `igd-4090-pd-repl-0-prefill-0-prefill-cvhjh` | `10.202.72.16` | prefill | 6 | `igd-4090-pd-repl` | |
| 17 | `igd-4090-pd-repl-0-router-fkhdd` | `10.202.72.13` | router | 2 | `igd-4090-pd-repl` | |

### 清单里有 Pod、但不在这条 request plane 上的

| Pod | 原因 |
|---|---|
| `igd-4090-pd-repl-01-0-prefill-0-prefill-2djkl` (`10.202.72.1`) | `/health` 中无注册，未参与路由 |
| `igd-4090-pd-repl-01-0-decode-0-decode-52fnd` (`10.202.72.35`) | 同上 |
| `igd-4090-pd-repl-01-0-router-285vv` (`10.202.72.35`) | 同上 |
| `igd-*-planner-*`（4 个） | Planner 是控制面，不在 request plane |
| `ji-aionlineinferenceservice-qwen7b-*`（4 个） | 未被本 Frontend 发现（不同 discovery 作用域） |
| `ji-legacy-upgrade-20260921-*`（2 个） | 同上 |
| `ji-jupyter-*`（8 个） | 开发环境，与推理无关 |
| `curl-test` | ImagePullBackOff |

---

## 3. 对采集范围的直接影响

### 3.1 只改 Frontend 时，能覆盖到哪里

```
Frontend(172.22.16.9) ──tcp/msgpack──► 落到 14 个候选实例中的任意一个
     [span 有]                              [span 无，除非对方也接入]
```

- ✅ Frontend 自身的 handler span、`generate` 派发 span、TCP 出口 span：**全部可得**
- ❌ 落在 #2~#17 任意一个上的入口 span：**不可得**
- 结论：trace 会**断在 Frontend 的出口**，这是本次交付的预期边界

### 3.2 改动量的准确答案

| 范围 | Pod 数 | 重建影响 |
|---|---|---|
| 阶段 1（本次） | **1**（仅 Frontend） | 零中断，滚动更新，`maxSurge=1/maxUnavailable=0` + readiness `/health` |
| 阶段 2（若要连成完整 trace） | 3（本 DGD worker） | 各自 4 卡重载 Qwen3.8-27B，服务中断数分钟 |
| 无法自行覆盖 | 13 个外部实例 | 需 nanhu 团队配合，或改 discovery 作用域 |

### 3.3 两个会干扰验收的现象

1. **`10.202.72.11` 被两个 Pod 共用**（hostNetwork）。即使对方接入 OTEL，这个 IP 的 span 也无法靠 IP 区分归属，
   必须依赖 `k8sattributes` 的 `k8s.pod.name`。**这是坚持走 Collector 而非直连 Vector 的又一个理由。**
2. **`igd-142-agg-mn` 的 `wkr-lttzd`** Pod 在跑但未注册，说明该 Deployment 的 discovery 状态不一致，
   排查落点时不要以 Pod 列表为准，要以 `/health` 的 instances 为准。

---

## 4. 待确认

- [ ] 阶段 1 只改 `#1 Frontend`，是否认可？
- [ ] `#5~#17` 这 13 个外部实例，本次是否需要去协调对方接入？（建议：不做，跨团队，且会影响他人服务）
- [ ] `igd-4090-pd-repl-01` 那 3 个未注册的 Pod，是废弃残留还是待上线？会影响后续阶段 2 的落点分析。
