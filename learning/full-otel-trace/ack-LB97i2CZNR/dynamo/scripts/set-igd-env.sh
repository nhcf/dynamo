#!/usr/bin/env bash
# set-igd-env.sh — 幂等地给 Grove/InferGraphDeployment 的组件容器增/改 env。
#
# 背景（本集群 ack-LB97i2CZNR，nhss-job 命名空间的 igd-*）：
#   InferGraphDeployment(nanhu-tech.io) -> PodCliqueSet(grove.io)
#     -> [PodCliqueScalingGroup] -> PodClique -> Pod
#   运行中 Pod 的容器 env 不可变，直接 patch PodClique 会被
#   grove.io/pod-template-hash 回滚，所以唯一正确的落点是
#   InferGraphDeployment.spec.replicaSpecs.<component>.env，
#   由 controller 往下传播并重建 Pod（GPU worker 重建会重载模型）。
#
# 关键坑（脚本已处理）：CRD 没有 strategic-merge key，且 replicaSpec.env 是
#   数组；`kubectl patch --type=merge` 会【整体替换】该数组。因此必须读取原
#   env、拼成"原有 + 新增(按 name 去重)"的完整列表再下发，否则误删既有 env。
#
# 副作用：修改会触发命中组件的 Pod 重建，worker 是 GPU Pod、会重新加载模型。
#   线上执行前先向用户确认。
#
# 用法:
#   scripts/set-igd-env.sh --namespace nhss-job --igd igd-142-agg \
#     --components router,worker \
#     --set DYN_REQUEST_TRACE=1 \
#     --set DYN_REQUEST_TRACE_RECORDS=request_end \
#     --set DYN_REQUEST_TRACE_SINKS=stderr \
#     --set DYN_LOGGING_CONSOLE_FORMAT=jsonl \
#     --watch
#
#   --kubeconfig PATH  不传则用环境变量 KUBECONFIG，再不行用默认 context
#   --dry-run          只做 server-side dry-run + 打印计划，不落库
#   --watch            apply 后轮询命中组件 Pod 重建到 1/1（最长 15 分钟）
#   --no-verify        跳过 apply 后对 live igd + 运行中 Pod env 的核验
set -euo pipefail

log() { echo "[set-igd-env] $*" >&2; }

NS="" ; IGD="" ; COMPS="" ; WATCH=0 ; VERIFY=1 ; DRYRUN=0 ; KC=""
declare -a SETS=()

# 同时支持 --opt VAL 与 --opt=VAL：先把带等号的展开成两个参数
defs=()
for a in "$@"; do
  case "$a" in
    --*=*) defs+=("${a%%=*}" "${a#*=}") ;;
    *)     defs+=("$a") ;;
  esac
done
set -- "${defs[@]}"

while [ $# -gt 0 ]; do
  case "$1" in
    --namespace)  NS="$2"; shift 2 ;;
    --igd)        IGD="$2"; shift 2 ;;
    --components) COMPS="$2"; shift 2 ;;
    --set)        SETS+=("$2"); shift 2 ;;
    --kubeconfig) KC="$2"; shift 2 ;;
    --watch)      WATCH=1; shift ;;
    --no-verify)  VERIFY=0; shift ;;
    --dry-run)    DRYRUN=1; shift ;;
    -h|--help)    sed -n '2,35p' "$0"; exit 0 ;;
    *) log "未知参数: $1"; exit 2 ;;
  esac
done

{ [ -n "$NS" ] && [ -n "$IGD" ] && [ -n "$COMPS" ] && [ "${#SETS[@]}" -gt 0 ]; } || {
  log "缺少 --namespace/--igd/--components/--set"; exit 2; }

KKA=()
if   [ -n "$KC" ];          then KKA=(--kubeconfig="$KC")
elif [ -n "${KUBECONFIG:-}" ]; then KKA=(--kubeconfig="$KUBECONFIG")
fi

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
IGD_JSON="$TMP/igd.json"; PATCH="$TMP/patch.json"; WANT="$TMP/want.txt"

# 1) 读取 live igd，构建"原有 env + 新增(去重)"完整列表，生成 merge patch
kubectl "${KKA[@]}" -n "$NS" get infergraphdeployment "$IGD" -o json > "$IGD_JSON"

# 把 SETS 写成 KEY\tVALUE 每行一条，交给 python 读，避免命令行嵌套引号
: > "$TMP/sets.tsv"
for kv in "${SETS[@]}"; do printf '%s\t%s\n' "${kv%%=*}" "${kv#*=}" >> "$TMP/sets.tsv"; done

