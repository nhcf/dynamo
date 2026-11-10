# ack-LB nhss-job Grove/InferGraphDeployment 运维脚本

针对 ack-LB97i2CZNR 集群 `nhss-job` 命名空间下的 `igd-*` 部署。这类部署不是
`deploy/` 里的 nhi `DynamoGraphDeployment`，而是自定义 CRD
`nanhu-tech.io/InferGraphDeployment`，经 NVIDIA Grove 落地：

```
InferGraphDeployment(nanhu-tech.io)
  -> PodCliqueSet(grove.io)
    -> [PodCliqueScalingGroup]   # worker 这类可伸缩组
      -> PodClique
        -> Pod                   # planner / router / worker 各一个容器
```

## set-igd-env.sh

幂等地给某个 InferGraphDeployment 的组件容器增/改 env。

```bash
scripts/set-igd-env.sh \
  --kubeconfig <ack-LB kubeconfig> \
  --namespace nhss-job --igd igd-142-agg --components router,worker \
  --set DYN_REQUEST_TRACE=1 \
  --set DYN_REQUEST_TRACE_RECORDS=request_end \
  --set DYN_REQUEST_TRACE_SINKS=stderr \
  --set DYN_LOGGING_CONSOLE_FORMAT=jsonl \
  --watch
```

| 参数 | 说明 |
|---|---|
| `--namespace / --igd / --components` | 目标 InferGraphDeployment 与要改的组件（逗号分隔，取自 `spec.replicaSpecs` 的键） |
| `--set K=V` | 可重复；env 值一律按字符串写入 |
| `--dry-run` | 只做 server-side dry-run + 打印每个组件 `旧 -> 新` env 数，不落库 |
| `--watch` | apply 后轮询命中组件 Pod 重建到 `1/1`（最长 15 分钟） |
| `--no-verify` | 跳过 apply 后对 live igd 与运行中 Pod env 的核验 |
| `--kubeconfig` | 不传则用 `$KUBECONFIG`，再不行用默认 context（ack-LB 需显式指定） |

支持 `--opt VAL` 与 `--opt=VAL` 两种写法。脚本幂等：重复施加同名 env 不会增长。

## 为什么这样改（三个坑）

1. **落点**：运行中 Pod 的容器 env 不可变；直接 `patch PodClique` 会被
   `grove.io/pod-template-hash` 回滚。唯一持久且正确的位置是
   `InferGraphDeployment.spec.replicaSpecs.<component>.env`，controller 会往下
   传播。脚本内置了"读 live → 生成完整 env 列表 → server-side dry-run → apply
   → 核验"的流程。
2. **env 数组会被整体替换**：CRD 无 strategic-merge key，`kubectl patch
   --type=merge` 对数组是替换而非合并。脚本先读原 env、按 name 去掉同名旧值、
   再追加新值，拼成完整列表下发，避免误删 `DYN_ROUTER_*` / `DYN_FPM_*` 等既有 env。
3. **会重建 Pod**：命中组件的 Pod 被替换，**worker 是 GPU Pod、重建会重新加载模型**
   并有短暂中断。线上执行前先向用户确认。

## request trace 到 stderr（本脚本的一次实际用途）

给 router/worker 下发以下 env 后，Dynamo 会初始化 request-trace 并把记录写到 stderr：

```
DYN_REQUEST_TRACE=1
DYN_REQUEST_TRACE_RECORDS=request_end
DYN_REQUEST_TRACE_SINKS=stderr
DYN_LOGGING_CONSOLE_FORMAT=jsonl
```

生效判据（router 启动日志）：

```
INFO dynamo_llm::request_trace: Request trace initialized ... sinks=["stderr"] records=["request_end"]
```

注意：`request_end` 结构化记录只在有真实推理请求走完后才落 stderr；仅有启动日志、
无流量时看不到记录属正常。`planner` 组件不处理推理请求，给它加这组 env 不会产出 trace，
通常只下发 `router,worker` 即可。
