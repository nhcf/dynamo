def publish_kv_hint_capabilities(
    runtime_config: ModelRuntimeConfig,
    engine_args: AsyncEngineArgs,
    worker_type: WorkerType,
    dp_range: tuple[int, int] = (0, 1),
    *,
    publish_source_endpoints: bool = True,
) -> None:
    """Publish each versioned KV hint capability that vLLM supports."""
    _publish_kv_transfer_hint_capability(
        runtime_config,
        engine_args,
        worker_type,
        dp_range,
        publish_source_endpoints=publish_source_endpoints,
    )
