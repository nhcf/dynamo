# Dynamo source learning notes

Offline Chinese tutorials and deployment notes maintained alongside Dynamo.
The commit message and maintenance instructions use English; tutorial prose remains Chinese.

## 分类导航

### Dynamo 源码教程

- [Complete 00–12 module navigation](index.html)
- [00 System architecture and runtime shapes](system-map.html)
- [01 Observability source walkthrough](observability.html)
- [02 Frontend and protocol pipeline](frontend.html)
- [03 Runtime and communications](runtime.html)
- [04 KV-aware routing and scheduling](router.html)
- [05 Engine adapters and Rust Sidecar](backends.html)
- [06 Prefill/decode disaggregation](pd-serving.html)
- [07 KV cache and memory management](kv-cache.html)
- [08 Planner source walkthrough](planner.html)
- [Planner deployment and Frontend interaction](planner-deployment.html)
- [09 Control-plane architecture and observability](control-plane.html)
- [10 Reliability and streaming lifecycle](reliability.html)
- [11 Global routing and pool views](global-routing.html)
- [12 Verification, replay, and performance experiments](validation.html)
- [PD trace capabilities and boundaries](pd-trace.html)
- [Storage and events architecture and observability](storage-events.html)

All roadmap modules now have architecture and selected source analysis.
This is not an exhaustive file audit or a claim that every feature was runtime-tested.
Global Rust placeholders, native Sidecar capabilities, and unwired consumers are
identified separately from implemented request paths.

### 部署与链路追踪

- [ACK deployment and trace checklist](ack-LB97i2CZNR/index.html)
- [ACK 部署记录与配套文件清单](ack-LB97i2CZNR/dynamo-test-deployment.md)
- [ACK request-plane span design](ack-LB97i2CZNR/request-path-trace-design.html)
- [ACK request-plane trace walkthrough](ack-LB97i2CZNR/request-path-trace-walkthrough.html)
- [ACK Trace 采集草案与回退记录](ack-LB97i2CZNR/trace-collection-plan.md)
- [maas-dev historical platform audit](maas-dev-deployment/index.html)
- [maas-dev Higress metrics collection plan](maas-dev-deployment/higress-metrics-collection-plan.md)
- [maas-prod Higress 与 LiteLLM OTel Collector 接入说明](maas-prod/otel-trace-integration.md)
- [maas-prod LiteLLM OTel A/B 测试](maas-prod/README.md)

### 指标与 Grafana

- [Prometheus metrics classification and usage](prometheus-metrics-guide.html)
- [HiCache and Mooncake metrics and tracing](kvstore-hicache-metrics.html)
- [ACK Dynamo Grafana dashboards](ack-LB97i2CZNR/dashboard/README.md)
- [NHI 模型服务 Grafana 看板](nhi-dashboard/README.md)
- [Mooncake Master Grafana dashboard](mooncake/dashboard/README.md)

### 模型服务调优

- [模型服务持续自我改进平台设计与实施方案 V0.4](model-serving-autotuning-design-v0.1.md)
- [模型服务智能调优与持续自我改进 Issue 提案稿](model-serving-self-improvement-issue-draft.md)
- [ACK Dynamo Replay 调优闭环实施方案](ack-LB97i2CZNR/dynamo-replay-optimization-plan.md)

### SLA 日报

- [采集与日报方案](daily-report/report.md)及[生成脚本](daily-report/run_report.py)、[HTML 模板](daily-report/report-template.html)、[上传脚本](daily-report/zjic-report-upload/upload_report.py)
- 日报生成路径为 `daily-report/YYYY-MM-DD/index.html`。

### 构建与维护

- 根目录的 `build_*.py`、`validate.mjs` 和 [serve.sh](serve.sh) 分别用于重建教程、校验页面和本地预览。
- 根目录的 `.mmd` 是图源，`*source-manifest.json` 记录源码出处。maas-dev 审计目录的 JSON 快照提供报告的重建输入。
- `maintenance/` 存放本地维护草稿。

## 历史归档

### ACK

