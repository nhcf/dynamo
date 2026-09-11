# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from typing import Any, Optional

import uvloop
from prometheus_client import REGISTRY, CollectorRegistry, multiprocess
from vllm.config import VllmConfig
from vllm.distributed.kv_events import ZmqEventPublisher
from vllm.usage.usage_lib import UsageContext
from vllm.v1.engine.async_llm import AsyncLLM
from vllm.v1.metrics.prometheus import setup_multiprocess_prometheus

from dynamo.common.config_dump import dump_config
from dynamo.common.configuration.groups.router_args import build_router_config
from dynamo.common.utils.graceful_shutdown import install_signal_handlers
from dynamo.common.utils.prometheus import (
    LLMBackendMetrics,
    register_engine_metrics_callback,
)
from dynamo.common.utils.runtime import create_runtime
from dynamo.common.utils.topology import apply_topology_config
from dynamo.llm import (
    KvEventPublisher,
    ModelInput,
    ModelRuntimeConfig,
    ModelType,
    WorkerType,
    register_model,
)
from dynamo.runtime import Endpoint
from dynamo.runtime.logging import configure_dynamo_logging
from dynamo.vllm.kv_hints import publish_kv_hint_capabilities
from dynamo.vllm.worker_factory import WorkerFactory

from . import envs
from .args import Config, _uses_dynamo_connector, configure_rl_logprobs_mode, parse_args
from .cache_info import get_configured_kv_event_block_size
from .capacity import (
    get_metrics_model_name,
    get_spec_decode_runtime_data,
    per_rank_kv_blocks,
    publish_vllm_token_budget,
)
from .dp_topology import get_dp_range_for_worker
from .engine_generate import publish_engine_generate_capability
from .handlers import apply_data_parallel_runtime_config
from .headless import run_dynamo_headless
from .instrumented_scheduler import ENV_FPM_BENCHMARK_OUTPUT_PATH, ENV_FPM_WORKER_ID
from .kv_connector_protocols import (
    disable_hybrid_kv_cache_manager_for_incompatible_pd_connector,
)
from .publisher import DYNAMO_COMPONENT_REGISTRY, StatLoggerFactory
from .state_agent import (
    StateAgentLifecycle,
    start_attachment_owner,
    state_agent_settings,
)

configure_dynamo_logging()
logger = logging.getLogger(__name__)
shutdown_endpoints: list = []
SPEC_DECODE_RUNTIME_KEY = "spec_decode"
TOOL_CALL_STRUCTURAL_TAG_EXCLUDES_REASONING_RUNTIME_KEY = (
    "tool_call_structural_tag_excludes_reasoning"
)


def publish_vllm_structural_tag_reasoning_policy(
    runtime_config: ModelRuntimeConfig, vllm_config: VllmConfig
) -> None:
    """Tell the frontend whether the vLLM tool tag must exclude reasoning.

    vLLM delays its tool grammar only when reasoning constraints are disabled
    *and* its engine-side reasoning parser can detect the end of reasoning. In
    that case, the frontend tag must not model the reasoning block again.

    Otherwise, keep the frontend's compatibility behavior so its response
    parser can close the prompt-injected reasoning block before parsing tools.
    """
    structured_outputs_config = vllm_config.structured_outputs_config
    enable_in_reasoning = structured_outputs_config.enable_in_reasoning
    has_reasoning_parser = bool(structured_outputs_config.reasoning_parser)
    runtime_config.set_engine_specific(
        TOOL_CALL_STRUCTURAL_TAG_EXCLUDES_REASONING_RUNTIME_KEY,
        json.dumps(has_reasoning_parser and not enable_in_reasoning),
    )


