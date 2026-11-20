    vLLM's ``MultiConnector`` instantiates each child with a
    ``KVTransferConfig`` made from the child's own entry, with
    ``engine_id`` falling back to the wrapper's when the entry doesn't
    override it. The protocol must be bound the same way: binding to the
    wrapper config would leak the wrapper's ``engine_id`` into
    ``remote_engine_id``, and the decode side could never match the
    transfer to the child connector that owns it.
    """
    child_kv_cfg = copy.copy(kv_cfg)
    # Children don't inherit the wrapper's extra config — that's where the
    # "connectors" list itself lives.
    child_kv_cfg.kv_connector_extra_config = {}
    for key, value in sub_config.items():
        setattr(child_kv_cfg, key, value)
    child_vllm_config = copy.copy(vllm_config)
    child_vllm_config.kv_transfer_config = child_kv_cfg
    return child_vllm_config
