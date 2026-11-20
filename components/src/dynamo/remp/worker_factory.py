        logger.info(
            "Registered engine routes: control/sleep, control/wake_up, "
            "control/scale_elastic_ep, control/ep_capacity, "
            "control/start_profile, control/stop_profile, "
            "%s"
            "and RL admin routes: %s%s",
            (
                "control/switch_parallel_strategy, control/parallel_strategy_state, "
                if enable_tp_pp_switch
                else ""
            ),
            ", ".join(sorted(rl_routes)),
            " (LoRA routes: load_lora, unload_lora)" if lora_enabled else "",
        )