async def worker(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    config = parse_args(argv)

    # Internal endpoint children have identical configuration. Only the
    # owning process writes the requested dump path.
    dump_config(config.dump_config_to, config)

    # Name the model. Use either the full path (vllm and sglang do the same),
    # or the HF name (e.g. "Qwen/Qwen3-0.6B"), depending on cmd line params.
    if not config.served_model_name:
        config.served_model_name = config.engine_args.served_model_name = config.model

    configure_rl_logprobs_mode(config)

    # Model weights must exist locally. No remote download is performed.
    if not os.path.exists(config.model):
        raise FileNotFoundError(
            f"Model path does not exist: {config.model}. "
            "Model weights must be available locally before starting the worker."
        )

    # HEADLESS MODE: bypass DistributedRuntime entirely.
    # Workers run vLLM only (no NATS, etcd, or dynamo endpoints).
    if config.headless:
        run_dynamo_headless(config)
        return

    shutdown_event = asyncio.Event()
    state_agent_lifecycle = StateAgentLifecycle()
    runtime, loop = create_runtime(
        discovery_backend=config.discovery_backend,
        request_plane=config.request_plane,
        event_plane=config.event_plane,
        response_plane=config.response_plane,
    )

    # [gluo FIXME] should be after init() below? 'shutdown_endpoints' are populated
    # there
    install_signal_handlers(
        loop,
        runtime,
        shutdown_endpoints,
        shutdown_event,
        pre_shutdown_callback=state_agent_lifecycle.close,
    )

    # Use WorkerFactory to appropriate initialize worker based on config flags
    factory = WorkerFactory(
        setup_vllm_engine_fn=setup_vllm_engine,
        setup_kv_event_publisher_fn=setup_kv_event_publisher,
        setup_kv_state_attachment_owner_fn=setup_kv_state_attachment_owner,
        register_vllm_model_fn=register_vllm_model,
        setup_fpm_relay_fn=setup_fpm_relay,
        setup_metrics_collection_fn=setup_metrics_collection,
        state_agent_lifecycle=state_agent_lifecycle,
    )
    await factory.create(
        runtime,
        config,
        shutdown_event,
        shutdown_endpoints,
    )

    logger.debug("Worker function completed, exiting...")


def setup_metrics_collection(
    config: Config, generate_endpoint: Endpoint, logger: logging.Logger
) -> None:
    """Set up metrics collection for vLLM and LMCache metrics.

    In multiprocess mode (PROMETHEUS_MULTIPROC_DIR set), metrics are stored:
      1. In-memory: Metric objects in global REGISTRY
      2. On-disk: Metric values in .db files (PROMETHEUS_MULTIPROC_DIR)

    MultiProcessCollector reads from .db files but adding it to REGISTRY can fail
    with "Duplicated timeseries" if PROMETHEUS_MULTIPROC_DIR was set before process
    started (K8s deployments) because metrics are already in REGISTRY.

    Solution: Try adding MultiProcessCollector to REGISTRY. If that fails, use
    separate registry for multiprocess collection and register callbacks to both
    registries to ensure all metrics (vllm, lmcache, dynamo_component) are collected.

    Auto-label injection:
        Hierarchy labels (dynamo_namespace, dynamo_component, dynamo_endpoint) are automatically
        injected into engine metrics to align Python metrics with Rust auto-labels.
        Additional labels can be provided via inject_labels parameter.
    """
    metrics_model_name = get_metrics_model_name(config)

    engine_metric_prefixes = ["vllm:", "lmcache:"]

    if config.engine_args.disable_log_stats is False:
        # Register the dedicated dynamo_component registry callback
        # IMPORTANT: We do NOT use MultiProcessCollector for DYNAMO_COMPONENT_REGISTRY
        # because our gauges use in-memory values which work fine for single-process
        # and multi-process (each process has its own gauge with dp_rank label).
        # Using MultiProcessCollector would read from .db files which causes stale
        # values to accumulate across test runs.
        register_engine_metrics_callback(
            endpoint=generate_endpoint,
            registry=DYNAMO_COMPONENT_REGISTRY,
        )

        multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
        if multiproc_dir and not os.path.isdir(multiproc_dir):
            try:
                os.makedirs(multiproc_dir, exist_ok=True)
            except OSError:
                pass
        if multiproc_dir and os.path.isdir(multiproc_dir):
            try:
                # MultiProcessCollector reads metrics from .db files in PROMETHEUS_MULTIPROC_DIR
                # Adding it to REGISTRY allows collecting both in-memory and .db file metrics
                multiprocess.MultiProcessCollector(REGISTRY)
                logger.debug("Added MultiProcessCollector to global REGISTRY")
                register_engine_metrics_callback(
                    endpoint=generate_endpoint,
                    registry=REGISTRY,
                    metric_prefix_filters=engine_metric_prefixes,
                    namespace_name=config.namespace,
                    component_name=config.component,
                    endpoint_name=config.endpoint,
                    model_name=metrics_model_name,
                )
            except ValueError as e:
                # Conflict: metrics already in REGISTRY, MultiProcessCollector tries to add same metrics from .db files
                # Solution: Use separate registry that ONLY reads from .db files (no in-memory conflicts)
                logger.debug(
                    f"Could not add MultiProcessCollector to REGISTRY ({e}), using separate registry"
                )
                multiproc_registry = CollectorRegistry()
                multiprocess.MultiProcessCollector(multiproc_registry)

                # Register both registries to collect all metrics
                # Global REGISTRY has in-memory metrics (vllm)
                register_engine_metrics_callback(
                    endpoint=generate_endpoint,
                    registry=REGISTRY,
                    metric_prefix_filters=["vllm:"],
                    namespace_name=config.namespace,
                    component_name=config.component,
                    endpoint_name=config.endpoint,
                    model_name=metrics_model_name,
                )
                # Multiproc registry has .db file metrics (lmcache, possibly vllm duplicates)
                register_engine_metrics_callback(
                    endpoint=generate_endpoint,
                    registry=multiproc_registry,
                    metric_prefix_filters=engine_metric_prefixes,
                    namespace_name=config.namespace,
                    component_name=config.component,
                    endpoint_name=config.endpoint,
                    model_name=metrics_model_name,
                )
        else:
            if multiproc_dir:
                logger.warning(
                    f"PROMETHEUS_MULTIPROC_DIR={multiproc_dir} is not a valid directory, "
                    "falling back to single-process metrics"
                )
            # No multiprocess mode
            register_engine_metrics_callback(
                endpoint=generate_endpoint,
                registry=REGISTRY,
                metric_prefix_filters=engine_metric_prefixes,
                namespace_name=config.namespace,
                component_name=config.component,
                endpoint_name=config.endpoint,
                model_name=metrics_model_name,
            )


def _resolve_image_token_id(config: Config, vllm_config: VllmConfig) -> Optional[int]:
    """Routing-side image-placeholder token id for the served model.

    Resolved via the SAME Rust logic the frontend uses
    (`dynamo._core.resolve_routing_image_token_id` ->
    `lightseek_mm::resolve_routing_tokens`), returning `chat_placeholder_token_id`
    so the KV-event normalizer keys on the identical token the frontend
    substitutes `pad_value` over — no per-family drift between the two.

    Returns None when the bindings lack the `mm-routing` feature or the model
    isn't in the MM-routing registry — in both cases the frontend also skips MM
    routing, so a worker-side no-op is consistent (events pass through).
    """
    try:
        from dynamo._core import resolve_routing_image_token_id
    except ImportError:
        return None

    # Model weights are always local; use the model path directly.
    model_dir = config.model
    return resolve_routing_image_token_id(config.model, model_dir)


def _resolve_video_token_id(vllm_config: VllmConfig) -> Optional[int]:
    hf_config = vllm_config.model_config.hf_config
    for field in ("video_token_id", "video_token_index"):
        token_id = getattr(hf_config, field, None)
        if token_id is not None:
            return int(token_id)
    return None


def setup_kv_event_publisher(
    config: Config,
    generate_endpoint: Endpoint,
    vllm_config: VllmConfig,
    consolidator_enabled: bool = False,
    consolidator_port: Optional[int] = 5558,
) -> Optional[list[KvEventPublisher]]:
    """
    list[KvEventPublisher] | None
    Set up KV event publishers for prefix caching if enabled.
    Creates one publisher per dp_rank since each dp_rank publishes to a different port.
    Args:
        config: Worker configuration
        generate_endpoint: Endpoint for worker ID
        vllm_config: vLLM configuration
        consolidator_enabled: If True, subscribe to kv eventconsolidator's ZMQ endpoint
        consolidator_port: Port where kv event consolidator publishes (default: 5558)

    Returns:
        List of KvEventPublisher instances (one per dp_rank) if prefix caching is enabled, None otherwise.
    """
    if not config.engine_args.enable_prefix_caching:
        return None

    if config.engine_args.kv_events_config is None:
        return None

    # Check if kv_cache_events are explicitly disabled
    if not config.engine_args.kv_events_config.enable_kv_cache_events:
        logger.info(
            "KV event publishing skipped: enable_kv_cache_events=False in kv_events_config"
        )
        return None

    # Get DP rank range managed by this worker to create publishers for corresponding dp_ranks,
    # all served workers should cover all ranks.
    dp_start, dp_size = get_dp_range_for_worker(vllm_config)
    kv_publishers = []
    kv_event_block_size = get_configured_kv_event_block_size(vllm_config)
    # Placeholder token ids the frontend substitutes pad_value over. Pass them
    # to the KV publisher so the router-side normalizer rewrites image and
    # video runs in vLLM BlockStored events to the same canonical scheme.
    # Missing ids leave their modality unchanged.
    image_token_id = _resolve_image_token_id(config, vllm_config)
    video_token_id = _resolve_video_token_id(vllm_config)

    for dp_rank in range(dp_start, dp_start + dp_size):
        if consolidator_enabled:
            # TODO: Use different port for each dp_rank once KVBM supports DP
            zmq_endpoint = f"tcp://127.0.0.1:{consolidator_port}"
            logger.info(
                f"KV event publisher for dp_rank={dp_rank} subscribing to consolidator at {zmq_endpoint}"
            )
        else:
            # Each dp_rank publishes to a different port
            zmq_endpoint = ZmqEventPublisher.offset_endpoint_port(
                config.engine_args.kv_events_config.endpoint,
                data_parallel_rank=dp_rank,
            ).replace("*", "127.0.0.1")
            logger.info(
                f"KV event publisher for dp_rank={dp_rank} subscribing to vLLM at {zmq_endpoint}"
            )

        kv_publisher = KvEventPublisher(
            endpoint=generate_endpoint,
            kv_block_size=kv_event_block_size,
            zmq_endpoint=zmq_endpoint,
            zmq_topic="",
            enable_local_indexer=config.enable_local_indexer,
            dp_rank=dp_rank,
            image_token_id=image_token_id,
            kv_state_endpoint=config.kv_state_endpoint,
            video_token_id=video_token_id,
        )
        kv_publishers.append(kv_publisher)

        logger.info(
            f"Worker reading KV events for dp_rank={dp_rank} from {zmq_endpoint}"
        )

    return kv_publishers if kv_publishers else None


async def setup_kv_state_attachment_owner(
    config: Config,
    generate_endpoint: Endpoint,
    vllm_config: VllmConfig,
):
    return await start_attachment_owner(
        config,
        generate_endpoint,
        vllm_config,
        _resolve_image_token_id(config, vllm_config),
    )


def setup_fpm_relay(
    config: Config,
    generate_endpoint: Endpoint,
    vllm_config: VllmConfig,
) -> Optional[list]:
    """
    Set up forward pass metrics relays for the event plane.

    Creates one FpmEventRelay per dp_rank. Each relay subscribes to the
    local raw ZMQ PUB from InstrumentedScheduler (in the EngineCore child
    process) and re-publishes to the Dynamo event plane with automatic
    discovery registration.

    Returns:
        List of FpmEventRelay instances, or None if FPM is not enabled.
    """
    if not (envs.is_set("DYN_FORWARDPASS_METRIC_PORT") or config.fpm_trace):
        return None

    try:
        from dynamo.llm import FpmEventRelay
    except ImportError:
        logger.warning(
            "FpmEventRelay not available (Rust bindings not built with FPM support). "
            "Forward pass metrics will not be relayed to the event plane."
        )
        return None

    dp_start, dp_size = get_dp_range_for_worker(vllm_config)
    relays = []

    for dp_rank in range(dp_start, dp_start + dp_size):
        base_port = envs.DYN_FORWARDPASS_METRIC_PORT
        zmq_endpoint = f"tcp://127.0.0.1:{base_port + dp_rank}"

        relay = FpmEventRelay(
            endpoint=generate_endpoint,
            zmq_endpoint=zmq_endpoint,
        )
        relays.append(relay)

        logger.info(f"FPM relay for dp_rank={dp_rank} subscribing to {zmq_endpoint}")

    return relays if relays else None


def _tp_pp_switch_requested(engine_args: Any) -> bool:
    defaults = {
        "tp_pp_switch_prebuild_strategies": [],
        "tp_pp_switch_kv_transfer_window_size": 1,
        "tp_pp_switch_kv_transfer_max_scratch_size_mb": 256,
        "tp_pp_switch_weight_load_mode": "disk",
        "tp_pp_switch_weight_cache_dir": "/dev/shm",
    }
    for name, default in defaults.items():
        value = getattr(engine_args, name, None)
        if value is None:
            continue
        if name == "tp_pp_switch_prebuild_strategies" and value == []:
            continue
        if value != default:
            return True
    return False


def _validate_aggregated_tp_pp_switch_engine(
    config: Config,
    engine_args: Any,
    vllm_config: VllmConfig,
    engine_client: AsyncLLM,
) -> None:
    """Fail before registration when the loaded vLLM cannot switch TP/PP."""
    if not _tp_pp_switch_requested(engine_args):
        return

    mode = getattr(config.disaggregation_mode, "value", config.disaggregation_mode)
    if mode not in (None, "none", "aggregated"):
        return

    switch_method = getattr(engine_client, "switch_parallel_strategy", None)
    if not callable(switch_method):
        raise RuntimeError(
            "Elastic TP/PP switching was configured, but the installed vLLM "
            "does not expose AsyncLLM.switch_parallel_strategy; install the "
            "ElasticVllm fork instead of native vLLM"
        )

    parallel_config = vllm_config.parallel_config
    for name in (
        "data_parallel_size",
        "decode_context_parallel_size",
        "prefill_context_parallel_size",
    ):
        if getattr(parallel_config, name, 1) != 1:
            raise RuntimeError(
                f"Elastic TP/PP switching requires {name}=1, "
                f"got {getattr(parallel_config, name)}"
            )

    backend = getattr(engine_args, "distributed_executor_backend", None)
    if backend not in (None, "mp", "ray"):
        raise RuntimeError(
            "Elastic TP/PP switching requires MultiprocExecutor or RayExecutorV2"
        )

    runner_type = getattr(vllm_config.model_config, "runner_type", None)
    if isinstance(runner_type, str) and "v2" in runner_type.lower():
        raise RuntimeError(
            "Elastic TP/PP switching supports the V1 legacy GPUModelRunner, "
            "not the V2 ModelRunner"
        )


def setup_vllm_engine(
    config: Config,
    stat_logger: Optional[StatLoggerFactory] = None,
    fpm_worker_id: Optional[str] = None,
) -> tuple[AsyncLLM, VllmConfig, Any, Any, Optional[LLMBackendMetrics]]:
    # vLLM v0.11.0 bug: vllm/v1.metrics/prometheus.py:79 passes TemporaryDirectory object
    # instead of .name string, causing false error on exit. Set PROMETHEUS_MULTIPROC_DIR
    # ourselves to avoid this and handle cleanup properly.
    prometheus_temp_dir = None
    existing_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if existing_dir and not os.path.isdir(existing_dir):
        logger.warning(
            f"PROMETHEUS_MULTIPROC_DIR={existing_dir} does not exist, recreating"
        )
        os.makedirs(existing_dir, exist_ok=True)
    if "PROMETHEUS_MULTIPROC_DIR" not in os.environ:
        prometheus_temp_dir = tempfile.TemporaryDirectory(prefix="vllm_prometheus_")
        os.environ["PROMETHEUS_MULTIPROC_DIR"] = prometheus_temp_dir.name
        logger.debug(
            f"Created PROMETHEUS_MULTIPROC_DIR at: {os.environ['PROMETHEUS_MULTIPROC_DIR']}"
        )

    setup_multiprocess_prometheus()  # call vLLM's library's function to setup multiprocess prometheus
    logger.debug(
        f"Prometheus multiproc dir set to: {os.environ.get('PROMETHEUS_MULTIPROC_DIR')}"
    )

    # Construct Prometheus gauges AFTER setup_multiprocess_prometheus() so Gauge objects
    # see the correct ValueClass (multiprocess vs in-memory).
    component_gauges: Optional[LLMBackendMetrics] = None
    component_gauges = LLMBackendMetrics(
        registry=DYNAMO_COMPONENT_REGISTRY,
        model_name=config.served_model_name or "",
        component_name=config.component or "",
    )

    # If a StatLoggerFactory was provided, give it the gauges so the loggers
    # it creates can publish Prometheus metrics.
    if stat_logger is not None:
        stat_logger.component_gauges = component_gauges

    os.environ["VLLM_NO_USAGE_STATS"] = "1"  # Avoid internal HTTP requests
    os.environ["VLLM_WORKER_MULTIPROC_METHOD"] = "spawn"

    engine_args = config.engine_args

    if engine_args.enable_lora:
        if "VLLM_ALLOW_RUNTIME_LORA_UPDATING" not in os.environ:
            os.environ["VLLM_ALLOW_RUNTIME_LORA_UPDATING"] = "True"
        if "VLLM_LORA_MODULES_LOADING_TIMEOUT" not in os.environ:
            os.environ["VLLM_LORA_MODULES_LOADING_TIMEOUT"] = "600"

    # Taken from build_async_engine_client_from_engine_args()
    usage_context = UsageContext.OPENAI_API_SERVER
    vllm_config = engine_args.create_engine_config(usage_context=usage_context)
    disable_hybrid_kv_cache_manager_for_incompatible_pd_connector(vllm_config)
    default_sampling_params = vllm_config.model_config.get_diff_sampling_param()

    # Set up consolidator endpoints if KVBM (DynamoConnector) is enabled
    consolidator_endpoints = None
    if _uses_dynamo_connector(config.engine_args):
        try:
            from kvbm.vllm_integration.consolidator_config import (
                get_consolidator_endpoints,
            )

            consolidator_endpoints = get_consolidator_endpoints(vllm_config)
        except Exception as e:
            logger.warning(
                f"KVBM connector is enabled but failed to get consolidator endpoints: {e}. "
                "Continuing without KV event consolidation. "
                "Ensure 'kvbm' package is installed if this feature is needed."
            )
    # Store consolidator endpoints in additional_config (vLLM 0.16+ uses strict
    # dataclass fields; monkey-patching attributes onto VllmConfig is no longer safe).
    vllm_config.additional_config["consolidator_endpoints"] = consolidator_endpoints

    # Pass runtime-only worker identity to InstrumentedScheduler via the
    # environment so it does not perturb vLLM's config hash.
    if fpm_worker_id is not None:
        os.environ[ENV_FPM_WORKER_ID] = fpm_worker_id

    # Pass benchmark config to InstrumentedScheduler via additional_config.
    if hasattr(config, "_benchmark_additional_config"):
        # Dense DP ranks are independent vLLM engines and cannot synchronize a
        # multi-rank self-benchmark. Reject before AsyncLLM starts those engines.
        if (
            vllm_config.parallel_config.data_parallel_size > 1
            and not vllm_config.model_config.is_moe
        ):
            raise ValueError(
                "--benchmark-mode cannot be combined with --data-parallel-size "
                f"{vllm_config.parallel_config.data_parallel_size} on a dense "
                "(non-MoE) model. The attention-DP self-benchmark requires an MoE "
                "model because vLLM runs dense data-parallel ranks as independent "
                "engines. Use --data-parallel-size 1 or benchmark an MoE model."
            )
        bench = config._benchmark_additional_config
        if fpm_worker_id and bench["output_path"] == "/tmp/benchmark_results.json":
            short_id = fpm_worker_id[-8:]
            os.environ[
                ENV_FPM_BENCHMARK_OUTPUT_PATH
            ] = f"/tmp/benchmark_results_{short_id}.json"
        vllm_config.additional_config["benchmark"] = bench
        logger.info("Benchmark config injected into additional_config")

    factory = []
    if stat_logger:
        factory.append(stat_logger)

    # Time engine initialization
    start_time = time.time()
    engine_client = AsyncLLM.from_vllm_config(
        vllm_config=vllm_config,
        usage_context=usage_context,
        stat_loggers=factory,
        enable_log_requests=engine_args.enable_log_requests,
        disable_log_stats=engine_args.disable_log_stats,
    )
    _validate_aggregated_tp_pp_switch_engine(
        config, engine_args, vllm_config, engine_client
    )
    load_time = time.time() - start_time

    # Record model load time.
    if component_gauges is not None:
        component_gauges.set_model_load_time(load_time)

    logger.info(f"worker for {config.served_model_name} has been initialized")

    try:
        runtime_values = get_engine_cache_info(engine_client)
    except BaseException:
        try:
            engine_client.shutdown()
        except Exception:
            logger.exception("Failed to shut down engine client after startup error")
        raise
    vllm_config.cache_config.block_size = runtime_values["block_size"]

    return (
        engine_client,
        vllm_config,
        default_sampling_params,
        prometheus_temp_dir,
        component_gauges,
    )


async def register_vllm_model(
    model_input: ModelInput,
    model_type: ModelType,
    generate_endpoint: Endpoint,
    config: Config,
    engine_client: AsyncLLM,
    vllm_config: VllmConfig,
    worker_type: WorkerType,
    needs: list[list[WorkerType]] | None = None,
) -> None:
    """
    Helper function to register a vLLM model with runtime configuration.

    Args:
        model_input: Input type for the model (e.g., ModelInput.Tokens)
        model_type: OpenAI surface this card exposes (e.g., ModelType.Chat).
            Prefill workers have no OpenAI surface — their role is carried by
            `worker_type=WorkerType.Prefill` — but pass the legacy
            `ModelType.Prefill` marker bit (not a surface) so an old frontend
            still detects them during the cross-version rollout.
        generate_endpoint: Endpoint to register
        config: Configuration object
        engine_client: vLLM engine client
        vllm_config: vLLM configuration
        worker_type: The disaggregation role this worker plays
            (Prefill / Decode / Encode / Aggregated). Required by the
            frontend's model-serving-readiness check.
        needs: Peer worker types required to serve traffic, in DNF form
            (list of alternative AND-sets).
    """
    runtime_config = ModelRuntimeConfig()
    publish_vllm_structural_tag_reasoning_policy(runtime_config, vllm_config)
    dp_range = get_dp_range_for_worker(vllm_config)
    state_agent_enabled = state_agent_settings(config) is not None
    apply_data_parallel_runtime_config(runtime_config, dp_range)
    publish_kv_hint_capabilities(
        runtime_config,
        config.engine_args,
        worker_type,
        dp_range,
        publish_source_endpoints=not state_agent_enabled,
    )
    runtime_config.context_length = vllm_config.model_config.max_model_len
    tower_connector_lora_enabled = bool(
        vllm_config.lora_config
        and getattr(vllm_config.lora_config, "enable_tower_connector_lora", False)
    )
    if publish_engine_generate_capability(
        runtime_config,
        model_input,
        model_type,
        worker_type,
        tower_connector_lora_enabled,
    ):
        logging.info("Published vLLM engine-native generate capability")
    if model_type != ModelType.Embedding:
        publish_vllm_token_budget(
            runtime_config, vllm_config.model_config.max_model_len
        )

    # Get runtime configuration from vLLM engine
    logging.info(
        f"Getting engine runtime configuration metadata from vLLM engine for {model_type}..."
    )
    runtime_values = get_engine_cache_info(engine_client)
    num_gpu_blocks = runtime_values["num_gpu_blocks"]
    # Get data_parallel_size from vllm_config (defaults to 1)
    if num_gpu_blocks is None:
        # TODO(upstream-vllm): remove this workaround once vLLM propagates
        # num_gpu_blocks from Ray DP workers back to the main-process vllm_config.
        # With Ray-based data-parallel backend, num_gpu_blocks is computed inside
        # Ray worker processes and is never written back to the main-process
        # vllm_config.  Use 0 as a sentinel so the Rust runtime can still register
        # the model; KV-cache capacity metrics will be unavailable in this mode.
        logging.warning(
            "num_gpu_blocks is None (expected when using --data-parallel-backend ray). "
            "Setting total_kv_blocks=0 for model registration."
        )
        num_gpu_blocks = 0
    runtime_config.total_kv_blocks = per_rank_kv_blocks(num_gpu_blocks, dp_range[1])
    runtime_config.max_num_seqs = runtime_values["max_num_seqs"]
    runtime_config.max_num_batched_tokens = runtime_values["max_num_batched_tokens"]
    runtime_config.enable_local_indexer = config.enable_local_indexer
    runtime_config.kv_event_publishing_enabled = config.use_kv_events
    runtime_config.kv_state_endpoint = config.kv_state_endpoint
    if state_agent_enabled:
        runtime_config.kv_event_source_mode = "state_agent_v2"

    # Add tool/reasoning parsers for decode/aggregated workers. Prefill
    # workers have no OpenAI surface and don't run a parser — key off
    # `worker_type` to skip them.
    if worker_type != WorkerType.Prefill:
        runtime_config.tool_call_parser = config.dyn_tool_call_parser
        runtime_config.reasoning_parser = config.dyn_reasoning_parser
        if config.dyn_default_thinking_mode is not None:
            runtime_config.set_engine_specific(
                "default_thinking_mode",
                json.dumps(config.dyn_default_thinking_mode),
            )
    runtime_config.exclude_tools_when_tool_choice_none = (
        config.exclude_tools_when_tool_choice_none
    )
    runtime_config.set_structural_tag_mode(
        "on" if config.dyn_enable_structural_tag else "off"
    )
    runtime_config.set_structural_tag_scope(config.dyn_structural_tag_scope)
    runtime_config.set_structural_tag_schema(config.dyn_structural_tag_schema)

    # Propagate stream_interval so the frontend can respect --stream-interval.
    # set_engine_specific requires a JSON-encoded string (the Rust binding
    # parses it with serde_json::from_str); str(int) happens to be valid JSON.
    stream_interval = getattr(config.engine_args, "stream_interval", None)
    if stream_interval is not None:
        runtime_config.set_engine_specific("stream_interval", str(stream_interval))

    spec_decode = get_spec_decode_runtime_data(config, vllm_config)
    if spec_decode is not None:
        runtime_config.set_engine_specific(
            SPEC_DECODE_RUNTIME_KEY, json.dumps(spec_decode)
        )
        logging.info("Published vLLM spec decode runtime metadata: %s", spec_decode)

    # Set topology and KV transfer policy for topology-aware routing
    apply_topology_config(runtime_config)

    await register_model(
        model_input,
        model_type,
        generate_endpoint,
        config.model,
        config.served_model_name,
        kv_cache_block_size=runtime_values["kv_event_block_size"],
        runtime_config=runtime_config,
        custom_template_path=config.custom_jinja_template,
        worker_type=worker_type,
        needs=needs,
        # Advertise this worker set's own routing strategy when --router-mode is
        # set; None inherits the frontend's global config. Combined with
        # worker_type, this is what lets a disaggregated deployment route to its
        # prefill and decode tiers differently.
        router_config=build_router_config(config.router_advertisement),
        model_aliases=config.served_model_aliases or None,
        # Advertise LoRA capacity on the BASE card so the frontend can place the first
        # adapter onto an idle worker. Decode, aggregated, and prefill workers all serve
        # lifecycle registration; embeddings and classify still do not.
        max_gpu_lora_count=_base_model_lora_capacity(config, model_type),
    )


def _base_model_lora_capacity(config: Config, model_type: ModelType) -> int | None:
    if not getattr(config.engine_args, "enable_lora", False):
        return None
    # Pooling-family workers (embedding, classify|pooling) do not serve the
    # LoRA load endpoints, so they must not advertise adapter capacity. Use
    # capability checks, not identity: the classify worker registers the
    # combined ModelType.Classify | ModelType.Pooling bits.
    if (
        model_type.supports_embedding()
        or model_type.supports_classify()
        or model_type.supports_pooling()
    ):
        return None
    return config.engine_args.max_loras


def get_engine_cache_info(engine: AsyncLLM) -> dict[str, Any]:
    """Return vLLM cache and scheduler limits used for model registration."""

    try:
        # Get values directly from vllm_config instead of collective_rpc
        kv_event_block_size = get_configured_kv_event_block_size(engine.vllm_config)
        cache_values = {
            "num_gpu_blocks": engine.vllm_config.cache_config.num_gpu_blocks,
            "block_size": engine.vllm_config.cache_config.block_size,
            "kv_event_block_size": kv_event_block_size,
        }

        scheduler_values = {
            "max_num_seqs": engine.vllm_config.scheduler_config.max_num_seqs,
            "max_num_batched_tokens": engine.vllm_config.scheduler_config.max_num_batched_tokens,
        }

        logging.debug(f"Cache config values: {cache_values}")
        logging.debug(f"Scheduler config values: {scheduler_values}")
        return {
            "num_gpu_blocks": cache_values["num_gpu_blocks"],
            "block_size": cache_values["block_size"],
            "kv_event_block_size": cache_values["kv_event_block_size"],
            "max_num_seqs": scheduler_values["max_num_seqs"],
            "max_num_batched_tokens": scheduler_values["max_num_batched_tokens"],
        }
    except Exception as e:
        logging.error(f"Failed to get configuration values from vLLM config: {e}")
        raise


def main() -> None:
    uvloop.run(worker())


if __name__ == "__main__":
    main()
