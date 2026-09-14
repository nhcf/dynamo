# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the traffic-triggered rolling TP/PP switch controller.

No GPU, no vLLM, no Dynamo, no sockets: the controller's three transport seams
(``_fetch_metrics``, ``_get_json``, ``_post_json``) are replaced by in-memory
fakes.  Run with ``python -m unittest`` or ``pytest``.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from dataclasses import dataclass, field
from typing import Any

_IMPORTS = (
    "ACTIVE_REQUESTS_METRIC",
    "ControllerConfig",
    "Decision",
    "ElasticSwitchController",
    "Strategy",
    "sum_metric",
)

try:  # canonical path, as the rest of components/src/dynamo/frontend/tests does
    from dynamo.frontend.elastic_controller import (  # type: ignore[assignment]
        ACTIVE_REQUESTS_METRIC,
        ControllerConfig,
        Decision,
        ElasticSwitchController,
        Strategy,
        sum_metric,
    )
except ImportError:  # standalone run with no dynamo package installed
    from elastic_controller import (
        ACTIVE_REQUESTS_METRIC,
        ControllerConfig,
        Decision,
        ElasticSwitchController,
        Strategy,
        sum_metric,
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
@dataclass
class FakeWorker:
    """One worker's control plane, as the Dynamo handler would answer it."""

    tp: int = 2
    pp: int = 2
    physical_world_size: int = 4
    failed: bool = False
    is_switching: bool = False
    supported: bool = True
    unreachable: bool = False
    # What a switch POST answers with.
    switch_status: str = "ok"
    switch_message: str = "TP/PP switch completed"
    switch_delay_s: float = 0.0
    switch_raises: Exception | None = None
    # False models the *unfixed* worker: EngineCore mutates its own process's
    # parallel_config and nothing propagates back, so the state route keeps
    # reporting startup values forever.  True models the fix under test.
    report_applied: bool = True
    num_gpu_blocks: int = 1024

    def state(self) -> dict[str, Any]:
        if not self.supported:
            return {
                "status": "unsupported",
                "message": "The installed vLLM does not expose Elastic TP/PP switching",
            }
        return {
            "status": "ok",
            "tensor_parallel_size": self.tp,
            "pipeline_parallel_size": self.pp,
            "data_parallel_size": 1,
            "world_size": self.tp * self.pp,
            "physical_world_size": self.physical_world_size,
            "num_gpu_blocks": self.num_gpu_blocks,
            "is_switching": self.is_switching,
            "failed": self.failed,
        }


class FakeController(ElasticSwitchController):
    """Controller whose transport is a dict of FakeWorkers plus a metrics queue."""

    def __init__(
        self,
        cfg: ControllerConfig,
        workers: dict[str, FakeWorker],
        metrics: list[str] | None = None,
        metrics_error: Exception | None = None,
    ) -> None:
        super().__init__(cfg)
        self.workers = workers
        self._metrics = list(metrics or [])
        self._metrics_error = metrics_error
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.gets: list[str] = []

    # -- seams ----------------------------------------------------------
    def _fetch_metrics(self) -> str:
        if self._metrics_error is not None:
            raise self._metrics_error
        if self._metrics:
            return self._metrics.pop(0)
        return ""

    def _worker_for(self, url: str) -> FakeWorker:
        base = url.split("/engine/")[0]
        worker = self.workers.get(base)
        if worker is None:
            raise AssertionError(f"test asked for unconfigured worker {base!r}")
        if worker.unreachable:
            raise OSError(f"connection refused: {base}")
        return worker

    def _get_json(self, url: str, timeout: float) -> dict[str, Any]:
        self.gets.append(url)
        return self._worker_for(url).state()

    def _post_json(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.posts.append((url.split("/engine/")[0], payload))
        worker = self._worker_for(url)
        if worker.switch_delay_s:
            time.sleep(worker.switch_delay_s)
        if worker.switch_raises is not None:
            raise worker.switch_raises
        if worker.switch_status == "ok" and worker.report_applied:
            worker.tp = payload["target_tensor_parallel_size"]
            worker.pp = payload["target_pipeline_parallel_size"]
            worker.num_gpu_blocks = payload.get("target_num_blocks") or worker.num_gpu_blocks
        # Shape mirrors handlers.py: {"status", "message", **_parallel_strategy_state()}.
        # state() carries its own "status": "ok", so the caller's status must be
        # applied *last* or every response looks successful.
        response = worker.state()
        response["status"] = worker.switch_status
        response["message"] = worker.switch_message
        return response


def exposition(*samples: tuple[str, float]) -> str:
    """Build a Prometheus text exposition for the active-requests gauge."""
    lines = [
        "# HELP dynamo_frontend_active_requests Number of requests currently being handled",
        "# TYPE dynamo_frontend_active_requests gauge",
    ]
    for model, value in samples:
        lines.append(f'dynamo_frontend_active_requests{{model="{model}"}} {value}')
    return "\n".join(lines) + "\n"


def make_cfg(**overrides: Any) -> ControllerConfig:
    base: dict[str, Any] = dict(
        enabled=True,
        worker_urls=("http://w1:9091",),
        expected_workers=1,
        poll_interval_s=0.01,
        concurrency_factor_up=10.0,
        stable_polls=3,
        cooldown_s=60.0,
        strategy_up=Strategy(4, 1),
        switch_timeout_s=5.0,
        http_timeout_s=1.0,
    )
    base.update(overrides)
    return ControllerConfig(**base)


class _AsyncCase(unittest.IsolatedAsyncioTestCase):
    def make(self, workers: dict[str, FakeWorker] | None = None, **cfg_overrides: Any):
        workers = workers if workers is not None else {"http://w1:9091": FakeWorker()}
        kwargs: dict[str, Any] = dict(
            worker_urls=tuple(workers), expected_workers=len(workers)
        )
        kwargs.update(cfg_overrides)  # caller may override expected_workers on purpose
        return FakeController(make_cfg(**kwargs), workers)


# ---------------------------------------------------------------------------
# Metrics parsing
# ---------------------------------------------------------------------------
class TestMetricsParsing(unittest.TestCase):
    def test_missing_gauge_is_zero(self):
        self.assertEqual(sum_metric("# nothing here\n", ACTIVE_REQUESTS_METRIC), 0)
        self.assertEqual(sum_metric("", ACTIVE_REQUESTS_METRIC), 0)

    def test_help_and_type_lines_skipped(self):
        text = exposition(("m", 3))
        self.assertEqual(sum_metric(text, ACTIVE_REQUESTS_METRIC), 3)

    def test_multiple_models_summed(self):
        text = exposition(("a", 2), ("b", 5))
        self.assertEqual(sum_metric(text, ACTIVE_REQUESTS_METRIC), 7)

    def test_model_filter(self):
        text = exposition(("a", 2), ("b", 5))
        self.assertEqual(sum_metric(text, ACTIVE_REQUESTS_METRIC, model="b"), 5)
        self.assertEqual(sum_metric(text, ACTIVE_REQUESTS_METRIC, model="zzz"), 0)

    def test_similar_metric_names_not_matched(self):
        text = (
            'dynamo_frontend_lora_active_requests{model="a"} 99\n'
            'dynamo_frontend_inflight_requests{model="a"} 7\n'
            'dynamo_frontend_active_requests{model="a"} 3\n'
        )
        self.assertEqual(sum_metric(text, ACTIVE_REQUESTS_METRIC), 3)

    def test_non_finite_samples_ignored(self):
        text = (
            'dynamo_frontend_active_requests{model="a"} NaN\n'
            'dynamo_frontend_active_requests{model="b"} +Inf\n'
            'dynamo_frontend_active_requests{model="c"} 4\n'
        )
        self.assertEqual(sum_metric(text, ACTIVE_REQUESTS_METRIC), 4)

    def test_unlabelled_series_counted_when_no_filter(self):
        text = "dynamo_frontend_active_requests 6\n"
        self.assertEqual(sum_metric(text, ACTIVE_REQUESTS_METRIC), 6)
        self.assertEqual(sum_metric(text, ACTIVE_REQUESTS_METRIC, model="a"), 0)

    def test_escaped_label_value(self):
        text = 'dynamo_frontend_active_requests{model="a\\"b"} 5\n'
        self.assertEqual(sum_metric(text, ACTIVE_REQUESTS_METRIC, model='a"b'), 5)


# ---------------------------------------------------------------------------
# Policy: hysteresis, cooldown, the feedback hazard
# ---------------------------------------------------------------------------
class TestPolicy(_AsyncCase):
    async def test_no_switch_below_watermark(self):
        ctrl = self.make()
        ctrl._metrics = [exposition(("m", 5))] * 10  # 5 < 10*1
        for _ in range(6):
            decision = await ctrl._poll_once()
            self.assertEqual(decision.action, "noop")
        self.assertEqual(ctrl.posts, [])

    async def test_fires_on_the_kth_consecutive_sample(self):
        """Pins the exact index: with stable_polls=K the switch fires on sample K,
        not K+1.  Subtle enough to be worth its own test."""
        ctrl = self.make(stable_polls=3)
        ctrl._metrics = [exposition(("m", 50))] * 6
        actions = [(await ctrl._poll_once()).action for _ in range(4)]
        self.assertEqual(actions, ["noop", "noop", "switch_up", "noop"])

    async def test_hysteresis_requires_consecutive_breaches(self):
        ctrl = self.make(stable_polls=3)
        high = exposition(("m", 50))
        low = exposition(("m", 1))
        # A single dip must reset the streak, so the 5th poll is the first to see
        # three consecutive breaches.
        ctrl._metrics = [high, low, high, high, high]
        decisions = [await ctrl._poll_once() for _ in range(5)]
        self.assertEqual(
            [d.action for d in decisions], ["noop", "noop", "noop", "noop", "switch_up"]
        )
        self.assertEqual(decisions[4].target, Strategy(4, 1))

    async def test_scrape_failure_does_not_reset_streak_or_count_as_sample(self):
        ctrl = self.make()
        high = exposition(("m", 50))
        ctrl._metrics = [high, high]
        d1 = await ctrl._poll_once()
        d2 = await ctrl._poll_once()
        self.assertEqual((d1.action, d2.action), ("noop", "noop"))
        self.assertEqual(ctrl._streak_up, 2)

        ctrl._metrics_error = OSError("connection reset")  # a blip
        d3 = await ctrl._poll_once()
        self.assertEqual(d3.action, "noop")
        self.assertEqual(d3.reason, "scrape_failed")
        self.assertEqual(ctrl._streak_up, 2, "a metrics blip must not cancel an impending switch")
        self.assertIsNone(d3.concurrency)

        ctrl._metrics_error = None
        ctrl._metrics = [high]
        d4 = await ctrl._poll_once()
        self.assertEqual(d4.action, "switch_up", "streak survived the blip")

    async def test_cooldown_suppresses_after_campaign(self):
        ctrl = self.make(cooldown_s=60.0)
        ctrl._metrics = [exposition(("m", 50))] * 10
        for _ in range(2):  # streak reaches K-1
            await ctrl._poll_once()
        fired = await ctrl._poll_once()  # Kth consecutive sample -> fires
        self.assertEqual(fired.action, "switch_up")
        await ctrl._run_campaign(fired.target)
        self.assertEqual(len(ctrl.posts), 1)

        # Still hot, streak rebuilds, but the cooldown must hold.
        for _ in range(5):
            decision = await ctrl._poll_once()
            self.assertEqual(decision.action, "noop")
            self.assertEqual(decision.reason, "cooldown")
        self.assertEqual(len(ctrl.posts), 1, "no second switch during cooldown")

    async def test_campaign_active_suppresses_evaluation(self):
        ctrl = self.make()
        ctrl._metrics = [exposition(("m", 50))] * 10
        for _ in range(3):
            await ctrl._poll_once()
        ctrl._campaign_active = True  # what a switch in flight looks like
        ctrl._streak_up = ctrl.cfg.stable_polls + 5
        decision = await ctrl._poll_once()
        self.assertEqual(decision.action, "noop")
        self.assertEqual(decision.reason, "campaign_active")

    async def test_feedback_guard_parked_requests_do_not_retrigger(self):
        """Concurrency RISES during a switch (parked add_request calls are still
        counted).  A second campaign must not be launched because of it."""
        worker = FakeWorker(switch_delay_s=0.05)
        ctrl = self.make({"http://w1:9091": worker}, cooldown_s=30.0)
        ctrl._metrics = [exposition(("m", 50))] * 5
        for _ in range(2):
            await ctrl._poll_once()
        fired = await ctrl._poll_once()
        self.assertEqual(fired.action, "switch_up")

        # Concurrency inflates to 4x while the switch drains.
        ctrl._metrics = [exposition(("m", 200))] * 20
        await ctrl._run_campaign(fired.target)
        for _ in range(4):
            decision = await ctrl._poll_once()
            self.assertEqual(decision.action, "noop")
            self.assertEqual(decision.reason, "cooldown")
        self.assertEqual(len(ctrl.posts), 1)

    async def test_second_campaign_at_target_switches_nobody(self):
        """The structural backstop: if the cooldown ever expires while load is
        still inflated, every worker is already at target so nothing switches."""
        worker = FakeWorker(tp=4, pp=1)
        ctrl = self.make({"http://w1:9091": worker}, cooldown_s=0.0)
        ctrl._metrics = [exposition(("m", 500))] * 20
        for _ in range(2):
            await ctrl._poll_once()
        fired = await ctrl._poll_once()
        self.assertEqual(fired.action, "switch_up")
        result = await ctrl._run_campaign(fired.target)
        self.assertEqual(result.switched, 0)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(ctrl.posts, [])
        self.assertTrue(result.ok)

    async def test_denominator_is_configured_not_live(self):
        """expected_workers must not track the reachable instance count, or
        unregistering a worker would lower the threshold and re-trigger."""
        workers = {
            "http://w1:9091": FakeWorker(),
            "http://w2:9091": FakeWorker(unreachable=True),  # gone from the pool
        }
        ctrl = self.make(workers, expected_workers=2, concurrency_factor_up=10.0)
        ctrl._metrics = [exposition(("m", 15))] * 10  # 15 < 10*2 == 20
        for _ in range(5):
            decision = await ctrl._poll_once()
            self.assertEqual(decision.action, "noop")
            self.assertEqual(decision.threshold, 20.0, "denominator stayed configured")
        self.assertEqual(ctrl.posts, [])

    async def test_scale_down_watermark(self):
        ctrl = self.make(
            concurrency_factor_down=2.0,
            strategy_down=Strategy(2, 2),
        )
        ctrl.workers["http://w1:9091"].tp = 4
        ctrl.workers["http://w1:9091"].pp = 1
        ctrl._metrics = [exposition(("m", 1))] * 10  # 1 < 2*1
        for _ in range(2):
            await ctrl._poll_once()
        decision = await ctrl._poll_once()
        self.assertEqual(decision.action, "switch_down")
        self.assertEqual(decision.target, Strategy(2, 2))
        result = await ctrl._run_campaign(decision.target)
        self.assertEqual(result.switched, 1)
        self.assertEqual(ctrl.posts[0][1]["target_tensor_parallel_size"], 2)
        self.assertEqual(ctrl.posts[0][1]["target_pipeline_parallel_size"], 2)

    async def test_decision_threshold_reflects_expected_workers(self):
        ctrl = self.make(expected_workers=3, concurrency_factor_up=10.0)
        ctrl._metrics = [exposition(("m", 0))]
        decision = await ctrl._poll_once()
        self.assertEqual(decision.threshold, 30.0)


# ---------------------------------------------------------------------------
# Campaign semantics
# ---------------------------------------------------------------------------
class TestCampaign(_AsyncCase):
    async def test_skip_when_already_at_target(self):
        """Written first on purpose: this is the behaviour that is impossible
        until the worker reports the strategy it actually applied."""
        ctrl = self.make({"http://w1:9091": FakeWorker(tp=4, pp=1)})
        result = await ctrl._run_campaign(Strategy(4, 1))
        self.assertEqual(result.switched, 0)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(ctrl.posts, [], "no POST for a worker already at target")
        self.assertIn("already at", result.outcomes[0].reason)

    async def test_unfixed_worker_reports_stale_state_after_ok(self):
        """F4 regression guard: without the applied-strategy fix, a successful
        switch still reads back as the startup topology."""
        ctrl = self.make({"http://w1:9091": FakeWorker(tp=2, pp=2, report_applied=False)})
        result = await ctrl._run_campaign(Strategy(4, 1))
        self.assertEqual(result.switched, 1, "the switch itself did happen")
        self.assertIn("stale", result.outcomes[0].reason)

    async def test_rolling_order_is_sequential_and_complete(self):
        workers = {f"http://w{i}:9091": FakeWorker() for i in (1, 2, 3)}
        ctrl = self.make(workers)
        order: list[str] = []
        original = ctrl._post_json

        def recording_post(url, payload, timeout):
            order.append(url.split("/engine/")[0])
            return original(url, payload, timeout)

        ctrl._post_json = recording_post  # type: ignore[method-assign]
        result = await ctrl._run_campaign(Strategy(4, 1))
        self.assertEqual(result.switched, 3)
        self.assertEqual(order, ["http://w1:9091", "http://w2:9091", "http://w3:9091"])
        self.assertTrue(result.ok)

    async def test_unreachable_worker_is_skipped_not_fatal(self):
        workers = {
            "http://w1:9091": FakeWorker(unreachable=True),
            "http://w2:9091": FakeWorker(),
        }
        ctrl = self.make(workers)
        result = await ctrl._run_campaign(Strategy(4, 1))
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.switched, 1)
        self.assertTrue(result.ok, "an unreachable worker must not abort the fleet")
        self.assertEqual(result.outcomes[0].reason, "unreachable")

    async def test_unsupported_worker_skipped(self):
        workers = {
            "http://w1:9091": FakeWorker(supported=False),
            "http://w2:9091": FakeWorker(),
        }
        ctrl = self.make(workers)
        result = await ctrl._run_campaign(Strategy(4, 1))
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.switched, 1)
        self.assertEqual(ctrl.posts, [("http://w2:9091", ctrl.posts[0][1])])

    async def test_worker_already_switching_is_skipped(self):
        workers = {
            "http://w1:9091": FakeWorker(is_switching=True),
            "http://w2:9091": FakeWorker(),
        }
        ctrl = self.make(workers)
        result = await ctrl._run_campaign(Strategy(4, 1))
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.switched, 1)

    async def test_conflict_continues(self):
        workers = {
            "http://w1:9091": FakeWorker(switch_status="conflict", switch_message="busy"),
            "http://w2:9091": FakeWorker(),
        }
        ctrl = self.make(workers)
        result = await ctrl._run_campaign(Strategy(4, 1))
        self.assertTrue(result.ok, "conflict means 'not now', not 'broken'")
        self.assertEqual(result.switched, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(len(ctrl.posts), 2, "the campaign moved on to the next worker")

    async def test_failed_worker_aborts(self):
        workers = {
            "http://w1:9091": FakeWorker(failed=True),
            "http://w2:9091": FakeWorker(),
        }
        ctrl = self.make(workers)
        result = await ctrl._run_campaign(Strategy(4, 1))
        self.assertFalse(result.ok)
        self.assertIn("restart", result.aborted_reason)
        self.assertEqual(ctrl.posts, [], "never POST to a worker that already failed")
        self.assertEqual(len(result.outcomes), 1, "stopped at the first worker")

    async def test_unavailable_aborts(self):
        ctrl = self.make(
            {
                "http://w1:9091": FakeWorker(
                    switch_status="unavailable", switch_message="executor unsafe; restart"
                )
            }
        )
        result = await ctrl._run_campaign(Strategy(4, 1))
        self.assertFalse(result.ok)
        self.assertTrue(result.outcomes[0].abort)

    async def test_error_status_aborts(self):
        ctrl = self.make(
            {"http://w1:9091": FakeWorker(switch_status="error", switch_message="bad request")}
        )
        result = await ctrl._run_campaign(Strategy(4, 1))
        self.assertFalse(result.ok)
        self.assertIn("error", result.aborted_reason)

    async def test_transport_exception_aborts(self):
        ctrl = self.make(
            {"http://w1:9091": FakeWorker(switch_raises=OSError("connection reset"))}
        )
        result = await ctrl._run_campaign(Strategy(4, 1))
        self.assertFalse(result.ok)
        self.assertIn("switch request failed", result.aborted_reason)

    async def test_timeout_aborts_without_assuming_failure(self):
        ctrl = self.make(
            {"http://w1:9091": FakeWorker(switch_delay_s=0.4)}, switch_timeout_s=0.05
        )
        result = await ctrl._run_campaign(Strategy(4, 1))
        self.assertFalse(result.ok)
        self.assertIn("do not assume failure", result.aborted_reason)

    async def test_target_exceeding_physical_world_refused_before_post(self):
        ctrl = self.make({"http://w1:9091": FakeWorker(physical_world_size=4)})
        result = await ctrl._run_campaign(Strategy(8, 1))  # world 8 > physical 4
        self.assertFalse(result.ok)
        self.assertIn("physical_world_size", result.aborted_reason)
        self.assertEqual(ctrl.posts, [], "must be refused before touching the engine")

    async def test_pause_routing_defaults_false_at_n1(self):
        ctrl = self.make({"http://w1:9091": FakeWorker()})
        await ctrl._run_campaign(Strategy(4, 1))
        self.assertIs(ctrl.posts[0][1]["pause_routing"], False)

    async def test_pause_routing_passed_when_configured(self):
        ctrl = self.make({"http://w1:9091": FakeWorker()}, pause_routing=True)
        await ctrl._run_campaign(Strategy(4, 1))
        self.assertIs(ctrl.posts[0][1]["pause_routing"], True)

    async def test_switch_payload_uses_wait_and_queue(self):
        """The whole no-lost-request property rests on these two fields."""
        ctrl = self.make({"http://w1:9091": FakeWorker()})
        await ctrl._run_campaign(Strategy(4, 1))
        payload = ctrl.posts[0][1]
        self.assertEqual(payload["request_handling"], "wait")
        self.assertEqual(payload["admission_handling"], "queue")
        self.assertEqual(payload["new_world_size"], 4)
        self.assertEqual(payload["target_tensor_parallel_size"], 4)
        self.assertEqual(payload["target_pipeline_parallel_size"], 1)
        self.assertEqual(payload["retry_after"], 1)

    async def test_campaign_bookkeeping(self):
        ctrl = self.make({"http://w1:9091": FakeWorker()})
        self.assertFalse(ctrl.campaign_active)
        self.assertEqual(ctrl.campaigns_run, 0)
        await ctrl._run_campaign(Strategy(4, 1))
        self.assertEqual(ctrl.campaigns_run, 1)
        self.assertFalse(ctrl.campaign_active, "flag must clear after the campaign")
        snap = ctrl.snapshot()
        self.assertEqual(snap["campaigns_run"], 1)
        self.assertFalse(snap["campaign_active"])

    async def test_campaign_active_flag_clears_even_on_abort(self):
        ctrl = self.make({"http://w1:9091": FakeWorker(failed=True)})
        await ctrl._run_campaign(Strategy(4, 1))
        self.assertFalse(ctrl.campaign_active)


# ---------------------------------------------------------------------------
# Config / parsing
# ---------------------------------------------------------------------------
class TestStrategyParse(unittest.TestCase):
    def test_valid_forms(self):
        self.assertEqual(Strategy.parse("4x1"), Strategy(4, 1))
        self.assertEqual(Strategy.parse("4X1"), Strategy(4, 1))
        self.assertEqual(Strategy.parse("2*2"), Strategy(2, 2))
        self.assertEqual(Strategy.parse(" 2 x 2 "), Strategy(2, 2))
        self.assertEqual(Strategy.parse("4x1").world_size, 4)
        self.assertEqual(str(Strategy(4, 1)), "4x1")

    def test_invalid_forms(self):
        for bad in ("", "4", "x1", "4x", "0x1", "4x0", "-1x2", "fourxone", "4,1"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    Strategy.parse(bad)


class TestFromEnv(unittest.TestCase):
    def test_disabled_by_default(self):
        cfg = ControllerConfig.from_env({})
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.strategy_up, Strategy(4, 1))
        self.assertIsNone(cfg.strategy_down)
        self.assertEqual(cfg.concurrency_factor_up, 10.0)
        self.assertFalse(cfg.pause_routing)

    def test_full_parse(self):
        cfg = ControllerConfig.from_env(
            {
                "DYN_ELASTIC_SWITCH_ENABLE": "true",
                "DYN_ELASTIC_SWITCH_MODEL": "Qwen3.8-27B",
                "DYN_ELASTIC_SWITCH_METRICS_URL": "http://127.0.0.1:9090/metrics",
                "DYN_ELASTIC_SWITCH_WORKER_URLS": "http://w1:9091/, http://w2:9091",
                "DYN_ELASTIC_SWITCH_EXPECTED_WORKERS": "2",
                "DYN_ELASTIC_SWITCH_POLL_INTERVAL_S": "1.5",
                "DYN_ELASTIC_SWITCH_FACTOR_UP": "8",
                "DYN_ELASTIC_SWITCH_FACTOR_DOWN": "2",
                "DYN_ELASTIC_SWITCH_STABLE_POLLS": "4",
                "DYN_ELASTIC_SWITCH_COOLDOWN_S": "120",
                "DYN_ELASTIC_SWITCH_STRATEGY_UP": "4x1",
                "DYN_ELASTIC_SWITCH_STRATEGY_DOWN": "2x2",
                "DYN_ELASTIC_SWITCH_PAUSE_ROUTING": "yes",
                "DYN_ELASTIC_SWITCH_TIMEOUT_S": "45",
            }
        )
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.model, "Qwen3.8-27B")
        self.assertEqual(cfg.worker_urls, ("http://w1:9091", "http://w2:9091"))
        self.assertEqual(cfg.expected_workers, 2)
        self.assertEqual(cfg.poll_interval_s, 1.5)
        self.assertEqual(cfg.concurrency_factor_up, 8.0)
        self.assertEqual(cfg.concurrency_factor_down, 2.0)
        self.assertEqual(cfg.stable_polls, 4)
        self.assertEqual(cfg.cooldown_s, 120.0)
        self.assertEqual(cfg.strategy_down, Strategy(2, 2))
        self.assertTrue(cfg.pause_routing)
        self.assertEqual(cfg.switch_timeout_s, 45.0)

    def test_expected_workers_defaults_to_url_count(self):
        cfg = ControllerConfig.from_env(
            {
                "DYN_ELASTIC_SWITCH_ENABLE": "true",
                "DYN_ELASTIC_SWITCH_WORKER_URLS": "http://w1:9091,http://w2:9091,http://w3:9091",
            }
        )
        self.assertEqual(cfg.expected_workers, 3)

    def test_enabled_requires_worker_urls(self):
        with self.assertRaises(ValueError) as ctx:
            ControllerConfig.from_env({"DYN_ELASTIC_SWITCH_ENABLE": "true"})
        self.assertIn("WORKER_URLS", str(ctx.exception))

    def test_down_watermark_must_be_below_up(self):
        with self.assertRaises(ValueError) as ctx:
            ControllerConfig.from_env(
                {
                    "DYN_ELASTIC_SWITCH_ENABLE": "true",
                    "DYN_ELASTIC_SWITCH_WORKER_URLS": "http://w1:9091",
                    "DYN_ELASTIC_SWITCH_FACTOR_UP": "5",
                    "DYN_ELASTIC_SWITCH_FACTOR_DOWN": "5",
                    "DYN_ELASTIC_SWITCH_STRATEGY_DOWN": "2x2",
                }
            )
        self.assertIn("oscillates", str(ctx.exception))

    def test_down_watermark_requires_down_strategy(self):
        with self.assertRaises(ValueError) as ctx:
            ControllerConfig.from_env(
                {
                    "DYN_ELASTIC_SWITCH_ENABLE": "true",
                    "DYN_ELASTIC_SWITCH_WORKER_URLS": "http://w1:9091",
                    "DYN_ELASTIC_SWITCH_FACTOR_DOWN": "2",
                }
            )
        self.assertIn("STRATEGY_DOWN", str(ctx.exception))

    def test_bad_number_is_reported_with_its_env_name(self):
        with self.assertRaises(ValueError) as ctx:
            ControllerConfig.from_env(
                {
                    "DYN_ELASTIC_SWITCH_ENABLE": "true",
                    "DYN_ELASTIC_SWITCH_WORKER_URLS": "http://w1:9091",
                    "DYN_ELASTIC_SWITCH_FACTOR_UP": "lots",
                }
            )
        self.assertIn("FACTOR_UP", str(ctx.exception))

    def test_bad_strategy_fails_at_startup_not_mid_campaign(self):
        with self.assertRaises(ValueError):
            ControllerConfig.from_env(
                {
                    "DYN_ELASTIC_SWITCH_ENABLE": "true",
                    "DYN_ELASTIC_SWITCH_WORKER_URLS": "http://w1:9091",
                    "DYN_ELASTIC_SWITCH_STRATEGY_UP": "tp4",
                }
            )

    def test_disabled_config_skips_validation(self):
        cfg = ControllerConfig.from_env({})  # no WORKER_URLS, but disabled
        self.assertFalse(cfg.enabled)


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------
class TestRunLoop(_AsyncCase):
    async def test_disabled_run_returns_immediately(self):
        ctrl = self.make({"http://w1:9091": FakeWorker()}, enabled=False)
        await asyncio.wait_for(ctrl.run(), timeout=1.0)
        self.assertEqual(ctrl.posts, [])

    async def test_run_drives_a_full_campaign_then_cooldown_holds(self):
        ctrl = self.make({"http://w1:9091": FakeWorker()}, cooldown_s=30.0)
        ctrl._metrics = [exposition(("m", 500))] * 200
        task = asyncio.create_task(ctrl.run())
        deadline = time.monotonic() + 5.0
        while ctrl.campaigns_run == 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self.assertEqual(ctrl.campaigns_run, 1)
        await asyncio.sleep(0.2)  # plenty of polls at poll_interval_s=0.01
        self.assertEqual(ctrl.campaigns_run, 1, "cooldown prevented a second campaign")
        self.assertEqual(len(ctrl.posts), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_a_raising_poll_does_not_kill_the_task(self):
        ctrl = self.make({"http://w1:9091": FakeWorker()})
        calls = {"n": 0}
        original = ctrl._poll_once

        async def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated bug")
            return await original()

        ctrl._poll_once = flaky  # type: ignore[method-assign]
        ctrl._metrics = [exposition(("m", 0))] * 50
        task = asyncio.create_task(ctrl.run())
        await asyncio.sleep(0.2)
        self.assertFalse(task.done(), "the controller must survive a bad poll")
        self.assertGreater(calls["n"], 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    unittest.main()
