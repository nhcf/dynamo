    host_ip = get_host_ip()
    os.environ["VLLM_NIXL_SIDE_CHANNEL_HOST"] = host_ip
    logger.info("Set VLLM_NIXL_SIDE_CHANNEL_HOST to %s (auto-detected)", host_ip)
