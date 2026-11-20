    logger.warning(
        "vLLM selects internal DP load balancing. If you are launching multiple "
        "workers for DP deployment, hybrid or external load balancing is recommended."
    )
    return (
        parallel_config.data_parallel_rank,
        parallel_config.data_parallel_size,
    )
