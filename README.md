<h1 align="center" style="border-bottom: none">
    <a href="" target="_blank">
        <img alt="NanHuInfer" src="docs/images/word-logo.png">
    </a>
</h1>

<div align="center" style="line-height: 2;">
  [<a href="/README.md">English</a>] | [<a href="/README_CN.md">中文(简体)</a>]
  <br>

  | **[Roadmap](https://github.com/nanhu-tech/NanHuInfer/issues/1)** | **[Merge rule](https://github.com/nanhu-tech/NanHuInfer/issues/4)** | **[Docs](https://github.com/nanhu-tech/NanHuInfer/blob/main/docs/README.md)** | **[Design Proposals](https://github.com/nanhu-tech/NanHuInfer/blob/main/docs/architecture.md)** |
  <br>

  [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
  &nbsp;&nbsp;
  ![Supported Engines](https://img.shields.io/badge/engines-Dynamo%20%7C%20Sglang%20%7C%20Vllm-purple.svg)
  &nbsp;&nbsp;
  ![Architecture](https://img.shields.io/badge/architecture-P%2FD%20Disaggregation-orange.svg)
  &nbsp;&nbsp;
  ![Project Status](https://img.shields.io/badge/status-Stable-green.svg)
  [![Artifact Hub](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/nanhu-infer)](https://artifacthub.io/packages/search?repo=nanhu-infer)
</div>

<p align="center">NanHuInfer is an open-source project for an inference framework with <b>high throughput, low latency, and high efficiency</b>, designed <b>for domestic inference clusters</b>. Based on the open-source project <a href="https://github.com/ai-dynamo/dynamo/tree/v1.4.2"><b>Dynamo v1.4.2</b></a>, it enables cloud-based deployment and management of large model inference clusters, implements the P/D disaggregation architecture on domestic computing power clusters, and supports intelligent scheduling of request messages to enhance the performance and cost optimization of inference systems.</p>

## Upstream Foundations

NanHuInfer started evolving from **[NVIDIA Dynamo v1.4.2](https://github.com/ai-dynamo/dynamo/tree/v1.4.2)**, extending it with domestic accelerator support, inference scheduling, and platform integration. This version identifies the starting point of development; it does not imply that the current code is identical to that upstream release.

**KV Cache functionality builds on SGLang HiCache and Mooncake**: HiCache provides hierarchical KV Cache management, while Mooncake participates as cache storage and transfer infrastructure. The SGLang adapter integrates cache capacity and runtime metadata. The retained Dynamo KV Block Manager sources (`lib/kvbm-*`) are separate from this integration path.

## Core Features

### 🚀 Cloud-Based Deployment and High Availability of Inference Clusters

Leveraging cloud-native technologies and Custom Resource Definitions (CRDs), it enables automated management of inference service creation, scaling, monitoring, and disaster recovery. Based on the custom resource type `InferGraphDeployment`, it automatically creates and manages a series of native Kubernetes resources. A hierarchical fault tolerance mechanism is implemented to maintain traffic switching for faulty services or enable rapid recovery.

### 🏁 Deployment across Heterogeneous Accelerator Clusters

The NanHuInfer suite can be deployed on a Kubernetes cluster containing multiple accelerator types. A Runtime release is built as separate images for each accelerator software stack, while each serving deployment and the workers registered under its Router remain homogeneous.

### 🖥️ QoS-Aware Intelligent Routing and Load Balancing

The intelligent routing component optimizes the inference performance of large language models through a dual-strategy approach. It conducts intelligent analysis based on request features to dynamically route queries to worker nodes with the highest cache data relevance. Meanwhile, it implements dynamic load balancing by incorporating real-time utilization metrics (including memory occupancy rate, computing load, etc.) reported by worker nodes.

### ✨ Inference Engine Optimization

It focuses on optimizing the performance of modern inference engines (e.g., vLLM) at the request-resource scheduling level. Through collaborative scheduling optimization in terms of data and resource dimensions, it enhances the user-expected latency achievement rate of the inference engine.

### 📝 Domestic Computing Power Resource Profiling Test

In light of the characteristics of domestic acceleration cards, it completes the computing power profiling of domestic computing cards.

## Software Architecture

NanHuInfer is divided into three responsibility planes. A plane defines a system responsibility and dependency boundary; it does not map one-to-one to a programming language or repository directory:

- **Resource orchestration control plane (Go):** the Operator reconciles Kubernetes/Grove resources and topology, while the existing Go Planner temporarily preserves the compatibility scaling loop.
- **Component and strategy plane (Python):** hosts the Frontend, Router, Planner, Profiler, Replay, and Common for API ingress, component composition, and policy expression.
- **Serving Runtime and inference execution plane (Rust + SGLang + accelerators):** contains the Rust Runtime, SGLang Adapter/workers, and accelerators; it provides discovery, endpoints, request streams, events, and lifecycle, executes model computation, owns real KV state, and transfers KV tensors.

FPM, KV Events, Prometheus, and KV tensors are cross-plane data paths rather than additional control planes. Accelerator-specific image variants are managed through the same resource orchestration control plane.

<p align="center">
  <img src="./docs/images/nanhuinfer-readme-overview.svg" alt="NanHuInfer three-plane architecture: Go resource orchestration, Python components and strategy, and a Serving Runtime and inference execution plane containing the Rust Runtime, SGLang workers, and accelerators" width="900" />
</p>

**[Architecture Deep Dive →](docs/architecture.md)**

### Core Components

*   **Cloud-Based One-Click Deployment and Management**: Enables partitioned deployment in general computing zones and intelligent computing zones based on the CRD approach

    *   Provides one-click deployment of inference clusters and supports scaling capabilities

    *   Supports fault-tolerant inference capabilities between instances

*   **Support Framework**: The intelligent routing component optimizes the inference performance of large language models through a two-layer strategy

    *   Reinforcement learning assignment algorithm based on state feedback

    *   Predictive scheduling without state feedback

*   **Inference Engine with Multi-Backend Support**: Supports multiple backend inference engines (e.g., vLLM, TensorRT-LLM, SGLang, etc.)

    *   Frontend: Supports multiple modes of requests.

    *   Processor: Responsible for intelligent scheduling and load balancing, undertakes frontend requests, serves as an intelligent routing hub, and assigns tasks to appropriate backend worker nodes.

    *   Worker Node: Encapsulates the inference engine logic of vllm, sglang, and other types.

## Repository Architecture

The repository groups resource orchestration, Python components, runtime libraries, and platform services. The tree below shows two directory levels with a description for every entry. `components/src/dynamo/` is treated as a single first-level entry, with all its immediate subdirectories listed at the second level; the intermediate `components/` and `src/` wrappers are collapsed. Submodules are included; Git metadata, local tool state, caches, build outputs, and empty directories are omitted.

```text
NanHuInfer/
├── .agent/  # Repository agent skills
│   └── skills/  # Task-specific automation skills
├── .cargo/  # Rust build settings (Tokio cfg and static PCRE2)
├── .devcontainer/  # Development container configuration
├── .github/  # GitHub collaboration and CI configuration
│   ├── ISSUE_TEMPLATE/  # Issue templates
│   ├── perf-gate/  # Performance gate configuration and runners
│   └── workflows/  # Build, copyright and performance workflows
├── benchmarks/  # Dynamo performance benchmarks, load generators and trace tools
│   ├── burstgpt_loadgen/  # BurstGPT workload conversion
│   ├── frontend/  # Frontend performance benchmarks and profiling scripts
│   ├── incluster/  # Kubernetes benchmark Job manifests
│   ├── mocker/  # Mock engine concurrency and callback benchmarks
│   ├── multimodal/  # Multimodal workloads, media tools and benchmark sweeps
│   ├── nat_trace/  # NeMo Agent Toolkit trace conversion for workload replay
│   ├── omni/  # Omni model benchmark workloads
│   ├── prefix_data_generator/  # Prefix-aware workload analysis and synthesis
│   ├── request_trace/  # Request trace conversion to Perfetto format
│   ├── router/  # KV routing, prefix reuse and priority benchmarks
│   └── sin_load_generator/  # Sinusoidal load generation
├── components/src/dynamo/  # Python components and inference backend adapters
│   ├── common/  # Shared configuration, metrics and component utilities
│   ├── frontend/  # HTTP API ingress and request pre/post-processing
│   ├── global_planner/  # Global capacity planning and scaling coordination
│   ├── global_router/  # Request routing across inference pools
│   ├── kv_dc_relay/  # Data-center KV event relay and cache summaries
│   ├── mocker/  # Simulated inference workers
│   ├── planner/  # Worker capacity planning and scaling policies
│   ├── profiler/  # Inference performance profiling and SLA analysis
│   ├── replay/  # Request trace replay and reporting
│   ├── router/  # KV-aware request routing entrypoint
│   ├── sglang/  # SGLang integration, including HiCache/Mooncake metadata
│   ├── squeeze_evolve/  # Experimental multi-model evolutionary orchestration
│   ├── thunderagent_router/  # Agent workload routing and capacity management
│   ├── tokenspeed/  # TokenSpeed inference backend adapter
│   ├── trtllm/  # TensorRT-LLM inference backend adapter
│   └── vllm/  # vLLM inference backend adapter
├── container/  # Core image build entrypoints
│   ├── templates/  # Rendered Dockerfile source templates
│   ├── deps/  # Framework dependencies and vendor patches
│   └── tests/  # Container build and protocol checks
├── deploy/  # Go resource orchestration control plane
│   ├── operator/  # Kubernetes Operator, multi-stage image build, CRDs and deployment resources
│   └── planner/  # Compatibility capacity planner and scaling loop
├── docs/  # Architecture and project documentation
│   ├── analysis/  # Platform and request analysis
│   ├── images/  # Documentation diagrams and logos
│   ├── keps/  # Design proposals and enhancement plans
│   ├── maas/  # Model service platform documentation
│   └── roadmap/  # Roadmap materials
├── lib/  # Runtime libraries, bindings and supporting services
│   ├── backend-common/  # Shared backend utilities
│   ├── bench/  # Benchmarking and request trace tools
│   ├── bindings/  # Python, C and KV block manager bindings
│   ├── data-gen/  # Synthetic workload data generation
│   ├── gpu_memory_service/  # GPU memory sharing and lifecycle service
│   ├── kv-hashing/  # KV block hashing
│   ├── kv-router/  # KV indexing and routing algorithms
│   ├── kvbm-common/  # KV block manager shared types
│   ├── kvbm-config/  # KV block manager configuration
│   ├── kvbm-consolidator/  # KV block consolidation
│   ├── kvbm-engine/  # KV block manager engine integration
│   ├── kvbm-kernels/  # KV block transfer and processing kernels
│   ├── kvbm-logical/  # Logical KV block management
│   ├── kvbm-physical/  # Physical KV storage management
│   ├── llm/  # LLM request processing and serving primitives
│   ├── memory/  # Memory allocation and transfer abstractions
│   ├── mocker/  # Inference simulation and scheduling models
│   ├── rl/  # Reinforcement learning worker discovery API
│   ├── runtime/  # Distributed runtime, discovery and communication
│   ├── sidecar/  # Backend sidecars and communication protocols
│   ├── tokens/  # Token data structures and utilities
│   └── truthy/  # Shared boolean flag parsing
├── platform/  # Product and platform services
│   ├── automodelpulse/  # Model evaluation, inspection and task scheduling
│   ├── batchFlow/  # Batch inference service and monitor source
│   ├── global-planner/  # Platform global planning and observability service
│   ├── higress-nanhu/  # Higress gateway integration submodule
│   ├── litellm-nanhu/  # LiteLLM integration submodule
│   ├── maas-controller/  # Platform controllers, routing synchronization and metering
│   └── manager/  # Management backend and APIs
├── recipes/  # Model deployment recipes and tuning configurations
│   ├── templates/  # Templates for future accuracy and performance evaluation records
│   ├── 021-1188b-a50b-20261030/  # 1188B A100 deployment and PP5/PP8 benchmarks
│   ├── 021-236b-a21b-instruct-0212-rl-new/  # A100 vLLM deployment and benchmarks
│   ├── 021-236b-a21b-256k/  # A100 SGLang deployment, FA2 fixes and performance optimization
│   ├── 021-236b-a21b-reasoning-0630/  # A100 SGLang deployment and benchmarks
│   ├── deepseek-v4-flash/  # DeepSeek-V4-Flash aggregate and disaggregated deployment records
│   ├── deepseek-v4-flash-0731/  # DeepSeek-V4-Flash-0731 V5000 and W64 deployment records
│   ├── deepseek-v4-pro-0813/  # DeepSeek-V4-Pro-0813 deployment and benchmark records
│   ├── deepseek-v4.1-flash/  # DeepSeek-V4.1-Flash deployment and existing optimization A/B records
│   ├── deepseek-v4.1-flash-1017/  # Separate DeepSeek-V4.1-Flash-1017 L3 deployment records
│   ├── glm-5.1/  # GLM-5.1 V5000 DP-aware deployment and patch
│   ├── glm-5.2/  # GLM-5.2 V5000 SGLang and W64 vLLM benchmarks
│   ├── glm-5.3/  # GLM-5.3 aggregate and L3 Mooncake deployment records
│   ├── glm-5.3-flash/  # Separate GLM-5.3-Flash deployment and benchmarks
│   ├── kimi-2.6/  # Kimi-2.6 V5000 speculative decoding tuning and patch
│   ├── kimi-k3/  # Kimi-K3 aggregate and L3 Mooncake deployment records
│   ├── qwen3.6-35b-a3b/  # Qwen3.6 RTX 4090 D Kubernetes and Docker recipes
│   └── qwen3.8-27b/  # Qwen3.8-27B aggregate and L3 Mooncake benchmarks
├── scripts/  # Build, deployment and experiment scripts
│   ├── 3.4-het-ai-inference/  # Heterogeneous inference experiments and large-scale tests
│   ├── build/  # Build scripts
│   └── deploy/  # Deployment and teardown scripts
└── tests/  # Dynamo cross-component integration, end-to-end and environment checks
    ├── basic/  # Basic backend and CUDA consistency checks
    ├── benchmarks/  # Benchmark workflow tests
    ├── dependencies/  # Dependency and package compatibility checks
    ├── deploy/  # Deployment and checkpoint tests
    ├── fault_tolerance/  # Fault injection, recovery, migration and cancellation tests
    ├── frontend/  # Frontend API, preprocessing and observability integration tests
    ├── gpu_memory_service/  # GPU memory service lifecycle and failover tests
    ├── kvbm_integration/  # KV Block Manager engine integration tests
    ├── lmcache/  # LMCache integration and evaluation scripts
    ├── mm_router/  # Multimodal routing end-to-end tests
    ├── parity/  # Migration notice for parser conformance tests
    ├── rl/  # Reinforcement learning worker discovery and weight update tests
    ├── router/  # Routing and KV event end-to-end tests
    ├── runtime/  # Runtime controls and serving smoke tests
    ├── serve/  # Inference backend serving end-to-end tests
    ├── utils/  # Shared test fixtures, clients and hardware utilities
    ├── vllm_self_benchmark/  # vLLM self-benchmark GPU tests
    └── wheels/  # Python wheel installation smoke tests
```

The Rust workspace and Python wheels are integrated into the [SGLang Runtime image build](container/README.md), using Dynamo's render.py, context.yaml and template layout. Runtime bindings and Python components are built and installed against the selected vendor base image. Source availability, image packaging, and on-device execution are separate validation stages; wheel installation does not certify model execution or P/D transfer. The Python Planner lives in `components/src/dynamo/planner/`, while the compatibility Go Planner lives in `deploy/planner/`. See [System Architecture](docs/architecture.md) for responsibilities and evolution plans.

Use `make -C deploy/operator docker-build` or `make -C deploy/planner docker-build` to build Go images inside component-owned multi-stage Dockerfiles. Run `bash scripts/check-architecture-layout.sh` to check the source layout. See [Build and deployment entrypoints](docs/build-deploy.md).

Internal deployments still use the separate [nanhuinfer-artifacthub repository](https://gitlab.zhejianglab.com/nanhu-tech/nanhuinfer-artifacthub) for environment configuration and the ArtifactHub / ArgoCD deployment workflow. It is not a submodule of this repository; maintain its checkout and deployment configuration separately. Building images here does not update that repository or deploy workloads.

## Copyright Statement

- The core inference framework evolved from NVIDIA Dynamo v1.4.2; upstream copyright and license notices are retained in the source.
- KV Cache functionality builds on SGLang HiCache and Mooncake; their upstream copyright and license notices are retained in the corresponding source.
- Refer to the licenses distributed with individual third-party components and submodules for their license terms.

## Project License

NanHuInfer adopts the Apache 2.0 open-source license. For details, please refer to the [LICENSE](./LICENSE) file.