- Collector 配置快照包括[指标变更前](ack-LB97i2CZNR/archive/collector/otel-collector-config-before-metrics.yaml)、[Mixed 指标变更前](ack-LB97i2CZNR/archive/collector/otel-collector-config-before-mixed-metrics-20261102-164419.yaml)、[Trace 统一前](ack-LB97i2CZNR/archive/collector/otel-collector-config-before-trace-unify.yaml)、[11 月 3 日备份](ack-LB97i2CZNR/archive/collector/otel-collector-config-20261103-1731.json)、[端口变更前](ack-LB97i2CZNR/archive/collector/otel-collector-config-before-port-change-20261106-113003.yaml)和[端口适配后](ack-LB97i2CZNR/archive/collector/otel-collector-config-after-port-adaptive-20261106.yaml)。
- 看板历史材料包括[建板前表达式探查脚本](ack-LB97i2CZNR/archive/dashboard/validate_exprs.py)和 [P0 部署记录](ack-LB97i2CZNR/archive/dashboard/dashboard-p0-deployment-2026-11-03.json)。
- 采集方案的两份等价 DGD 快照已合并为一份[回退快照](ack-LB97i2CZNR/evidence/03-dgd-before-patch.json)。

### maas-dev

- [指标配置移除前的 Collector 备份](maas-dev-deployment/archive/collector/otel-collector-config-before-metrics-removal-20261102-170238.yaml)和[当时使用的裁剪脚本](maas-dev-deployment/archive/collector/strip-metrics.py)。

模型服务调优主设计的正文版本为 V0.4，文件名仍带 `v0.1`。Issue 提案稿是同一项目的摘要，链接到主设计。

## Evidence and version boundaries

The original observability, Planner, PD Trace, and index source excerpts are pinned to
`2f296b59274867529ea8deef994e6954520024b6`. All other source chapters use
`9734418e5bc71027e6bd04a09314bf1765208c72`, except the 2026-11-03 Planner
deployment chapter, pinned to `a908c1a14501b4bb03a7c80833edec0f0e571986`.
Compare revisions before combining claims.
Source revisions identify upstream code commits; source links avoid local
documentation-only SHAs. Builders read their pinned Git revision,
independently of the current checkout.
The `*-source-manifest.json` files and `source-manifest.json` retain excerpt paths,
line ranges, revisions, and source hashes; they are part of the documentation.

The user-provided ACK snapshot describes a private SGLang runtime tagged 1.4.1.
It is a separate evidence source from the pinned source analysis and the maas-dev audit.
The ACK YAML fragment is a merge reference, not a complete deployment manifest.
Collector addresses must be confirmed and replaced before use.

## Rebuild

Python 3 and the pinned Git objects are required. No cluster access is needed.

```sh
python3 learning/build_tutorial.py
python3 learning/build_planner.py
python3 learning/build_pd_trace.py
python3 learning/build_control_plane.py
python3 learning/build_storage_events.py
python3 learning/build_reliability.py
python3 learning/build_pd_serving.py
python3 learning/build_remaining.py
python3 learning/build_planner_deployment.py
python3 learning/maas-dev-deployment/render.py
```

The architecture and lifecycle diagram sources are adjacent `.mmd` files; the chapters
also present their architecture as offline HTML, without an external renderer.

The eight remaining-map chapters and their adjacent architecture diagrams are built
by `build_remaining.py`. Its sources are the pinned Git objects; diagram lane definitions
live in the builder. The other `.mmd` diagrams remain directly maintained.

The HTML walkthroughs and source manifests are generated artifacts. Update their
Python builders and regenerate them instead of editing the outputs directly.
The ACK landing page, deployment snapshot, checklist, YAML fragment, span-design page, walkthrough, and collector reference are maintained directly.

## Browser validation

Requires Node.js 22+ and Chrome. Set `CHROME_BIN` if Chrome is not installed at
`/opt/google/chrome/chrome`.

```sh
node learning/validate.mjs observability.html
node learning/validate.mjs planner.html
node learning/validate.mjs pd-trace.html
node learning/validate.mjs control-plane.html
node learning/validate.mjs storage-events.html
node learning/validate.mjs reliability.html
node learning/validate.mjs pd-serving.html
node learning/validate.mjs planner-deployment.html
for page in system-map frontend runtime router backends kv-cache global-routing validation; do
  node learning/validate.mjs "$page.html" || exit 1
done
```

The validator checks rendering, source counts, navigation, interactive controls,
and desktop/mobile widths. It removes its temporary Chrome profile and prints
results to stdout instead of retaining reports or screenshots in the repository.
These checks do not run inference, exercise Kubernetes, or prove deployment readiness.

The maas-dev JSON snapshots are inputs to its report renderer. Keep them with
the report. `collect.py` is a separate, explicit live read-only collection tool;
do not run it as part of an offline documentation rebuild.

## Repository hygiene

Keep published documents, reproducible builders, validators, and required evidence.
Do not commit Python caches, browser profiles, screenshots, generated validation
reports, transient graph coverage diagnostics, or superseded drafts.
Keep unpublished skillpack issue drafts local; they are ignored under `maintenance/`.