NS_="$NS" IGD_="$IGD" COMPS_="$COMPS" \
python3 - "$IGD_JSON" "$PATCH" "$TMP/sets.tsv" "$WANT" <<'PY'
import json, os, sys
igd_path, patch_path, sets_path, want_path = sys.argv[1:5]
comps = os.environ["COMPS_"].split(",")
new, names = [], []
for line in open(sets_path):
    line = line.rstrip("\n")
    if not line:
        continue
    k, v = line.split("\t", 1)
    new.append({"name": k, "value": str(v)})   # env value 必须是字符串
    names.append(k)
nn = set(names)
rs = json.load(open(igd_path))["spec"]["replicaSpecs"]
patch = {"spec": {"replicaSpecs": {}}}
for c in comps:
    if c not in rs:
        sys.exit(f"replicaSpecs 里没有组件 {c}；现有: {list(rs.keys())}")
    existing = rs[c].get("env", []) or []
    kept = [e for e in existing if e.get("name") not in nn]
    patch["spec"]["replicaSpecs"][c] = {"env": kept + new}
    print(f"[plan] {c}: {len(existing)} -> {len(kept)+len(new)} env", file=sys.stderr)
json.dump(patch, open(patch_path, "w"), ensure_ascii=False)
open(want_path, "w").write("\n".join(names) + "\n")
PY

# 2) server-side dry-run（同时验证 CRD schema，如 planner 之前无 env 字段）
log "server-side dry-run 校验 ..."
kubectl "${KKA[@]}" -n "$NS" patch infergraphdeployment "$IGD" \
  --type=merge --patch-file="$PATCH" --dry-run=server >/dev/null
log "dry-run 通过。"
if [ "$DRYRUN" = 1 ]; then log "--dry-run：不下发。"; exit 0; fi

# 3) 正式下发
kubectl "${KKA[@]}" -n "$NS" patch infergraphdeployment "$IGD" \
  --type=merge --patch-file="$PATCH" >/dev/null
log "已下发到 InferGraphDeployment/$IGD。"

# 组件名 -> pod 名匹配正则（worker/router/planner 等）
POD_RE=$(echo "$COMPS" | sed 's/,/|/g')

# 4) 可选：轮询命中组件 Pod 重建到 1/1
if [ "$WATCH" = 1 ]; then
  log "等待命中组件 Pod 重建（GPU worker 会重载模型，最长 15 分钟）..."
  for i in $(seq 1 45); do
    LINE=$(kubectl "${KKA[@]}" -n "$NS" get pods --no-headers 2>/dev/null \
           | grep -E -- "-($POD_RE)" | awk '{printf "%s:%s ", $1, $2}')
    echo "  poll#$i $LINE" >&2
    if [ -n "$LINE" ] && ! grep -q '0/1' <<<"$LINE"; then log "重建完成。"; break; fi
    sleep 20
  done
fi

# 5) 核验 live igd 与运行中 Pod 的 env
if [ "$VERIFY" = 1 ]; then
  log "核验 live igd replicaSpecs env:"
  WANT="$WANT" COMPS_="$COMPS" python3 -c "
import json, os, sys
want = [w for w in open(os.environ['WANT']).read().split() if w]
rs = json.load(sys.stdin)['spec']['replicaSpecs']
for c in os.environ['COMPS_'].split(','):
    have = {e['name'] for e in (rs[c].get('env', []) or [])}
    print(f'  {c}: 在位={[n for n in want if n in have]} 缺失={[n for n in want if n not in have]}')
" < <(kubectl "${KKA[@]}" -n "$NS" get infergraphdeployment "$IGD" -o json)

  log "核验运行中 Pod env:"
  for p in $(kubectl "${KKA[@]}" -n "$NS" get pods --no-headers 2>/dev/null \
             | grep -E -- "-($POD_RE)" | grep '1/1' | awk '{print $1}'); do
    WANT="$WANT" python3 -c "
import json, os, sys
want = [w for w in open(os.environ['WANT']).read().split() if w]
p = json.load(sys.stdin)
env = {e['name']: e.get('value') for ct in p['spec']['containers']
       for e in ct.get('env', []) if e['name'] in want}
print(f\"  {p['metadata']['name']}: {env}\")
" < <(kubectl "${KKA[@]}" -n "$NS" get pod "$p" -o json)
  done
fi

log "完成。"
