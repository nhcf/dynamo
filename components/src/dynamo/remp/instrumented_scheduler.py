    def _bench_write_results(self) -> None:
        self._bench_finish_timing()
        completed_points = len(self._bench_results)
        skipped_points = len(self._bench_skipped_points)
        missing_phases = list(getattr(self, "_bench_missing_phases", []))
        error = getattr(self, "_bench_grid_error", None)
        dp_size = getattr(self, "_bench_dp_size", 1)
        iteration_groups = list(getattr(self, "_bench_iteration_groups", []))
        measured_iteration_seconds = sum(
            float(group.get("wall_time", 0.0)) for group in iteration_groups
        )
        elapsed_seconds = float(self._bench_elapsed_seconds or 0.0)
        timing_valid = (
            bool(self._bench_started_at)
            and bool(self._bench_completed_at)
            and measured_iteration_seconds <= elapsed_seconds + 1e-12
        )
        coverage_complete = completed_points == self._bench_expected_points
        stop_reason = getattr(self, "_bench_stop_reason", None)
        status = (
            "failed"
            if error is not None
            else "partial"
            if stop_reason is not None and not coverage_complete
            else "complete"
        )
        usable = (
            error is None
            and completed_points > 0
            and len(iteration_groups) == completed_points
            and all(group.get("complete") for group in iteration_groups)
            and skipped_points == 0
            and not missing_phases
            and timing_valid
        )
        output = {
            "schema_version": 2,
            "artifact_type": "rank",
            "status": status,
            "valid": coverage_complete
            and len(iteration_groups) == completed_points
            and all(group.get("complete") for group in iteration_groups)
            and skipped_points == 0
            and not missing_phases
            and error is None
            and timing_valid,
            "usable": usable,
            "stop_reason": stop_reason if status == "partial" else None,
            "timing_valid": timing_valid,
            "run_id": getattr(self, "_bench_run_id", None),
            "grid_digest": getattr(self, "_bench_grid_digest", None),
            "timing": {
                "started_at": self._bench_started_at,
                "completed_at": self._bench_completed_at,
                "benchmark_elapsed_seconds": elapsed_seconds,
                "measured_iteration_seconds": measured_iteration_seconds,
            },
            "dp": {
                "rank": getattr(self, "_fpm_dp_rank", 0),
                "size": dp_size,
            },
            "synchronization": {
                "enabled": dp_size > 1,
                "coordinator_rank": 0,
                "port": (
                    int(os.environ.get(ENV_FPM_PORT, str(DEFAULT_FPM_PORT))) + dp_size
                    if dp_size > 1
                    else None
                ),
            },
            "coverage": {
                "expected_points": self._bench_expected_points,
                "completed_points": completed_points,
                "skipped_points": skipped_points,
            },
            "config": asdict(self._bench_config),
            "limits": {
                "max_num_scheduled_tokens": self.max_num_scheduled_tokens,
                "max_num_running_reqs": self.max_num_running_reqs,
                "max_model_len": self.max_model_len,
                "block_size": self.block_size,
                "num_gpu_blocks": self.cache_config.num_gpu_blocks,
                "configured_max_batch_size": self.max_num_running_reqs,
                "feasible_max_batch_size": getattr(
                    self, "_bench_feasible_max_decode_batch_size", 0
                ),
            },
            "measurement_policy": {
                "decode": "steady_state_second_step",
                "prefill": "single_step",
            },
            "capacity": {
                "common": (
                    asdict(negotiated_capacity)
                    if (
                        negotiated_capacity := getattr(
                            self, "_bench_negotiated_capacity", None
                        )
                    )
                    is not None
                    else None
                ),
            },
            "cudagraph": {
                "mode": getattr(self, "_bench_cudagraph_mode", "NONE"),
                "prefill_mode": getattr(self, "_bench_prefill_cudagraph_mode", "NONE"),
                "decode_mode": getattr(self, "_bench_decode_cudagraph_mode", "NONE"),
                "max_capture_size": getattr(
                    self, "_bench_max_cudagraph_capture_size", 0
                ),
                "capture_sizes": getattr(self, "_bench_cudagraph_capture_sizes", []),
                "prefill_capture_sizes": getattr(
                    self, "_bench_prefill_capture_sizes", []
                ),
                "decode_capture_sizes": getattr(
                    self, "_bench_decode_capture_sizes", []
                ),
            },
            "results": [
                {"point": asdict(r.point), "fpms": r.fpms} for r in self._bench_results
            ],
            "iteration_groups": iteration_groups,
            "skipped_points": [
                {"point": asdict(skipped.point), "reason": skipped.reason}
                for skipped in self._bench_skipped_points
            ],
            "missing_phases": missing_phases,
            "error": error,
        }
        dest = self._bench_config.output_path
        tmp = dest + ".tmp"
        with open(tmp, "w") as f:
            json.dump(output, f, indent=2)
        os.replace(tmp, dest)
        logger.info(
            "Benchmark results written to %s (%d points)",
            dest,
            len(self._bench_results),
        )
